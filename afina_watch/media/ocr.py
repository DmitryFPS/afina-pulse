from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def ocr_image(path: Path) -> str:
    """Лёгкий локальный OCR. Основной разбор картинок делает VLM;
    Tesseract — страховка, если VLM не поднят."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        img = Image.open(path)
        text = pytesseract.image_to_string(img, lang="rus+eng")
        return (text or "").strip()
    except Exception:
        log.debug("tesseract failed on %s", path, exc_info=True)
        return ""
