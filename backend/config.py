from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev")

    database_url: str = Field(
        default="postgresql+asyncpg://istore:istore@localhost:5432/istore"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://istore:istore@localhost:5432/istore"
    )

    redis_url: str = Field(default="redis://localhost:6379/0")

    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="istore")
    minio_secret_key: str = Field(default="istorepass")
    minio_secure: bool = Field(default=False)
    minio_bucket_originals: str = Field(default="istore-originals")
    minio_bucket_served: str = Field(default="istore-served")
    minio_bucket_faces: str = Field(default="istore-faces")

    jwt_secret: str = Field(default="dev-only-jwt-secret-CHANGE-IN-PROD")
    jwt_lifetime_seconds: int = Field(default=60 * 60 * 24)

    clip_model_name: str = Field(default="ViT-L-14")
    clip_pretrained: str = Field(default="openai")
    vision_enabled: bool = Field(default=True)


settings = Settings()
