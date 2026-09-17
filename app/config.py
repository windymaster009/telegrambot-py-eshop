from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: frozenset[int] = Field(default_factory=frozenset)
    support_username: str = "@support"
    shop_name: str = "Windy Shop"
    database_url: str = "sqlite+aiosqlite:///shop.db"
    payment_qr_path: Path = Path("assets/payment_qr.png")
    payment_account_name: str = "YOUR NAME"
    payment_account_number: str = "000 000 000"
    payment_expiry_minutes: int = Field(default=30, ge=5, le=1440)

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> frozenset[int]:
        if value in (None, ""):
            return frozenset()
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return frozenset(value)  # type: ignore[arg-type]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
