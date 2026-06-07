from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "NeoMarket B2B"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default="postgresql+asyncpg://neomarket:secret@localhost:5432/b2b_db",
    )

    secret_key: str = Field(default="dev-secret-key-change-in-production")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    service_key: str = Field(default="internal-service-key")
    b2c_internal_url: str = "http://b2c_app:8002"
    moderation_internal_url: str = "http://moderation_app:8003"

    minio_endpoint: str = "http://minio:9000"
    minio_bucket: str = "neomarket-images"


settings = Settings()