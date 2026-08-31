from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_model = None
_model_name = None


def transcribe(path: Path, model_name: str = "medium", device: str = "auto") -> str:
    global _model, _model_name
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper не установлен — ASR пропущен")
        return ""

    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    compute = "float16" if device == "cuda" else "int8"
    if _model is None or _model_name != f"{model_name}:{device}":
        log.info("loading whisper %s on %s", model_name, device)
        _model = WhisperModel(model_name, device=device, compute_type=compute)
        _model_name = f"{model_name}:{device}"

    segments, info = _model.transcribe(str(path), vad_filter=True)
    parts = [s.text.strip() for s in segments if s.text and s.text.strip()]
    log.debug("asr lang=%s duration=%.1f parts=%d", info.language, info.duration, len(parts))
    return "\n".join(parts)
