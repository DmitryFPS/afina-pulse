from afina_watch.auth.telegram import normalize_phone, validate_bot_token, validate_phone
from afina_watch.auth.facebook import validate_app_id


def test_phone_ok():
    assert validate_phone("+79001234567") == "+79001234567"
    assert validate_phone("8 (900) 123-45-67") == "+79001234567"


def test_phone_bad():
    try:
        validate_phone("123")
        assert False
    except ValueError:
        pass


def test_bot_token_bad():
    try:
        validate_bot_token("nope")
        assert False
    except ValueError:
        pass


def test_app_id():
    assert validate_app_id("1234567890") == "1234567890"
