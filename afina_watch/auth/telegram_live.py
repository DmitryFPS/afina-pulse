from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("afina_watch.tg_live")


class QrLiveError(RuntimeError):
    pass


class TelegramQrLive:
    """One in-process Telethon QR login. wait() must run while the phone scans."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self.client: Any = None
        self.qr: Any = None
        self.status = "idle"
        self.error: str | None = None
        self.url: str | None = None
        self.me: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.qr = None
        self.status = "idle"
        self.url = None

    async def start(self, api_id: int, api_hash: str) -> dict[str, Any]:
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise QrLiveError(
                "Telethon не установлен. На ПК: pip install telethon"
            ) from exc
        if not api_id or not api_hash:
            raise QrLiveError("Нет api_id/api_hash")
        await self.close()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            str(self.session_dir / "afina-qr"),
            int(api_id),
            api_hash.strip(),
            device_model="Desktop",
            system_version="Windows 10",
            app_version="5.16.2 x64",
            lang_code="en",
            system_lang_code="en-US",
        )
        await self.client.connect()
        if await self.client.is_user_authorized():
            await self._store_me()
            self.status = "connected"
            return self.snapshot()
        self.qr = await self.client.qr_login()
        self.url = self.qr.url
        self.status = "pending_qr"
        self.error = None
        self._task = asyncio.create_task(self._wait_loop())
        return self.snapshot()

    async def _wait_loop(self) -> None:
        from telethon.errors import SessionPasswordNeededError

        while self.status == "pending_qr" and self.qr is not None:
            try:
                await self.qr.wait(timeout=25)
                if self.client and await self.client.is_user_authorized():
                    await self._store_me()
                    self.status = "connected"
                    return
            except SessionPasswordNeededError:
                self.status = "pending_2fa"
                return
            except asyncio.CancelledError:
                return
            except asyncio.TimeoutError:
                try:
                    await self.qr.recreate()
                    self.url = self.qr.url
                except Exception as exc:  # noqa: BLE001
                    self.status = "error"
                    self.error = f"Не удалось обновить QR: {exc}"
                    return
            except Exception as exc:  # noqa: BLE001
                self.status = "error"
                self.error = str(exc)
                return

    async def confirm_2fa(self, password: str) -> dict[str, Any]:
        if not self.client:
            raise QrLiveError("Сначала покажите живой QR")
        await self.client.sign_in(password=password)
        await self._store_me()
        self.status = "connected"
        self.error = None
        return self.snapshot()

    async def _store_me(self) -> None:
        me = await self.client.get_me()
        self.me = {
            "id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "first_name": getattr(me, "first_name", None),
            "phone": getattr(me, "phone", None),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": self.status != "error",
            "live": True,
            "status": self.status,
            "url": self.url if self.status == "pending_qr" else None,
            "expires_in": 25 if self.status == "pending_qr" else 0,
            "error": self.error,
            "me": self.me,
        }
