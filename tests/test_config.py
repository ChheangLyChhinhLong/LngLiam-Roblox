from pydantic import ValidationError

from app.config import Settings


def settings_values(**overrides):
    return {
        "bot_token": "bot-token",
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "service-role-key",
        "stock_encryption_key": "stock-key",
        "khqrpay_api_key": "",
        "khqrpay_aba_key": "",
        "khqrpay_bakong_key": "",
        **overrides,
    }


def test_settings_accept_each_khqrpay_key_separately():
    aba_settings = Settings(**settings_values(khqrpay_aba_key="aba-key"))
    bakong_settings = Settings(**settings_values(khqrpay_bakong_key="bakong-key"))

    assert aba_settings.khqrpay_aba_api_key == "aba-key"
    assert aba_settings.khqrpay_bakong_key == ""
    assert bakong_settings.khqrpay_aba_api_key == ""
    assert bakong_settings.khqrpay_bakong_key == "bakong-key"


def test_settings_keep_legacy_khqrpay_key_as_aba_fallback():
    settings = Settings(**settings_values(khqrpay_api_key="legacy-key"))

    assert settings.khqrpay_aba_api_key == "legacy-key"


def test_settings_require_at_least_one_khqrpay_key():
    try:
        Settings(**settings_values())
    except ValidationError as exc:
        assert "Configure KHQRPAY_ABA_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing KHQRPay keys to be rejected")
