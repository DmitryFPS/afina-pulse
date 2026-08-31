from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

APP_ID_RE = re.compile(r"^\d{5,20}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-|]{20,}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_LOGIN_RE = re.compile(r"^\+?[0-9][0-9 \-()]{6,18}$")
TOTP_RE = re.compile(r"^\d{6,8}$")
RECOVERY_RE = re.compile(r"^[A-Za-z0-9]{8,14}$")

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

MONITOR_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_read_user_content",
    "groups_show_list",
    "public_profile",
    "email",
]


def validate_app_id(app_id: str) -> str:
    app_id = (app_id or "").strip()
    if not APP_ID_RE.match(app_id):
        raise ValueError("App ID должен состоять только из цифр")
    return app_id


def validate_login(login: str) -> str:
    login = (login or "").strip()
    if EMAIL_RE.match(login):
        return login.lower()
    compact = login.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if PHONE_LOGIN_RE.match(login) and len(re.sub(r"\D", "", compact)) >= 7:
        return compact
    raise ValueError("Введите email или номер телефона, привязанный к Facebook")


def validate_password(password: str) -> str:
    if not password or len(password) < 6:
        raise ValueError("Пароль Facebook должен быть не короче 6 символов")
    return password


def validate_2fa_code(code: str) -> str:
    code = (code or "").strip().replace(" ", "")
    if TOTP_RE.match(code) or RECOVERY_RE.match(code):
        return code
    raise ValueError("Код 2FA — 6–8 цифр из приложения/SMS или резервный код")


def make_qr_login_payload(landing: str | None = None) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    url = landing or f"https://www.facebook.com/qr?token={token}"
    return {"token": token, "url": url, "expires_in": 60}


def validate_token(token: str) -> str:
    token = (token or "").strip()
    if len(token) < 20:
        raise ValueError("Access token слишком короткий")
    return token


def oauth_dialog_url(app_id: str, redirect_uri: str, state: str) -> str:
    app_id = validate_app_id(app_id)
    q = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(MONITOR_SCOPES),
        }
    )
    return f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?{q}"


async def exchange_code(
    app_id: str, app_secret: str, redirect_uri: str, code: str, timeout: float = 12.0
) -> dict[str, Any]:
    app_id = validate_app_id(app_id)
    if not (app_secret or "").strip():
        raise ValueError("App Secret обязателен")
    if not (code or "").strip():
        raise ValueError("Нет кода авторизации")
    params = {
        "client_id": app_id,
        "client_secret": app_secret.strip(),
        "redirect_uri": redirect_uri,
        "code": code.strip(),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{GRAPH}/oauth/access_token", params=params)
        data = r.json()
    if "access_token" not in data:
        raise PermissionError(data.get("error", {}).get("message") or "Не удалось обменять код")
    return data


async def exchange_long_lived(
    app_id: str, app_secret: str, short_token: str, timeout: float = 12.0
) -> dict[str, Any]:
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": validate_app_id(app_id),
        "client_secret": (app_secret or "").strip(),
        "fb_exchange_token": short_token,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{GRAPH}/oauth/access_token", params=params)
        data = r.json()
    if "access_token" not in data:
        raise PermissionError(data.get("error", {}).get("message") or "Не удалось получить long-lived token")
    return data


async def inspect_user_token(token: str, timeout: float = 10.0) -> dict[str, Any]:
    token = validate_token(token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        me = await client.get(f"{GRAPH}/me", params={"fields": "id,name", "access_token": token})
        me_data = me.json()
        if "id" not in me_data:
            raise PermissionError(me_data.get("error", {}).get("message") or "Токен отклонён Graph API")
        pages = await client.get(
            f"{GRAPH}/me/accounts",
            params={"fields": "id,name,access_token,tasks", "access_token": token},
        )
        pages_data = pages.json()
        perms = await client.get(f"{GRAPH}/me/permissions", params={"access_token": token})
        perms_data = perms.json()
    page_list = pages_data.get("data") or []
    return {
        "ok": True,
        "user_id": me_data.get("id"),
        "name": me_data.get("name"),
        "pages": [
            {"id": p.get("id"), "name": p.get("name"), "tasks": p.get("tasks") or []}
            for p in page_list
        ],
        "permissions": perms_data.get("data") or [],
        "page_count": len(page_list),
        "limitation": (
            "Graph API отдаёт страницы и группы, которыми вы управляете. "
            "Личная лента и чужие группы, где вы просто участник, через официальный API недоступны."
        ),
    }


def probe_import_dir(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    files = [f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in {".json", ".jsonl"}]
    return {"ok": True, "path": str(p.resolve()), "json_files": len(files), "files": files[:20]}
