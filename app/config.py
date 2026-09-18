from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    # NoDecode lets the validator accept either 1,2 or [1,2].
    admin_ids: Annotated[frozenset[int], NoDecode] = Field(default_factory=frozenset)
    support_username: str = "@support"
    shop_name: str = "Windy Shop"

    mongo_uri: SecretStr
    mongo_db_name: str = "eshop"
    api_key: SecretStr = Field(min_length=32)

    payment_qr_path: Path = Path("assets/payment_qr.png")
    payment_account_name: str = "YOUR NAME"
    payment_account_number: str = "000 000 000"
    payment_expiry_minutes: int = Field(default=30, ge=5, le=1440)
    topup_expiry_minutes: int = Field(default=15, ge=5, le=1440)
    admin_test_mode_enabled: bool = False

    auto_topup_enabled: bool = False
    payment_check_bot_token: SecretStr | None = None
    aba_payment_group_id: int | None = None
    aba_payment_bot_username: str = "PayWayByABA_bot"
    payment_reader_api_id: int | None = Field(default=None, ge=1)
    payment_reader_api_hash: SecretStr | None = None
    payment_reader_session: SecretStr | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> frozenset[int]:
        if value in (None, ""):
            return frozenset()
        if isinstance(value, int):
            return frozenset({value})
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                cleaned = cleaned[1:-1]
            return frozenset(int(item.strip()) for item in cleaned.split(",") if item.strip())
        return frozenset(value)  # type: ignore[arg-type]

    @field_validator(
        "payment_check_bot_token",
        "aba_payment_group_id",
        "payment_reader_api_id",
        "payment_reader_api_hash",
        "payment_reader_session",
        mode="before",
    )
    @classmethod
    def blank_optional_values(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("aba_payment_bot_username")
    @classmethod
    def normalize_bot_username(cls, value: str) -> str:
        cleaned = value.strip().removeprefix("@").lower()
        if not cleaned:
            raise ValueError("ABA_PAYMENT_BOT_USERNAME cannot be empty")
        return cleaned

    @field_validator("mongo_db_name")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character in cleaned for character in '/\\."$'):
            raise ValueError("MONGO_DB_NAME contains an invalid character")
        return cleaned

    @field_validator("payment_qr_path")
    @classmethod
    def resolve_payment_qr_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def payment_reader_configured(self) -> bool:
        return (
            self.payment_reader_api_id is not None
            and self.payment_reader_api_hash is not None
            and self.payment_reader_session is not None
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
