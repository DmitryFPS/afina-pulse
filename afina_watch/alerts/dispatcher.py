from __future__ import annotations

import logging

import httpx

from afina_watch.config import AlertsCfg
from afina_watch.models import WatchResult

log = logging.getLogger(__name__)


def format_alert(result: WatchResult) -> str:
    item = result.item
    rules = ", ".join(h.rule_id for h in result.hits)
    why = "; ".join(h.llm_why for h in result.hits if h.llm_why)
    blob = (result.digest.search_blob or item.text or "")[:600]
    return (
        f"[{item.platform.value}] {item.source_title or item.source_id}\n"
        f"rules: {rules}\n"
        f"{why}\n"
        f"{item.permalink or item.id}\n\n"
        f"{blob}"
    )


class Dispatcher:
    def __init__(self, cfg: AlertsCfg):
        self.cfg = cfg

    async def emit(self, result: WatchResult) -> None:
        text = format_alert(result)
        if self.cfg.stdout:
            print("\n=== MATCH ===\n" + text + "\n", flush=True)
        if self.cfg.telegram.enabled and self.cfg.telegram.bot_token and self.cfg.telegram.chat_id:
            await self._tg(text)
        if self.cfg.webhook.enabled and self.cfg.webhook.url:
            await self._hook(result)

    async def _tg(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.cfg.telegram.bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    url,
                    json={
                        "chat_id": self.cfg.telegram.chat_id,
                        "text": text[:3900],
                        "disable_web_page_preview": True,
                    },
                )
                if r.status_code >= 400:
                    log.error("tg alert %s %s", r.status_code, r.text[:200])
        except Exception:
            log.exception("tg alert failed")

    async def _hook(self, result: WatchResult) -> None:
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                await c.post(self.cfg.webhook.url, json=result.model_dump(mode="json"))
        except Exception:
            log.exception("webhook failed")
