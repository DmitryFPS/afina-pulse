from afina_watch.nlp.keywords import keyword_hits


def test_plain_and_regex():
    text = "Обход санкций через третьи страны"
    assert "санкц" in keyword_hits(text, ["санкц", "foo"])
    assert "/обход.санк/" in keyword_hits(text, ["/обход.санк/"])
    assert keyword_hits(text, ["неттакого"]) == []
