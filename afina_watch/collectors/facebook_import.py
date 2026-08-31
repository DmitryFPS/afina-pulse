from __future__ import annotations

"""Inbox-коллектор для чужих Facebook-инструментов.

Не скрейпит Facebook. Читает JSON/JSONL, который вы выгрузили
Forage, fbn, расширением или руками, и приводит к NormalizedItem.

Ожидаемые формы одного поста (берём что найдём):

  Forage-ish:
    {id, url, text|message|content, created_at|timestamp|created_time,
     author|{id,name}, attachments|images|media: [{url,type}]}

  kevinzg/facebook-scraper:
    {post_id, post_url, text, time, user_id, username, images}

  fbn notify payload / плоский dict с permalink.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from afina_watch.models import MediaKind, MediaRef, NormalizedItem, Platform

log = logging.getLogger(__name__)


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _parse_ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        if v > 10_000_000_000:
            v = v / 1000
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if isinstance(v, str) and v:
        raw = v.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def parse_fb_post(raw: dict[str, Any], default_source: str = "inbox") -> NormalizedItem | None:
    pid = raw.get("id") or raw.get("post_id") or raw.get("postId")
    url = raw.get("url") or raw.get("post_url") or raw.get("permalink") or raw.get("permalink_url")
    if not pid and url:
        pid = url.rstrip("/").split("/")[-1]
    if not pid:
        return None

    text = raw.get("text") or raw.get("message") or raw.get("content") or raw.get("story") or ""
    author = raw.get("author") or raw.get("from") or raw.get("user") or {}
    if isinstance(author, str):
        author = {"name": author}
    author_id = str(
        raw.get("user_id") or raw.get("author_id") or author.get("id") or ""
    ) or None
    author_name = (
        raw.get("username")
        or raw.get("author_name")
        or author.get("name")
        or author.get("username")
    )
    source_id = str(
        raw.get("group_id")
        or raw.get("page_id")
        or raw.get("source_id")
        or default_source
    )
    source_title = raw.get("group_name") or raw.get("page_name") or raw.get("source_title")

    media: list[MediaRef] = []
    for key in ("media", "attachments", "images", "photos", "videos"):
        for blob in _as_list(raw.get(key)):
            if isinstance(blob, str):
                kind = MediaKind.video if any(x in blob for x in (".mp4", "video")) else MediaKind.photo
                media.append(MediaRef(kind=kind, url=blob))
                continue
            if not isinstance(blob, dict):
                continue
            url_m = (
                blob.get("url")
                or blob.get("src")
                or blob.get("uri")
                or (blob.get("media") or {}).get("image", {}).get("src")
            )
            if not url_m:
                continue
            t = str(blob.get("type") or blob.get("kind") or key).lower()
            kind = MediaKind.video if "video" in t else MediaKind.photo
            media.append(
                MediaRef(kind=kind, url=url_m, filename=blob.get("filename") or blob.get("title"))
            )

    ts = raw.get("created_at") or raw.get("created_time") or raw.get("timestamp") or raw.get("time")
    return NormalizedItem(
        id=f"fb:{pid}",
        platform=Platform.facebook,
        source_id=source_id,
        source_title=source_title,
        author_id=author_id,
        author_name=author_name,
        permalink=url,
        published_at=_parse_ts(ts),
        text=str(text),
        media=media,
        raw=raw,
    )


def _iter_payload(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, dict):
        for key in ("posts", "data", "items", "results"):
            if isinstance(obj.get(key), list):
                return [x for x in obj[key] if isinstance(x, dict)]
        return [obj]
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


class FacebookImportCollector:
    """Смотрит каталог inbox и забирает новые JSON/JSONL файлы."""

    name = "facebook_import"

    def __init__(self, inbox_dir: str, poll_seconds: int = 20, archive: bool = True):
        self.inbox = Path(inbox_dir)
        self.poll_seconds = poll_seconds
        self.archive = archive
        self._seen_files: set[str] = set()

    async def stream(self) -> AsyncIterator[NormalizedItem]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        done = self.inbox / "_done"
        if self.archive:
            done.mkdir(exist_ok=True)
        log.info("facebook inbox watch %s", self.inbox)
        while True:
            files = sorted(
                p
                for p in self.inbox.iterdir()
                if p.is_file() and p.suffix.lower() in {".json", ".jsonl"} and not p.name.startswith("_")
            )
            for path in files:
                key = str(path)
                if key in self._seen_files:
                    continue
                try:
                    async for item in self._emit_file(path):
                        yield item
                    self._seen_files.add(key)
                    if self.archive:
                        path.rename(done / path.name)
                except Exception:
                    log.exception("facebook inbox parse failed %s", path.name)
            await asyncio.sleep(self.poll_seconds)

    async def _emit_file(self, path: Path) -> AsyncIterator[NormalizedItem]:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            objs = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                objs.append(json.loads(line))
        else:
            objs = _iter_payload(json.loads(text))
        log.info("facebook inbox %s posts=%d", path.name, len(objs))
        for raw in objs:
            item = parse_fb_post(raw, default_source=path.stem)
            if item:
                yield item
