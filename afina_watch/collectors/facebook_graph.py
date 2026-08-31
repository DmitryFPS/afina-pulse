from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx

from afina_watch.config import FacebookCfg
from afina_watch.models import MediaKind, MediaRef, NormalizedItem, Platform

log = logging.getLogger(__name__)


class FacebookGraphCollector:
    """Только официальный Graph API: страницы, которыми вы управляете."""

    name = "facebook_graph"

    def __init__(self, cfg: FacebookCfg):
        self.cfg = cfg
        self._seen: set[str] = set()
        self.since: datetime | None = None

    async def stream(self) -> AsyncIterator[NormalizedItem]:
        if not self.cfg.enabled:
            return
        tokens = self.cfg.graph.page_tokens
        if not tokens:
            log.warning("facebook.graph.page_tokens пуст — коллектор спит")
            return

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                for page in tokens:
                    try:
                        async for item in self._poll_page(client, page.page_id, page.token):
                            yield item
                    except Exception:
                        log.exception("facebook graph poll failed page=%s", page.page_id)
                await asyncio.sleep(self.cfg.graph.poll_seconds)

    async def _poll_page(
        self, client: httpx.AsyncClient, page_id: str, token: str
    ) -> AsyncIterator[NormalizedItem]:
        ver = self.cfg.graph.api_version
        url = f"https://graph.facebook.com/{ver}/{page_id}/feed"
        params = {
            "access_token": token,
            "fields": "id,message,created_time,permalink_url,from,attachments{type,url,media,subattachments,title,description}",
            "limit": 25,
        }
        if self.since:
            params["since"] = int(self.since.timestamp())
        r = await client.get(url, params=params)
        if r.status_code >= 400:
            log.error("graph %s %s %s", page_id, r.status_code, r.text[:300])
            return
        data = r.json().get("data") or []
        for post in data:
            pid = str(post.get("id"))
            if not pid or pid in self._seen:
                continue
            self._seen.add(pid)
            if len(self._seen) > 50_000:
                self._seen = set(list(self._seen)[-10_000:])
            yield self._to_item(page_id, post)

    def _to_item(self, page_id: str, post: dict[str, Any]) -> NormalizedItem:
        media: list[MediaRef] = []
        atts = ((post.get("attachments") or {}).get("data")) or []
        for a in atts:
            media.extend(self._att_media(a))
            for sub in ((a.get("subattachments") or {}).get("data")) or []:
                media.extend(self._att_media(sub))

        created = post.get("created_time")
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else datetime.now(timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)

        author = post.get("from") or {}
        return NormalizedItem(
            id=f"fb:{post.get('id')}",
            platform=Platform.facebook,
            source_id=page_id,
            source_title=author.get("name"),
            author_id=str(author.get("id") or "") or None,
            author_name=author.get("name"),
            permalink=post.get("permalink_url"),
            published_at=ts,
            text=post.get("message") or "",
            media=media,
            raw=post,
        )

    def _att_media(self, att: dict[str, Any]) -> list[MediaRef]:
        t = (att.get("type") or "").lower()
        kind = MediaKind.other
        if "photo" in t or "image" in t:
            kind = MediaKind.photo
        elif "video" in t:
            kind = MediaKind.video
        media = att.get("media") or {}
        image = media.get("image") or {}
        src = image.get("src") or att.get("url") or media.get("source")
        if not src:
            return []
        return [MediaRef(kind=kind, url=src, filename=att.get("title"))]
