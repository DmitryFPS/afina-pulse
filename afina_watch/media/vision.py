from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from afina_watch.config import LlmCfg

log = logging.getLogger(__name__)

CAPTION_PROMPT = (
    "Опиши изображение по-русски. Если есть текст — перепиши его целиком. "
    "Если таблица, реквизиты, паспорт, скрин чата, карта, схема — скажи это явно. "
    "Коротко, факты, без воды."
)


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def caption_images(paths: list[Path], cfg: LlmCfg) -> list[str]:
    if not paths:
        return []
    if cfg.provider != "ollama":
        return _openai_compat_vision(paths, cfg)

    out: list[str] = []
    for p in paths:
        try:
            with httpx.Client(timeout=180) as client:
                r = client.post(
                    f"{cfg.host.rstrip('/')}/api/chat",
                    json={
                        "model": cfg.vision_model,
                        "stream": False,
                        "messages": [
                            {
                                "role": "user",
                                "content": CAPTION_PROMPT,
                                "images": [_b64(p)],
                            }
                        ],
                    },
                )
            r.raise_for_status()
            msg = r.json().get("message") or {}
            text = (msg.get("content") or "").strip()
            if text:
                out.append(text)
        except Exception:
            log.exception("vlm caption failed %s", p.name)
    return out


def _openai_compat_vision(paths: list[Path], cfg: LlmCfg) -> list[str]:
    out: list[str] = []
    url = f"{cfg.host.rstrip('/')}/v1/chat/completions"
    for p in paths:
        try:
            data_url = f"data:image/jpeg;base64,{_b64(p)}"
            with httpx.Client(timeout=180) as client:
                r = client.post(
                    url,
                    json={
                        "model": cfg.vision_model,
                        "temperature": cfg.temperature,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": CAPTION_PROMPT},
                                    {"type": "image_url", "image_url": {"url": data_url}},
                                ],
                            }
                        ],
                    },
                )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            if text:
                out.append(text)
        except Exception:
            log.exception("compat vlm failed %s", p.name)
    return out
