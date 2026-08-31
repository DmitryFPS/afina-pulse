from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from afina_watch.config import WatchConfig
from afina_watch.store.db import Store


def create_app(cfg: WatchConfig) -> FastAPI:
    store = Store(cfg.app.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.open()
        yield
        await store.close()

    app = FastAPI(title="Afina Watch", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True, "rules": [r.id for r in cfg.rules if r.enabled]}

    @app.get("/matches")
    async def matches(limit: int = 50):
        return await store.recent(limit=min(limit, 500))

    @app.get("/rules")
    async def rules():
        return [r.model_dump() for r in cfg.rules]

    @app.get("/window")
    async def window():
        stats = await store.hot_stats()
        return {"window": cfg.window.model_dump(), **stats}

    @app.get("/archives")
    async def archives():
        return await store.list_archives()

    return app
