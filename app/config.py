from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="local", validation_alias="APP_ENV")
    log_level: str = Field(default="info", validation_alias="LOG_LEVEL")
    service_name: str = Field(default="stockbrief-api", validation_alias="SERVICE_NAME")
    service_version: str = Field(default="0.1.0", validation_alias="SERVICE_VERSION")
    api_base_path: str = Field(default="/v1", validation_alias="API_BASE_PATH")
    database_url: str = Field(
        default="",
        validation_alias="DATABASE_URL",
    )
    database_secret_arn: str = Field(default="", validation_alias="DATABASE_SECRET_ARN")
    database_host: str = Field(default="", validation_alias="DATABASE_HOST")
    database_port: int = Field(default=5432, validation_alias="DATABASE_PORT")
    database_name: str = Field(default="stockbrief", validation_alias="DATABASE_NAME")
    database_sslmode: str = Field(default="require", validation_alias="DATABASE_SSLMODE")
    database_pool_size: int = Field(default=5, validation_alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=10, validation_alias="DATABASE_MAX_OVERFLOW")
    database_pool_recycle_seconds: int = Field(default=1800, validation_alias="DATABASE_POOL_RECYCLE_SECONDS")
    database_pool_timeout_seconds: int = Field(default=30, validation_alias="DATABASE_POOL_TIMEOUT_SECONDS")
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="CORS_ALLOWED_ORIGINS",
    )
    opendart_api_key: str = Field(default="", validation_alias="OPENDART_API_KEY")
    naver_client_id: str = Field(default="", validation_alias="NAVER_CLIENT_ID")
    naver_client_secret: str = Field(default="", validation_alias="NAVER_CLIENT_SECRET")
    krx_api_key: str = Field(default="", validation_alias="KRX_API_KEY")
    krx_api_key_header: str = Field(default="Authorization", validation_alias="KRX_API_KEY_HEADER")
    krx_daily_url: str = Field(default="", validation_alias="KRX_DAILY_URL")
    external_api_secret_arn: str = Field(default="", validation_alias="EXTERNAL_API_SECRET_ARN")
    ingestion_raw_bucket: str = Field(default="", validation_alias="INGESTION_RAW_BUCKET")
    cognito_user_pool_id: str = Field(default="", validation_alias="COGNITO_USER_POOL_ID")
    cognito_app_client_id: str = Field(default="", validation_alias="COGNITO_APP_CLIENT_ID")
    cognito_issuer: str = Field(default="", validation_alias="COGNITO_ISSUER")
    cognito_jwks_url: str = Field(default="", validation_alias="COGNITO_JWKS_URL")
    chat_provider: Literal["mock", "bedrock"] = Field(default="mock", validation_alias="CHAT_PROVIDER")
    bedrock_chat_model_id: str = Field(default="apac.amazon.nova-micro-v1:0", validation_alias="BEDROCK_CHAT_MODEL_ID")
    bedrock_chat_region: str = Field(default="", validation_alias="BEDROCK_CHAT_REGION")
    bedrock_chat_max_tokens: int = Field(default=700, validation_alias="BEDROCK_CHAT_MAX_TOKENS")
    bedrock_chat_temperature: float = Field(default=0.2, validation_alias="BEDROCK_CHAT_TEMPERATURE")
    bedrock_chat_timeout_seconds: float = Field(default=8.0, validation_alias="BEDROCK_CHAT_TIMEOUT_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
