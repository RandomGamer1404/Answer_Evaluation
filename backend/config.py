import os
from typing import Optional

# NEW: load .env from backend folder before reading env vars
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
except Exception:
    pass

class Settings:
    # Server Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # File handling
    UPLOAD_DIR: str = "uploads"
    CACHE_FILE: str = "evaluation_cache.json"
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB
    
    # OCR Configuration
    OCR_ENGINE: str = os.getenv("OCR_ENGINE", "mistral")  # default to Mistral
    TESSERACT_PATH: Optional[str] = None  # Set if tesseract not in PATH
    MISTRAL_API_KEY: str = os.getenv("MISTRAL_API_KEY", "")

    # Hugging Face
    HF_TOKEN: str = os.getenv("HF_TOKEN", "")  # now loaded from .env

    # Model Configuration
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    LLM_MODEL_NAME: str = "mistralai/Mistral-7B-Instruct-v0.3"
    LOAD_IN_4BIT: bool = True

    # LLM backend
    LLM_BACKEND: str = "hf"
    HF_INFERENCE_MODEL: str = "mistralai/Mistral-7B-Instruct-v0.2"
    HF_CHAT_BASE_URL: str = "https://router.huggingface.co"
    HF_CHAT_PATH: str = "/v1/chat/completions"

    # Fallback models for Inference API
    HF_FALLBACK_MODELS: list[str] = [
        "microsoft/Phi-3-mini-4k-instruct",
        "HuggingFaceH4/zephyr-7b-beta",
        "google/gemma-2b-it"
    ]

    # Speed/Eval settings
    FAST_MODE: bool = False
    MAX_NEW_TOKENS_EXTRACTION: int = 96
    MAX_NEW_TOKENS_EVAL: int = 1024  # increased from 512
    INPUT_TOKENS_LIMIT_EXTRACTION: int = 512
    INPUT_TOKENS_LIMIT_EVAL: int = 2048  # increased from 1024
    GEN_TIMEOUT_EXTRACTION: int = 30
    GEN_TIMEOUT_EVAL: int = 90  # increased from 60
    IGNORE_CACHE_FOR_EXTRACTION: bool = True

    # CORS
    FRONTEND_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

settings = Settings()

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)