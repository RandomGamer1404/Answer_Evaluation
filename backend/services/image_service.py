import os
import uuid
from typing import List, Optional
from PIL import Image
import logging

logger = logging.getLogger(__name__)

class ImageService:
    def __init__(self):
        self.supported_formats = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}
        self.max_file_size = 10 * 1024 * 1024  # 10MB
    
    def validate_image(self, file_path: str) -> bool:
        """Validate if file is a valid image"""
        try:
            with Image.open(file_path) as img:
                img.verify()
            return True
        except Exception as e:
            logger.error(f"Image validation failed for {file_path}: {e}")
            return False
    
    def get_image_info(self, file_path: str) -> dict:
        """Get image information"""
        try:
            with Image.open(file_path) as img:
                return {
                    "format": img.format,
                    "mode": img.mode,
                    "size": img.size,
                    "width": img.width,
                    "height": img.height
                }
        except Exception as e:
            logger.error(f"Failed to get image info for {file_path}: {e}")
            return {}
    
    def resize_image(self, file_path: str, max_width: int = 1920, max_height: int = 1080) -> str:
        """Resize image if it's too large"""
        try:
            with Image.open(file_path) as img:
                if img.width <= max_width and img.height <= max_height:
                    return file_path
                
                # Calculate new size maintaining aspect ratio
                ratio = min(max_width / img.width, max_height / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                
                # Resize and save
                resized_img = img.resize(new_size, Image.Resampling.LANCZOS)
                resized_path = file_path.replace('.', '_resized.')
                resized_img.save(resized_path, img.format)
                
                # Remove original and rename resized
                os.remove(file_path)
                os.rename(resized_path, file_path)
                
                logger.info(f"Resized image from {img.size} to {new_size}")
                return file_path
        except Exception as e:
            logger.error(f"Failed to resize image {file_path}: {e}")
            return file_path