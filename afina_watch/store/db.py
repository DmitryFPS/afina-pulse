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

CREATE TABLE IF NOT EXISTS connections (
  platform TEXT PRIMARY KEY,
  method TEXT NOT NULL,
  status TEXT NOT NULL,
  label TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT,
  kind TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS runtime_rules (
  id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  keywords_json TEXT NOT NULL DEFAULT '[]',
  phrases_json TEXT NOT NULL DEFAULT '[]',
  semantic_threshold REAL,
  always_llm INTEGER NOT NULL DEFAULT 0,
  sources_json TEXT NOT NULL DEFAULT '{}',
  actions_json TEXT NOT NULL DEFAULT '["store"]'
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
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

    async def get_connection(self, platform: str) -> dict | None:
        assert self._db
        cur = await self._db.execute("SELECT * FROM connections WHERE platform=?", (platform,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_connections(self) -> list[dict]:
        assert self._db
        cur = await self._db.execute("SELECT * FROM connections")
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_connection(
        self,
        platform: str,
        method: str,
        status: str,
        label: str | None = None,
        meta: dict | None = None,
        error: str | None = None,
    ) -> dict:
        assert self._db
        import json

        await self._db.execute(
            """INSERT INTO connections (platform, method, status, label, meta_json, error, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(platform) DO UPDATE SET
                 method=excluded.method,
                 status=excluded.status,
                 label=excluded.label,
                 meta_json=excluded.meta_json,
                 error=excluded.error,
                 updated_at=excluded.updated_at
            """,
            (platform, method, status, label, json.dumps(meta or {}, ensure_ascii=False), error, _now()),
        )
        await self._db.commit()
        row = await self.get_connection(platform)
        assert row
        return row

    async def delete_connection(self, platform: str) -> None:
        assert self._db
        await self._db.execute("DELETE FROM connections WHERE platform=?", (platform,))
        await self._db.commit()

    async def list_sources(self, platform: str | None = None) -> list[dict]:
        assert self._db
        if platform:
            cur = await self._db.execute(
                "SELECT * FROM sources WHERE platform=? ORDER BY title", (platform,)
            )
        else:
            cur = await self._db.execute("SELECT * FROM sources ORDER BY platform, title")
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_source(
        self,
        sid: str,
        platform: str,
        source_id: str,
        title: str | None,
        kind: str,
        enabled: bool = True,
        meta: dict | None = None,
    ) -> None:
        assert self._db
        import json

        await self._db.execute(
            """INSERT INTO sources (id, platform, source_id, title, kind, enabled, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 kind=excluded.kind,
                 enabled=excluded.enabled,
                 meta_json=excluded.meta_json
            """,
            (sid, platform, source_id, title, kind, 1 if enabled else 0, json.dumps(meta or {})),
        )
        await self._db.commit()

    async def set_source_enabled(self, sid: str, enabled: bool) -> None:
        assert self._db
        await self._db.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if enabled else 0, sid))
        await self._db.commit()

    async def delete_source(self, sid: str) -> None:
        assert self._db
        await self._db.execute("DELETE FROM sources WHERE id=?", (sid,))
        await self._db.commit()

    async def list_runtime_rules(self) -> list[dict]:
        assert self._db
        import json

        cur = await self._db.execute("SELECT * FROM runtime_rules ORDER BY id")
        out = []
        for r in await cur.fetchall():
            d = dict(r)
            d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
            d["phrases"] = json.loads(d.pop("phrases_json") or "[]")
            d["sources"] = json.loads(d.pop("sources_json") or "{}")
            d["actions"] = json.loads(d.pop("actions_json") or "[]")
            d["enabled"] = bool(d["enabled"])
            d["always_llm"] = bool(d["always_llm"])
            out.append(d)
        return out

    async def upsert_runtime_rule(self, rule: dict) -> dict:
        assert self._db
        import json

        rid = (rule.get("id") or "").strip()
        if not rid:
            raise ValueError("id обязателен")
        await self._db.execute(
            """INSERT INTO runtime_rules
               (id, enabled, keywords_json, phrases_json, semantic_threshold, always_llm, sources_json, actions_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 enabled=excluded.enabled,
                 keywords_json=excluded.keywords_json,
                 phrases_json=excluded.phrases_json,
                 semantic_threshold=excluded.semantic_threshold,
                 always_llm=excluded.always_llm,
                 sources_json=excluded.sources_json,
                 actions_json=excluded.actions_json
            """,
            (
                rid,
                1 if rule.get("enabled", True) else 0,
                json.dumps(rule.get("keywords") or [], ensure_ascii=False),
                json.dumps(rule.get("phrases") or [], ensure_ascii=False),
                rule.get("semantic_threshold"),
                1 if rule.get("always_llm") else 0,
                json.dumps(rule.get("sources") or {"telegram": ["*"], "facebook": ["*"]}, ensure_ascii=False),
                json.dumps(rule.get("actions") or ["store"], ensure_ascii=False),
            ),
        )
        await self._db.commit()
        rules = {r["id"]: r for r in await self.list_runtime_rules()}
        return rules[rid]

    async def delete_runtime_rule(self, rid: str) -> None:
        assert self._db
        await self._db.execute("DELETE FROM runtime_rules WHERE id=?", (rid,))
        await self._db.commit()

    async def get_setting(self, key: str) -> str | None:
        assert self._db
        cur = await self._db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self._db.commit()
