from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from afina_watch.alerts.dispatcher import Dispatcher
from afina_watch.collectors.facebook_browser import FacebookBrowserCollector
from afina_watch.collectors.facebook_graph import FacebookGraphCollector
from afina_watch.collectors.facebook_import import FacebookImportCollector
from afina_watch.collectors.telegram_relay import TelegramRelayCollector
from afina_watch.config import WatchConfig
from afina_watch.media.pipeline import MediaPipeline
from afina_watch.models import NormalizedItem, WatchResult
from afina_watch.nlp.matcher import Matcher
from afina_watch.store.archive import archive_hot
from afina_watch.store.db import Store
from afina_watch.window import in_window, lookback_start

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: WatchConfig):
        self.cfg = cfg
        self.store = Store(cfg.app.db_path)
        self.alerts = Dispatcher(cfg.alerts)
        self.matcher = Matcher(cfg)
        self.media = MediaPipeline(
            cache_dir=Path(cfg.app.media_cache),
            llm=cfg.llm,
            matching=cfg.matching,
            mongo=cfg.telegram.mongo if cfg.telegram.enabled else None,
        )
        self._queue: asyncio.Queue[NormalizedItem] = asyncio.Queue(maxsize=256)

    def _collectors(self) -> list:
        out = []
        if self.cfg.telegram.enabled:
            out.append(TelegramRelayCollector(self.cfg.telegram))
        if self.cfg.facebook.enabled:
            graph = FacebookGraphCollector(self.cfg.facebook)
            graph.since = lookback_start(self.cfg.window)
            out.append(graph)
            out.append(FacebookBrowserCollector(self.cfg.facebook))
            if self.cfg.facebook.import_box.enabled:
                box = self.cfg.facebook.import_box
                out.append(
                    FacebookImportCollector(box.inbox_dir, box.poll_seconds, box.archive)
                )
        return out

    async def _pump(self, collector) -> None:
        try:
            async for item in collector.stream():
                await self._queue.put(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("collector crashed %s", getattr(collector, "name", collector))

    async def _worker(self, wid: int) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self.handle(item)
            except Exception:
                log.exception("worker %s failed on %s", wid, item.id)
            finally:
                self._queue.task_done()

    async def _auto_archive_loop(self) -> None:
        hours = max(1, self.cfg.window.auto_archive_every_hours)
        while True:
            await asyncio.sleep(hours * 3600)
            try:
                info = await archive_hot(
                    self.store, self.cfg.window, self.cfg.app.media_cache, close_window=False
                )
                log.info("auto-archive %s", info)
            except Exception:
                log.exception("auto-archive failed")

    async def handle(self, item: NormalizedItem) -> WatchResult | None:
        if not in_window(item, self.cfg.window):
            log.debug("drop older than lookback %s %s", item.id, item.published_at)
            return None
        if await self.store.has_item(item.id):
            log.debug("skip duplicate %s", item.id)
            return None

        digest = await asyncio.to_thread(self.media.digest, item)
        hits = await asyncio.to_thread(self.matcher.match, item, digest)
        result = WatchResult(item=item, digest=digest, hits=hits, matched=bool(hits))
        await self.store.save(result)
        if result.matched:
            await self.alerts.emit(result)
        else:
            log.debug("stored no-match %s", item.id)
        return result

    async def backfill(self, days: int | None = None) -> int:
        days = days or self.cfg.window.lookback_days
        since = datetime.now(timezone.utc) - timedelta(days=days)
        n = 0
        await self.store.open()
        try:
            if self.cfg.telegram.enabled:
                col = TelegramRelayCollector(self.cfg.telegram)
                async for item in col.backfill(since):
                    got = await self.handle(item)
                    if got is not None:
                        n += 1
            if self.cfg.facebook.enabled:
                graph = FacebookGraphCollector(self.cfg.facebook)
                graph.since = since
                # один проход, не вечный poll
                tokens = self.cfg.facebook.graph.page_tokens
                if tokens:
                    import httpx

                    async with httpx.AsyncClient(timeout=30) as client:
                        for page in tokens:
                            async for item in graph._poll_page(client, page.page_id, page.token):
                                got = await self.handle(item)
                                if got is not None:
                                    n += 1
            log.info("backfill done days=%s items=%s", days, n)
            return n
        finally:
            await self.store.close()

    async def archive(self, close_window: bool = False) -> dict:
        await self.store.open()
        try:
            return await archive_hot(
                self.store,
                self.cfg.window,
                self.cfg.app.media_cache,
                close_window=close_window,
            )
        finally:
            await self.store.close()

    async def run(self) -> None:
        await self.store.open()
        workers = [
            asyncio.create_task(self._worker(i), name=f"worker-{i}")
            for i in range(max(1, self.cfg.app.workers))
        ]
        pumps = [
            asyncio.create_task(self._pump(c), name=f"pump-{c.name}")
            for c in self._collectors()
        ]
        extra = []
        if self.cfg.window.auto_archive:
            extra.append(asyncio.create_task(self._auto_archive_loop(), name="auto-archive"))
        if not pumps:
            raise SystemExit("ни один коллектор не включён — проверьте configs/watch.yaml")
        log.info(
            "watch started collectors=%d workers=%d lookback=%dd hot=%dd",
            len(pumps),
            len(workers),
            self.cfg.window.lookback_days,
            self.cfg.window.hot_days,
        )
        try:
            await asyncio.gather(*pumps, *workers, *extra)
        finally:
            await self.store.close()
