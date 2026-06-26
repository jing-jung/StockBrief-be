from datetime import date
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.orm import (
    Base,
    ChatMessage,
    CompanyIdentifier,
    IngestionRun,
    RecommendationScore,
    User,
    Watchlist,
)

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_VERSION_DIR = API_ROOT / "migrations/versions"


EXPECTED_TABLES = {
    "stocks",
    "company_identifiers",
    "financial_statements",
    "disclosures",
    "news_items",
    "price_metrics",
    "source_documents",
    "evidence_chunks",
    "recommendation_score_rules",
    "recommendation_scores",
    "recommendation_reasons",
    "risk_signals",
    "api_cache_entries",
    "external_api_call_logs",
    "chat_sessions",
    "chat_messages",
    "users",
    "user_preferences",
    "watchlists",
    "ingestion_runs",
}


def test_metadata_contains_mvp_tables() -> None:
    assert EXPECTED_TABLES.issubset(Base.metadata.tables.keys())


def test_initial_migration_creates_mvp_tables() -> None:
    migration = (API_ROOT / "migrations/versions/0001_initial_mvp_schema.py").read_text()

    for table_name in EXPECTED_TABLES - {"users", "user_preferences", "watchlists", "ingestion_runs"}:
        assert f'"{table_name}"' in migration


def test_p1_auth_migration_creates_user_state_tables() -> None:
    migration = (API_ROOT / "migrations/versions/0002_p1_auth_user_state.py").read_text()

    for table_name in {"users", "user_preferences", "watchlists"}:
        assert f'"{table_name}"' in migration
    assert '"cognito_sub"' in migration
    assert '"user_id"' in migration


def test_create_all_builds_core_tables_and_columns() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))

    stock_columns = {column["name"] for column in inspector.get_columns("stocks")}
    assert {"ticker", "company_name", "market", "is_active"}.issubset(stock_columns)

    score_columns = {
        column["name"] for column in inspector.get_columns("recommendation_scores")
    }
    assert {
        "ticker",
        "as_of_date",
        "score_version",
        "total_score",
        "component_scores",
        "missing_data",
        "data_freshness",
    }.issubset(score_columns)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert {"id", "cognito_sub", "email", "email_verified", "nickname"}.issubset(user_columns)
    assert "password" not in user_columns


def test_ingestion_runs_schema_is_declared() -> None:
    table = Base.metadata.tables["ingestion_runs"]
    assert {"run_id", "job_type", "provider", "status", "input_hash", "started_at"}.issubset(
        table.c.keys()
    )

    constraints = {constraint.name for constraint in IngestionRun.__table__.constraints}
    assert "uq_ingestion_runs_run_id" in constraints
    indexes = {index.name for index in IngestionRun.__table__.indexes}
    assert "uq_ingestion_runs_active_input_hash" in indexes


def test_chat_messages_session_id_index_is_declared() -> None:
    indexes = {index.name for index in ChatMessage.__table__.indexes}

    assert "ix_chat_messages_session_id" in indexes


def test_create_all_builds_chat_messages_session_id_index() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("chat_messages")}
    assert "ix_chat_messages_session_id" in indexes


def test_chat_message_session_lookup_plan_uses_session_id_index() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with engine.connect() as connection:
        plan = connection.execute(
            text("EXPLAIN QUERY PLAN SELECT * FROM chat_messages WHERE session_id = 'chat-1'")
        ).fetchall()

    plan_text = " ".join(str(column) for row in plan for column in row)
    assert "ix_chat_messages_session_id" in plan_text


def test_ingestion_runs_migration_creates_table() -> None:
    migration = (API_ROOT / "migrations/versions/0003_ingestion_runs.py").read_text()
    input_hash_guard_migration = (
        API_ROOT / "migrations/versions/0004_ingestion_input_hash_guard.py"
    ).read_text()

    assert '"ingestion_runs"' in migration
    assert "uq_ingestion_runs_run_id" in migration
    assert "ix_ingestion_runs_job_type_provider_status" in migration
    assert "uq_ingestion_runs_active_input_hash" in input_hash_guard_migration
    assert "status IN ('started', 'succeeded')" in input_hash_guard_migration
    assert "Cannot create uq_ingestion_runs_active_input_hash" in input_hash_guard_migration
    assert "having count(*) > 1" in input_hash_guard_migration


def test_chat_messages_session_id_index_migration_is_declared() -> None:
    migration = (
        API_ROOT / "migrations/versions/0005_chat_messages_session_id_index.py"
    ).read_text()

    assert "ix_chat_messages_session_id" in migration
    assert '"chat_messages"' in migration
    assert '"session_id"' in migration
    assert 'context.dialect.name == "postgresql"' in migration
    assert "context.autocommit_block()" in migration
    assert "postgresql_concurrently=True" in migration


def test_alembic_revision_chain_points_to_existing_revisions() -> None:
    revisions: dict[str, str | None] = {}

    for path in MIGRATION_VERSION_DIR.glob("*.py"):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        revision = module.revision
        down_revision = module.down_revision
        assert down_revision is None or isinstance(down_revision, str)
        revisions[revision] = down_revision

    for revision, down_revision in revisions.items():
        if down_revision is not None:
            assert down_revision in revisions, f"{revision} points at missing down_revision"


def test_alembic_version_table_is_widened_before_long_revision_ids() -> None:
    widen_migration = (
        MIGRATION_VERSION_DIR / "0005a_alembic_version_width.py"
    ).read_text(encoding="utf-8")
    chat_index_migration = (
        MIGRATION_VERSION_DIR / "0005_chat_messages_session_id_index.py"
    ).read_text(encoding="utf-8")

    assert "revision: str = \"0005a_alembic_version_width\"" in widen_migration
    assert "down_revision: str | None = \"0004_ingestion_input_hash_guard\"" in widen_migration
    assert '"alembic_version"' in widen_migration
    assert '"version_num"' in widen_migration
    assert "sa.String(length=255)" in widen_migration
    assert "down_revision: str | None = \"0005a_alembic_version_width\"" in chat_index_migration


def test_db_schema_documents_input_hash_migration_precheck() -> None:
    schema_doc = (API_ROOT / "docs/engineering/DB_SCHEMA.md").read_text()

    assert "0004_ingestion_input_hash_guard" in schema_doc
    assert "select input_hash, count(*) as duplicate_count" in schema_doc
    assert "where status in ('started', 'succeeded')" in schema_doc
    assert "having count(*) > 1" in schema_doc
    assert "migration intentionally fails with an explicit message" in schema_doc


def test_required_uniqueness_constraints_are_declared() -> None:
    stocks = Base.metadata.tables["stocks"]
    assert stocks.c.ticker.unique is True

    identifier_constraints = {
        constraint.name for constraint in CompanyIdentifier.__table__.constraints
    }
    assert "uq_company_identifiers_provider_type_value" in identifier_constraints
    assert "uq_company_identifiers_ticker_provider_type" in identifier_constraints

    score_constraints = {
        constraint.name for constraint in RecommendationScore.__table__.constraints
    }
    assert "uq_recommendation_scores_ticker_date_version" in score_constraints

    user_constraints = {constraint.name for constraint in User.__table__.constraints}
    assert "uq_users_cognito_sub" in user_constraints

    watchlist_constraints = {constraint.name for constraint in Watchlist.__table__.constraints}
    assert "uq_watchlists_user_ticker" in watchlist_constraints


def test_can_insert_minimal_stock_identifier_and_score() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.execute(
            Base.metadata.tables["stocks"].insert().values(
                ticker="005930",
                company_name="Samsung Electronics",
                market="KOSPI",
                is_active=True,
            )
        )
        session.add(
            CompanyIdentifier(
                ticker="005930",
                provider="OpenDART",
                identifier_type="corp_code",
                identifier_value="00126380",
                is_primary=True,
            )
        )
        session.add(
            CompanyIdentifier(
                ticker="005930",
                provider="OpenDART",
                identifier_type="stock_code",
                identifier_value="005930",
                is_primary=False,
            )
        )
        session.execute(
            Base.metadata.tables["recommendation_scores"].insert().values(
                ticker="005930",
                as_of_date=date(2026, 6, 9),
                score_version="score-rules-2026-06-01",
                total_score=78.5,
                evidence_level="strong",
                component_scores=[],
                evidence_count=2,
                missing_data=[],
                data_freshness={"as_of": "2026-06-09"},
                is_candidate_eligible=True,
            )
        )
        session.commit()

    with Session(engine) as session:
        identifiers = session.query(CompanyIdentifier).all()
        assert {identifier.identifier_type for identifier in identifiers} == {
            "corp_code",
            "stock_code",
        }
