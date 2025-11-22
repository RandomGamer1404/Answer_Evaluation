import os
import PyPDF2
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Try to import fitz (PyMuPDF) with error handling
try:
    import fitz  # PyMuPDF
    PDF_READER_LIB = "PyMuPDF"
except ImportError:
    logger.warning("PyMuPDF (fitz) not found. Using PyPDF2 only.")
    PDF_READER_LIB = "PyPDF2"

class PDFService:
    @staticmethod
    def extract_text_from_pdf(pdf_path: str) -> str:
        """Extract text from PDF (for answer keys that are text-based)"""
        try:
            if PDF_READER_LIB == "PyMuPDF":
                # Try PyMuPDF first
                doc = fitz.open(pdf_path)
                text = ""
                for page in doc:
                    text += page.get_text() + "\n"
                doc.close()
                
                if text.strip():
                    return PDFService._clean_text(text)
            
            # Fallback to PyPDF2
            return PDFService._extract_with_pypdf2(pdf_path)
            
        except Exception as e:
            logger.error(f"Error reading PDF {pdf_path}: {e}")
            return PDFService._extract_with_pypdf2(pdf_path)
    
    @staticmethod
    def _extract_with_pypdf2(pdf_path: str) -> str:
        """Fallback PDF extraction using PyPDF2"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
                return PDFService._clean_text(text)
        except Exception as e:
            logger.error(f"PyPDF2 extraction error for {pdf_path}: {e}")
            return ""
    
    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean extracted text but preserve line breaks for parsing."""
        if not text:
            return ""
        
        import re
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # Remove hyphenated line breaks first
        text = re.sub(r'-\s*\n', '', text)
        # Collapse spaces/tabs but keep newlines
        text = re.sub(r'[ \t\f\v]+', ' ', text)
        # Trim spaces at end of lines
        text = re.sub(r'[ \t]+\n', '\n', text)
        # Squash many blank lines to a single blank line
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove non-ASCII
        text = re.sub(r'[^\x00-\x7F]+', '', text)
        return text.strip()
    
    @staticmethod
    def validate_pdf(file_path: str) -> bool:
        """Validate if file is a valid PDF"""
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                return len(pdf_reader.pages) > 0
        except:
            return False