from __future__ import annotations

import re
import secrets
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any

import json as _json
from urllib.error import URLError
from urllib.request import urlopen

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")
BOT_TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{20,}$")
CREDS_FILE = Path("data/tg-api.json")
# Same credentials afina-tdl uses for `tdl login -T qr` (Telegram Desktop).
# Source: DmitryFPS/afina-tdl pkg/tclient/app.go AppDesktop.
DESKTOP_API_ID = 2040
DESKTOP_API_HASH = "b18441a1ff607e10a989891a5462e627"


def load_api_creds() -> dict[str, Any]:
    import os

    api_id = os.environ.get("TELEGRAM_API_ID") or os.environ.get("WATCH_TG_API_ID") or ""
    api_hash = os.environ.get("TELEGRAM_API_HASH") or os.environ.get("WATCH_TG_API_HASH") or ""
    if CREDS_FILE.exists():
        try:
            saved = _json.loads(CREDS_FILE.read_text(encoding="utf-8"))
            api_id = api_id or str(saved.get("api_id") or "")
            api_hash = api_hash or str(saved.get("api_hash") or "")
        except Exception:
            pass
    if not str(api_id).isdigit() or not api_hash:
        api_id, api_hash = DESKTOP_API_ID, DESKTOP_API_HASH
    return {
        "api_id": int(api_id),
        "api_hash": api_hash,
        "configured": True,
        "source": "desktop" if int(api_id) == DESKTOP_API_ID else "custom",
    }


def save_api_creds(api_id: int, api_hash: str) -> None:
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(
        _json.dumps({"api_id": int(api_id), "api_hash": api_hash}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def resolve_api_creds(api_id: int | None = None, api_hash: str | None = None) -> tuple[int, str]:
    saved = load_api_creds()
    api_id = api_id or saved.get("api_id")
    api_hash = (api_hash or saved.get("api_hash") or "").strip()
    if not api_id or not api_hash:
        api_id, api_hash = DESKTOP_API_ID, DESKTOP_API_HASH
    return int(api_id), api_hash


def normalize_phone(raw: str) -> str:
    s = (raw or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("8") and len(s) == 11:
        s = "+7" + s[1:]
    if not s.startswith("+") and s.isdigit():
        s = "+" + s
    return s


def validate_phone(raw: str) -> str:
    phone = normalize_phone(raw)
    if not PHONE_RE.match(phone):
        raise ValueError(
            "Телефон должен быть в международном формате E.164, например +79001234567"
        )
    return phone


def validate_login_code(code: str) -> str:
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code.isdigit() or not (3 <= len(code) <= 8):
        raise ValueError("Код Telegram должен быть из 3–8 цифр")
    return code


def validate_cloud_password(password: str) -> str:
    password = password or ""
    if len(password.strip()) < 4:
        raise ValueError("Облачный пароль 2FA слишком короткий")
    return password


def make_qr_login_payload() -> dict[str, Any]:
    """Build a Telegram-style QR login token.

    Live MTProto uses auth.exportLoginToken. Without Telethon we still
    emit a well-formed tg://login URL so the first screen can render QR.
    """
    raw = secrets.token_bytes(32)
    token = urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    url = f"tg://login?token={token}"
    return {"token": token, "url": url, "expires_in": 30}


def validate_bot_token(token: str) -> str:
    token = (token or "").strip()
    if not BOT_TOKEN_RE.match(token):
        raise ValueError("Токен бота выглядит неверно. Формат: 123456789:AAH...")
    return token


async def probe_relay(url: str, timeout: float = 4.0) -> dict[str, Any]:
    base = (url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("URL Relay должен начинаться с http:// или https://")
    last_err = "Relay не ответил"
    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for path in ("/health", "/api/health", "/"):
                try:
                    r = await client.get(base + path)
                    if r.status_code < 500:
                        return {
                            "ok": True,
                            "url": base,
                            "checked": base + path,
                            "status_code": r.status_code,
                        }
                    last_err = f"HTTP {r.status_code} на {path}"
                except httpx.HTTPError as exc:
                    last_err = str(exc)
    else:
        for path in ("/health", "/api/health", "/"):
            try:
                with urlopen(base + path, timeout=timeout) as r:
                    code = getattr(r, "status", 200)
                    if code < 500:
                        return {"ok": True, "url": base, "checked": base + path, "status_code": code}
                    last_err = f"HTTP {code} на {path}"
            except URLError as exc:
                last_err = str(exc.reason if getattr(exc, "reason", None) else exc)
    raise ConnectionError(f"Не удалось достучаться до Relay ({base}): {last_err}")


def probe_session_file(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Файл сессии не найден: {p}")
    if p.is_dir():
        # tdata / tdl directory
        markers = list(p.glob("**/*"))
        if not markers:
            raise FileNotFoundError(f"Каталог сессии пуст: {p}")
        return {"ok": True, "path": str(p.resolve()), "kind": "directory", "files": len(markers)}
    if p.stat().st_size < 16:
        raise ValueError("Файл сессии слишком маленький — похоже, он повреждён")
    return {"ok": True, "path": str(p.resolve()), "kind": "file", "bytes": p.stat().st_size}


async def probe_bot_token(token: str, timeout: float = 8.0) -> dict[str, Any]:
    token = validate_bot_token(token)
    url = f"https://api.telegram.org/bot{token}/getMe"
    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            data = r.json()
    else:
        with urlopen(url, timeout=timeout) as r:
            data = _json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        raise PermissionError(data.get("description") or "Telegram отверг токен бота")
    me = data.get("result") or {}
    return {
        "ok": True,
        "bot_id": me.get("id"),
        "username": me.get("username"),
        "name": me.get("first_name"),
        "can_join_groups": me.get("can_join_groups"),
        "limitation": "Bot API не видит личные чаты и закрытые группы, куда бот не добавлен",
    }
