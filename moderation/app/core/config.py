from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "NeoMarket Moderation"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default="postgresql+asyncpg://neomarket:secret@localhost:5432/mod_db",
    )

    secret_key: str = Field(default="dev-secret-key-change-in-production")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    service_key: str = Field(default="internal-service-key")
    b2b_internal_url: str = "http://b2b_app:8001"

    claim_timeout_minutes: int = Field(
        default=30,
        description="Timeout for IN_REVIEW. After expiry ticket returns to PENDING",
    )


settings = Settings()
