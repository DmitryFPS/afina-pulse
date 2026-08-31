from datetime import datetime, timedelta, timezone

from afina_watch.config import WindowCfg
from afina_watch.models import NormalizedItem, Platform
from afina_watch.store.archive import pack_rows
from afina_watch.window import in_window


def _item(days_ago: int) -> NormalizedItem:
    return NormalizedItem(
        id=f"tg:1:{days_ago}",
        platform=Platform.telegram,
        source_id="1",
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        text="x",
    )


def test_seven_day_window():
    w = WindowCfg(lookback_days=7)
    assert in_window(_item(0), w)
    assert in_window(_item(6), w)
    assert not in_window(_item(8), w)


def test_pack_zip(tmp_path):
    rows = [
        {
            "item_id": "tg:1:1",
            "platform": "telegram",
            "source_id": "1",
            "source_title": "lab",
            "permalink": None,
            "published_at": "2026-08-24T00:00:00+00:00",
            "ingested_at": "2026-08-31T00:00:00+00:00",
            "matched": 1,
            "text": "hello",
            "item_json": "{}",
            "digest_json": "{}",
            "media_paths": "",
        }
    ]
    dest = tmp_path / "a.zip"
    info = pack_rows(rows, dest, include_media=False, media_root=tmp_path)
    assert dest.exists()
    assert info["items"] == 1
    assert info["matches"] == 1
