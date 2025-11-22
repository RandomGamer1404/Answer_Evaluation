import os
import json
import base64
import logging
from typing import List, Dict, Any, Optional
from huggingface_hub import InferenceClient
from config import settings
from pathlib import Path
import time

logger = logging.getLogger(__name__)

class DiagramEvaluator:
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Qwen VL diagram evaluator"""
        self.api_key = api_key or settings.HF_TOKEN
        if not self.api_key:
            raise ValueError("HF_TOKEN not provided for Qwen VL API")
        self.client = InferenceClient(api_key=self.api_key)
        # Use a valid repo id (no colon/provider suffix)
        self.model = "Qwen/Qwen2.5-VL-32B-Instruct"
        logger.info("Qwen VL Diagram Evaluator initialized")

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64 for API"""
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"Error encoding image {image_path}: {e}")
            raise

    def _create_data_url(self, image_path: str) -> str:
        """Create data URL from image path"""
        try:
            with open(image_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode('utf-8')
                # Detect image format
                ext = Path(image_path).suffix.lower()
                if ext in ['.jpg', '.jpeg']:
                    mime_type = 'image/jpeg'
                elif ext == '.png':
                    mime_type = 'image/png'
                elif ext == '.gif':
                    mime_type = 'image/gif'
                else:
                    mime_type = 'image/jpeg'  # default
                
                return f"data:{mime_type};base64,{image_data}"
        except Exception as e:
            logger.error(f"Error creating data URL for {image_path}: {e}")
            raise

    def compare_diagrams(self, answer_key_path: str, student_diagram_path: str) -> Dict[str, Any]:
        """Compare student diagram with answer key diagram"""
        try:
            logger.info(f"Comparing diagrams: {student_diagram_path} vs {answer_key_path}")
            
            # Create data URLs
            answer_key_url = self._create_data_url(answer_key_path)
            student_url = self._create_data_url(student_diagram_path)
            
            # Build evaluation prompt
            evaluation_prompt = """
You are an expert diagram evaluator. Compare the student's diagram with the answer key diagram and provide detailed evaluation.

Analyze these aspects:
1. Overall accuracy and correctness
2. Component labeling accuracy
3. Structural relationships and connections
4. Missing or incorrect elements
5. Visual clarity and organization

Provide your response in this JSON format:
{
    "overall_score": 0.85,
    "accuracy_score": 0.80,
    "labeling_score": 0.90,
    "structure_score": 0.85,
    "completeness_score": 0.80,
    "clarity_score": 0.90,
    "detailed_feedback": "Detailed analysis of the diagram...",
    "correct_elements": ["Element 1", "Element 2"],
    "missing_elements": ["Missing Element 1"],
    "incorrect_elements": ["Incorrect Element 1"],
    "suggestions": ["Suggestion 1", "Suggestion 2"]
}

Answer Key Diagram (Reference):
[First image shows the correct/expected diagram]

Student's Diagram (To be evaluated):
[Second image shows the student's submitted diagram]
"""

            # Make API call
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": evaluation_prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": answer_key_url}
                            },
                            {
                                "type": "image_url", 
                                "image_url": {"url": student_url}
                            }
                        ]
                    }
                ],
                max_tokens=1024,
                temperature=0.1
            )
            
            # Parse response
            response_text = completion.choices[0].message.content
            logger.info(f"Qwen VL response length: {len(response_text)}")
            
            # Try to extract JSON from response
            try:
                # Look for JSON block in response
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                
                if start_idx != -1 and end_idx != 0:
                    json_str = response_text[start_idx:end_idx]
                    result = json.loads(json_str)
                    
                    # Validate and normalize scores
                    for score_key in ['overall_score', 'accuracy_score', 'labeling_score', 
                                    'structure_score', 'completeness_score', 'clarity_score']:
                        if score_key in result:
                            result[score_key] = max(0.0, min(1.0, float(result.get(score_key, 0.0))))
                    
                    return result
                else:
                    # Fallback: create structured response from text
                    return {
                        "overall_score": 0.5,
                        "accuracy_score": 0.5,
                        "labeling_score": 0.5,
                        "structure_score": 0.5,
                        "completeness_score": 0.5,
                        "clarity_score": 0.5,
                        "detailed_feedback": response_text,
                        "correct_elements": [],
                        "missing_elements": [],
                        "incorrect_elements": [],
                        "suggestions": ["Review the model response for detailed feedback"]
                    }
                    
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse failed, using fallback: {e}")
                return {
                    "overall_score": 0.5,
                    "accuracy_score": 0.5,
                    "labeling_score": 0.5,
                    "structure_score": 0.5,
                    "completeness_score": 0.5,
                    "clarity_score": 0.5,
                    "detailed_feedback": response_text,
                    "correct_elements": [],
                    "missing_elements": [],
                    "incorrect_elements": [],
                    "suggestions": ["Please review the detailed feedback above"]
                }
                
        except Exception as e:
            logger.error(f"Diagram comparison failed: {e}")
            return {
                "overall_score": 0.0,
                "accuracy_score": 0.0,
                "labeling_score": 0.0,
                "structure_score": 0.0,
                "completeness_score": 0.0,
                "clarity_score": 0.0,
                "detailed_feedback": f"Evaluation failed: {str(e)}",
                "correct_elements": [],
                "missing_elements": [],
                "incorrect_elements": [],
                "suggestions": ["Please try uploading again with clear, high-quality images"]
            }

    def evaluate_batch_diagrams(self, answer_key_path: str, student_diagram_paths: List[str]) -> List[Dict[str, Any]]:
        """Evaluate multiple student diagrams against single answer key"""
        results = []
        
        for i, student_path in enumerate(student_diagram_paths):
            try:
                logger.info(f"Evaluating diagram {i+1}/{len(student_diagram_paths)}")
                result = self.compare_diagrams(answer_key_path, student_path)
                result["student_diagram"] = Path(student_path).name
                results.append(result)
                
                # Small delay to avoid rate limiting
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Failed to evaluate diagram {student_path}: {e}")
                results.append({
                    "student_diagram": Path(student_path).name,
                    "overall_score": 0.0,
                    "error": str(e),
                    "detailed_feedback": "Failed to process this diagram"
                })
        
        return results

    def get_letter_grade(self, score: float) -> str:
        """Convert numerical score to letter grade"""
        percentage = score * 100
        if percentage >= 97: return 'A+'
        elif percentage >= 93: return 'A'
        elif percentage >= 90: return 'A-'
        elif percentage >= 87: return 'B+'
        elif percentage >= 83: return 'B'
        elif percentage >= 80: return 'B-'
        elif percentage >= 77: return 'C+'
        elif percentage >= 73: return 'C'
        elif percentage >= 70: return 'C-'
        elif percentage >= 60: return 'D'
        else: return 'F'