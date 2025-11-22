import io
import pytesseract
from PIL import Image
from typing import List, Optional
from config import settings
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Mistral import (optional)
try:
    from mistralai import Mistral, DocumentURLChunk
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False
    logger.info("mistralai not installed; Mistral OCR disabled.")

# Try to import fitz (PyMuPDF) with better error handling
try:
    import pymupdf as fitz  # Try new import style first
    PDF_READER_LIB = "PyMuPDF"
    logger.info("Successfully imported PyMuPDF")
except ImportError:
    try:
        import fitz  # Fallback to old import style
        PDF_READER_LIB = "PyMuPDF"
        logger.info("Successfully imported PyMuPDF (legacy import)")
    except ImportError as e:
        logger.warning(f"PyMuPDF import failed: {e}. Falling back to PyPDF2 for PDF reading.")
        PDF_READER_LIB = "PyPDF2"
        # Import PyPDF2 as fallback
        import PyPDF2

# Try to import easyocr
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    logger.warning("EasyOCR not available. Only Tesseract will be used.")
    EASYOCR_AVAILABLE = False

class OCRService:
    """
    Unified OCR service supporting:
      - tesseract
      - easyocr
      - mistral (cloud OCR + markdown via Mistral API)
    """
    def __init__(self, engine: str = "tesseract"):
        self.engine = engine.lower()
        self._mistral_client = None

        if self.engine == "mistral":
            if not MISTRAL_AVAILABLE:
                raise RuntimeError("mistralai package not installed. Install and retry.")
            if not settings.MISTRAL_API_KEY:
                raise RuntimeError("MISTRAL_API_KEY not set in environment/.env.")
            self._mistral_client = Mistral(api_key=settings.MISTRAL_API_KEY)
            logger.info("Mistral OCR engine initialized.")
            return  # skip local OCR init

        # Original engines
        if self.engine == "tesseract":
            # Set tesseract path if specified
            if hasattr(settings, 'TESSERACT_PATH') and settings.TESSERACT_PATH:
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_PATH
        elif self.engine == "easyocr":
            if not EASYOCR_AVAILABLE:
                logger.warning("EasyOCR not available, falling back to tesseract.")
                self.engine = "tesseract"
            else:
                self.reader = easyocr.Reader(['en'])
        else:
            raise ValueError(f"Unsupported OCR engine: {engine}")

    # Public dispatcher
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        if self.engine == "mistral":
            return self._mistral_extract_markdown(pdf_path)
        if self.engine == "tesseract":
            if PDF_READER_LIB == "PyMuPDF":
                return self._extract_with_pymupdf(pdf_path)
            return self._extract_with_pypdf2(pdf_path)
        if self.engine == "easyocr":
            if PDF_READER_LIB == "PyMuPDF":
                return self._extract_with_pymupdf(pdf_path)
            return self._extract_with_pypdf2(pdf_path)
        return ""

    # ---------------- Mistral OCR ----------------
    def _mistral_extract_markdown(self, pdf_path: str) -> str:
        try:
            p = Path(pdf_path)
            if not p.is_file():
                logger.error(f"Mistral OCR: file not found {pdf_path}")
                return ""
            # Upload
            up = self._mistral_client.files.upload(
                file={"file_name": p.name, "content": p.read_bytes()},
                purpose="ocr",
            )
            signed = self._mistral_client.files.get_signed_url(file_id=up.id, expiry=1)
            resp = self._mistral_client.ocr.process(
                document=DocumentURLChunk(document_url=signed.url),
                model="mistral-ocr-latest",
                include_image_base64=False
            )
            pages = resp.pages or []
            combined = "\n\n---PAGE BREAK---\n\n".join(
                (pg.markdown or f"[Page {i+1} - empty]") for i, pg in enumerate(pages)
            )
            
            # Log first 200 chars to debug OCR quality
            logger.info(f"Mistral OCR result preview: {combined[:200]}...")
            
            return self._clean_text(combined)
        except Exception as e:
            logger.error(f"Mistral OCR error: {e}")
            return ""

    def extract_structured_json(self, pdf_path: str) -> dict:
        """Optional structured JSON extraction via Mistral LLM after OCR."""
        if self.engine != "mistral":
            logger.warning("Structured JSON extraction only supported with engine='mistral'.")
            return {}
        try:
            markdown = self._mistral_extract_markdown(pdf_path)
            if not markdown:
                return {}
            chat = self._mistral_client.chat.complete(
                model="pixtral-12b-latest",
                messages=[{
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": (
                            "Convert the following OCR markdown of an exam paper into structured JSON.\n"
                            "Return ONLY a JSON object with fields: questions:[{number,question_text,answer_points:[{text}]}].\n"
                            f"Markdown:\n{markdown}"
                        )
                    }]
                }],
                response_format={"type": "json_object"},
                temperature=0
            )
            raw = chat.choices[0].message.content
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if not m:
                return {}
            import json as _json
            return _json.loads(m.group(0))
        except Exception as e:
            logger.error(f"Mistral structured JSON error: {e}")
            return {}

    # ------------- existing methods (_extract_with_pymupdf, _extract_with_pypdf2, _perform_ocr, _tesseract_ocr, _easyocr_ocr, _clean_text) stay unchanged below -------------
    def _extract_with_pymupdf(self, pdf_path: str) -> str:
        """Extract text using PyMuPDF"""
        doc = fitz.open(pdf_path)
        extracted_text = ""
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # First try to extract text directly (for text-based PDFs)
            page_text = page.get_text()
            
            # If no text found or very little text, use OCR
            if len(page_text.strip()) < 50:  # Threshold for OCR
                logger.info(f"Using OCR for page {page_num + 1}")
                
                # Convert page to image
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                
                # Perform OCR
                ocr_text = self._perform_ocr(img)
                extracted_text += ocr_text + "\n"
            else:
                extracted_text += page_text + "\n"
        
        doc.close()
        return self._clean_text(extracted_text)
    
    def _extract_with_pypdf2(self, pdf_path: str) -> str:
        """Extract text using PyPDF2 and OCR"""
        logger.info("Using PyPDF2 for PDF processing - OCR may be limited")
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if len(page_text.strip()) < 50:
                        logger.warning("Limited text found with PyPDF2. Consider installing PyMuPDF for better OCR support.")
                    text += page_text + "\n"
                return self._clean_text(text)
        except Exception as e:
            logger.error(f"PyPDF2 extraction error: {e}")
            return ""
    
    def _perform_ocr(self, image: Image.Image) -> str:
        """Perform OCR based on configured engine"""
        if self.engine == "tesseract":
            return self._tesseract_ocr(image)
        elif self.engine == "easyocr" and EASYOCR_AVAILABLE:
            return self._easyocr_ocr(image)
        else:
            return self._tesseract_ocr(image)
    
    def _tesseract_ocr(self, image: Image.Image) -> str:
        """Perform OCR using Tesseract"""
        try:
            # Configure Tesseract for better accuracy
            config = '--oem 3 --psm 6 -l eng'
            text = pytesseract.image_to_string(image, config=config)
            return text
        except Exception as e:
            logger.error(f"Tesseract OCR error: {e}")
            return ""
    
    def _easyocr_ocr(self, image: Image.Image) -> str:
        """Perform OCR using EasyOCR"""
        try:
            # Convert PIL Image to numpy array for EasyOCR
            import numpy as np
            img_array = np.array(image)
            
            results = self.reader.readtext(img_array)
            text = " ".join([result[1] for result in results])
            return text
        except Exception as e:
            logger.error(f"EasyOCR error: {e}")
            return ""
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize extracted text but preserve line breaks."""
        if not text:
            return ""
        
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'-\s*\n', '', text)
        text = re.sub(r'[ \t\f\v]+', ' ', text)
        text = re.sub(r'[ \t]+\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[^\x00-\x7F]+', '', text)
        return text.strip()