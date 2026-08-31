from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from afina_watch.config import LlmCfg, RuleCfg
from afina_watch.models import MediaDigest, NormalizedItem

log = logging.getLogger(__name__)

SYSTEM = """Ты фильтр мониторинга соцсетей. Тебе дают пост (текст + OCR + речь + описание картинок)
и правило (ключевые слова и смысловые фразы).
Ответь ТОЛЬКО JSON:
{"match": true|false, "score": 0.0-1.0, "why": "кратко", "tags": ["..."]}
match=true только если содержание реально про смысл правила, не из-за случайного слова.
Учитывай медиа: скрин, таблица, реквизиты, карта — это тоже содержание."""


def _extract_json(s: str) -> dict[str, Any] | None:
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class LlmJudge:
    def __init__(self, cfg: LlmCfg):
        self.cfg = cfg

    def judge(self, item: NormalizedItem, digest: MediaDigest, rule: RuleCfg) -> dict[str, Any]:
        user = (
            f"Правило: {rule.id}\n"
            f"Ключевые слова: {rule.keywords}\n"
            f"Смысловые фразы: {rule.phrases}\n\n"
            f"Платформа: {item.platform.value}\n"
            f"Источник: {item.source_title or item.source_id}\n"
            f"Автор: {item.author_name or item.author_id}\n"
            f"Текст:\n{digest.search_blob[: self.cfg.max_item_chars]}\n"
        )
        raw = self._complete(user)
        data = _extract_json(raw) or {"match": False, "score": 0.0, "why": "bad-json", "tags": []}
        return {
            "match": bool(data.get("match")),
            "score": float(data.get("score") or 0),
            "why": str(data.get("why") or ""),
            "tags": list(data.get("tags") or []),
        }

    def _complete(self, user: str) -> str:
        if self.cfg.provider == "ollama":
            return self._ollama(user)
        return self._openai(user)

    def _ollama(self, user: str) -> str:
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{self.cfg.host.rstrip('/')}/api/chat",
                json={
                    "model": self.cfg.text_model,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": self.cfg.temperature},
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
            return (r.json().get("message") or {}).get("content") or ""

    def _openai(self, user: str) -> str:
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{self.cfg.host.rstrip('/')}/v1/chat/completions",
                json={
                    "model": self.cfg.text_model,
                    "temperature": self.cfg.temperature,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
