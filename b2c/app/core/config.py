from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "NeoMarket B2C"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default="postgresql+asyncpg://neomarket:secret@localhost:5433/b2c_db",
    )

    secret_key: str = Field(default="b2c-dev-secret-key")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Межсервисный ключ — B2C отправляет его в B2B
    service_key: str = Field(default="internal-service-key-b2c")
    b2b_internal_url: str = "http://b2b_app:8001"


settings = Settings()
