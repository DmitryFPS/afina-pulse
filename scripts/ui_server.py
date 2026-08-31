#!/usr/bin/env python3
"""Stdlib UI server used when FastAPI extras are not installed.

Production path remains: python -m afina_watch serve
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "afina_watch" / "api" / "static"
DATA = ROOT / "data"
DB = Path("/tmp/afina-watch.sqlite")
sys.path.insert(0, str(ROOT))

from afina_watch.auth import facebook as fb_auth  # noqa: E402
from afina_watch.auth import telegram as tg_auth  # noqa: E402

LOCK = threading.Lock()
PENDING: dict = {}


def db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS connections (
          platform TEXT PRIMARY KEY, method TEXT, status TEXT, label TEXT,
          meta_json TEXT DEFAULT '{}', error TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, platform TEXT, source_id TEXT, title TEXT,
          kind TEXT, enabled INTEGER DEFAULT 1, meta_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS runtime_rules (
          id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1,
          keywords_json TEXT DEFAULT '[]', phrases_json TEXT DEFAULT '[]',
          semantic_threshold REAL, always_llm INTEGER DEFAULT 0,
          sources_json TEXT DEFAULT '{}', actions_json TEXT DEFAULT '["store"]'
        );
        CREATE TABLE IF NOT EXISTS matches (
          id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, platform TEXT,
          source_id TEXT, permalink TEXT, published_at TEXT, rule_ids TEXT,
          search_blob TEXT, result_json TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS archives (
          id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, since TEXT, until TEXT,
          items INTEGER, matches INTEGER, bytes INTEGER, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS items (
          item_id TEXT PRIMARY KEY, platform TEXT, source_id TEXT, source_title TEXT,
          permalink TEXT, published_at TEXT, ingested_at TEXT, archived INTEGER DEFAULT 0,
          matched INTEGER DEFAULT 0, text TEXT, item_json TEXT, digest_json TEXT, media_paths TEXT
        );
        """
    )
    conn.commit()
    return conn


def public_conn(row):
    if not row:
        return None
    meta = json.loads(row["meta_json"] or "{}")
    for k in list(meta):
        if "token" in k or "secret" in k or k in {"password"}:
            meta[k] = "••••"
    return {
        "platform": row["platform"],
        "method": row["method"],
        "status": row["status"],
        "label": row["label"],
        "error": row["error"],
        "updated_at": row["updated_at"],
        "meta": meta,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(STATIC), **k)

    def log_message(self, fmt, *args):
        sys.stderr.write("ui %s\n" % (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            data = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/static/"):
            self.path = path[len("/static"):] or "/"
            return SimpleHTTPRequestHandler.do_GET(self)
        if path == "/api/health":
            return self._json(200, {"ok": True, "name": "afina-watch", "mode": "stdlib-ui"})
        if path == "/api/status":
            with LOCK, db() as conn:
                rows = [public_conn(r) for r in conn.execute("SELECT * FROM connections")]
            by = {r["platform"]: r for r in rows if r}
            return self._json(200, {
                "ok": True,
                "telegram": by.get("telegram") or {"platform": "telegram", "status": "disconnected"},
                "facebook": by.get("facebook") or {"platform": "facebook", "status": "disconnected"},
                "connected_any": any((by.get(p) or {}).get("status") == "connected" for p in ("telegram", "facebook")),
                "window": {"lookback_days": 7, "hot_days": 7},
                "hot": {"hot_items": 0, "hot_matches": 0},
                "rules_count": 0,
                "llm": {"provider": "ollama", "text_model": "qwen2.5:7b", "vision_model": "qwen2.5vl:7b"},
            })
        if path == "/api/matches":
            with LOCK, db() as conn:
                items = [dict(r) for r in conn.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 50")]
            return self._json(200, {"ok": True, "items": items, "count": len(items)})
        if path == "/api/rules":
            with LOCK, db() as conn:
                items = []
                for r in conn.execute("SELECT * FROM runtime_rules"):
                    items.append({
                        "id": r["id"], "enabled": bool(r["enabled"]),
                        "keywords": json.loads(r["keywords_json"] or "[]"),
                        "phrases": json.loads(r["phrases_json"] or "[]"),
                        "semantic_threshold": r["semantic_threshold"],
                        "always_llm": bool(r["always_llm"]),
                    })
            return self._json(200, {"ok": True, "items": items})
        if path == "/api/sources":
            with LOCK, db() as conn:
                items = [dict(r) for r in conn.execute("SELECT * FROM sources")]
            return self._json(200, {"ok": True, "items": items})
        if path == "/api/archives":
            with LOCK, db() as conn:
                items = [dict(r) for r in conn.execute("SELECT * FROM archives ORDER BY id DESC")]
            return self._json(200, {"ok": True, "items": items})
        if path == "/api/window":
            return self._json(200, {"ok": True, "window": {"lookback_days": 7, "hot_days": 7}, "hot_items": 0, "hot_matches": 0})
        if path == "/api/telegram/connect/qr/status":
            st = PENDING.get("telegram") or {}
            return self._json(200, {"ok": True, "status": st.get("step") or "idle", "url": st.get("url")})
        if path == "/api/facebook/connect/qr/status":
            st = PENDING.get("facebook") or {}
            return self._json(200, {"ok": True, "status": st.get("step") or "idle", "url": st.get("url")})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path in ("/api/telegram/connect", "/api/facebook/connect"):
            plat = "telegram" if "telegram" in path else "facebook"
            with LOCK, db() as conn:
                conn.execute("DELETE FROM connections WHERE platform=?", (plat,))
                conn.commit()
            return self._json(200, {"ok": True})
        if path.startswith("/api/rules/"):
            rid = path.rsplit("/", 1)[-1]
            with LOCK, db() as conn:
                conn.execute("DELETE FROM runtime_rules WHERE id=?", (rid,))
                conn.commit()
            return self._json(200, {"ok": True})
        if path.startswith("/api/sources/"):
            sid = path.rsplit("/", 1)[-1]
            with LOCK, db() as conn:
                conn.execute("DELETE FROM sources WHERE id=?", (sid,))
                conn.commit()
            return self._json(200, {"ok": True})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            return self._json(400, {"ok": False, "error": "некорректный JSON"})
        try:
            return self._handle_post(path, body)
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except FileNotFoundError as exc:
            return self._json(404, {"ok": False, "error": str(exc)})
        except PermissionError as exc:
            return self._json(401, {"ok": False, "error": str(exc)})
        except ConnectionError as exc:
            return self._json(502, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._json(400, {"ok": False, "error": str(exc)})

    def _save_conn(self, platform, method, status, label=None, meta=None, error=None):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with LOCK, db() as conn:
            conn.execute(
                """INSERT INTO connections(platform,method,status,label,meta_json,error,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(platform) DO UPDATE SET method=excluded.method,status=excluded.status,
                     label=excluded.label,meta_json=excluded.meta_json,error=excluded.error,updated_at=excluded.updated_at""",
                (platform, method, status, label, json.dumps(meta or {}), error, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM connections WHERE platform=?", (platform,)).fetchone()
        return public_conn(row)

    def _handle_post(self, path, body):
        if path == "/api/telegram/connect/relay":
            info = __import__("asyncio").run(tg_auth.probe_relay(body.get("url", "")))
            row = self._save_conn("telegram", "relay", "connected", f"Relay {info['url']}", info)
            return self._json(200, {"ok": True, "connection": row, "probe": info})
        if path == "/api/telegram/connect/session":
            info = tg_auth.probe_session_file(body.get("path", ""))
            row = self._save_conn("telegram", "session_file", "connected", info["path"], info)
            return self._json(200, {"ok": True, "connection": row, "probe": info})
        if path == "/api/telegram/connect/bot":
            info = __import__("asyncio").run(tg_auth.probe_bot_token(body.get("token", "")))
            row = self._save_conn("telegram", "bot", "connected", f"@{info.get('username')}", {"bot_token": body.get("token"), **info})
            return self._json(200, {"ok": True, "connection": row, "probe": info})
        if path == "/api/telegram/connect/phone":
            phone = tg_auth.validate_phone(body.get("phone", ""))
            PENDING["telegram"] = {"phone": phone, "step": "code"}
            row = self._save_conn("telegram", "phone", "pending_code", phone, {"phone": phone})
            return self._json(200, {"ok": True, "next": "code", "code_sent": False, "detail": "Номер принят. Введите код из Telegram.", "connection": row})
        if path == "/api/telegram/connect/code":
            st = PENDING.get("telegram") or {}
            if not st.get("phone"):
                raise ValueError("Сначала отправьте номер телефона")
            code = tg_auth.validate_login_code(body.get("code") or "")
            password = (body.get("password") or "").strip()
            if password:
                tg_auth.validate_cloud_password(password)
                PENDING.pop("telegram", None)
                row = self._save_conn("telegram", "phone", "connected", st["phone"], {"phone": st["phone"], "with_2fa": True})
                return self._json(200, {"ok": True, "next": "done", "connection": row})
            PENDING["telegram"]["step"] = "2fa"
            row = self._save_conn("telegram", "phone", "pending_2fa", st["phone"], {"phone": st["phone"]})
            return self._json(200, {
                "ok": True,
                "next": "2fa",
                "detail": "На аккаунте может быть облачный пароль. Введите его, если Telegram его запросил.",
                "connection": row,
            })
        if path == "/api/telegram/connect/2fa":
            st = PENDING.get("telegram") or {}
            tg_auth.validate_cloud_password(body.get("password") or "")
            label = st.get("phone") or "QR"
            PENDING.pop("telegram", None)
            row = self._save_conn("telegram", st.get("method") or "phone", "connected", label, {"with_2fa": True})
            return self._json(200, {"ok": True, "next": "done", "connection": row})
        if path == "/api/telegram/connect/qr/start":
            payload = tg_auth.make_qr_login_payload()
            PENDING["telegram"] = {"method": "qr", "step": "scan", **payload}
            row = self._save_conn("telegram", "qr", "pending_qr", "ожидание скана QR", payload)
            return self._json(200, {
                "ok": True,
                "next": "scan",
                "qr": payload,
                "detail": "Откройте Telegram → Настройки → Устройства → Подключить устройство.",
                "connection": row,
            })
        if path == "/api/telegram/connect/qr/confirm":
            st = PENDING.get("telegram") or {}
            if st.get("method") != "qr":
                raise ValueError("Сначала сгенерируйте QR Telegram")
            if (body.get("password") or "").strip():
                tg_auth.validate_cloud_password(body["password"])
                PENDING.pop("telegram", None)
                row = self._save_conn("telegram", "qr", "connected", "QR + 2FA", {"with_2fa": True})
                return self._json(200, {"ok": True, "next": "done", "connection": row})
            PENDING["telegram"]["step"] = "2fa"
            row = self._save_conn("telegram", "qr", "pending_2fa", "QR ждёт облачный пароль")
            return self._json(200, {
                "ok": True,
                "next": "2fa",
                "detail": "Если включена двухэтапная проверка — введите облачный пароль.",
                "connection": row,
            })
        if path == "/api/telegram/connect/qr/skip-2fa":
            PENDING.pop("telegram", None)
            row = self._save_conn("telegram", "qr", "connected", "QR-сессия")
            return self._json(200, {"ok": True, "next": "done", "connection": row})
        if path == "/api/facebook/connect/password":
            login = fb_auth.validate_login(body.get("login") or "")
            fb_auth.validate_password(body.get("password") or "")
            PENDING["facebook"] = {"method": "password", "login": login, "step": "2fa"}
            row = self._save_conn("facebook", "password", "pending_2fa", login, {"login": login})
            return self._json(200, {
                "ok": True,
                "next": "2fa",
                "detail": "Пароль принят. Введите код 2FA из приложения, SMS, WhatsApp или резервный код.",
                "connection": row,
            })
        if path == "/api/facebook/connect/2fa":
            st = PENDING.get("facebook") or {}
            fb_auth.validate_2fa_code(body.get("code") or "")
            login = st.get("login") or "facebook"
            PENDING.pop("facebook", None)
            row = self._save_conn("facebook", st.get("method") or "password", "connected", login, {"login": login})
            return self._json(200, {"ok": True, "next": "done", "connection": row})
        if path == "/api/facebook/connect/qr/start":
            payload = fb_auth.make_qr_login_payload()
            PENDING["facebook"] = {"method": "qr", "step": "scan", **payload}
            row = self._save_conn("facebook", "qr", "pending_qr", "ожидание скана QR", payload)
            return self._json(200, {
                "ok": True,
                "next": "scan",
                "qr": payload,
                "detail": "Отсканируйте QR в приложении Facebook и подтвердите вход на телефоне.",
                "connection": row,
            })
        if path == "/api/facebook/connect/qr/confirm":
            st = PENDING.get("facebook") or {}
            if st.get("method") != "qr":
                raise ValueError("Сначала сгенерируйте QR Facebook")
            if body.get("code"):
                fb_auth.validate_2fa_code(body.get("code") or "")
                PENDING.pop("facebook", None)
                row = self._save_conn("facebook", "qr", "connected", "QR + 2FA")
                return self._json(200, {"ok": True, "next": "done", "connection": row})
            PENDING["facebook"]["step"] = "2fa"
            row = self._save_conn("facebook", "qr", "pending_2fa", "QR ждёт 2FA")
            return self._json(200, {"ok": True, "next": "2fa", "detail": "Введите код 2FA, если Facebook его запросил.", "connection": row})
        if path == "/api/facebook/connect/qr/skip-2fa":
            PENDING.pop("facebook", None)
            row = self._save_conn("facebook", "qr", "connected", "QR-сессия Facebook")
            return self._json(200, {"ok": True, "next": "done", "connection": row})
        if path == "/api/facebook/connect/import":
            info = fb_auth.probe_import_dir(body.get("path") or str(DATA / "fb-inbox"))
            row = self._save_conn("facebook", "import_box", "connected", info["path"], info)
            return self._json(200, {"ok": True, "connection": row, "probe": info})
        if path == "/api/facebook/connect/token":
            info = __import__("asyncio").run(fb_auth.inspect_user_token(body.get("token", "")))
            row = self._save_conn("facebook", "graph_token", "connected", info.get("name"), {"access_token": body.get("token"), **info})
            return self._json(200, {"ok": True, "connection": row, "probe": info})
        if path == "/api/facebook/oauth/start":
            app_id = fb_auth.validate_app_id(body.get("app_id", ""))
            if not (body.get("app_secret") or "").strip():
                raise ValueError("App Secret обязателен")
            redirect = "http://127.0.0.1:8091/api/facebook/oauth/callback"
            url = fb_auth.oauth_dialog_url(app_id, redirect, "local")
            return self._json(200, {"ok": True, "url": url, "redirect_uri": redirect})
        if path == "/api/rules":
            rid = (body.get("id") or "").strip()
            if not rid:
                raise ValueError("id обязателен")
            with LOCK, db() as conn:
                conn.execute(
                    """INSERT INTO runtime_rules(id,enabled,keywords_json,phrases_json,semantic_threshold,always_llm)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,keywords_json=excluded.keywords_json,
                         phrases_json=excluded.phrases_json,semantic_threshold=excluded.semantic_threshold,always_llm=excluded.always_llm""",
                    (rid, 1 if body.get("enabled", True) else 0, json.dumps(body.get("keywords") or []),
                     json.dumps(body.get("phrases") or []), body.get("semantic_threshold"), 1 if body.get("always_llm") else 0),
                )
                conn.commit()
            return self._json(200, {"ok": True, "item": body})
        if path == "/api/sources":
            plat = body.get("platform")
            if plat not in {"telegram", "facebook"}:
                raise ValueError("platform: telegram или facebook")
            sid = f"{plat}:{body.get('kind') or 'custom'}:{body.get('source_id')}"
            with LOCK, db() as conn:
                conn.execute(
                    """INSERT INTO sources(id,platform,source_id,title,kind,enabled,meta_json)
                       VALUES(?,?,?,?,?,1,'{}')
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title""",
                    (sid, plat, body.get("source_id"), body.get("title") or body.get("source_id"), body.get("kind") or "custom"),
                )
                conn.commit()
            return self._json(200, {"ok": True, "id": sid})
        if path == "/api/archives/close":
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            path_zip = str(DATA / "archive" / f"{now[:10]}.zip")
            (DATA / "archive").mkdir(parents=True, exist_ok=True)
            Path(path_zip).write_bytes(b"PK\x05\x06" + b"\x00" * 18)
            with LOCK, db() as conn:
                conn.execute(
                    "INSERT INTO archives(path,since,until,items,matches,bytes,created_at) VALUES(?,?,?,?,?,?,?)",
                    (path_zip, now, now, 0, 0, 22, now),
                )
                conn.commit()
            return self._json(200, {"ok": True, "archive": {"path": path_zip}})
        return self._json(404, {"ok": False, "error": "not found"})


def main():
    import os

    host = os.environ.get("AFINA_HOST", "0.0.0.0")
    port = int(os.environ.get("AFINA_PORT", "8091"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Afina Watch UI http://127.0.0.1:{port}", flush=True)
    print(f"Afina Watch UI http://{host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
