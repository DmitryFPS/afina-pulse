from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from afina_watch.config import TelegramCfg
from afina_watch.models import MediaKind, MediaRef, NormalizedItem, Platform

log = logging.getLogger(__name__)


def _kind_from_mime(mime: str | None, hint: str | None = None) -> MediaKind:
    m = (mime or hint or "").lower()
    if m.startswith("image") or "photo" in m:
        return MediaKind.photo
    if m.startswith("video"):
        return MediaKind.video
    if "voice" in m or "ogg" in m:
        return MediaKind.voice
    if m.startswith("audio"):
        return MediaKind.audio
    if "sticker" in m:
        return MediaKind.sticker
    return MediaKind.document if m else MediaKind.other


def event_to_item(ev: dict[str, Any]) -> NormalizedItem | None:
    """Relay event → NormalizedItem. Payload shape may differ by Relay version;
    we read several common keys and ignore junk."""
    payload = ev.get("payload") or ev.get("data") or ev
    if not isinstance(payload, dict):
        return None

    msg = payload.get("message") or payload.get("post") or payload
    text = (
        msg.get("text")
        or msg.get("message")
        or payload.get("text")
        or ev.get("text")
        or ""
    )
    if isinstance(text, dict):
        text = text.get("text") or ""

    source_id = str(
        payload.get("peer_id")
        or payload.get("chat_id")
        or payload.get("channel_id")
        or ev.get("source_id")
        or ev.get("route_id")
        or "unknown"
    )
    mid = str(
        payload.get("message_id")
        or msg.get("id")
        or ev.get("_id")
        or ev.get("id")
        or ""
    )
    if not mid:
        return None

    media: list[MediaRef] = []
    for raw_m in _iter_media(payload, msg):
        media.append(raw_m)

    ts = payload.get("date") or ev.get("created_at") or ev.get("ts")
    published = _parse_ts(ts)

    return NormalizedItem(
        id=f"tg:{source_id}:{mid}",
        platform=Platform.telegram,
        source_id=source_id,
        source_title=payload.get("chat_title") or payload.get("source_title"),
        author_id=str(payload.get("from_id") or payload.get("author_id") or "") or None,
        author_name=payload.get("from_name") or payload.get("author_name"),
        permalink=payload.get("permalink") or payload.get("url"),
        published_at=published,
        text=str(text),
        media=media,
        raw=ev,
    )


def _iter_media(payload: dict[str, Any], msg: dict[str, Any]) -> list[MediaRef]:
    out: list[MediaRef] = []
    blobs = []
    for key in ("media", "attachments", "files"):
        v = msg.get(key) or payload.get(key)
        if isinstance(v, list):
            blobs.extend(v)
        elif isinstance(v, dict):
            blobs.append(v)
    gfs = payload.get("gridfs") or payload.get("media_gridfs")
    if isinstance(gfs, list):
        blobs.extend(gfs)
    elif isinstance(gfs, dict):
        blobs.append(gfs)

    for b in blobs:
        if not isinstance(b, dict):
            continue
        out.append(
            MediaRef(
                kind=_kind_from_mime(b.get("mime") or b.get("mime_type"), b.get("type")),
                file_id=str(b.get("file_id") or b.get("id") or "") or None,
                gridfs_id=str(b.get("gridfs_id") or b.get("grid_id") or b.get("_id") or "")
                or None,
                url=b.get("url"),
                mime=b.get("mime") or b.get("mime_type"),
                filename=b.get("filename") or b.get("name"),
                duration_sec=b.get("duration"),
            )
        )
    return out


def _parse_ts(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class TelegramRelayCollector:
    name = "telegram"

    def __init__(self, cfg: TelegramCfg):
        self.cfg = cfg

    async def stream(self) -> AsyncIterator[NormalizedItem]:
        mode = self.cfg.mode
        if mode in {"kafka", "both"}:
            async for item in self._kafka():
                yield item
        elif mode == "mongo_tail":
            async for item in self._mongo_tail():
                yield item

    async def _kafka(self) -> AsyncIterator[NormalizedItem]:
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            log.error("aiokafka не установлен")
            return

        consumer = AIOKafkaConsumer(
            self.cfg.kafka.topic,
            bootstrap_servers=self.cfg.kafka.bootstrap,
            group_id=self.cfg.kafka.group_id,
            enable_auto_commit=True,
            auto_offset_reset="latest",
            value_deserializer=lambda v: v,
        )
        await consumer.start()
        log.info("kafka subscribed %s / %s", self.cfg.kafka.bootstrap, self.cfg.kafka.topic)
        try:
            async for msg in consumer:
                try:
                    body = msg.value
                    ev = json.loads(body.decode("utf-8")) if isinstance(body, (bytes, bytearray)) else body
                    if isinstance(ev, str):
                        ev = json.loads(ev)
                    item = event_to_item(ev if isinstance(ev, dict) else {"payload": ev})
                    if item:
                        yield item
                except Exception:
                    log.exception("kafka message parse failed")
        finally:
            await consumer.stop()

    async def _mongo_tail(self) -> AsyncIterator[NormalizedItem]:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError:
            log.error("motor не установлен")
            return

        client = AsyncIOMotorClient(self.cfg.mongo.uri)
        col = client[self.cfg.mongo.database][self.cfg.mongo.events_collection]
        last_id = None
        log.info("mongo tail %s.%s", self.cfg.mongo.database, self.cfg.mongo.events_collection)
        while True:
            q: dict[str, Any] = {"persist_status": {"$in": ["done", None]}}
            if last_id is not None:
                q["_id"] = {"$gt": last_id}
            cursor = col.find(q).sort("_id", 1).limit(100)
            n = 0
            async for doc in cursor:
                last_id = doc["_id"]
                n += 1
                doc["_id"] = str(doc["_id"])
                item = event_to_item(doc)
                if item:
                    yield item
            if n == 0:
                await asyncio.sleep(2)

    async def backfill(self, since: datetime) -> AsyncIterator[NormalizedItem]:
        """Догон архива Relay за окно (по умолчанию 7 дней)."""
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError:
            log.error("motor не установлен — backfill невозможен")
            return

        client = AsyncIOMotorClient(self.cfg.mongo.uri)
        col = client[self.cfg.mongo.database][self.cfg.mongo.events_collection]
        q: dict[str, Any] = {
            "$or": [
                {"payload.date": {"$gte": since}},
                {"created_at": {"$gte": since}},
                {"ts": {"$gte": since.timestamp()}},
            ]
        }
        log.info("telegram backfill since=%s", since.isoformat())
        cursor = col.find(q).batch_size(100)
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            item = event_to_item(doc)
            if item and item.published_at >= since:
                yield item
