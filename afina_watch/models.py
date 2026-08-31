from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Platform(str, Enum):
    telegram = "telegram"
    facebook = "facebook"


class MediaKind(str, Enum):
    photo = "photo"
    video = "video"
    audio = "audio"
    voice = "voice"
    document = "document"
    sticker = "sticker"
    other = "other"


class MediaRef(BaseModel):
    kind: MediaKind
    file_id: str | None = None
    gridfs_id: str | None = None
    url: str | None = None
    mime: str | None = None
    filename: str | None = None
    local_path: str | None = None
    duration_sec: float | None = None


class NormalizedItem(BaseModel):
    id: str
    platform: Platform
    source_id: str
    source_title: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    permalink: str | None = None
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    text: str = ""
    media: list[MediaRef] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class MediaDigest(BaseModel):
    ocr_text: str = ""
    asr_text: str = ""
    captions: list[str] = Field(default_factory=list)
    search_blob: str = ""


class MatchHit(BaseModel):
    rule_id: str
    keyword_hits: list[str] = Field(default_factory=list)
    semantic_score: float | None = None
    llm_match: bool | None = None
    llm_score: float | None = None
    llm_why: str | None = None
    tags: list[str] = Field(default_factory=list)


class WatchResult(BaseModel):
    item: NormalizedItem
    digest: MediaDigest
    hits: list[MatchHit]
    matched: bool
