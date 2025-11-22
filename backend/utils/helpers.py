"""
Utility functions for the PDF Answer Evaluation system
"""
import os
import hashlib
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

def generate_file_hash(file_path: str) -> Optional[str]:
    """Generate SHA256 hash of a file"""
    try:
        with open(file_path, 'rb') as f:
            file_hash = hashlib.sha256()
            for chunk in iter(lambda: f.read(4096), b""):
                file_hash.update(chunk)
        return file_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error generating hash for {file_path}: {e}")
        return None

def validate_file_extension(filename: str, allowed_extensions: set) -> bool:
    """Validate file extension"""
    if not filename:
        return False
    
    file_ext = os.path.splitext(filename)[1].lower()
    return file_ext in allowed_extensions

def format_evaluation_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """Format evaluation results for better readability"""
    if not results:
        return {}
    
    formatted = {
        "summary": results.get("summary", {}),
        "detailed_results": []
    }
    
    for result in results.get("detailed_results", []):
        formatted_result = {
            "question_number": result.get("question_number"),
            "marks_obtained": round(result.get("marks_obtained", 0), 2),
            "total_marks": result.get("total_marks"),
            "percentage": round((result.get("marks_obtained", 0) / result.get("total_marks", 1)) * 100, 2),
            "feedback": result.get("feedback", ""),
            "question": result.get("question", "")[:100] + "..." if len(result.get("question", "")) > 100 else result.get("question", "")
        }
        formatted["detailed_results"].append(formatted_result)
    
    return formatted