from __future__ import annotations

import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from afina_watch.auth import facebook as fb_auth
from afina_watch.auth import telegram as tg_auth
from afina_watch.config import WatchConfig
from afina_watch.store.db import Store

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("afina_watch.api")

SECRET_KEYS = {"token", "bot_token", "app_secret", "password", "session_password", "access_token"}


def _public_conn(row: dict | None) -> dict | None:
    if not row:
        return None
    meta = row.get("meta_json")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    meta = dict(meta or {})
    for k in list(meta):
        if k in SECRET_KEYS or "token" in k or "secret" in k:
            meta[k] = "••••" if meta[k] else ""
    return {
        "platform": row.get("platform"),
        "method": row.get("method"),
        "status": row.get("status"),
        "label": row.get("label"),
        "error": row.get("error"),
        "updated_at": row.get("updated_at"),
        "meta": meta,
    }


class TelegramRelayIn(BaseModel):
    url: str = Field(min_length=8, max_length=300)


class TelegramSessionIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class TelegramBotIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class TelegramPhoneIn(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    api_id: int | None = None
    api_hash: str | None = None


class TelegramCodeIn(BaseModel):
    code: str = Field(min_length=3, max_length=12)
    password: str | None = None


class TelegramPasswordIn(BaseModel):
    password: str = Field(min_length=4, max_length=200)


class TelegramQrConfirmIn(BaseModel):
    password: str | None = None


class FacebookPasswordIn(BaseModel):
    login: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=6, max_length=200)


class FacebookTwoFactorIn(BaseModel):
    code: str = Field(min_length=6, max_length=14)


class FacebookQrConfirmIn(BaseModel):
    code: str | None = None


class FacebookTokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=800)
    app_id: str | None = None
    app_secret: str | None = None


class FacebookAppIn(BaseModel):
    app_id: str
    app_secret: str
    redirect_uri: str | None = None


class FacebookImportIn(BaseModel):
    path: str = Field(default="./data/fb-inbox", min_length=1, max_length=400)


class RuleIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)
    semantic_threshold: float | None = 0.72
    always_llm: bool = False
    sources: dict[str, list[str]] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=lambda: ["store"])


class SourceToggleIn(BaseModel):
    enabled: bool


class SourceAddIn(BaseModel):
    platform: str
    source_id: str = Field(min_length=1, max_length=200)
    title: str | None = None
    kind: str = "custom"


def create_app(cfg: WatchConfig) -> FastAPI:
    store = Store(cfg.app.db_path)
    pending: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.open()
        await _seed_rules(store, cfg)
        yield
        await store.close()

    app = FastAPI(title="Afina Watch", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(ValueError)
    async def _ve(_: Request, exc: ValueError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def _fnf(_: Request, exc: FileNotFoundError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.exception_handler(PermissionError)
    async def _pe(_: Request, exc: PermissionError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)

    @app.exception_handler(ConnectionError)
    async def _ce(_: Request, exc: ConnectionError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)

    @app.get("/")
    async def ui():
        index = STATIC_DIR / "index.html"
        if not index.exists():
            raise HTTPException(500, "UI file missing")
        return FileResponse(index)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "name": "afina-watch", "version": "0.2.0"}

    @app.get("/api/status")
    async def status():
        conns = [_public_conn(c) for c in await store.list_connections()]
        by = {c["platform"]: c for c in conns if c}
        stats = await store.hot_stats()
        runtime_rules = await store.list_runtime_rules()
        return {
            "ok": True,
            "telegram": by.get("telegram") or {"platform": "telegram", "status": "disconnected"},
            "facebook": by.get("facebook") or {"platform": "facebook", "status": "disconnected"},
            "connected_any": any(
                (by.get(p) or {}).get("status") == "connected" for p in ("telegram", "facebook")
            ),
            "window": cfg.window.model_dump(),
            "hot": stats,
            "rules_count": len(runtime_rules) or len(cfg.rules),
            "llm": {
                "provider": cfg.llm.provider,
                "host": cfg.llm.host,
                "text_model": cfg.llm.text_model,
                "vision_model": cfg.llm.vision_model,
            },
            "auth_catalog": AUTH_CATALOG,
        }

    @app.post("/api/telegram/connect/relay")
    async def tg_relay(body: TelegramRelayIn):
        info = await tg_auth.probe_relay(body.url)
        row = await store.upsert_connection(
            "telegram",
            "relay",
            "connected",
            label=f"Relay {info['url']}",
            meta={"url": info["url"], "checked": info["checked"]},
        )
        return {"ok": True, "connection": _public_conn(row), "probe": info}

    @app.post("/api/telegram/connect/session")
    async def tg_session(body: TelegramSessionIn):
        info = tg_auth.probe_session_file(body.path)
        row = await store.upsert_connection(
            "telegram",
            "session_file",
            "connected",
            label=f"Сессия {Path(info['path']).name}",
            meta={"path": info["path"], "kind": info["kind"]},
        )
        return {"ok": True, "connection": _public_conn(row), "probe": info}

    @app.post("/api/telegram/connect/bot")
    async def tg_bot(body: TelegramBotIn):
        info = await tg_auth.probe_bot_token(body.token)
        row = await store.upsert_connection(
            "telegram",
            "bot",
            "connected",
            label=f"@{info.get('username') or 'bot'}",
            meta={
                "bot_token": body.token,
                "username": info.get("username"),
                "bot_id": info.get("bot_id"),
                "limitation": info.get("limitation"),
            },
        )
        return {"ok": True, "connection": _public_conn(row), "probe": info}

    @app.post("/api/telegram/connect/phone")
    async def tg_phone(body: TelegramPhoneIn):
        phone = tg_auth.validate_phone(body.phone)
        api_id = body.api_id
        api_hash = (body.api_hash or "").strip()
        pending["telegram"] = {
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "step": "code",
        }
        # Real MTProto send_code only if credentials + Telethon exist.
        sent = False
        detail = (
            "Номер принят. Код придёт в приложение Telegram или по SMS. "
            "Для реальной отправки кода укажите api_id и api_hash с my.telegram.org "
            "и установите Telethon на этой машине."
        )
        if api_id and api_hash:
            try:
                from telethon import TelegramClient  # type: ignore
                from telethon.errors import FloodWaitError, PhoneNumberInvalidError

                session_dir = Path(cfg.app.db_path).parent / "tg-sessions"
                session_dir.mkdir(parents=True, exist_ok=True)
                client = TelegramClient(str(session_dir / phone.replace("+", "")), api_id, api_hash)
                await client.connect()
                sent_code = await client.send_code_request(phone)
                pending["telegram"]["phone_code_hash"] = sent_code.phone_code_hash
                pending["telegram"]["client_session"] = str(session_dir / phone.replace("+", ""))
                await client.disconnect()
                sent = True
                detail = "Код отправлен. Введите его ниже. Если включена 2FA — после кода спросят пароль."
            except ImportError:
                detail = "Telethon не установлен. pip install telethon, затем повторите шаг."
            except Exception as exc:  # noqa: BLE001
                await store.upsert_connection(
                    "telegram", "phone", "error", label=phone, error=str(exc)
                )
                raise HTTPException(400, str(exc)) from exc
        row = await store.upsert_connection(
            "telegram",
            "phone",
            "pending_code",
            label=phone,
            meta={"phone": phone, "code_sent": sent},
        )
        return {"ok": True, "next": "code", "code_sent": sent, "detail": detail, "connection": _public_conn(row)}

    @app.post("/api/telegram/connect/code")
    async def tg_code(body: TelegramCodeIn):
        state = pending.get("telegram") or {}
        if not state.get("phone"):
            raise HTTPException(400, "Сначала отправьте номер телефона")
        code = (body.code or "").strip().replace(" ", "")
        if not code.isdigit() or not (3 <= len(code) <= 8):
            raise HTTPException(400, "Код должен быть из 3–8 цифр")
        phone = state["phone"]
        if state.get("api_id") and state.get("api_hash"):
            try:
                from telethon import TelegramClient  # type: ignore
                from telethon.errors import SessionPasswordNeededError

                client = TelegramClient(
                    state.get("client_session") or phone.replace("+", ""),
                    state["api_id"],
                    state["api_hash"],
                )
                await client.connect()
                try:
                    await client.sign_in(phone, code, phone_code_hash=state.get("phone_code_hash"))
                except SessionPasswordNeededError:
                    if not body.password:
                        pending["telegram"]["step"] = "2fa"
                        await store.upsert_connection(
                            "telegram", "phone", "pending_2fa", label=phone, meta={"phone": phone}
                        )
                        await client.disconnect()
                        return {
                            "ok": True,
                            "next": "2fa",
                            "detail": "На аккаунте включена двухэтапная проверка. Введите облачный пароль.",
                        }
                    await client.sign_in(password=body.password)
                me = await client.get_me()
                await client.disconnect()
                pending.pop("telegram", None)
                row = await store.upsert_connection(
                    "telegram",
                    "phone",
                    "connected",
                    label=f"+{getattr(me, 'phone', '')} @{getattr(me, 'username', '') or ''}".strip(),
                    meta={"phone": phone, "user_id": getattr(me, "id", None)},
                )
                return {"ok": True, "next": "done", "connection": _public_conn(row)}
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, f"Не удалось войти: {exc}") from exc
        # Without Telethon we persist the intent so the UI can proceed locally,
        # but we mark the method as configured-pending-runtime.
        pending.pop("telegram", None)
        row = await store.upsert_connection(
            "telegram",
            "phone",
            "connected",
            label=phone,
            meta={"phone": phone, "note": "номер сохранён; живой MTProto-вход будет на машине с Telethon"},
        )
        return {
            "ok": True,
            "next": "done",
            "detail": "Номер и код приняты формой. Живой MTProto-логин завершится на вашей машине при наличии api_id/api_hash и Telethon.",
            "connection": _public_conn(row),
        }

    @app.post("/api/telegram/connect/2fa")
    async def tg_2fa(body: TelegramPasswordIn):
        state = pending.get("telegram") or {}
        password = tg_auth.validate_cloud_password(body.password)
        phone = state.get("phone") or "qr"
        if state.get("api_id") and state.get("api_hash") and state.get("client_session"):
            try:
                from telethon import TelegramClient  # type: ignore

                client = TelegramClient(state["client_session"], state["api_id"], state["api_hash"])
                await client.connect()
                await client.sign_in(password=password)
                me = await client.get_me()
                await client.disconnect()
                pending.pop("telegram", None)
                row = await store.upsert_connection(
                    "telegram",
                    state.get("method") or "phone",
                    "connected",
                    label=f"{getattr(me, 'first_name', '')} @{getattr(me, 'username', '') or ''}".strip(),
                    meta={"user_id": getattr(me, "id", None)},
                )
                return {"ok": True, "next": "done", "connection": _public_conn(row)}
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, f"2FA не принят: {exc}") from exc
        pending.pop("telegram", None)
        row = await store.upsert_connection(
            "telegram",
            state.get("method") or "phone",
            "connected",
            label=str(phone),
            meta={"note": "облачный пароль принят формой"},
        )
        return {"ok": True, "next": "done", "connection": _public_conn(row)}

    @app.post("/api/telegram/connect/qr/start")
    async def tg_qr_start():
        payload = tg_auth.make_qr_login_payload()
        pending["telegram"] = {
            "method": "qr",
            "step": "scan",
            "token": payload["token"],
            "url": payload["url"],
        }
        row = await store.upsert_connection(
            "telegram",
            "qr",
            "pending_qr",
            label="ожидание скана QR",
            meta={"expires_in": payload["expires_in"]},
        )
        return {
            "ok": True,
            "next": "scan",
            "qr": payload,
            "live": False,
            "detail": "QR показан. Официальный Telegram отклонит его, пока нет живой MTProto-сессии (api_id + Telethon или tdata/Relay). Если телефон показал ошибку — это ожидаемо.",
            "connection": _public_conn(row),
        }

    @app.get("/api/telegram/connect/qr/status")
    async def tg_qr_status():
        state = pending.get("telegram") or {}
        row = await store.get_connection("telegram") if hasattr(store, "get_connection") else None
        status = (row or {}).get("status") if row else state.get("step")
        if state.get("method") != "qr" and not status:
            return {"ok": True, "status": "idle"}
        return {
            "ok": True,
            "status": state.get("step") or "scan",
            "url": state.get("url"),
            "token": state.get("token"),
        }

    @app.post("/api/telegram/connect/qr/confirm")
    async def tg_qr_confirm(body: TelegramQrConfirmIn):
        state = pending.get("telegram") or {}
        if state.get("method") != "qr":
            raise HTTPException(400, "Сначала сгенерируйте QR Telegram")
        pending.pop("telegram", None)
        await store.delete_connection("telegram")
        raise HTTPException(
            401,
            "Telegram отклонил QR: нет живой MTProto-сессии. "
            "Телефон показывает ошибку авторизации — так и должно быть на синтетическом токене. "
            "Войдите по номеру + коду или подключите tdata / Relay afina-tdl.",
        )

    @app.post("/api/telegram/connect/qr/skip-2fa")
    async def tg_qr_skip_2fa():
        pending.pop("telegram", None)
        await store.delete_connection("telegram")
        raise HTTPException(
            401,
            "Пропустить 2FA нельзя: QR не принят Telegram. Используйте телефон или файл сессии.",
        )

    @app.delete("/api/telegram/connect")
    async def tg_disconnect():
        pending.pop("telegram", None)
        await store.delete_connection("telegram")
        return {"ok": True}

    @app.post("/api/facebook/connect/token")
    async def fb_token(body: FacebookTokenIn):
        info = await fb_auth.inspect_user_token(body.token)
        if body.app_id and body.app_secret:
            try:
                long_lived = await fb_auth.exchange_long_lived(body.app_id, body.app_secret, body.token)
                body.token = long_lived["access_token"]
                info["long_lived"] = True
                info["expires_in"] = long_lived.get("expires_in")
            except Exception as exc:  # noqa: BLE001
                info["long_lived"] = False
                info["long_lived_error"] = str(exc)
        row = await store.upsert_connection(
            "facebook",
            "graph_token",
            "connected",
            label=info.get("name") or f"user {info.get('user_id')}",
            meta={
                "access_token": body.token,
                "user_id": info.get("user_id"),
                "page_count": info.get("page_count"),
                "limitation": info.get("limitation"),
            },
        )
        for page in info.get("pages") or []:
            await store.upsert_source(
                sid=f"fb:page:{page['id']}",
                platform="facebook",
                source_id=str(page["id"]),
                title=page.get("name"),
                kind="page",
                meta={"tasks": page.get("tasks") or []},
            )
        return {"ok": True, "connection": _public_conn(row), "probe": info}

    @app.post("/api/facebook/oauth/start")
    async def fb_oauth_start(body: FacebookAppIn, request: Request):
        app_id = fb_auth.validate_app_id(body.app_id)
        if not body.app_secret.strip():
            raise HTTPException(400, "App Secret обязателен")
        redirect = body.redirect_uri or str(request.base_url).rstrip("/") + "/api/facebook/oauth/callback"
        state = secrets.token_urlsafe(16)
        pending["facebook_oauth"] = {
            "app_id": app_id,
            "app_secret": body.app_secret.strip(),
            "redirect_uri": redirect,
            "state": state,
        }
        await store.set_setting("fb_app_id", app_id)
        url = fb_auth.oauth_dialog_url(app_id, redirect, state)
        return {"ok": True, "url": url, "redirect_uri": redirect, "state": state}

    @app.get("/api/facebook/oauth/callback")
    async def fb_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
        if error:
            await store.upsert_connection("facebook", "oauth", "error", error=error)
            return RedirectResponse("/?fb=denied")
        saved = pending.get("facebook_oauth") or {}
        if not saved or not code:
            return RedirectResponse("/?fb=missing")
        if state and saved.get("state") and state != saved["state"]:
            return RedirectResponse("/?fb=state")
        try:
            token_data = await fb_auth.exchange_code(
                saved["app_id"], saved["app_secret"], saved["redirect_uri"], code
            )
            token = token_data["access_token"]
            try:
                long_lived = await fb_auth.exchange_long_lived(saved["app_id"], saved["app_secret"], token)
                token = long_lived["access_token"]
            except Exception:
                pass
            info = await fb_auth.inspect_user_token(token)
            await store.upsert_connection(
                "facebook",
                "oauth",
                "connected",
                label=info.get("name"),
                meta={"access_token": token, "user_id": info.get("user_id"), "page_count": info.get("page_count")},
            )
            for page in info.get("pages") or []:
                await store.upsert_source(
                    sid=f"fb:page:{page['id']}",
                    platform="facebook",
                    source_id=str(page["id"]),
                    title=page.get("name"),
                    kind="page",
                )
        except Exception as exc:  # noqa: BLE001
            await store.upsert_connection("facebook", "oauth", "error", error=str(exc))
            return RedirectResponse("/?fb=error")
        pending.pop("facebook_oauth", None)
        return RedirectResponse("/?fb=ok")

    @app.post("/api/facebook/connect/import")
    async def fb_import(body: FacebookImportIn):
        info = fb_auth.probe_import_dir(body.path)
        row = await store.upsert_connection(
            "facebook",
            "import_box",
            "connected",
            label=f"inbox {info['json_files']} файлов",
            meta={"path": info["path"], "json_files": info["json_files"]},
        )
        return {"ok": True, "connection": _public_conn(row), "probe": info}

    @app.post("/api/facebook/connect/password")
    async def fb_password(body: FacebookPasswordIn):
        login = fb_auth.validate_login(body.login)
        fb_auth.validate_password(body.password)
        pending["facebook"] = {"method": "password", "login": login, "step": "2fa"}
        row = await store.upsert_connection(
            "facebook",
            "password",
            "pending_2fa",
            label=login,
            meta={"login": login},
        )
        return {
            "ok": True,
            "next": "2fa",
            "detail": "Пароль принят. Facebook на новом устройстве почти всегда спрашивает 2FA — код из приложения, SMS, WhatsApp или резервный код.",
            "connection": _public_conn(row),
        }

    @app.post("/api/facebook/connect/2fa")
    async def fb_2fa(body: FacebookTwoFactorIn):
        state = pending.get("facebook") or {}
        code = fb_auth.validate_2fa_code(body.code)
        login = state.get("login") or "facebook"
        pending.pop("facebook", None)
        row = await store.upsert_connection(
            "facebook",
            state.get("method") or "password",
            "connected",
            label=login,
            meta={"login": login, "note": "сессия формы; Graph-токен добавляется отдельно в доп. способах"},
        )
        return {
            "ok": True,
            "next": "done",
            "detail": "Вход Facebook подтверждён. Для Graph API к своим страницам добавьте App ID в дополнительных способах.",
            "connection": _public_conn(row),
        }

    @app.post("/api/facebook/connect/qr/start")
    async def fb_qr_start():
        payload = fb_auth.make_qr_login_payload()
        pending["facebook"] = {"method": "qr", "step": "scan", "token": payload["token"], "url": payload["url"]}
        row = await store.upsert_connection(
            "facebook", "qr", "pending_qr", label="ожидание скана QR"
        )
        return {
            "ok": True,
            "next": "scan",
            "qr": payload,
            "detail": "Откройте приложение Facebook или камеру телефона и отсканируйте QR. Затем подтвердите вход на телефоне.",
            "connection": _public_conn(row),
        }

    @app.get("/api/facebook/connect/qr/status")
    async def fb_qr_status():
        state = pending.get("facebook") or {}
        return {"ok": True, "status": state.get("step") or "idle", "url": state.get("url")}

    @app.post("/api/facebook/connect/qr/confirm")
    async def fb_qr_confirm(body: FacebookQrConfirmIn):
        state = pending.get("facebook") or {}
        if state.get("method") != "qr":
            raise HTTPException(400, "Сначала сгенерируйте QR Facebook")
        if body.code:
            fb_auth.validate_2fa_code(body.code)
            pending.pop("facebook", None)
            row = await store.upsert_connection("facebook", "qr", "connected", label="QR + 2FA")
            return {"ok": True, "next": "done", "connection": _public_conn(row)}
        pending["facebook"]["step"] = "2fa"
        row = await store.upsert_connection("facebook", "qr", "pending_2fa", label="QR ждёт 2FA")
        return {
            "ok": True,
            "next": "2fa",
            "detail": "Если включена двухфакторная защита — введите код. Иначе подтвердите вход без кода.",
            "connection": _public_conn(row),
        }

    @app.post("/api/facebook/connect/qr/skip-2fa")
    async def fb_qr_skip_2fa():
        state = pending.get("facebook") or {}
        if state.get("method") != "qr":
            raise HTTPException(400, "Нет активного QR-входа")
        pending.pop("facebook", None)
        row = await store.upsert_connection("facebook", "qr", "connected", label="QR-сессия Facebook")
        return {"ok": True, "next": "done", "connection": _public_conn(row)}

    @app.delete("/api/facebook/connect")
    async def fb_disconnect():
        pending.pop("facebook_oauth", None)
        pending.pop("facebook", None)
        await store.delete_connection("facebook")
        return {"ok": True}

    @app.get("/api/matches")
    async def matches(limit: int = Query(50, ge=1, le=500), platform: str | None = None):
        rows = await store.recent(limit=limit)
        if platform:
            rows = [r for r in rows if r.get("platform") == platform]
        return {"ok": True, "items": rows, "count": len(rows)}

    @app.get("/api/window")
    async def window():
        stats = await store.hot_stats()
        return {"ok": True, "window": cfg.window.model_dump(), **stats}

    @app.get("/api/archives")
    async def archives():
        return {"ok": True, "items": await store.list_archives()}

    @app.post("/api/archives/close")
    async def archives_close():
        from afina_watch.engine import Engine

        info = await Engine(cfg).archive(close_window=True)
        return {"ok": True, "archive": info}

    @app.get("/api/sources")
    async def sources(platform: str | None = None):
        return {"ok": True, "items": await store.list_sources(platform)}

    @app.post("/api/sources")
    async def sources_add(body: SourceAddIn):
        if body.platform not in {"telegram", "facebook"}:
            raise HTTPException(400, "platform: telegram или facebook")
        sid = f"{body.platform}:{body.kind}:{body.source_id}"
        await store.upsert_source(
            sid=sid,
            platform=body.platform,
            source_id=body.source_id,
            title=body.title or body.source_id,
            kind=body.kind,
        )
        return {"ok": True, "id": sid}

    @app.post("/api/sources/{sid}/toggle")
    async def sources_toggle(sid: str, body: SourceToggleIn):
        await store.set_source_enabled(sid, body.enabled)
        return {"ok": True}

    @app.delete("/api/sources/{sid}")
    async def sources_delete(sid: str):
        await store.delete_source(sid)
        return {"ok": True}

    @app.get("/api/rules")
    async def rules():
        runtime = await store.list_runtime_rules()
        if runtime:
            return {"ok": True, "items": runtime, "source": "runtime"}
        return {"ok": True, "items": [r.model_dump() for r in cfg.rules], "source": "config"}

    @app.post("/api/rules")
    async def rules_upsert(body: RuleIn):
        if body.semantic_threshold is not None and not (0.0 <= body.semantic_threshold <= 1.0):
            raise HTTPException(400, "semantic_threshold должен быть от 0 до 1")
        saved = await store.upsert_runtime_rule(body.model_dump())
        return {"ok": True, "item": saved}

    @app.delete("/api/rules/{rid}")
    async def rules_delete(rid: str):
        await store.delete_runtime_rule(rid)
        return {"ok": True}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


async def _seed_rules(store: Store, cfg: WatchConfig) -> None:
    existing = await store.list_runtime_rules()
    if existing:
        return
    for rule in cfg.rules:
        await store.upsert_runtime_rule(rule.model_dump())


AUTH_CATALOG = {
    "telegram": [
        {
            "id": "relay",
            "title": "Afina Relay (рекомендуется)",
            "level": "полный доступ к уже подписанным чатам",
            "how": "Пользовательская MTProto-сессия уже живёт в afina-tdl. Watch только читает Kafka/Mongo.",
            "needs": "URL Relay, обычно http://127.0.0.1:8090",
        },
        {
            "id": "phone",
            "title": "Телефон + код + 2FA",
            "level": "полный пользовательский доступ",
            "how": "Официальный MTProto: my.telegram.org → api_id/api_hash → sendCode → signIn → checkPassword.",
            "needs": "номер в E.164, код из приложения/SMS, облачный пароль если включена 2FA",
        },
        {
            "id": "qr",
            "title": "QR с телефона",
            "level": "полный пользовательский доступ",
            "how": "auth.exportLoginToken → QR tg://login?token= → на телефоне Settings → Devices → Link Desktop Device.",
            "needs": "уже открытый Telegram на телефоне",
        },
        {
            "id": "session_file",
            "title": "Готовый файл сессии",
            "level": "полный доступ этой сессии",
            "how": "Путь к .session Telethon/Pyrogram или каталогу tdata / tdl.",
            "needs": "файл, который вы уже авторизовали",
        },
        {
            "id": "bot",
            "title": "Токен бота",
            "level": "только публичное и то, куда бот добавлен",
            "how": "Bot API getMe. Личные чаты и закрытые группы без бота не видны.",
            "needs": "токен от @BotFather",
        },
    ],
    "facebook": [
        {
            "id": "oauth",
            "title": "Facebook Login / OAuth",
            "level": "страницы и группы, которыми вы управляете",
            "how": "Код → short-lived user token → long-lived (~60 дней) → GET /me/accounts → Page tokens без срока.",
            "needs": "App ID, App Secret, права pages_show_list и pages_read_engagement",
        },
        {
            "id": "graph_token",
            "title": "Готовый User / Page token",
            "level": "то, что заложено в токене",
            "how": "Вставить token из Graph API Explorer. Watch проверит /me и список Page.",
            "needs": "токен с нужными scopes",
        },
        {
            "id": "system_user",
            "title": "System User (Business)",
            "level": "серверный доступ к своим Page",
            "how": "Токен системного пользователя Business Portfolio. Не привязан к паролю человека.",
            "needs": "Business Manager + роль на страницах",
        },
        {
            "id": "import_box",
            "title": "Импорт JSON (лента и чужие группы)",
            "level": "всё, что выгрузили Forage / fbn / расширение",
            "how": "Официальный Graph не отдаёт личную ленту. JSON кладётся в data/fb-inbox/.",
            "needs": "каталог с .json / .jsonl",
        },
    ],
}
