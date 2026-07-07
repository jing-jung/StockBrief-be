from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


def uuid_pk() -> uuid.UUID:
    return uuid.uuid4()


class Stock(Base):
    __tablename__ = "stocks"

    ticker: Mapped[str] = mapped_column(String(6), primary_key=True, unique=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    company_name_en: Mapped[str | None] = mapped_column(Text)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    listing_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    identifiers: Mapped[list[CompanyIdentifier]] = relationship(back_populates="stock")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("cognito_sub", name="uq_users_cognito_sub"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    cognito_sub: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nickname: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    preferences: Mapped[UserPreference | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    watchlist_items: Mapped[list[Watchlist]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_preferences_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="preferences")


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("user_id", "ticker", name="uq_watchlists_user_ticker"),
        Index("ix_watchlists_user_saved_at", "user_id", "saved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(6), ForeignKey("stocks.ticker"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    sector: Mapped[str | None] = mapped_column(Text)
    memo: Mapped[str | None] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="watchlist_items")


class CompanyIdentifier(Base):
    __tablename__ = "company_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "identifier_type",
            "identifier_value",
            name="uq_company_identifiers_provider_type_value",
        ),
        UniqueConstraint(
            "ticker",
            "provider",
            "identifier_type",
            name="uq_company_identifiers_ticker_provider_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
    identifier_value: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    stock: Mapped[Stock] = relationship(back_populates="identifiers")


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_source_documents_source_external_id"),
        Index("ix_source_documents_ticker_source_type", "ticker", "source_type"),
        Index("ix_source_documents_source_type_id_published_at", "source_type", "id", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str | None] = mapped_column(ForeignKey("stocks.ticker"))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)


class FinancialStatement(Base):
    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("ticker", "fiscal_year", "fiscal_period", name="uq_financial_statements_ticker_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(10), nullable=False)
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    operating_income: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    net_income: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Disclosure(Base):
    __tablename__ = "disclosures"
    __table_args__ = (
        UniqueConstraint("provider", "receipt_no", name="uq_disclosures_provider_receipt_no"),
        Index("ix_disclosures_ticker_published_at", "ticker", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    receipt_no: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    disclosure_type: Mapped[str] = mapped_column(String(100), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_documents.id"))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("source_url", name="uq_news_items_source_url"),
        Index("ix_news_items_ticker_published_at", "ticker", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_documents.id"))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PriceMetric(Base):
    __tablename__ = "price_metrics"
    __table_args__ = (UniqueConstraint("ticker", "trade_date", name="uq_price_metrics_ticker_trade_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    trading_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    change_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    volatility_20d: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    momentum_20d: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (
        UniqueConstraint("evidence_id", name="uq_evidence_chunks_evidence_id"),
        Index("ix_evidence_chunks_ticker_evidence_type", "ticker", "evidence_type"),
        Index("ix_evidence_chunks_ticker_published_at", "ticker", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    evidence_id: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(100), nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)


class RecommendationScoreRule(Base):
    __tablename__ = "recommendation_score_rules"
    __table_args__ = (
        UniqueConstraint("rule_version", "component", name="uq_recommendation_score_rules_version_component"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    formula: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RecommendationScore(Base):
    __tablename__ = "recommendation_scores"
    __table_args__ = (
        UniqueConstraint("ticker", "as_of_date", "score_version", name="uq_recommendation_scores_ticker_date_version"),
        Index("ix_recommendation_scores_candidate_rank", "as_of_date", "is_candidate_eligible", "total_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    score_version: Mapped[str] = mapped_column(String(100), nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    evidence_level: Mapped[str] = mapped_column(String(20), nullable=False)
    component_scores: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_data: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    data_freshness: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_candidate_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RecommendationReason(Base):
    __tablename__ = "recommendation_reasons"
    __table_args__ = (UniqueConstraint("reason_id", name="uq_recommendation_reasons_reason_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    reason_id: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_score_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendation_scores.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_document_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RiskSignal(Base):
    __tablename__ = "risk_signals"
    __table_args__ = (
        Index("ix_risk_signals_ticker_as_of_date", "ticker", "as_of_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    ticker: Mapped[str] = mapped_column(ForeignKey("stocks.ticker"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    risk_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    penalty_points: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ApiCacheEntry(Base):
    __tablename__ = "api_cache_entries"
    __table_args__ = (
        UniqueConstraint("provider", "cache_key", name="uq_api_cache_entries_provider_cache_key"),
        Index("ix_api_cache_entries_provider_expires_at", "provider", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    cache_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExternalApiCallLog(Base):
    __tablename__ = "external_api_call_logs"
    __table_args__ = (Index("ix_external_api_call_logs_provider_called_at", "provider", "called_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    request_params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_chat_sessions_session_id"),
        Index("ix_chat_sessions_user_updated_at", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(ForeignKey("stocks.ticker"))
    candidate_as_of: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_chat_messages_message_id"),
        Index("ix_chat_messages_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("chat_sessions.session_id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(ForeignKey("stocks.ticker"))
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    safety_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IngestionRun(Base):
    """Tracks each ingestion job run for idempotency and replay detection.

    Every ingestion job must write a row here. Before re-running, check
    `status` and `input_hash` to decide whether to skip or mark as replayed.

    Provider upsert keys (used by ingestion jobs, not enforced here):
    - OpenDART disclosure: provider + receipt_no
    - NAVER news: provider + source_url_hash
    - KRX price: ticker + trade_date + source
    - financial statement: ticker + fiscal_year + fiscal_period + source_document_id
    - source document: source_type + source_name + external_id, fallback content_hash
    - score: ticker + as_of_date + score_version
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_ingestion_runs_run_id"),
        Index(
            "uq_ingestion_runs_active_input_hash",
            "input_hash",
            unique=True,
            postgresql_where=text("status IN ('started', 'succeeded')"),
            sqlite_where=text("status IN ('started', 'succeeded')"),
        ),
        Index("ix_ingestion_runs_job_type_provider_status", "job_type", "provider", "status"),
        Index("ix_ingestion_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid_pk)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    target_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
