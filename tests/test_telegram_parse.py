from afina_watch.collectors.telegram_relay import event_to_item


def test_event_to_item_minimal():
    ev = {
        "payload": {
            "peer_id": "123",
            "message_id": 9,
            "text": "hello afina",
            "chat_title": "lab",
            "media": [{"mime": "image/jpeg", "gridfs_id": "abc"}],
        }
    }
    item = event_to_item(ev)
    assert item is not None
    assert item.id == "tg:123:9"
    assert item.text == "hello afina"
    assert item.media[0].gridfs_id == "abc"
