from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from afina_watch.config import LlmCfg, MatchingCfg, MongoCfg
from afina_watch.media.asr import transcribe
from afina_watch.media.ffmpeg_util import extract_audio, extract_frames
from afina_watch.media.ocr import ocr_image
from afina_watch.media.vision import caption_images
from afina_watch.models import MediaDigest, MediaKind, MediaRef, NormalizedItem

log = logging.getLogger(__name__)


class MediaPipeline:
    def __init__(
        self,
        cache_dir: Path,
        llm: LlmCfg,
        matching: MatchingCfg,
        mongo: MongoCfg | None = None,
    ):
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.matching = matching
        self.mongo = mongo
        self._fs = None

    def _gridfs(self):
        if self._fs is not None:
            return self._fs
        if not self.mongo:
            return None
        from pymongo import MongoClient
        from gridfs import GridFS

        client = MongoClient(self.mongo.uri)
        self._fs = GridFS(client[self.mongo.database])
        return self._fs

    def materialize(self, item: NormalizedItem, ref: MediaRef) -> Path | None:
        if ref.local_path and Path(ref.local_path).exists():
            return Path(ref.local_path)

        dest_dir = self.cache / item.platform.value / item.source_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(ref.filename or "").suffix
        if not suffix and ref.mime:
            suffix = mimetypes.guess_extension(ref.mime) or ""
        name = ref.filename or ref.file_id or ref.gridfs_id or "bin"
        dest = dest_dir / f"{item.id.replace(':', '_')}__{Path(name).name}{suffix}"

        if dest.exists() and dest.stat().st_size > 0:
            ref.local_path = str(dest)
            return dest

        if ref.gridfs_id:
            fs = self._gridfs()
            if fs is None:
                log.warning("gridfs id=%s но mongo не сконфигурирован", ref.gridfs_id)
            else:
                try:
                    from bson import ObjectId

                    oid: Any = ref.gridfs_id
                    if len(ref.gridfs_id) == 24:
                        oid = ObjectId(ref.gridfs_id)
                    data = fs.get(oid).read()
                    dest.write_bytes(data)
                    ref.local_path = str(dest)
                    return dest
                except Exception:
                    log.exception("gridfs download failed %s", ref.gridfs_id)

        if ref.url:
            try:
                with httpx.Client(timeout=60, follow_redirects=True) as c:
                    r = c.get(ref.url)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                    ref.local_path = str(dest)
                    return dest
            except Exception:
                log.exception("url download failed %s", ref.url[:80])
        return None

    def digest(self, item: NormalizedItem) -> MediaDigest:
        ocr_parts: list[str] = []
        asr_parts: list[str] = []
        captions: list[str] = []
        vision_paths: list[Path] = []

        for ref in item.media:
            path = self.materialize(item, ref)
            if path is None:
                continue
            kind = ref.kind
            if kind in {MediaKind.photo, MediaKind.sticker, MediaKind.document}:
                text = ocr_image(path)
                if text:
                    ocr_parts.append(text)
                vision_paths.append(path)
            elif kind in {MediaKind.video, MediaKind.audio, MediaKind.voice}:
                work = path.parent / f"{path.stem}_work"
                work.mkdir(exist_ok=True)
                try:
                    wav = work / "audio.wav"
                    extract_audio(path, wav)
                    asr = transcribe(wav, self.llm.whisper_model, self.llm.whisper_device)
                    if asr:
                        asr_parts.append(asr)
                except Exception:
                    log.exception("asr failed %s", path.name)
                if kind == MediaKind.video and self.matching.run_vlm_on_media:
                    try:
                        frames = extract_frames(
                            path,
                            work / "frames",
                            self.matching.video_max_frames,
                            self.matching.video_max_seconds,
                        )
                        vision_paths.extend(frames)
                    except Exception:
                        log.exception("frames failed %s", path.name)

        if self.matching.run_vlm_on_media and vision_paths:
            captions = caption_images(vision_paths[:12], self.llm)

        blob_parts = [item.text, *ocr_parts, *asr_parts, *captions]
        search_blob = "\n".join(p for p in blob_parts if p).strip()
        if len(search_blob) > self.llm.max_item_chars:
            search_blob = search_blob[: self.llm.max_item_chars]

        return MediaDigest(
            ocr_text="\n".join(ocr_parts),
            asr_text="\n".join(asr_parts),
            captions=captions,
            search_blob=search_blob,
        )
