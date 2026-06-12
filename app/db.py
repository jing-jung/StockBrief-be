from collections.abc import Generator
from functools import lru_cache
from urllib.parse import quote

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.services.external.aws_secrets import load_secret_json


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        resolve_database_url(),
        pool_pre_ping=True,
        **_pool_options(settings),
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@lru_cache
def resolve_database_url() -> str:
    settings = get_settings()
    if settings.app_env == "local" and settings.database_url:
        return settings.database_url
    if settings.database_url and not settings.database_secret_arn:
        return settings.database_url
    if settings.database_secret_arn:
        secret = load_secret_json(settings.database_secret_arn)
        database_url = secret.get("DATABASE_URL")
        if isinstance(database_url, str) and database_url:
            return database_url
        database_url = _database_url_from_secret_credentials(secret)
        if database_url:
            return database_url
        raise RuntimeError(
            "DATABASE_SECRET_ARN must resolve to a secret containing DATABASE_URL "
            "or username/password credentials with DATABASE_HOST configured."
        )
    if settings.database_url:
        return settings.database_url
    raise RuntimeError("DATABASE_URL or DATABASE_SECRET_ARN must be configured.")


def _database_url_from_secret_credentials(secret: dict[str, object]) -> str | None:
    settings = get_settings()
    username = secret.get("username")
    password = secret.get("password")
    if not (
        isinstance(username, str)
        and username
        and isinstance(password, str)
        and settings.database_host
    ):
        return None
    user = quote(username, safe="")
    secret_password = quote(password, safe="")
    return (
        f"postgresql+psycopg://{user}:{secret_password}"
        f"@{settings.database_host}:{settings.database_port}/{settings.database_name}"
    )


def _pool_options(settings) -> dict[str, int]:
    database_url = resolve_database_url()
    if database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle_seconds,
        "pool_timeout": settings.database_pool_timeout_seconds,
    }
