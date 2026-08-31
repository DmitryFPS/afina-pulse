from __future__ import annotations

from datetime import datetime, timedelta, timezone

from afina_watch.config import WindowCfg
from afina_watch.models import NormalizedItem


def lookback_start(window: WindowCfg, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=window.lookback_days)


def in_window(item: NormalizedItem, window: WindowCfg, now: datetime | None = None) -> bool:
    start = lookback_start(window, now)
    ts = item.published_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= start
