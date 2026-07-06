from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Protocol

import boto3
from botocore.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.orm import (
    Disclosure,
    EvidenceChunk,
    IngestionRun,
    NewsItem,
    PriceMetric,
    RecommendationScore,
    SourceDocument,
    Stock,
)
from app.services.external.aws_secrets import load_secret_json
from app.services.external.clients import (
    KRX_PROVIDER,
    NAVER_PROVIDER,
    OPENDART_PROVIDER,
    KrxClient,
    NaverNewsClient,
    OpenDartClient,
)
from app.services.external.transport import urllib_transport
from app.services.external.types import ExternalApiResult, ExternalRequest, ExternalTransport
from app.services.ingestion_idempotency import IngestionIdempotencyService
from app.services.recommendation.materializer import materialize_recommendation_scores


logger = logging.getLogger(__name__)
SUPPORTED_PROVIDERS = (OPENDART_PROVIDER, NAVER_PROVIDER, KRX_PROVIDER)
MAX_TICKERS_PER_BATCH = 20
MAX_OPENDART_PAGE_COUNT = 100
MAX_NAVER_NEWS_DISPLAY = 50
PROVIDER_EGRESS_ENDPOINTS = {
    OPENDART_PROVIDER: "https://opendart.fss.or.kr/api/list.json",
    NAVER_PROVIDER: "https://openapi.naver.com/v1/search/news.json",
}
PROVIDER_EGRESS_TIMEOUT_SECONDS = 3.0
RAW_ARCHIVE_PROBE_PROVIDER = "STOCKBRIEF_PROBE"
RAW_ARCHIVE_PROBE_TICKER = "healthcheck"


class PayloadArchiver(Protocol):
    def archive(
        self,
        *,
        run_id: str,
        provider: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> str | None:
        ...


class NoopPayloadArchiver:
    def archive(
        self,
        *,
        run_id: str,
        provider: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> str | None:
        return None


class S3PayloadArchiver:
    def __init__(self, *, bucket: str, client: Any | None = None) -> None:
        self.bucket = bucket
        self.client = client or boto3.client(
            "s3",
            config=Config(
                connect_timeout=5,
                read_timeout=5,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def archive(
        self,
        *,
        run_id: str,
        provider: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> str | None:
        key = f"raw/provider={provider}/ticker={ticker}/run_id={run_id}.json"
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return f"s3://{self.bucket}/{key}"


@dataclass(frozen=True)
class ProviderIngestionRequest:
    provider: str
    tickers: list[str]
    source_date: str
    run_id: str | None = None
    page_count: int = 10
    news_display: int = 10

    @classmethod
    def from_event(cls, event: dict[str, object]) -> ProviderIngestionRequest:
        provider = _normalize_provider(str(event.get("provider") or "").strip())
        tickers_value = event.get("tickers")
        if isinstance(tickers_value, str):
            tickers = [item.strip() for item in tickers_value.split(",") if item.strip()]
        elif isinstance(tickers_value, list):
            tickers = [str(item).strip() for item in tickers_value if str(item).strip()]
        else:
            tickers = []

        source_date = str(event.get("source_date") or datetime.now(timezone.utc).date().isoformat())
        return cls(
            provider=provider,
            tickers=tickers,
            source_date=source_date,
            run_id=_string_or_none(event.get("run_id")),
            page_count=_positive_int(event.get("page_count"), default=10),
            news_display=_positive_int(event.get("news_display"), default=10),
        )


@dataclass(frozen=True)
class TickerIngestionResult:
    ticker: str
    run_id: str
    status: str
    result_counts: dict[str, int]
    raw_archive_uri: str | None = None
    error_summary: dict[str, Any] | None = None


class ProviderIngestionService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        archiver: PayloadArchiver | None = None,
    ) -> None:
        self.session = session
        self.settings = hydrate_external_api_settings(settings or get_settings())
        self.idempotency = IngestionIdempotencyService(session)
        self.archiver = archiver or _archiver_from_settings(self.settings)

    def run_provider_batch(self, request: ProviderIngestionRequest) -> dict[str, Any]:
        if request.provider not in SUPPORTED_PROVIDERS:
            return {
                "ok": False,
                "error": "unsupported_provider",
                "supported_providers": list(SUPPORTED_PROVIDERS),
            }
        tickers = _unique_tickers(request.tickers)
        if not tickers:
            return {"ok": False, "error": "tickers_required"}
        limit_violations = _request_limit_violations(request)
        if limit_violations:
            return {
                "ok": False,
                "error": "request_limit_exceeded",
                "violations": limit_violations,
                "limits": _request_limits(),
            }

        results = [self._run_ticker(request=request, ticker=ticker) for ticker in tickers]
        failed = [item for item in results if item.status in {"failed", "partial_failed"}]
        return {
            "ok": not failed,
            "provider": request.provider,
            "source_date": request.source_date,
            "results": [_result_dict(item) for item in results],
        }

    def _run_ticker(self, *, request: ProviderIngestionRequest, ticker: str) -> TickerIngestionResult:
        run_id = build_run_id(
            provider=request.provider,
            source_date=request.source_date,
            ticker=ticker,
        )
        if request.run_id:
            run_id = f"{request.run_id}-{ticker}"
        input_hash = build_request_hash(
            provider=request.provider,
            ticker=ticker,
            source_date=request.source_date,
            request_params={
                "page_count": request.page_count,
                "news_display": request.news_display,
            },
        )

        try:
            run = self.idempotency.start_or_restart_run(
                run_id=run_id,
                job_type=_job_type(request.provider),
                provider=request.provider,
                target_scope={
                    "ticker": ticker,
                    "source_date": request.source_date,
                },
                input_hash=input_hash,
            )
        except ValueError as exc:
            return TickerIngestionResult(
                ticker=ticker,
                run_id=run_id,
                status="failed",
                result_counts={},
                error_summary={"code": exc.__class__.__name__, "message": str(exc)},
            )
        except Exception as exc:
            self.session.rollback()
            return TickerIngestionResult(
                ticker=ticker,
                run_id=run_id,
                status="failed",
                result_counts={},
                error_summary={"code": exc.__class__.__name__, "message": str(exc)},
            )

        if run.status == self.idempotency.SUCCEEDED_STATUS:
            return TickerIngestionResult(
                ticker=ticker,
                run_id=run_id,
                status="replayed",
                result_counts={"inserted": 0, "updated": 0, "skipped": 1},
            )

        try:
            external_result = self._fetch_provider_result(request=request, ticker=ticker)
            raw_archive_uri = self.archiver.archive(
                run_id=run_id,
                provider=request.provider,
                ticker=ticker,
                payload=external_result.payload,
            )
            result_counts = self._persist_result(
                ticker=ticker,
                provider=request.provider,
                result=external_result,
                raw_archive_uri=raw_archive_uri,
            )
            if external_result.data_status == "fallback":
                completed = self.idempotency.mark_partial_failed(
                    run=run,
                    result_counts=result_counts,
                    error_summary={
                        "code": "provider_fallback",
                        "missing_data": external_result.missing_data,
                    },
                )
            elif (
                request.provider == KRX_PROVIDER
                and result_counts["inserted"] + result_counts["updated"] == 0
            ):
                completed = self.idempotency.mark_partial_failed(
                    run=run,
                    result_counts=result_counts,
                    error_summary={
                        "code": "krx_price_rows_not_persisted",
                        "result_counts": result_counts,
                    },
                )
            else:
                completed = self.idempotency.mark_succeeded(
                    run=run,
                    result_counts=result_counts,
                )
            return TickerIngestionResult(
                ticker=ticker,
                run_id=run_id,
                status=completed.status,
                result_counts=result_counts,
                raw_archive_uri=raw_archive_uri,
                error_summary=completed.error_summary,
            )
        except Exception as exc:
            self.session.rollback()
            failed = self.idempotency.mark_failed_by_run_id(
                run_id=run_id,
                error_summary={"code": exc.__class__.__name__, "message": str(exc)},
            )
            return TickerIngestionResult(
                ticker=ticker,
                run_id=run_id,
                status=failed.status,
                result_counts={},
                error_summary=failed.error_summary,
            )

    def _fetch_provider_result(
        self,
        *,
        request: ProviderIngestionRequest,
        ticker: str,
    ) -> ExternalApiResult:
        stock = self.session.get(Stock, ticker)
        if request.provider == OPENDART_PROVIDER:
            return OpenDartClient(settings=self.settings, session=self.session).list_disclosures(
                ticker=ticker,
                page_count=request.page_count,
            )
        if request.provider == KRX_PROVIDER:
            return KrxClient(settings=self.settings, session=self.session).daily_trading(
                ticker=ticker,
                base_date=_compact_source_date(request.source_date),
                market=stock.market if stock else "KOSPI",
            )
        company_name = stock.company_name if stock else ticker
        return NaverNewsClient(settings=self.settings, session=self.session).search_news(
            ticker=ticker,
            company_name=company_name,
            display=request.news_display,
        )

    def _persist_result(
        self,
        *,
        ticker: str,
        provider: str,
        result: ExternalApiResult,
        raw_archive_uri: str | None,
    ) -> dict[str, int]:
        if result.data_status == "fallback":
            return {"inserted": 0, "updated": 0, "skipped": 1}
        if provider == OPENDART_PROVIDER:
            return self._persist_disclosures(
                ticker=ticker,
                result=result,
                raw_archive_uri=raw_archive_uri,
            )
        if provider == KRX_PROVIDER:
            return self._persist_krx_prices(
                ticker=ticker,
                result=result,
                raw_archive_uri=raw_archive_uri,
            )
        return self._persist_news(
            ticker=ticker,
            result=result,
            raw_archive_uri=raw_archive_uri,
        )

    def _persist_disclosures(
        self,
        *,
        ticker: str,
        result: ExternalApiResult,
        raw_archive_uri: str | None,
    ) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "skipped": 0}
        for item in _iter_dicts(result.payload.get("list")):
            receipt_no = str(item.get("rcept_no") or "").strip()
            if not receipt_no:
                counts["skipped"] += 1
                continue
            title = str(item.get("report_nm") or receipt_no).strip()
            source_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
            source_document = upsert_source_document(
                self.session,
                ticker=ticker,
                source_type="disclosure",
                source_name=OPENDART_PROVIDER,
                source_url=source_url,
                external_id=receipt_no,
                title=title,
                published_at=_parse_yyyymmdd(item.get("rcept_dt")),
                raw_content=json.dumps(item, ensure_ascii=False, sort_keys=True),
                metadata={
                    "provider": OPENDART_PROVIDER,
                    "raw_archive_uri": raw_archive_uri,
                },
            )
            upsert_evidence_chunk(
                self.session,
                source_document=source_document,
                ticker=ticker,
                evidence_id=f"ev_opendart_{ticker}_{receipt_no}",
                evidence_type="disclosure",
                chunk_text=title,
                source_url=source_url,
                published_at=_parse_yyyymmdd(item.get("rcept_dt")),
                metadata={
                    "provider": OPENDART_PROVIDER,
                    "receipt_no": receipt_no,
                    "raw_archive_uri": raw_archive_uri,
                },
            )
            existing = self.session.scalars(
                select(Disclosure).where(
                    Disclosure.provider == OPENDART_PROVIDER,
                    Disclosure.receipt_no == receipt_no,
                )
            ).first()
            payload = dict(item)
            payload["raw_archive_uri"] = raw_archive_uri
            if existing:
                existing.ticker = ticker
                existing.title = title
                existing.disclosure_type = str(item.get("rm") or item.get("report_nm") or "unknown")
                existing.published_at = _parse_yyyymmdd(item.get("rcept_dt")) or datetime.now(timezone.utc)
                existing.source_url = source_url
                existing.source_document_id = source_document.id
                existing.raw_payload = payload
                counts["updated"] += 1
            else:
                self.session.add(
                    Disclosure(
                        ticker=ticker,
                        provider=OPENDART_PROVIDER,
                        receipt_no=receipt_no,
                        title=title,
                        disclosure_type=str(item.get("rm") or item.get("report_nm") or "unknown"),
                        published_at=_parse_yyyymmdd(item.get("rcept_dt")) or datetime.now(timezone.utc),
                        source_url=source_url,
                        source_document_id=source_document.id,
                        raw_payload=payload,
                    )
                )
                counts["inserted"] += 1
        self.session.flush()
        return counts

    def _persist_krx_prices(
        self,
        *,
        ticker: str,
        result: ExternalApiResult,
        raw_archive_uri: str | None,
    ) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "skipped": 0}
        base_date = str(result.payload.get("base_date") or "").strip()
        for item in _iter_dicts(result.payload.get("OutBlock_1")):
            normalized = _normalize_krx_price_item(item, base_date=base_date)
            if normalized is None or normalized["ticker"] != ticker:
                counts["skipped"] += 1
                continue
            stock = self.session.get(Stock, ticker)
            trade_date = normalized["trade_date"]
            if stock is None or trade_date is None:
                counts["skipped"] += 1
                continue

            raw_content = json.dumps(item, ensure_ascii=False, sort_keys=True)
            upsert_source_document(
                self.session,
                ticker=ticker,
                source_type="price",
                source_name=KRX_PROVIDER,
                source_url=None,
                external_id=f"KRX:price:{ticker}:{trade_date.isoformat()}",
                title=f"{stock.company_name} KRX daily price {trade_date.isoformat()}",
                published_at=datetime.combine(trade_date, datetime.min.time(), tzinfo=timezone.utc),
                raw_content=raw_content,
                metadata={
                    "provider": KRX_PROVIDER,
                    "raw_archive_uri": raw_archive_uri,
                },
            )
            existing = self.session.scalars(
                select(PriceMetric).where(
                    PriceMetric.ticker == ticker,
                    PriceMetric.trade_date == trade_date,
                )
            ).first()
            if existing:
                existing.close_price = normalized["close_price"]
                existing.volume = normalized["volume"]
                existing.trading_value = normalized["trading_value"]
                existing.market_cap = normalized["market_cap"]
                existing.change_rate = normalized["change_rate"]
                existing.source = KRX_PROVIDER
                counts["updated"] += 1
            else:
                self.session.add(
                    PriceMetric(
                        ticker=ticker,
                        trade_date=trade_date,
                        close_price=normalized["close_price"],
                        volume=normalized["volume"],
                        trading_value=normalized["trading_value"],
                        market_cap=normalized["market_cap"],
                        change_rate=normalized["change_rate"],
                        source=KRX_PROVIDER,
                    )
                )
                counts["inserted"] += 1
        self.session.flush()
        return counts

    def _persist_news(
        self,
        *,
        ticker: str,
        result: ExternalApiResult,
        raw_archive_uri: str | None,
    ) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "skipped": 0}
        for item in _iter_dicts(result.payload.get("items")):
            source_url = str(item.get("originallink") or item.get("link") or "").strip()
            if not source_url:
                counts["skipped"] += 1
                continue
            title = _clean_provider_text(item.get("title")) or source_url
            published_at = _parse_rfc2822(item.get("pubDate"))
            source_document = upsert_source_document(
                self.session,
                ticker=ticker,
                source_type="news",
                source_name=NAVER_PROVIDER,
                source_url=source_url,
                external_id=_sha256(source_url),
                title=title,
                published_at=published_at,
                raw_content=json.dumps(item, ensure_ascii=False, sort_keys=True),
                metadata={
                    "provider": NAVER_PROVIDER,
                    "raw_archive_uri": raw_archive_uri,
                },
            )
            upsert_evidence_chunk(
                self.session,
                source_document=source_document,
                ticker=ticker,
                evidence_id=f"ev_naver_news_{ticker}_{_sha256(source_url)}",
                evidence_type="news",
                chunk_text=_clean_provider_text(item.get("description")) or title,
                source_url=source_url,
                published_at=published_at,
                metadata={
                    "provider": NAVER_PROVIDER,
                    "raw_archive_uri": raw_archive_uri,
                },
            )
            existing = self.session.scalars(
                select(NewsItem).where(NewsItem.source_url == source_url)
            ).first()
            payload = dict(item)
            payload["raw_archive_uri"] = raw_archive_uri
            if existing:
                existing.ticker = ticker
                existing.provider = NAVER_PROVIDER
                existing.title = title
                existing.summary = _string_or_none(item.get("description"))
                existing.publisher = _string_or_none(item.get("publisher"))
                existing.published_at = published_at
                existing.source_document_id = source_document.id
                existing.raw_payload = payload
                counts["updated"] += 1
            else:
                self.session.add(
                    NewsItem(
                        ticker=ticker,
                        provider=NAVER_PROVIDER,
                        title=title,
                        summary=_string_or_none(item.get("description")),
                        publisher=_string_or_none(item.get("publisher")),
                        published_at=published_at,
                        source_url=source_url,
                        source_document_id=source_document.id,
                        raw_payload=payload,
                    )
                )
                counts["inserted"] += 1
        self.session.flush()
        return counts


def handle_ingestion_event(event: dict[str, object]) -> dict[str, Any]:
    request = ProviderIngestionRequest.from_event(event)
    with get_session_factory()() as session:
        result = ProviderIngestionService(session).run_provider_batch(request)
    if event.get("raise_on_failure") is True and result.get("ok") is False:
        raise RuntimeError(f"ingestion_batch_failed:{result.get('provider')}")
    return result


def handle_refresh_score_snapshots_event(event: dict[str, object]) -> dict[str, Any]:
    with get_session_factory()() as session:
        result = refresh_score_snapshots(session, event)
        session.commit()
    if event.get("raise_on_failure") is True and result.get("ok") is False:
        raise RuntimeError("refresh_score_snapshots_failed")
    return result


def seed_krx_stock_universe_from_event(event: dict[str, object]) -> dict[str, Any]:
    source_date = _compact_source_date(
        event.get("source_date") or datetime.now(timezone.utc).date().isoformat()
    )
    markets = _event_markets(event)
    with get_session_factory()() as session:
        settings = hydrate_external_api_settings(get_settings())
        client = KrxClient(settings=settings, session=session)
        results = []
        totals = {"inserted": 0, "updated": 0, "skipped": 0}
        for market in markets:
            result = client.daily_trading(
                ticker="",
                base_date=source_date,
                market=market,
                bypass_cache=True,
            )
            counts = persist_krx_stock_master(
                session,
                market=market,
                payload=result.payload,
            )
            for key in totals:
                totals[key] += counts[key]
            results.append(
                {
                    "market": market,
                    "data_status": result.data_status,
                    "status_code": result.status_code,
                    "counts": counts,
                    "missing_data": result.missing_data,
                }
            )
        session.commit()
    return {
        "ok": any(item["counts"]["inserted"] + item["counts"]["updated"] > 0 for item in results),
        "operation": "seed_krx_stock_universe",
        "source_date": source_date,
        "markets": markets,
        "totals": totals,
        "results": results,
    }


def persist_krx_stock_master(
    session: Session,
    *,
    market: str,
    payload: dict[str, Any],
) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "skipped": 0}
    market_key = _normalize_provider_market(market)
    for item in _iter_dicts(payload.get("OutBlock_1")):
        normalized = _normalize_krx_stock_item(item, market=market_key)
        if normalized is None:
            counts["skipped"] += 1
            continue
        stock = session.get(Stock, normalized["ticker"])
        if stock is None:
            session.add(Stock(**normalized))
            counts["inserted"] += 1
        else:
            for key, value in normalized.items():
                setattr(stock, key, value)
            counts["updated"] += 1
        _upsert_krx_price_metric_from_item(session, item=item, payload=payload)
    session.flush()
    return counts


def _upsert_krx_price_metric_from_item(
    session: Session,
    *,
    item: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    base_date = str(payload.get("base_date") or payload.get("basDd") or "").strip()
    normalized = _normalize_krx_price_item(item, base_date=base_date)
    if normalized is None:
        return
    if normalized["trade_date"] is None:
        return
    existing = session.scalars(
        select(PriceMetric).where(
            PriceMetric.ticker == normalized["ticker"],
            PriceMetric.trade_date == normalized["trade_date"],
        )
    ).first()
    if existing is None:
        session.add(
            PriceMetric(
                ticker=normalized["ticker"],
                trade_date=normalized["trade_date"],
                close_price=normalized["close_price"],
                volume=normalized["volume"],
                trading_value=normalized["trading_value"],
                market_cap=normalized["market_cap"],
                change_rate=normalized["change_rate"],
                source=KRX_PROVIDER,
            )
        )
        return
    existing.close_price = normalized["close_price"]
    existing.volume = normalized["volume"]
    existing.trading_value = normalized["trading_value"]
    existing.market_cap = normalized["market_cap"]
    existing.change_rate = normalized["change_rate"]
    existing.source = KRX_PROVIDER


def refresh_score_snapshots(session: Session, event: dict[str, object]) -> dict[str, Any]:
    as_of_date = _event_as_of_date(event)
    requested_tickers = _event_tickers(event)
    ingestion_result: dict[str, Any] | None = None
    provider_statuses: dict[str, dict[str, Any]] = {}
    batch_tickers: list[str] | None = None

    if event.get("provider"):
        request = ProviderIngestionRequest.from_event(event)
        ingestion_result = ProviderIngestionService(session).run_provider_batch(request)
        provider_statuses = _provider_freshness_statuses(ingestion_result)
        refresh_tickers = _successful_ingestion_tickers(ingestion_result)
        target_tickers = _unique_tickers(request.tickers)
    else:
        refresh_tickers = _unique_tickers(requested_tickers)
        if not refresh_tickers:
            batch_tickers = _score_refresh_tickers(session, event)
            if batch_tickers is not None:
                refresh_tickers = batch_tickers
        target_tickers = refresh_tickers
        provider_statuses = {
            "refresh_operation": {
                "status": "stale",
                "reason": "no_provider_ingestion",
                "as_of": as_of_date.isoformat(),
            }
        }

    materializer_tickers = refresh_tickers if batch_tickers is not None else (refresh_tickers or None)
    if ingestion_result is not None and not refresh_tickers:
        refresh_result: dict[str, int | str] = {
            "processed": 0,
            "created": 0,
            "updated": 0,
            "reasons": 0,
            "risk_signals": 0,
        }
    else:
        refresh_result = materialize_recommendation_scores(
            session,
            as_of_date=as_of_date,
            tickers=materializer_tickers,
        )

    annotated = _annotate_score_provider_freshness(
        session,
        as_of_date=as_of_date,
        tickers=refresh_tickers or target_tickers,
        score_version=_string_or_none(refresh_result.get("score_version")),
        provider_statuses=provider_statuses,
    )
    provider_status = _aggregate_provider_status(provider_statuses)
    return {
        "ok": provider_status in {"success", "stale"} and int(refresh_result["processed"]) > 0,
        "operation": "refresh_score_snapshots",
        "as_of_date": as_of_date.isoformat(),
        "ingestion": ingestion_result,
        "successful_tickers": refresh_tickers,
        "failed_tickers": _failed_ingestion_tickers(ingestion_result),
        "provider_status": provider_status,
        "provider_freshness": provider_statuses,
        "batch": _score_refresh_batch_metadata(event, batch_tickers),
        "refresh": {
            **refresh_result,
            "provider_freshness_annotated": annotated,
        },
    }


def get_ingestion_status(event: dict[str, object] | None = None) -> dict[str, Any]:
    request = event or {}
    with get_session_factory()() as session:
        return summarize_ingestion_status(
            session,
            tickers=_event_tickers(request),
            providers=_event_providers(request),
            limit=_status_limit(request.get("limit")),
        )


def reconcile_stale_ingestion_runs(event: dict[str, object] | None = None) -> dict[str, Any]:
    request = event or {}
    with get_session_factory()() as session:
        return reconcile_stale_started_runs(
            session,
            max_age_minutes=_stale_run_max_age_minutes(request.get("max_age_minutes")),
            tickers=_event_tickers(request),
            providers=_event_providers(request),
            limit=_reconcile_limit(request.get("limit")),
            dry_run=_event_bool(request.get("dry_run"), default=True),
        )


def check_ingestion_scheduler_enable_gate(event: dict[str, object] | None = None) -> dict[str, Any]:
    request = event or {}
    providers = _event_providers(request) or list(SUPPORTED_PROVIDERS)
    tickers = _event_tickers(request) or ["005930"]
    status_limit = _status_limit(request.get("limit"))
    stale_max_age_minutes = _stale_run_max_age_minutes(request.get("max_age_minutes"))

    readiness = check_ingestion_readiness(providers=providers)
    raw_archive = check_raw_archive_write()
    provider_egress = check_provider_egress({"providers": providers})
    status = get_ingestion_status(
        {
            "tickers": tickers,
            "providers": providers,
            "limit": status_limit,
        }
    )
    stale_runs = reconcile_stale_ingestion_runs(
        {
            "tickers": tickers,
            "providers": providers,
            "max_age_minutes": stale_max_age_minutes,
            "dry_run": True,
        }
    )

    checks = {
        "readiness": readiness,
        "raw_archive": raw_archive,
        "provider_egress": provider_egress,
        "status": status,
        "stale_runs": stale_runs,
    }
    blockers = _scheduler_enable_gate_blockers(
        checks,
        providers=providers,
        tickers=tickers,
    )

    return {
        "ok": not blockers,
        "scheduler_enable_ready": not blockers,
        "providers": providers,
        "tickers": tickers,
        "checks": checks,
        "blockers": blockers,
    }


def summarize_ingestion_status(
    session: Session,
    *,
    tickers: list[str] | None = None,
    providers: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    normalized_tickers = _unique_tickers(tickers or [])
    normalized_providers = _unique_providers(providers or [])
    run_statement = (
        select(IngestionRun)
        .order_by(IngestionRun.started_at.desc(), IngestionRun.run_id.desc())
        .limit(limit)
    )
    if normalized_tickers:
        run_statement = run_statement.where(
            IngestionRun.target_scope["ticker"].as_string().in_(normalized_tickers)
        )
    if normalized_providers:
        run_statement = run_statement.where(IngestionRun.provider.in_(normalized_providers))
    runs = session.scalars(run_statement).all()
    evidence_statement = (
        select(EvidenceChunk, SourceDocument)
        .join(SourceDocument, SourceDocument.id == EvidenceChunk.source_document_id)
        .order_by(EvidenceChunk.fetched_at.desc(), EvidenceChunk.evidence_id.desc())
        .limit(limit)
    )
    if normalized_tickers:
        evidence_statement = evidence_statement.where(EvidenceChunk.ticker.in_(normalized_tickers))
    if normalized_providers:
        evidence_statement = evidence_statement.where(
            SourceDocument.source_name.in_(normalized_providers)
        )
    latest_evidence = session.execute(evidence_statement).all()
    return {
        "ok": True,
        "summary": {
            "run_status_counts": _run_status_counts(runs),
            "recent_run_count": len(runs),
            "latest_evidence_count": len(latest_evidence),
            "ticker_filter": normalized_tickers,
            "provider_filter": normalized_providers,
        },
        "recent_runs": [_run_status_dict(run) for run in runs],
        "latest_evidence": [
            _evidence_status_dict(chunk=chunk, source=source)
            for chunk, source in latest_evidence
        ],
    }


def reconcile_stale_started_runs(
    session: Session,
    *,
    max_age_minutes: int = 60,
    tickers: list[str] | None = None,
    providers: list[str] | None = None,
    limit: int = 50,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = _ensure_aware_datetime(now or datetime.now(timezone.utc))
    cutoff = observed_at - timedelta(minutes=max_age_minutes)
    normalized_tickers = _unique_tickers(tickers or [])
    normalized_providers = _unique_providers(providers or [])
    statement = (
        select(IngestionRun)
        .where(
            IngestionRun.status == "started",
            IngestionRun.started_at <= cutoff,
        )
        .order_by(IngestionRun.started_at.asc(), IngestionRun.run_id.asc())
        .limit(limit)
    )
    if normalized_tickers:
        statement = statement.where(
            IngestionRun.target_scope["ticker"].as_string().in_(normalized_tickers)
        )
    if normalized_providers:
        statement = statement.where(IngestionRun.provider.in_(normalized_providers))
    stale_runs = session.scalars(statement).all()
    if not dry_run:
        for run in stale_runs:
            run.status = "failed"
            run.completed_at = observed_at
            run.error_summary = {
                "code": "stale_started_run_reconciled",
                "max_age_minutes": max_age_minutes,
                "reconciled_at": _isoformat(observed_at),
            }
        session.commit()
        for run in stale_runs:
            session.refresh(run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "max_age_minutes": max_age_minutes,
        "cutoff_started_before": _isoformat(cutoff),
        "ticker_filter": normalized_tickers,
        "provider_filter": normalized_providers,
        "stale_count": len(stale_runs),
        "updated_count": 0 if dry_run else len(stale_runs),
        "stale_runs": [
            _stale_run_dict(run=run, observed_at=observed_at)
            for run in stale_runs
        ],
    }


def check_ingestion_readiness(
    settings: Settings | None = None,
    *,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    base_settings = settings or get_settings()
    selected_providers, provider_selection_issues = _provider_egress_selection(
        {} if providers is None else {"providers": providers}
    )
    issues: list[dict[str, str]] = list(provider_selection_issues)
    secret_load_error: dict[str, str] | None = None
    hydrated_settings = base_settings

    if base_settings.external_api_secret_arn:
        try:
            hydrated_settings = hydrate_external_api_settings(base_settings)
        except Exception as exc:
            secret_load_error = {
                "code": exc.__class__.__name__,
                "message": "External API secret could not be loaded.",
            }
            issues.append(
                {
                    "code": "external_api_secret_load_failed",
                    "field": "EXTERNAL_API_SECRET_ARN",
                }
            )
    else:
        issues.append(
            {
                "code": "missing_external_api_secret_arn",
                "field": "EXTERNAL_API_SECRET_ARN",
            }
        )

    if not hydrated_settings.ingestion_raw_bucket:
        issues.append(
            {
                "code": "missing_ingestion_raw_bucket",
                "field": "INGESTION_RAW_BUCKET",
            }
        )
    provider_checks = {
        OPENDART_PROVIDER: {
            "api_key_configured": bool(hydrated_settings.opendart_api_key),
        },
        NAVER_PROVIDER: {
            "client_id_configured": bool(hydrated_settings.naver_client_id),
            "client_secret_configured": bool(hydrated_settings.naver_client_secret),
        },
        KRX_PROVIDER: {
            "api_key_configured": bool(hydrated_settings.krx_api_key),
            "kospi_daily_url_configured": bool(
                hydrated_settings.krx_daily_url or hydrated_settings.krx_kospi_daily_url
            ),
            "kosdaq_daily_url_configured": bool(hydrated_settings.krx_kosdaq_daily_url),
        },
    }

    if OPENDART_PROVIDER in selected_providers and not hydrated_settings.opendart_api_key:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "OPENDART_API_KEY",
            }
        )
    if NAVER_PROVIDER in selected_providers and not hydrated_settings.naver_client_id:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "NAVER_CLIENT_ID",
            }
        )
    if NAVER_PROVIDER in selected_providers and not hydrated_settings.naver_client_secret:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "NAVER_CLIENT_SECRET",
            }
        )
    if KRX_PROVIDER in selected_providers and not hydrated_settings.krx_api_key:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "KRX_API_KEY",
            }
        )
    if KRX_PROVIDER in selected_providers and not _krx_daily_endpoints_configured(hydrated_settings):
        issues.append(
            {
                "code": "missing_provider_endpoint",
                "field": "KRX_KOSPI_DAILY_URL/KRX_KOSDAQ_DAILY_URL",
            }
        )

    return {
        "ok": not issues,
        "checks": {
            "raw_archive": {
                "configured": bool(hydrated_settings.ingestion_raw_bucket),
            },
            "external_api_secret": {
                "configured": bool(base_settings.external_api_secret_arn),
                "loaded": bool(base_settings.external_api_secret_arn) and secret_load_error is None,
                "error": secret_load_error,
            },
            "providers": {
                provider: provider_checks[provider]
                for provider in selected_providers
                if provider in provider_checks
            },
            "network": {
                "outbound_internet_egress_verified": False,
                "note": "This check does not call external provider APIs.",
            },
        },
        "issues": issues,
    }


def check_raw_archive_write(
    settings: Settings | None = None,
    *,
    archiver: PayloadArchiver | None = None,
) -> dict[str, Any]:
    base_settings = settings or get_settings()
    if not base_settings.ingestion_raw_bucket:
        return {
            "ok": False,
            "checks": {"raw_archive": {"configured": False, "write_verified": False}},
            "issues": [{"code": "missing_ingestion_raw_bucket", "field": "INGESTION_RAW_BUCKET"}],
        }

    probe_created_at = datetime.now(timezone.utc)
    probe_run_id = f"raw-archive-probe-{probe_created_at.strftime('%Y%m%dT%H%M%SZ')}"
    probe_payload = {
        "probe": "stockbrief-ingestion-raw-archive",
        "created_at": probe_created_at.isoformat(),
    }
    archive_writer = archiver or S3PayloadArchiver(bucket=base_settings.ingestion_raw_bucket)

    try:
        raw_archive_uri = archive_writer.archive(
            run_id=probe_run_id,
            provider=RAW_ARCHIVE_PROBE_PROVIDER,
            ticker=RAW_ARCHIVE_PROBE_TICKER,
            payload=probe_payload,
        )
        if raw_archive_uri is None:
            raise RuntimeError("raw archive probe did not return a URI")
    except Exception as exc:
        return {
            "ok": False,
            "checks": {
                "raw_archive": {
                    "configured": True,
                    "bucket": base_settings.ingestion_raw_bucket,
                    "write_verified": False,
                    "error_code": exc.__class__.__name__,
                }
            },
            "issues": [{"code": "raw_archive_write_failed", "field": "INGESTION_RAW_BUCKET"}],
        }

    return {
        "ok": True,
        "checks": {
            "raw_archive": {
                "configured": True,
                "bucket": base_settings.ingestion_raw_bucket,
                "write_verified": True,
                "raw_archive_uri": raw_archive_uri,
            }
        },
        "issues": [],
    }


def check_provider_egress(
    event: dict[str, object] | None = None,
    *,
    transport: ExternalTransport | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    selected_providers, provider_issues = _provider_egress_selection(event or {})
    checks: dict[str, dict[str, Any]] = {}
    issues = list(provider_issues)
    transport_fn = transport or urllib_transport
    base_settings = settings or get_settings()

    for provider in selected_providers:
        targets = _provider_egress_targets(provider, base_settings)
        target_checks: dict[str, dict[str, Any]] = {}
        for target in targets:
            label = target["label"]
            endpoint = target["endpoint"]
            if not endpoint:
                target_checks[label] = {
                    "reachable": False,
                    "endpoint": None,
                    "status_code": None,
                    "error_code": "missing_provider_endpoint",
                    "note": "Provider endpoint is not configured.",
                }
                issues.append(
                    {
                        "code": "missing_provider_endpoint",
                        "provider": provider,
                        "field": target["field"],
                    }
                )
                continue
            check = _check_provider_endpoint_egress(
                provider=provider,
                endpoint=endpoint,
                transport=transport_fn,
            )
            target_checks[label] = check
            if not check["reachable"]:
                issue = {
                    "code": "provider_egress_unreachable",
                    "provider": provider,
                    "endpoint": endpoint,
                }
                if provider == KRX_PROVIDER:
                    issue["market"] = label
                issues.append(issue)
        if provider == KRX_PROVIDER:
            checks[provider] = {
                "reachable": all(check["reachable"] for check in target_checks.values()),
                "markets": target_checks,
            }
        else:
            checks[provider] = next(iter(target_checks.values()))

    return {
        "ok": not issues,
        "checks": {
            "providers": checks,
        },
        "issues": issues,
    }


def _provider_egress_selection(event: dict[str, object]) -> tuple[list[str], list[dict[str, str]]]:
    raw_providers = event.get("providers") or event.get("provider")
    if raw_providers is None:
        return list(SUPPORTED_PROVIDERS), []
    if isinstance(raw_providers, str):
        requested = [raw_providers]
    elif isinstance(raw_providers, list):
        requested = [str(provider) for provider in raw_providers]
    else:
        return [], [{"code": "invalid_provider_selection", "field": "providers"}]

    selected: list[str] = []
    issues: list[dict[str, str]] = []
    for provider in requested:
        if provider not in SUPPORTED_PROVIDERS:
            issues.append(
                {
                    "code": "unsupported_provider",
                    "provider": provider,
                }
            )
            continue
        if provider not in selected:
            selected.append(provider)
    return selected, issues


def _provider_egress_targets(provider: str, settings: Settings) -> list[dict[str, str]]:
    if provider == KRX_PROVIDER:
        return [
            {
                "label": "KOSPI",
                "endpoint": settings.krx_daily_url or settings.krx_kospi_daily_url,
                "field": "KRX_DAILY_URL/KRX_KOSPI_DAILY_URL",
            },
            {
                "label": "KOSDAQ",
                "endpoint": settings.krx_kosdaq_daily_url,
                "field": "KRX_KOSDAQ_DAILY_URL",
            },
        ]
    return [
        {
            "label": provider,
            "endpoint": PROVIDER_EGRESS_ENDPOINTS[provider],
            "field": "endpoint",
        }
    ]


def _scheduler_enable_gate_blockers(
    checks: dict[str, dict[str, Any]],
    *,
    providers: list[str],
    tickers: list[str],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for check_name in ("readiness", "raw_archive", "provider_egress", "status", "stale_runs"):
        result = checks[check_name]
        if result.get("ok") is not True:
            blockers.append(
                {
                    "code": f"{check_name}_not_ready",
                    "check": check_name,
                    "issues": result.get("issues", []),
                }
            )

    status = checks["status"]
    if status.get("ok") is True:
        missing_smoke_runs = _missing_successful_manual_smoke_runs(
            status.get("recent_runs"),
            providers=providers,
            tickers=tickers,
        )
        if missing_smoke_runs:
            blockers.append(
                {
                    "code": "manual_ingestion_smoke_missing",
                    "check": "status",
                    "missing_runs": missing_smoke_runs,
                }
            )

    stale_runs = checks["stale_runs"]
    if stale_runs.get("ok") is True and stale_runs.get("stale_count", 0) > 0:
        blockers.append(
            {
                "code": "stale_ingestion_runs_present",
                "check": "stale_runs",
                "stale_count": stale_runs.get("stale_count", 0),
            }
        )
    return blockers


def _missing_successful_manual_smoke_runs(
    recent_runs: object,
    *,
    providers: list[str],
    tickers: list[str],
) -> list[dict[str, str]]:
    expected = [
        {"provider": provider, "ticker": ticker}
        for provider in providers
        for ticker in tickers
    ]
    if not isinstance(recent_runs, list):
        return expected

    succeeded = {
        (str(run.get("provider")), str(run.get("ticker")))
        for run in recent_runs
        if isinstance(run, dict)
        if run.get("status") == "succeeded"
    }
    return [run for run in expected if (run["provider"], run["ticker"]) not in succeeded]


def _check_provider_endpoint_egress(
    *,
    provider: str,
    endpoint: str,
    transport: ExternalTransport,
) -> dict[str, Any]:
    request = ExternalRequest(
        method="GET",
        url=endpoint,
        params={},
        timeout_seconds=PROVIDER_EGRESS_TIMEOUT_SECONDS,
    )
    try:
        response = transport(request)
        return {
            "reachable": True,
            "endpoint": endpoint,
            "status_code": response.status_code,
            "note": "Provider endpoint returned an HTTP response.",
        }
    except json.JSONDecodeError:
        return {
            "reachable": True,
            "endpoint": endpoint,
            "status_code": None,
            "error_code": "JSONDecodeError",
            "note": "Provider endpoint returned a non-JSON HTTP response.",
        }
    except Exception as exc:
        status_code = getattr(exc, "code", None)
        if isinstance(status_code, int):
            return {
                "reachable": True,
                "endpoint": endpoint,
                "status_code": status_code,
                "note": "Provider endpoint returned an HTTP error response.",
            }
        return {
            "reachable": False,
            "endpoint": endpoint,
            "status_code": None,
            "error_code": exc.__class__.__name__,
            "note": "Provider endpoint could not be reached from this runtime.",
        }


def hydrate_external_api_settings(settings: Settings) -> Settings:
    if (
        settings.opendart_api_key
        and settings.naver_client_id
        and settings.naver_client_secret
        and settings.krx_api_key
        and _krx_daily_endpoints_configured(settings)
    ):
        return settings
    if not settings.external_api_secret_arn:
        return settings
    secret = load_secret_json(settings.external_api_secret_arn)
    return settings.model_copy(
        update={
            "opendart_api_key": (
                settings.opendart_api_key
                or _first_secret_value(secret, "OPENDART_API_KEY", "opendart_api_key")
            ),
            "naver_client_id": (
                settings.naver_client_id
                or _first_secret_value(secret, "NAVER_CLIENT_ID", "naver_client_id")
            ),
            "naver_client_secret": (
                settings.naver_client_secret
                or _first_secret_value(secret, "NAVER_CLIENT_SECRET", "naver_client_secret")
            ),
            "krx_api_key": (
                settings.krx_api_key
                or _first_secret_value(secret, "KRX_API_KEY", "krx_api_key")
            ),
            "krx_daily_url": (
                settings.krx_daily_url
                or _first_secret_value(secret, "KRX_DAILY_URL", "krx_daily_url")
            ),
            "krx_kospi_daily_url": (
                _first_secret_value(secret, "KRX_KOSPI_DAILY_URL", "krx_kospi_daily_url")
                or settings.krx_kospi_daily_url
            ),
            "krx_kosdaq_daily_url": (
                _first_secret_value(secret, "KRX_KOSDAQ_DAILY_URL", "krx_kosdaq_daily_url")
                or settings.krx_kosdaq_daily_url
            ),
            "krx_api_key_header": (
                _first_secret_value(secret, "KRX_API_KEY_HEADER", "krx_api_key_header")
                or settings.krx_api_key_header
            ),
        }
    )


def _krx_daily_endpoints_configured(settings: Settings) -> bool:
    return bool(
        (settings.krx_daily_url or settings.krx_kospi_daily_url)
        and settings.krx_kosdaq_daily_url
    )


def build_run_id(*, provider: str, source_date: str, ticker: str) -> str:
    normalized_provider = provider.lower().replace("_", "-")
    return f"{normalized_provider}-{source_date}-{ticker}"


def build_request_hash(
    *,
    provider: str,
    ticker: str,
    source_date: str,
    request_params: dict[str, Any],
) -> str:
    return IngestionIdempotencyService.compute_input_hash(
        {
            "provider": provider,
            "ticker": ticker,
            "source_date": source_date,
            "request_hash": IngestionIdempotencyService.compute_input_hash(request_params),
        }
    )


def upsert_source_document(
    session: Session,
    *,
    ticker: str,
    source_type: str,
    source_name: str,
    source_url: str | None,
    external_id: str | None,
    title: str,
    published_at: datetime | None,
    raw_content: str,
    metadata: dict[str, Any],
) -> SourceDocument:
    content_hash = _sha256(raw_content)
    existing = None
    if external_id:
        existing = session.scalars(
            select(SourceDocument).where(
                SourceDocument.source_name == source_name,
                SourceDocument.external_id == external_id,
            )
        ).first()
    if existing is None:
        existing = session.scalars(
            select(SourceDocument).where(SourceDocument.content_hash == content_hash)
        ).first()

    if existing:
        existing.ticker = ticker
        existing.source_type = source_type
        existing.source_url = source_url
        existing.title = title
        existing.published_at = published_at
        existing.fetched_at = datetime.now(timezone.utc)
        existing.raw_content = raw_content
        existing.metadata_ = metadata
        return existing

    source_document = SourceDocument(
        ticker=ticker,
        source_type=source_type,
        source_name=source_name,
        source_url=source_url,
        external_id=external_id,
        title=title,
        published_at=published_at,
        fetched_at=datetime.now(timezone.utc),
        content_hash=content_hash,
        raw_content=raw_content,
        metadata_=metadata,
    )
    session.add(source_document)
    session.flush()
    return source_document


def upsert_evidence_chunk(
    session: Session,
    *,
    source_document: SourceDocument,
    ticker: str,
    evidence_id: str,
    evidence_type: str,
    chunk_text: str,
    source_url: str | None,
    published_at: datetime | None,
    metadata: dict[str, Any],
) -> EvidenceChunk:
    def apply_values(target: EvidenceChunk) -> EvidenceChunk:
        target.ticker = ticker
        target.source_document_id = source_document.id
        target.evidence_type = evidence_type
        target.chunk_text = cleaned_text
        target.source_url = source_url
        target.published_at = published_at
        target.fetched_at = fetched_at
        target.metadata_ = metadata
        return target

    existing = session.scalars(
        select(EvidenceChunk).where(EvidenceChunk.evidence_id == evidence_id)
    ).first()
    fetched_at = datetime.now(timezone.utc)
    cleaned_text = _clean_provider_text(chunk_text) or source_document.title
    if existing:
        return apply_values(existing)

    chunk = EvidenceChunk(
        evidence_id=evidence_id,
        ticker=ticker,
        source_document_id=source_document.id,
        evidence_type=evidence_type,
        chunk_text=cleaned_text,
        source_url=source_url,
        published_at=published_at,
        fetched_at=fetched_at,
        confidence=Decimal("0.9000"),
        metadata_=metadata,
    )
    try:
        with session.begin_nested():
            session.add(chunk)
            session.flush()
        return chunk
    except IntegrityError:
        logger.warning(
            "evidence_chunk_upsert_conflict_recovered evidence_id=%s ticker=%s source_document_id=%s",
            evidence_id,
            ticker,
            source_document.id,
        )
        if chunk in session:
            session.expunge(chunk)
        with session.no_autoflush:
            existing_after_conflict = session.scalars(
                select(EvidenceChunk).where(EvidenceChunk.evidence_id == evidence_id)
            ).first()
        if existing_after_conflict is None:
            raise
        return apply_values(existing_after_conflict)


def _event_as_of_date(event: dict[str, object]) -> date:
    raw = str(event.get("as_of_date") or event.get("source_date") or "").strip()
    if raw:
        if len(raw) == 8 and raw.isdigit():
            parsed_compact = _parse_yyyymmdd(raw)
            if parsed_compact is not None:
                return parsed_compact.date()
        parsed = _parse_iso_date(raw)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc).date()


def _successful_ingestion_tickers(result: dict[str, Any] | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    return [
        str(item["ticker"])
        for item in result.get("results", [])
        if isinstance(item, dict)
        if item.get("status") in {"succeeded", "replayed"}
        if item.get("ticker")
    ]


def _failed_ingestion_tickers(result: dict[str, Any] | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    return [
        str(item["ticker"])
        for item in result.get("results", [])
        if isinstance(item, dict)
        if item.get("status") in {"failed", "partial_failed"}
        if item.get("ticker")
    ]


def _provider_freshness_statuses(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    provider = str(result.get("provider") or "unknown")
    successful = _successful_ingestion_tickers(result)
    failed = _failed_ingestion_tickers(result)
    status = "success"
    if failed and successful:
        status = "partial_failed"
    elif failed:
        status = "failed"
    elif result.get("ok") is False:
        status = "failed"
    return {
        provider: {
            "status": status,
            "source_date": result.get("source_date"),
            "successful_tickers": successful,
            "failed_tickers": failed,
        }
    }


def _aggregate_provider_status(provider_statuses: dict[str, dict[str, Any]]) -> str:
    statuses = {str(item.get("status")) for item in provider_statuses.values()}
    if "failed" in statuses:
        return "failed"
    if "partial_failed" in statuses:
        return "partial_failed"
    if "stale" in statuses:
        return "stale"
    return "success"


def _annotate_score_provider_freshness(
    session: Session,
    *,
    as_of_date: date,
    tickers: list[str],
    score_version: str | None,
    provider_statuses: dict[str, dict[str, Any]],
) -> int:
    if not tickers or not provider_statuses:
        return 0
    statement = select(RecommendationScore).where(
        RecommendationScore.as_of_date == as_of_date,
        RecommendationScore.ticker.in_(_unique_tickers(tickers)),
    )
    if score_version:
        statement = statement.where(RecommendationScore.score_version == score_version)
    scores = session.scalars(statement).all()
    for score in scores:
        freshness = dict(score.data_freshness or {})
        providers = dict(freshness.get("providers") or {})
        providers.update(provider_statuses)
        freshness["providers"] = providers
        score.data_freshness = freshness
    session.flush()
    return len(scores)


def _normalize_krx_stock_item(
    item: dict[str, Any],
    *,
    market: str,
) -> dict[str, Any] | None:
    ticker = _ticker_from_provider(item, "ISU_SRT_CD", "isuSrtCd", "ticker", "ISU_CD", "isuCd")
    if not ticker or not ticker.isdigit() or len(ticker) != 6:
        return None
    name = _first_text(item, "ISU_ABBRV", "isuAbrv", "ISU_NM", "isuNm", "name")
    if not name:
        return None
    item_market = _first_text(item, "MKT_NM", "mktNm", "market") or market
    listing_date = _parse_yyyymmdd(_first_text(item, "LIST_DD", "listDd"))
    sector = _first_text(item, "SECT_TP_NM", "MKT_TP_NM", "secugrpNm", "SECUGRP_NM") or None
    return {
        "ticker": ticker,
        "company_name": name,
        "company_name_en": _first_text(item, "ISU_ENG_NM", "isuEngNm") or None,
        "market": _normalize_provider_market(item_market),
        "sector": sector,
        "industry": _first_text(item, "IDX_IND_NM", "industry") or sector,
        "listing_date": listing_date.date() if listing_date else None,
        "is_active": True,
    }


def _normalize_krx_price_item(
    item: dict[str, Any],
    *,
    base_date: str,
) -> dict[str, Any] | None:
    ticker = _ticker_from_provider(item, "ISU_CD", "isuCd", "ISU_SRT_CD", "isuSrtCd", "ticker")
    raw_date = _first_text(item, "BAS_DD", "basDd", "base_date") or base_date
    trade_date = _parse_yyyymmdd(_compact_source_date(raw_date))
    if not ticker or trade_date is None:
        return None
    return {
        "ticker": ticker,
        "trade_date": trade_date.date(),
        "close_price": _decimal_from_provider(item, "TDD_CLSPRC", "close_price", "close"),
        "volume": _decimal_from_provider(item, "ACC_TRDVOL", "volume"),
        "trading_value": _decimal_from_provider(item, "ACC_TRDVAL", "trading_value"),
        "market_cap": _decimal_from_provider(item, "MKTCAP", "market_cap"),
        "change_rate": _decimal_from_provider(item, "FLUC_RT", "change_rate"),
    }


def _ticker_from_provider(item: dict[str, Any], *keys: str) -> str:
    first_raw = ""
    for key in keys:
        raw = _first_text(item, key)
        if not raw:
            continue
        first_raw = first_raw or raw
        if raw.startswith("A") and raw[1:].isdigit() and len(raw) <= 7:
            return raw[1:].zfill(6)
        if raw.isdigit() and len(raw) <= 6:
            return raw.zfill(6)
    return first_raw


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _decimal_from_provider(item: dict[str, Any], *keys: str) -> Decimal | None:
    raw = _first_text(item, *keys).replace(",", "")
    if raw in {"", "-", "+"}:
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None


def _compact_source_date(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return raw
    parsed = _parse_iso_date(raw)
    if parsed is not None:
        return parsed.strftime("%Y%m%d")
    return raw.replace("-", "")


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _archiver_from_settings(settings: Settings) -> PayloadArchiver:
    if settings.ingestion_raw_bucket:
        return S3PayloadArchiver(bucket=settings.ingestion_raw_bucket)
    return NoopPayloadArchiver()


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower().replace("-", "_")
    if normalized == "opendart":
        return OPENDART_PROVIDER
    if normalized in {"naver", "naver_news"}:
        return NAVER_PROVIDER
    if normalized in {"krx", "krx_price", "krx_prices"}:
        return KRX_PROVIDER
    return provider


def _job_type(provider: str) -> str:
    if provider == OPENDART_PROVIDER:
        return "disclosure"
    if provider == KRX_PROVIDER:
        return "price"
    return "news"


def _first_secret_value(secret: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = secret.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _iter_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _unique_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for ticker in tickers:
        if ticker in seen:
            continue
        seen.add(ticker)
        unique.append(ticker)
    return unique


def _event_tickers(event: dict[str, object]) -> list[str]:
    tickers_value = event.get("tickers")
    if isinstance(tickers_value, str):
        return [item.strip() for item in tickers_value.split(",") if item.strip()]
    if isinstance(tickers_value, list):
        return [str(item).strip() for item in tickers_value if str(item).strip()]
    ticker_value = event.get("ticker")
    if isinstance(ticker_value, str) and ticker_value.strip():
        return [ticker_value.strip()]
    return []


def _event_providers(event: dict[str, object]) -> list[str]:
    providers_value = event.get("providers")
    if isinstance(providers_value, str):
        values = [item.strip() for item in providers_value.split(",") if item.strip()]
    elif isinstance(providers_value, list):
        values = [str(item).strip() for item in providers_value if str(item).strip()]
    else:
        provider_value = event.get("provider")
        values = [str(provider_value).strip()] if isinstance(provider_value, str) and provider_value.strip() else []
    return [_normalize_provider(value) for value in values]


def _event_markets(event: dict[str, object]) -> list[str]:
    markets_value = event.get("markets")
    if isinstance(markets_value, str):
        values = [item.strip() for item in markets_value.split(",") if item.strip()]
    elif isinstance(markets_value, list):
        values = [str(item).strip() for item in markets_value if str(item).strip()]
    else:
        market_value = event.get("market")
        values = [str(market_value).strip()] if isinstance(market_value, str) and market_value.strip() else []
    markets = [_normalize_provider_market(value) for value in values]
    return markets or ["KOSPI", "KOSDAQ"]


def _event_market_filter(event: dict[str, object]) -> list[str]:
    if "markets" not in event and "market" not in event:
        return []
    return _event_markets(event)


def _score_refresh_tickers(session: Session, event: dict[str, object]) -> list[str] | None:
    has_batch_selector = any(
        key in event
        for key in ("stock_limit", "stock_offset", "limit", "offset", "markets", "market")
    )
    if not has_batch_selector:
        return None
    markets = _event_market_filter(event)
    limit = _score_refresh_limit(event.get("stock_limit", event.get("limit")))
    offset = _nonnegative_int(event.get("stock_offset", event.get("offset")), default=0)
    statement = select(Stock.ticker).where(Stock.is_active.is_(True))
    if markets:
        statement = statement.where(Stock.market.in_(markets))
    return list(
        session.scalars(
            statement.order_by(Stock.ticker.asc()).limit(limit).offset(offset)
        ).all()
    )


def _score_refresh_batch_metadata(
    event: dict[str, object],
    tickers: list[str] | None,
) -> dict[str, object] | None:
    if tickers is None:
        return None
    return {
        "limit": _score_refresh_limit(event.get("stock_limit", event.get("limit"))),
        "offset": _nonnegative_int(event.get("stock_offset", event.get("offset")), default=0),
        "markets": _event_market_filter(event),
        "selected_count": len(tickers),
        "first_ticker": tickers[0] if tickers else None,
        "last_ticker": tickers[-1] if tickers else None,
    }


def _normalize_provider_market(market: str) -> str:
    normalized = market.strip().upper()
    if normalized in {"KOSDAQ", "KQ", "KSQ"}:
        return "KOSDAQ"
    return "KOSPI"


def _event_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return default


def _status_limit(value: object) -> int:
    limit = _positive_int(value, default=10)
    return min(limit, 50)


def _reconcile_limit(value: object) -> int:
    limit = _positive_int(value, default=50)
    return min(limit, 100)


def _score_refresh_limit(value: object) -> int:
    limit = _positive_int(value, default=100)
    return min(limit, 300)


def _stale_run_max_age_minutes(value: object) -> int:
    return max(_positive_int(value, default=60), 1)


def _unique_providers(providers: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for provider in providers:
        if provider in seen:
            continue
        seen.add(provider)
        unique.append(provider)
    return unique


def _run_status_counts(runs: list[IngestionRun]) -> dict[str, int]:
    counts = {
        "started": 0,
        "succeeded": 0,
        "partial_failed": 0,
        "failed": 0,
    }
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
    return counts


def _run_status_dict(run: IngestionRun) -> dict[str, Any]:
    target_scope = dict(run.target_scope or {})
    return {
        "run_id": run.run_id,
        "provider": run.provider,
        "job_type": run.job_type,
        "status": run.status,
        "ticker": target_scope.get("ticker"),
        "source_date": target_scope.get("source_date"),
        "started_at": _isoformat(run.started_at),
        "completed_at": _isoformat(run.completed_at),
        "result_counts": dict(run.result_counts or {}),
        "error_summary": run.error_summary,
    }


def _stale_run_dict(
    *,
    run: IngestionRun,
    observed_at: datetime,
) -> dict[str, Any]:
    target_scope = dict(run.target_scope or {})
    started_at = _ensure_aware_datetime(run.started_at)
    age_seconds = int((observed_at - started_at).total_seconds())
    return {
        "run_id": run.run_id,
        "provider": run.provider,
        "job_type": run.job_type,
        "status": run.status,
        "ticker": target_scope.get("ticker"),
        "source_date": target_scope.get("source_date"),
        "started_at": _isoformat(run.started_at),
        "completed_at": _isoformat(run.completed_at),
        "age_seconds": age_seconds,
        "error_summary": run.error_summary,
    }


def _evidence_status_dict(
    *,
    chunk: EvidenceChunk,
    source: SourceDocument,
) -> dict[str, Any]:
    return {
        "evidence_id": chunk.evidence_id,
        "ticker": chunk.ticker,
        "evidence_type": chunk.evidence_type,
        "source_name": source.source_name,
        "source_type": source.source_type,
        "source_identifier": source.external_id,
        "published_at": _isoformat(chunk.published_at or source.published_at),
        "fetched_at": _isoformat(chunk.fetched_at),
    }


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _ensure_aware_datetime(value).isoformat()


def _ensure_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_yyyymmdd(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_rfc2822(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _request_limit_violations(request: ProviderIngestionRequest) -> list[dict[str, int | str]]:
    checks = (
        ("tickers", len(request.tickers), MAX_TICKERS_PER_BATCH),
        ("page_count", request.page_count, MAX_OPENDART_PAGE_COUNT),
        ("news_display", request.news_display, MAX_NAVER_NEWS_DISPLAY),
    )
    return [
        {"field": field, "value": value, "max": max_value}
        for field, value, max_value in checks
        if value > max_value
    ]


def _request_limits() -> dict[str, int]:
    return {
        "max_tickers": MAX_TICKERS_PER_BATCH,
        "max_page_count": MAX_OPENDART_PAGE_COUNT,
        "max_news_display": MAX_NAVER_NEWS_DISPLAY,
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_provider_text(value: object) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split()).strip()


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _result_dict(result: TickerIngestionResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ticker": result.ticker,
        "run_id": result.run_id,
        "status": result.status,
        "result_counts": result.result_counts,
    }
    if result.raw_archive_uri:
        payload["raw_archive_uri"] = result.raw_archive_uri
    if result.error_summary:
        payload["error_summary"] = result.error_summary
    return payload
