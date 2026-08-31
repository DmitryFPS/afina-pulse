from afina_watch.collectors.facebook_import import parse_fb_post


def test_forage_shape():
    item = parse_fb_post(
        {
            "id": "123_456",
            "url": "https://www.facebook.com/groups/x/posts/456",
            "text": "ищем поставщика",
            "created_at": "2026-08-30T10:00:00Z",
            "author": {"id": "1", "name": "Ann"},
            "images": ["https://example.com/a.jpg"],
        }
    )
    assert item is not None
    assert item.id == "fb:123_456"
    assert item.author_name == "Ann"
    assert item.media[0].url.endswith("a.jpg")


def test_kevinzg_shape():
    item = parse_fb_post(
        {
            "post_id": "99",
            "post_url": "https://facebook.com/99",
            "text": "hello",
            "time": 1750000000,
            "username": "page",
            "images": ["https://example.com/b.jpg"],
        }
    )
    assert item is not None
    assert item.id == "fb:99"
    assert item.author_name == "page"
