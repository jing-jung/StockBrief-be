from app.config import Settings
from app import db


def test_resolve_database_url_prefers_local_env_when_local(monkeypatch) -> None:
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(
            APP_ENV="local",
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            DATABASE_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:stockbrief",
        ),
    )
    monkeypatch.setattr(
        db,
        "load_secret_json",
        lambda _secret_id: (_ for _ in ()).throw(AssertionError("secret should not be loaded")),
    )
    db.resolve_database_url.cache_clear()

    assert db.resolve_database_url() == "sqlite+pysqlite:///:memory:"


def test_resolve_database_url_uses_secret_when_env_url_missing(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(
            APP_ENV="prod",
            DATABASE_URL="",
            DATABASE_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:stockbrief",
        ),
    )
    monkeypatch.setattr(
        db,
        "load_secret_json",
        lambda secret_id: calls.append(secret_id) or {"DATABASE_URL": "postgresql+psycopg://prod"}
    )
    db.resolve_database_url.cache_clear()

    assert db.resolve_database_url() == "postgresql+psycopg://prod"
    assert calls == ["arn:aws:secretsmanager:ap-northeast-2:123:secret:stockbrief"]


def test_resolve_database_url_builds_proxy_url_from_secret_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(
            APP_ENV="prod",
            DATABASE_URL="",
            DATABASE_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:stockbrief",
            DATABASE_HOST="stockbrief.proxy-abc.ap-northeast-2.rds.amazonaws.com",
            DATABASE_PORT=5432,
            DATABASE_NAME="stockbrief",
        ),
    )
    monkeypatch.setattr(
        db,
        "load_secret_json",
        lambda _secret_id: {"username": "stockbrief_admin", "password": "secret/pass"},
    )
    db.resolve_database_url.cache_clear()

    assert (
        db.resolve_database_url()
        == "postgresql+psycopg://stockbrief_admin:secret%2Fpass@stockbrief.proxy-abc.ap-northeast-2.rds.amazonaws.com:5432/stockbrief"
    )


def test_pool_options_are_configurable_for_postgresql(monkeypatch) -> None:
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://stockbrief:stockbrief@db.example:5432/stockbrief",
        DATABASE_POOL_SIZE=3,
        DATABASE_MAX_OVERFLOW=4,
        DATABASE_POOL_RECYCLE_SECONDS=900,
        DATABASE_POOL_TIMEOUT_SECONDS=15,
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.resolve_database_url.cache_clear()

    assert db._pool_options(settings) == {
        "pool_size": 3,
        "max_overflow": 4,
        "pool_recycle": 900,
        "pool_timeout": 15,
    }


def test_pool_options_are_skipped_for_sqlite(monkeypatch) -> None:
    settings = Settings(DATABASE_URL="sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.resolve_database_url.cache_clear()

    assert db._pool_options(settings) == {}
