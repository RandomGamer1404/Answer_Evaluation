from fastapi import FastAPI, File, UploadFile, HTTPException, Form, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiofiles
import os
import uuid
from typing import Optional
import logging
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager

from config import settings
from services.ocr_service import OCRService
from services.pdf_service import PDFService
from models.evaluator import PDFAnswerEvaluator
from models.diagram_evaluator import DiagramEvaluator
from services.image_service import ImageService

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variable for evaluator
evaluator = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global evaluator
    logger.info("Starting PDF Answer Evaluation API...")
    logger.info("Preloading AI model (this may take a few minutes)...")
    
    try:
        evaluator = PDFAnswerEvaluator()
        # Validate HF token once at startup to avoid silent failures later
        try:
            ok = evaluator._check_hf_token_valid()
            logger.info(f"HF token status: {'valid' if ok else 'invalid'}")
        except Exception as e:
            logger.warning(f"HF token check failed at startup: {e}")
        logger.info("AI model preloaded successfully!")
    except Exception as e:
        logger.error(f"Failed to preload AI model: {e}")
        evaluator = None
    
    yield
    logger.info("Shutting down PDF Answer Evaluation API...")

app = FastAPI(
    title="PDF Answer Evaluation API",
    description="Backend API for evaluating student answers against answer keys using OCR and AI",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,   # explicitly allow the dev frontend
    allow_credentials=False,                   # no cookies -> can disable credentials
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(f"Allowed CORS origins: {settings.FRONTEND_ORIGINS}")

# Initialize services
ocr_service = OCRService(engine=settings.OCR_ENGINE)
pdf_service = PDFService()
image_service = ImageService()

def get_evaluator():
    """Get the preloaded evaluator"""
    global evaluator
    if evaluator is None:
        logger.warning("Evaluator not preloaded, initializing now...")
        evaluator = PDFAnswerEvaluator()
    return evaluator

async def save_upload_file(upload_file: UploadFile) -> str:
    """Save uploaded file and return the file path"""
    if not upload_file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    # Generate unique filename
    file_id = str(uuid.uuid4())
    file_extension = Path(upload_file.filename).suffix
    filename = f"{file_id}{file_extension}"
    file_path = os.path.join(settings.UPLOAD_DIR, filename)
    
    # Save file
    async with aiofiles.open(file_path, 'wb') as f:
        content = await upload_file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        await f.write(content)
    
    # Validate PDF
    if not pdf_service.validate_pdf(file_path):
        os.remove(file_path)
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    
    return file_path

def _is_supported_image_upload(upload: UploadFile) -> bool:
    """Accept common image MIME types or valid image extensions."""
    allowed_types = {
        "image/jpeg", "image/jpg", "image/png", "image/gif",
        "image/webp", "image/bmp", "image/tiff", "application/octet-stream"
    }
    if (upload.content_type or "").lower() in allowed_types:
        return True
    # Fallback: check extension
    ext = (os.path.splitext(upload.filename or "")[1] or "").lower()
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff'}

async def save_upload_image(upload_file: UploadFile) -> str:
    """Save uploaded image (jpg/png/webp/gif/tiff/bmp) and return file path."""
    # Validate by MIME/ext first
    if not _is_supported_image_upload(upload_file):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    # Generate unique filename (keep original extension)
    file_id = str(uuid.uuid4())
    file_extension = (Path(upload_file.filename or "").suffix or ".jpg").lower()
    filename = f"{file_id}{file_extension}"
    file_path = os.path.join(settings.UPLOAD_DIR, filename)

    # Save file
    async with aiofiles.open(file_path, 'wb') as f:
        content = await upload_file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        await f.write(content)

    # Validate it’s a real image
    if not image_service.validate_image(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Invalid image file")

    return file_path

@app.get("/")
async def root():
    return {
        "message": "PDF Answer Evaluation API",
        "version": "1.0.0",
        "model_loaded": evaluator is not None,
        "fast_mode": getattr(settings, "FAST_MODE", False),
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "ocr_engine": settings.OCR_ENGINE,
        "model_loaded": evaluator is not None,
        "ready_for_evaluation": evaluator is not None,
        "fast_mode": getattr(settings, "FAST_MODE", False),
        "llm_backend": getattr(settings, "LLM_BACKEND", "local"),
        "hf_model": getattr(settings, "HF_INFERENCE_MODEL", None),
    }

@app.post("/initialize-model")
async def initialize_model():
    """Preload the AI model"""
    global evaluator
    if evaluator is None:
        logger.info("Initializing AI model...")
        evaluator = PDFAnswerEvaluator()
        return {"status": "success", "message": "Model initialized successfully"}
    else:
        return {"status": "success", "message": "Model already loaded"}

@app.post("/evaluate")
async def evaluate_answers(
    answer_key: UploadFile = File(..., description="Answer key PDF file"),
    student_answer: UploadFile = File(..., description="Student answer PDF file"),
    max_questions: int = Form(10, description="Maximum number of questions to evaluate"),
    use_ocr_for_student: bool = Form(True, description="Use OCR for student answer extraction"),
    use_ocr_for_answer_key: bool = Form(False, description="Use OCR for answer key extraction")
):
    """
    Evaluate student answers against answer key
    
    - **answer_key**: PDF file containing the answer key
    - **student_answer**: PDF file containing student answers  
    - **max_questions**: Maximum number of questions to evaluate (default: 10)
    - **use_ocr_for_student**: Whether to use OCR for student answer extraction (default: True)
    - **use_ocr_for_answer_key**: Whether to use OCR for answer key extraction (default: False)
    """
    answer_key_path = None
    student_answer_path = None
    
    try:
        # Check if model is loaded
        if evaluator is None:
            raise HTTPException(status_code=503, detail="AI model not loaded. Please wait for initialization to complete.")
        
        # Save uploaded files
        logger.info("Saving uploaded files...")
        answer_key_path = await save_upload_file(answer_key)
        student_answer_path = await save_upload_file(student_answer)
        
        logger.info(f"Files saved: {answer_key_path}, {student_answer_path}")
        
        # Extract text from answer key (offload to thread)
        logger.info("Extracting text from answer key...")
        # 1) First try chosen method
        if use_ocr_for_answer_key:
            answer_key_text = await asyncio.to_thread(ocr_service.extract_text_from_pdf, answer_key_path)
        else:
            answer_key_text = await asyncio.to_thread(pdf_service.extract_text_from_pdf, answer_key_path)

        # 2) If empty, try the opposite
        if not answer_key_text or len(answer_key_text.strip()) < 50:
            logger.warning("Answer key minimal/empty, trying opposite method")
            if use_ocr_for_answer_key:
                answer_key_text = await asyncio.to_thread(pdf_service.extract_text_from_pdf, answer_key_path)
            else:
                answer_key_text = await asyncio.to_thread(ocr_service.extract_text_from_pdf, answer_key_path)

        # 3) If still empty, FORCE Mistral OCR regardless of global engine
        if not answer_key_text or len(answer_key_text.strip()) < 50:
            logger.warning("Answer key still empty, forcing Mistral OCR")
            try:
                mistral_ocr = OCRService(engine="mistral")
                answer_key_text = await asyncio.to_thread(mistral_ocr.extract_text_from_pdf, answer_key_path)
            except Exception as e:
                logger.error(f"Mistral OCR force failed: {e}")

        # 4) Last resort: rasterize + local OCR
        if not answer_key_text or len(answer_key_text.strip()) < 50:
            logger.warning("Answer key still empty after Mistral. Forcing page-by-page OCR")
            answer_key_text = await asyncio.to_thread(ocr_service.extract_text_force_ocr, answer_key_path)

        if not answer_key_text or len(answer_key_text.strip()) < 20:
            raise HTTPException(status_code=400, detail="Could not extract text from answer key PDF")

        # Student extraction (same fallbacks)
        logger.info("Extracting text from student answer using %s...", "OCR" if use_ocr_for_student else "text parser")
        if use_ocr_for_student:
            student_answer_text = await asyncio.to_thread(ocr_service.extract_text_from_pdf, student_answer_path)
        else:
            student_answer_text = await asyncio.to_thread(pdf_service.extract_text_from_pdf, student_answer_path)

        if not student_answer_text or len(student_answer_text.strip()) < 50:
            logger.warning("Student minimal/empty, trying opposite method")
            if use_ocr_for_student:
                student_answer_text = await asyncio.to_thread(pdf_service.extract_text_from_pdf, student_answer_path)
            else:
                student_answer_text = await asyncio.to_thread(ocr_service.extract_text_from_pdf, student_answer_path)

        if not student_answer_text or len(student_answer_text.strip()) < 50:
            logger.warning("Student still empty, forcing page-by-page OCR")
            student_answer_text = await asyncio.to_thread(ocr_service.extract_text_force_ocr, student_answer_path)

        if not student_answer_text or len(student_answer_text.strip()) < 20:
            raise HTTPException(status_code=400, detail="Could not extract text from student answer PDF")

        logger.info(f"Text extraction complete. Answer key: {len(answer_key_text)} chars, Student: {len(student_answer_text)} chars")
        
        # Evaluate (offload heavy CPU to thread so event loop remains responsive)
        logger.info("Starting evaluation...")
        eval_instance = get_evaluator()
        results = await asyncio.to_thread(
            eval_instance.evaluate_with_ocr_text,
            answer_key_text,
            student_answer_text,
            max_questions
        )
        
        if not results:
            raise HTTPException(status_code=500, detail="Evaluation failed")
        
        logger.info("Evaluation completed successfully")
        
        return JSONResponse(content={
            "status": "success",
            "results": results,
            "metadata": {
                "answer_key_filename": answer_key.filename,
                "student_answer_filename": student_answer.filename,
                "max_questions": max_questions,
                "ocr_engine_used": settings.OCR_ENGINE,
                "used_ocr_for_student": use_ocr_for_student,
                "used_ocr_for_answer_key": use_ocr_for_answer_key
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Evaluation error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    finally:
        # Clean up uploaded files
        for file_path in [answer_key_path, student_answer_path]:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Cleaned up file: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not remove file {file_path}: {e}")

@app.post("/evaluate-batch")
async def evaluate_batch(
    answer_key: UploadFile = File(...),
    student_answers: list[UploadFile] = File(...),
    max_questions: int = Form(10),
    use_ocr_for_student: bool = Form(True),
    use_ocr_for_answer_key: bool = Form(False)
):
    """
    Evaluate multiple student answers against a single answer key
    """
    answer_key_path = None
    student_paths = []
    
    try:
        # Check if model is loaded
        if evaluator is None:
            raise HTTPException(status_code=503, detail="AI model not loaded. Please wait for initialization to complete.")
        
        # Save answer key
        answer_key_path = await save_upload_file(answer_key)
        
        # Extract answer key text
        if use_ocr_for_answer_key:
            answer_key_text = ocr_service.extract_text_from_pdf(answer_key_path)
        else:
            answer_key_text = pdf_service.extract_text_from_pdf(answer_key_path)
        
        if not answer_key_text:
            raise HTTPException(status_code=400, detail="Could not extract text from answer key PDF")
        
        # Process each student answer
        eval_instance = get_evaluator()
        batch_results = []
        
        for student_file in student_answers:
            try:
                # Save student file
                student_path = await save_upload_file(student_file)
                student_paths.append(student_path)
                
                # Extract student text
                if use_ocr_for_student:
                    student_text = await asyncio.to_thread(ocr_service.extract_text_from_pdf, student_path)
                else:
                    student_text = await asyncio.to_thread(pdf_service.extract_text_from_pdf, student_path)
                
                if not student_text:
                    batch_results.append({
                        "student_filename": student_file.filename,
                        "status": "error",
                        "error": "Could not extract text from PDF"
                    })
                    continue
                
                # Evaluate
                results = await asyncio.to_thread(
                    eval_instance.evaluate_with_ocr_text,
                    answer_key_text,
                    student_text,
                    max_questions
                )
                
                batch_results.append({
                    "student_filename": student_file.filename,
                    "status": "success",
                    "results": results
                })
                
            except Exception as e:
                batch_results.append({
                    "student_filename": student_file.filename,
                    "status": "error",
                    "error": str(e)
                })
        
        return JSONResponse(content={
            "status": "success",
            "batch_results": batch_results,
            "metadata": {
                "answer_key_filename": answer_key.filename,
                "total_students": len(student_answers),
                "successful_evaluations": len([r for r in batch_results if r["status"] == "success"]),
                "max_questions": max_questions,
                "ocr_engine_used": settings.OCR_ENGINE
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch evaluation error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    finally:
        # Clean up files
        all_paths = [answer_key_path] + student_paths
        for file_path in all_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Could not remove file {file_path}: {e}")

@app.get("/llm-status")
async def llm_status():
    """Check HF token validity and current model config."""
    try:
        ok = evaluator._check_hf_token_valid() if evaluator else False
        return {
            "token_ok": ok,
            "llm_backend": getattr(settings, "LLM_BACKEND", "local"),
            "hf_model": getattr(settings, "HF_INFERENCE_MODEL", None),
            "fallback_models": getattr(settings, "HF_FALLBACK_MODELS", []),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/evaluate-diagram")
async def evaluate_diagram(
    answer_key: UploadFile = File(..., description="Answer key diagram image"),
    student_diagram: UploadFile = File(..., description="Student diagram image")
):
    """
    Evaluate a single student diagram against answer key diagram
    """
    answer_key_path = None
    student_diagram_path = None
    
    try:
        # Validate file types (MIME or extension)
        if not _is_supported_image_upload(answer_key):
            raise HTTPException(status_code=400, detail=f"Answer key must be an image file. Got {answer_key.content_type or 'unknown'}")
        if not _is_supported_image_upload(student_diagram):
            raise HTTPException(status_code=400, detail=f"Student diagram must be an image file. Got {student_diagram.content_type or 'unknown'}")
        
        # Save uploaded files (use image saver, not PDF saver)
        logger.info("Saving uploaded diagram files...")
        answer_key_path = await save_upload_image(answer_key)
        student_diagram_path = await save_upload_image(student_diagram)
        logger.info(f"Diagrams saved: {answer_key_path}, {student_diagram_path}")

        # Validate images with PIL (reject corrupted files early)
        if not image_service.validate_image(answer_key_path):
            raise HTTPException(status_code=400, detail="Answer key image is invalid or corrupted")
        if not image_service.validate_image(student_diagram_path):
            raise HTTPException(status_code=400, detail="Student diagram image is invalid or corrupted")
        
        # Optionally resize very large images
        answer_key_path = image_service.resize_image(answer_key_path)
        student_diagram_path = image_service.resize_image(student_diagram_path)

        # Initialize and evaluate
        diagram_evaluator = DiagramEvaluator()
        logger.info("Starting diagram evaluation...")
        results = await asyncio.to_thread(
            diagram_evaluator.compare_diagrams,
            answer_key_path,
            student_diagram_path
        )
        if not results:
            raise HTTPException(status_code=500, detail="Diagram evaluation failed")

        results["letter_grade"] = diagram_evaluator.get_letter_grade(results.get("overall_score", 0.0))
        logger.info("Diagram evaluation completed successfully")

        return JSONResponse(content={
            "status": "success",
            "results": results,
            "metadata": {
                "answer_key_filename": answer_key.filename,
                "student_diagram_filename": student_diagram.filename,
                "evaluation_engine": "Qwen VL"
            }
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Diagram evaluation error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        for file_path in [answer_key_path, student_diagram_path]:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Cleaned up file: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not remove file {file_path}: {e}")

@app.post("/evaluate-diagram-batch")
async def evaluate_diagram_batch(
    answer_key: UploadFile = File(..., description="Answer key diagram image"),
    student_diagrams: list[UploadFile] = File(..., description="Student diagram images")
):
    """
    Evaluate multiple student diagrams against a single answer key diagram
    """
    answer_key_path = None
    student_paths = []
    
    try:
        if not _is_supported_image_upload(answer_key):
            raise HTTPException(status_code=400, detail=f"Answer key must be an image file. Got {answer_key.content_type or 'unknown'}")
        for student_file in student_diagrams:
            if not _is_supported_image_upload(student_file):
                raise HTTPException(status_code=400, detail=f"All student diagrams must be image files. {student_file.filename} is not valid.")

        logger.info("Saving answer key diagram...")
        answer_key_path = await save_upload_image(answer_key)
        if not image_service.validate_image(answer_key_path):
            raise HTTPException(status_code=400, detail="Answer key image is invalid or corrupted")
        answer_key_path = image_service.resize_image(answer_key_path)

        logger.info(f"Saving {len(student_diagrams)} student diagrams...")
        for student_file in student_diagrams:
            student_path = await save_upload_image(student_file)
            if not image_service.validate_image(student_path):
                raise HTTPException(status_code=400, detail=f"Invalid or corrupted student image: {student_file.filename}")
            student_paths.append(image_service.resize_image(student_path))

        diagram_evaluator = DiagramEvaluator()
        logger.info("Starting batch diagram evaluation...")
        batch_results = await asyncio.to_thread(
            diagram_evaluator.evaluate_batch_diagrams,
            answer_key_path,
            student_paths
        )
        for result in batch_results:
            if "overall_score" in result:
                result["letter_grade"] = diagram_evaluator.get_letter_grade(result["overall_score"])

        return JSONResponse(content={
            "status": "success",
            "batch_results": batch_results,
            "metadata": {
                "answer_key_filename": answer_key.filename,
                "total_diagrams": len(student_diagrams),
                "successful_evaluations": len([r for r in batch_results if "error" not in r]),
                "evaluation_engine": "Qwen VL"
            }
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch diagram evaluation error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        # Clean up files
        all_paths = [answer_key_path] + student_paths
        for file_path in all_paths:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Could not remove file {file_path}: {e}")