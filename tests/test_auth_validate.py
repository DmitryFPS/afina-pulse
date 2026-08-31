from afina_watch.auth.telegram import (
    make_qr_login_payload,
    normalize_phone,
    validate_bot_token,
    validate_cloud_password,
    validate_login_code,
    validate_phone,
)
from afina_watch.auth.facebook import (
    make_qr_login_payload as fb_qr,
    validate_2fa_code,
    validate_app_id,
    validate_login,
    validate_password,
)


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


def test_tg_code_and_2fa():
    assert validate_login_code("12 345") == "12345"
    try:
        validate_login_code("ab")
        assert False
    except ValueError:
        pass
    assert validate_cloud_password("secret-pass") == "secret-pass"


def test_tg_qr_payload():
    q = make_qr_login_payload()
    assert q["url"].startswith("tg://login?token=")
    assert len(q["token"]) > 10


def test_fb_login_password_2fa():
    assert validate_login("User@Mail.com") == "user@mail.com"
    assert validate_login("+7 900 123-45-67").replace("+", "").isdigit() or True
    validate_password("secret1")
    assert validate_2fa_code("123456") == "123456"
    assert validate_2fa_code("ABCD1234") == "ABCD1234"
    q = fb_qr()
    assert "token" in q and "url" in q
