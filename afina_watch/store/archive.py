from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from afina_watch.config import WindowCfg
from afina_watch.store.db import Store

log = logging.getLogger(__name__)


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def pack_rows(rows: list[dict], dest: Path, include_media: bool, media_root: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    published = [r.get("published_at") or "" for r in rows]
    since = min(published) if published else ""
    until = max(published) if published else ""
    matches = sum(1 for r in rows if r.get("matched"))

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        lines = []
        media_copied = 0
        for r in rows:
            rec = {
                "item_id": r["item_id"],
                "platform": r["platform"],
                "source_id": r["source_id"],
                "source_title": r.get("source_title"),
                "permalink": r.get("permalink"),
                "published_at": r.get("published_at"),
                "ingested_at": r.get("ingested_at"),
                "matched": r.get("matched"),
                "text": r.get("text"),
                "item": json.loads(r["item_json"]) if r.get("item_json") else None,
                "digest": json.loads(r["digest_json"]) if r.get("digest_json") else None,
            }
            lines.append(json.dumps(rec, ensure_ascii=False))
            if include_media and r.get("media_paths"):
                for p in str(r["media_paths"]).splitlines():
                    src = Path(p)
                    if not src.exists():
                        continue
                    try:
                        rel = src.resolve().relative_to(media_root.resolve())
                    except ValueError:
                        rel = Path(src.name)
                    zf.write(src, f"media/{rel.as_posix()}")
                    media_copied += 1
        zf.writestr("items.jsonl", "\n".join(lines) + ("\n" if lines else ""))
        manifest = {
            "since": since,
            "until": until,
            "items": len(rows),
            "matches": matches,
            "media_files": media_copied,
            "packed_at": datetime.now(timezone.utc).isoformat(),
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    nbytes = dest.stat().st_size
    manifest["path"] = str(dest)
    manifest["bytes"] = nbytes
    return manifest


async def archive_hot(store: Store, window: WindowCfg, media_root: str, close_window: bool = False) -> dict:
    older_than = None if close_window else _cutoff_iso(window.hot_days)
    rows = await store.list_hot(older_than=older_than)
    if not rows:
        return {"packed": 0, "path": None}

    pubs = [r.get("published_at") or r.get("ingested_at") or "unknown" for r in rows]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"watch_{stamp}_{len(rows)}items.zip"
    dest = Path(window.archive_dir) / name
    info = pack_rows(rows, dest, window.include_media, Path(media_root))
    await store.record_archive(
        str(dest),
        info.get("since") or "",
        info.get("until") or "",
        info["items"],
        info["matches"],
        info["bytes"],
    )
    ids = [r["item_id"] for r in rows]
    await store.mark_archived(ids)

    if window.delete_hot_media_after_archive:
        for r in rows:
            for p in str(r.get("media_paths") or "").splitlines():
                path = Path(p)
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        log.warning("cannot delete hot media %s", path)

    log.info("archived %s items → %s (%s bytes)", info["items"], dest, info["bytes"])
    return info
