from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from afina_watch.models import WatchResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  source_id TEXT,
  permalink TEXT,
  published_at TEXT,
  rule_ids TEXT,
  search_blob TEXT,
  result_json TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_matches_item ON matches(item_id);
CREATE INDEX IF NOT EXISTS idx_matches_plat ON matches(platform, published_at);

CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  source_id TEXT,
  source_title TEXT,
  permalink TEXT,
  published_at TEXT,
  ingested_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0,
  matched INTEGER NOT NULL DEFAULT 0,
  text TEXT,
  item_json TEXT NOT NULL,
  digest_json TEXT,
  media_paths TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_hot ON items(archived, ingested_at);

CREATE TABLE IF NOT EXISTS archives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  since TEXT,
  until TEXT,
  items INTEGER,
  matches INTEGER,
  bytes INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def save_item(self, result: WatchResult) -> None:
        assert self._db
        item = result.item
        media = [m.local_path for m in item.media if m.local_path]
        await self._db.execute(
            """INSERT INTO items
               (item_id, platform, source_id, source_title, permalink, published_at,
                ingested_at, archived, matched, text, item_json, digest_json, media_paths)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
               ON CONFLICT(item_id) DO UPDATE SET
                 matched=excluded.matched,
                 digest_json=excluded.digest_json,
                 text=excluded.text
            """,
            (
                item.id,
                item.platform.value,
                item.source_id,
                item.source_title,
                item.permalink,
                item.published_at.isoformat(),
                _now(),
                1 if result.matched else 0,
                item.text,
                item.model_dump_json(),
                result.digest.model_dump_json(),
                "\n".join(media),
            ),
        )
        await self._db.commit()

    async def save_match(self, result: WatchResult) -> None:
        assert self._db
        item = result.item
        await self._db.execute(
            """INSERT INTO matches
               (item_id, platform, source_id, permalink, published_at, rule_ids, search_blob, result_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id,
                item.platform.value,
                item.source_id,
                item.permalink,
                item.published_at.isoformat(),
                ",".join(h.rule_id for h in result.hits),
                result.digest.search_blob[:4000],
                result.model_dump_json(),
            ),
        )
        await self._db.commit()

    async def save(self, result: WatchResult) -> None:
        """Совместимость: раньше сохранялись только матчи."""
        await self.save_item(result)
        if result.matched:
            await self.save_match(result)

    async def has_item(self, item_id: str) -> bool:
        assert self._db
        cur = await self._db.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,))
        return await cur.fetchone() is not None

    async def recent(self, limit: int = 50) -> list[dict]:
        assert self._db
        cur = await self._db.execute(
            "SELECT id, item_id, platform, source_id, permalink, published_at, rule_ids, created_at "
            "FROM matches ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def hot_stats(self) -> dict:
        assert self._db
        cur = await self._db.execute(
            "SELECT COUNT(*) n, SUM(matched) m FROM items WHERE archived=0"
        )
        row = await cur.fetchone()
        return {"hot_items": row["n"] or 0, "hot_matches": row["m"] or 0}

    async def list_hot(self, older_than: str | None = None) -> list[dict]:
        assert self._db
        if older_than:
            cur = await self._db.execute(
                "SELECT * FROM items WHERE archived=0 AND ingested_at < ? ORDER BY published_at",
                (older_than,),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM items WHERE archived=0 ORDER BY published_at"
            )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_archived(self, item_ids: list[str]) -> None:
        assert self._db
        if not item_ids:
            return
        await self._db.executemany(
            "UPDATE items SET archived=1 WHERE item_id=?",
            [(i,) for i in item_ids],
        )
        await self._db.commit()

    async def record_archive(self, path: str, since: str, until: str, items: int, matches: int, nbytes: int) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO archives (path, since, until, items, matches, bytes) VALUES (?,?,?,?,?,?)",
            (path, since, until, items, matches, nbytes),
        )
        await self._db.commit()

    async def list_archives(self) -> list[dict]:
        assert self._db
        cur = await self._db.execute("SELECT * FROM archives ORDER BY id DESC")
        return [dict(r) for r in await cur.fetchall()]
