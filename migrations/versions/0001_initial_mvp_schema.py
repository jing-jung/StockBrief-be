"""initial MVP schema

Revision ID: 0001_initial_mvp_schema
Revises: None
Create Date: 2026-06-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_mvp_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_col(nullable: bool = False) -> sa.Column:
    return sa.Column("id", sa.Uuid(as_uuid=True), nullable=nullable)


def created_at_col() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def upgrade() -> None:
    op.create_table(
        "stocks",
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("company_name_en", sa.Text(), nullable=True),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("listing_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        created_at_col(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("ticker"),
        sa.UniqueConstraint("ticker", name="uq_stocks_ticker"),
    )

    op.create_table(
        "company_identifiers",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("identifier_type", sa.String(length=50), nullable=False),
        sa.Column("identifier_value", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        created_at_col(),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "identifier_type", "identifier_value", name="uq_company_identifiers_provider_type_value"),
        sa.UniqueConstraint("ticker", "provider", "identifier_type", name="uq_company_identifiers_ticker_provider_type"),
    )

    op.create_table(
        "source_documents",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_name", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name", "external_id", name="uq_source_documents_source_external_id"),
    )
    op.create_index("ix_source_documents_ticker_source_type", "source_documents", ["ticker", "source_type"])

    op.create_table(
        "financial_statements",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=10), nullable=False),
        sa.Column("period_end_date", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Numeric(20, 2), nullable=True),
        sa.Column("operating_income", sa.Numeric(20, 2), nullable=True),
        sa.Column("net_income", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_assets", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_liabilities", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_equity", sa.Numeric(20, 2), nullable=True),
        sa.Column("source_document_id", sa.Uuid(as_uuid=True), nullable=True),
        created_at_col(),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "fiscal_year", "fiscal_period", name="uq_financial_statements_ticker_period"),
    )

    op.create_table(
        "disclosures",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("receipt_no", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("disclosure_type", sa.String(length=100), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        created_at_col(),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "receipt_no", name="uq_disclosures_provider_receipt_no"),
    )

    op.create_table(
        "news_items",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("sentiment_label", sa.String(length=20), nullable=True),
        sa.Column("source_document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        created_at_col(),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url", name="uq_news_items_source_url"),
    )

    op.create_table(
        "price_metrics",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close_price", sa.Numeric(20, 2), nullable=True),
        sa.Column("volume", sa.Numeric(20, 2), nullable=True),
        sa.Column("trading_value", sa.Numeric(20, 2), nullable=True),
        sa.Column("market_cap", sa.Numeric(20, 2), nullable=True),
        sa.Column("change_rate", sa.Numeric(10, 4), nullable=True),
        sa.Column("volatility_20d", sa.Numeric(10, 6), nullable=True),
        sa.Column("momentum_20d", sa.Numeric(10, 6), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        created_at_col(),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "trade_date", name="uq_price_metrics_ticker_trade_date"),
    )

    op.create_table(
        "evidence_chunks",
        uuid_col(),
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("source_document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evidence_type", sa.String(length=100), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", name="uq_evidence_chunks_evidence_id"),
    )
    op.create_index("ix_evidence_chunks_ticker_evidence_type", "evidence_chunks", ["ticker", "evidence_type"])

    op.create_table(
        "recommendation_score_rules",
        uuid_col(),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column("component", sa.String(length=100), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("formula", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        created_at_col(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_version", "component", name="uq_recommendation_score_rules_version_component"),
    )

    op.create_table(
        "recommendation_scores",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("score_version", sa.String(length=100), nullable=False),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("evidence_level", sa.String(length=20), nullable=False),
        sa.Column("component_scores", sa.JSON(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("missing_data", sa.JSON(), nullable=False),
        sa.Column("data_freshness", sa.JSON(), nullable=False),
        sa.Column("is_candidate_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        created_at_col(),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "as_of_date", "score_version", name="uq_recommendation_scores_ticker_date_version"),
    )
    op.create_index(
        "ix_recommendation_scores_candidate_rank",
        "recommendation_scores",
        ["as_of_date", "is_candidate_eligible", "total_score"],
    )

    op.create_table(
        "recommendation_reasons",
        uuid_col(),
        sa.Column("reason_id", sa.Text(), nullable=False),
        sa.Column("recommendation_score_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("component", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("source_document_ids", sa.JSON(), nullable=False),
        created_at_col(),
        sa.ForeignKeyConstraint(["recommendation_score_id"], ["recommendation_scores.id"]),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reason_id", name="uq_recommendation_reasons_reason_id"),
    )

    op.create_table(
        "risk_signals",
        uuid_col(),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("risk_tag", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("penalty_points", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        created_at_col(),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_signals_ticker_as_of_date", "risk_signals", ["ticker", "as_of_date"])

    op.create_table(
        "api_cache_entries",
        uuid_col(),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        created_at_col(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "cache_key", name="uq_api_cache_entries_provider_cache_key"),
    )
    op.create_index("ix_api_cache_entries_provider_expires_at", "api_cache_entries", ["provider", "expires_at"])

    op.create_table(
        "external_api_call_logs",
        uuid_col(),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("request_params", sa.JSON(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_api_call_logs_provider_called_at", "external_api_call_logs", ["provider", "called_at"])

    op.create_table(
        "chat_sessions",
        uuid_col(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("ticker", sa.String(length=6), nullable=True),
        sa.Column("candidate_as_of", sa.Date(), nullable=True),
        created_at_col(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_chat_sessions_session_id"),
    )

    op.create_table(
        "chat_messages",
        uuid_col(),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ticker", sa.String(length=6), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("safety_flags", sa.JSON(), nullable=False),
        created_at_col(),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.session_id"]),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_chat_messages_message_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_index("ix_external_api_call_logs_provider_called_at", table_name="external_api_call_logs")
    op.drop_table("external_api_call_logs")
    op.drop_index("ix_api_cache_entries_provider_expires_at", table_name="api_cache_entries")
    op.drop_table("api_cache_entries")
    op.drop_index("ix_risk_signals_ticker_as_of_date", table_name="risk_signals")
    op.drop_table("risk_signals")
    op.drop_table("recommendation_reasons")
    op.drop_index("ix_recommendation_scores_candidate_rank", table_name="recommendation_scores")
    op.drop_table("recommendation_scores")
    op.drop_table("recommendation_score_rules")
    op.drop_index("ix_evidence_chunks_ticker_evidence_type", table_name="evidence_chunks")
    op.drop_table("evidence_chunks")
    op.drop_table("price_metrics")
    op.drop_table("news_items")
    op.drop_table("disclosures")
    op.drop_table("financial_statements")
    op.drop_index("ix_source_documents_ticker_source_type", table_name="source_documents")
    op.drop_table("source_documents")
    op.drop_table("company_identifiers")
    op.drop_table("stocks")

