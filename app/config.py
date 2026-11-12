from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    telegram_admin_ids: str = ""
    supabase_url: str
    supabase_service_role_key: str
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    khqrpay_aba_key: str = ""
    khqrpay_bakong_key: str = ""
    khqrpay_api_key: str = ""
    khqrpay_base_url: str = "https://khqrpay.site/api/v1"
    khqrpay_webhook_secret: str = ""
    khqrpay_aba_webhook_secret: str = ""
    khqrpay_bakong_webhook_secret: str = ""
    khqrpay_webhook_url: str = ""
    public_base_url: str = ""
    stock_encryption_key: str
    poll_interval_seconds: int = 20
    port: int = 10000

    @model_validator(mode="after")
    def require_khqrpay_key(self) -> "Settings":
        if not (self.khqrpay_aba_api_key or self.khqrpay_bakong_key.strip()):
            raise ValueError(
                "Configure KHQRPAY_ABA_KEY or KHQRPAY_BAKONG_KEY"
            )
        return self

    @property
    def khqrpay_aba_api_key(self) -> str:
        return self.khqrpay_aba_key.strip() or self.khqrpay_api_key.strip()

    @property
    def khqrpay_webhook_secrets(self) -> tuple[str, ...]:
        return tuple(
            secret.strip()
            for secret in (
                self.khqrpay_aba_webhook_secret,
                self.khqrpay_bakong_webhook_secret,
                self.khqrpay_webhook_secret,
            )
            if secret.strip()
        )

    @property
    def admin_ids(self) -> set[int]:
        return {int(value.strip()) for value in self.telegram_admin_ids.split(",") if value.strip()}

    @property
    def app_base_url(self) -> str | None:
        value = self.public_base_url.strip() or self.khqrpay_webhook_url.strip()
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            return None
        return f"https://{parsed.netloc}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
