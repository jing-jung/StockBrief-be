from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import boto3
from botocore.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.orm import Disclosure, NewsItem, SourceDocument, Stock
from app.services.external.aws_secrets import load_secret_json
from app.services.external.clients import (
    NAVER_PROVIDER,
    OPENDART_PROVIDER,
    NaverNewsClient,
    OpenDartClient,
)
from app.services.external.types import ExternalApiResult
from app.services.ingestion_idempotency import IngestionIdempotencyService


SUPPORTED_PROVIDERS = (OPENDART_PROVIDER, NAVER_PROVIDER)


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
        if not request.tickers:
            return {"ok": False, "error": "tickers_required"}

        results = [self._run_ticker(request=request, ticker=ticker) for ticker in request.tickers]
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
        if request.provider == OPENDART_PROVIDER:
            return OpenDartClient(settings=self.settings, session=self.session).list_disclosures(
                ticker=ticker,
                page_count=request.page_count,
            )
        stock = self.session.get(Stock, ticker)
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
            title = str(item.get("title") or source_url).strip()
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


def hydrate_external_api_settings(settings: Settings) -> Settings:
    if settings.opendart_api_key and settings.naver_client_id and settings.naver_client_secret:
        return settings
    if not settings.external_api_secret_arn:
        return settings
    secret = load_secret_json(settings.external_api_secret_arn)
    return Settings(
        **settings.model_dump(),
        OPENDART_API_KEY=(
            settings.opendart_api_key
            or _first_secret_value(secret, "OPENDART_API_KEY", "opendart_api_key")
        ),
        NAVER_CLIENT_ID=(
            settings.naver_client_id
            or _first_secret_value(secret, "NAVER_CLIENT_ID", "naver_client_id")
        ),
        NAVER_CLIENT_SECRET=(
            settings.naver_client_secret
            or _first_secret_value(secret, "NAVER_CLIENT_SECRET", "naver_client_secret")
        ),
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
    return provider


def _job_type(provider: str) -> str:
    if provider == OPENDART_PROVIDER:
        return "disclosure"
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


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
