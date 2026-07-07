"""Provider ingestion package.

This package replaces the former ``app/services/ingestion.py`` monolith.
Every name that was previously a module-level attribute is re-exported here
so external imports and monkeypatch targets such as
``app.services.ingestion.OpenDartClient`` or
``app.services.ingestion.reconcile_stale_ingestion_runs`` keep resolving.
"""

from __future__ import annotations

import logging

from app.db import get_session_factory
from app.services.external.aws_secrets import load_secret_json
from app.services.external.clients import (
    KRX_PROVIDER,
    NAVER_PROVIDER,
    OPENDART_PROVIDER,
    KrxClient,
    NaverNewsClient,
    OpenDartClient,
)
from app.services.ingestion.archiver import (
    NoopPayloadArchiver,
    PayloadArchiver,
    S3PayloadArchiver,
    _archiver_from_settings,
)
from app.services.ingestion.event_helpers import (
    _event_as_of_date,
    _event_bool,
    _event_market_filter,
    _event_markets,
    _event_providers,
    _event_source_dates,
    _event_tickers,
    _first_secret_value,
    _job_type,
    _normalize_provider,
    _provider_payload_version,
    _unique_providers,
    _unique_tickers,
    build_request_hash,
    build_run_id,
)
from app.services.ingestion.krx_technicals import (
    _decimal_to_float,
    _refresh_krx_technical_metrics,
    _sample_stddev,
    _upsert_krx_price_metric_from_item,
    persist_krx_stock_master,
    seed_krx_stock_universe_from_event,
)
from app.services.ingestion.orchestrator import (
    ProviderIngestionService,
    UnregisteredTickerError,
    handle_ingestion_event,
)
from app.services.ingestion.parsing import (
    _clean_provider_text,
    _combined_opendart_result,
    _compact_source_date,
    _decimal_from_provider,
    _ensure_aware_datetime,
    _financial_statement_values,
    _first_text,
    _isoformat,
    _iter_dicts,
    _normalize_account_name,
    _normalize_krx_price_item,
    _normalize_krx_stock_item,
    _normalize_provider_market,
    _opendart_disclosure_window,
    _opendart_financial_years,
    _parse_iso_date,
    _parse_rfc2822,
    _parse_yyyymmdd,
    _sha256,
    _string_or_none,
    _ticker_from_provider,
)
from app.services.ingestion.persistence import (
    upsert_evidence_chunk,
    upsert_source_document,
)
from app.services.ingestion.readiness import (
    PROVIDER_EGRESS_ENDPOINTS,
    PROVIDER_EGRESS_TIMEOUT_SECONDS,
    RAW_ARCHIVE_PROBE_PROVIDER,
    RAW_ARCHIVE_PROBE_TICKER,
    _check_provider_endpoint_egress,
    _krx_daily_endpoints_configured,
    _missing_successful_manual_smoke_runs,
    _provider_egress_selection,
    _provider_egress_targets,
    _scheduler_enable_gate_blockers,
    check_ingestion_readiness,
    check_ingestion_scheduler_enable_gate,
    check_opendart_corp_code_alignment,
    check_provider_egress,
    check_raw_archive_write,
    hydrate_external_api_settings,
    reconcile_opendart_evidence_tickers,
)
from app.services.ingestion.request import (
    MAX_KRX_STOCK_UNIVERSE_SOURCE_DATES,
    MAX_NAVER_NEWS_DISPLAY,
    MAX_OPENDART_PAGE_COUNT,
    MAX_TICKERS_PER_BATCH,
    SUPPORTED_PROVIDERS,
    ProviderIngestionRequest,
    TickerIngestionResult,
    _nonnegative_int,
    _positive_int,
    _request_limit_violations,
    _request_limits,
    _result_dict,
)
from app.services.ingestion.score_refresh import (
    SCORE_REFRESH_UNIVERSE_LIMITS,
    _aggregate_provider_status,
    _annotate_score_provider_freshness,
    _event_score_universe,
    _failed_ingestion_tickers,
    _provider_freshness_statuses,
    _score_refresh_batch_metadata,
    _score_refresh_limit,
    _score_refresh_tickers,
    _successful_ingestion_tickers,
    handle_refresh_score_snapshots_event,
    refresh_score_snapshots,
)
from app.services.ingestion.status import (
    _evidence_status_dict,
    _reconcile_limit,
    _run_status_counts,
    _run_status_dict,
    _stale_run_dict,
    _stale_run_max_age_minutes,
    _status_limit,
    get_ingestion_status,
    reconcile_stale_ingestion_runs,
    reconcile_stale_started_runs,
    summarize_ingestion_status,
)


logger = logging.getLogger(__name__)
