from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.orm import Disclosure, IngestionRun, NewsItem, SourceDocument
from app.services.external.clients import NAVER_PROVIDER, OPENDART_PROVIDER
from app.services.external.types import ExternalApiResult, ExternalRequest, ExternalResponse
from app.services import ingestion as ingestion_module
from app.services.ingestion import (
    check_ingestion_readiness,
    check_provider_egress,
    NoopPayloadArchiver,
    ProviderIngestionRequest,
    ProviderIngestionService,
    build_request_hash,
    build_run_id,
    check_raw_archive_write,
    hydrate_external_api_settings,
    handle_ingestion_event,
)


class RecordingArchiver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def archive(
        self,
        *,
        run_id: str,
        provider: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> str:
        self.calls.append(
            {
                "run_id": run_id,
                "provider": provider,
                "ticker": ticker,
                "payload": payload,
            }
        )
        return f"s3://stockbrief-dev-raw/{provider}/{ticker}/{run_id}.json"


class FailingArchiver:
    def archive(
        self,
        *,
        run_id: str,
        provider: str,
        ticker: str,
        payload: dict[str, Any],
    ) -> str | None:
        raise RuntimeError("s3 endpoint unavailable with secret-like token")


def test_build_request_hash_uses_provider_ticker_source_date_and_request_params() -> None:
    base = build_request_hash(
        provider=OPENDART_PROVIDER,
        ticker="005930",
        source_date="2026-06-18",
        request_params={"page_count": 10},
    )

    assert base == build_request_hash(
        provider=OPENDART_PROVIDER,
        ticker="005930",
        source_date="2026-06-18",
        request_params={"page_count": 10},
    )
    assert base != build_request_hash(
        provider=OPENDART_PROVIDER,
        ticker="005930",
        source_date="2026-06-19",
        request_params={"page_count": 10},
    )
    assert base != build_request_hash(
        provider=OPENDART_PROVIDER,
        ticker="000660",
        source_date="2026-06-18",
        request_params={"page_count": 10},
    )


def test_provider_ingestion_request_normalizes_event_fields() -> None:
    request = ProviderIngestionRequest.from_event(
        {
            "provider": "naver-news",
            "tickers": "005930, 000660",
            "page_count": "0",
            "news_display": "3",
        }
    )

    assert request.provider == NAVER_PROVIDER
    assert request.tickers == ["005930", "000660"]
    assert request.page_count == 10
    assert request.news_display == 3


def test_provider_ingestion_rejects_requests_above_operational_limits(
    monkeypatch,
    seeded_session: Session,
) -> None:
    provider_called = False

    def fail_if_called(self, *, ticker: str, corp_code=None, page_count: int = 10):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider call should not run when request limits are exceeded")

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fail_if_called,
    )
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY="test-key"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=[f"{index:06d}" for index in range(21)],
            source_date="2026-06-18",
            page_count=101,
            news_display=51,
        )
    )

    assert result["ok"] is False
    assert result["error"] == "request_limit_exceeded"
    assert result["limits"] == {
        "max_tickers": 20,
        "max_page_count": 100,
        "max_news_display": 50,
    }
    assert result["violations"] == [
        {"field": "tickers", "value": 21, "max": 20},
        {"field": "page_count", "value": 101, "max": 100},
        {"field": "news_display", "value": 51, "max": 50},
    ]
    assert provider_called is False
    assert seeded_session.scalars(select(IngestionRun)).all() == []


def test_opendart_ingestion_upserts_disclosures_and_sources(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(self, *, ticker: str, corp_code=None, page_count: int = 10):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:mock:{page_count}",
            data_status="available",
            status_code=200,
            payload={
                "list": [
                    {
                        "rcept_no": "202606180001",
                        "report_nm": "반기보고서",
                        "rcept_dt": "20260618",
                        "rm": "정기공시",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    archiver = RecordingArchiver()
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY="test-key"),
        archiver=archiver,
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
        )
    )
    replay = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
        )
    )
    replay_with_different_run_id = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
            run_id="manual-rerun",
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "succeeded"
    assert result["results"][0]["result_counts"] == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }
    assert replay["results"][0]["status"] == "replayed"
    assert replay_with_different_run_id["results"][0]["status"] == "replayed"
    assert replay_with_different_run_id["results"][0]["run_id"] == "manual-rerun-005930"
    assert len(archiver.calls) == 1

    disclosure = seeded_session.scalars(
        select(Disclosure).where(Disclosure.receipt_no == "202606180001")
    ).one()
    assert disclosure.provider == OPENDART_PROVIDER
    assert disclosure.raw_payload["raw_archive_uri"].startswith("s3://stockbrief-dev-raw/")

    source_document = seeded_session.scalars(
        select(SourceDocument).where(
            SourceDocument.source_name == OPENDART_PROVIDER,
            SourceDocument.external_id == "202606180001",
        )
    ).one()
    assert source_document.source_type == "disclosure"
    assert source_document.metadata_["raw_archive_uri"].startswith("s3://stockbrief-dev-raw/")

    run = seeded_session.scalars(
        select(IngestionRun).where(
            IngestionRun.run_id == build_run_id(
                provider=OPENDART_PROVIDER,
                source_date="2026-06-18",
                ticker="005930",
            )
        )
    ).one()
    assert run.status == "succeeded"


def test_explicit_run_id_is_scoped_per_ticker_in_batch(
    monkeypatch,
    seeded_session: Session,
) -> None:
    provider_calls: list[str] = []

    def fake_list_disclosures(self, *, ticker: str, corp_code=None, page_count: int = 10):
        provider_calls.append(ticker)
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:mock:{page_count}",
            data_status="available",
            status_code=200,
            payload={
                "list": [
                    {
                        "rcept_no": f"20260618{ticker}",
                        "report_nm": "반기보고서",
                        "rcept_dt": "20260618",
                        "rm": "정기공시",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY="test-key"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930", "005930", "000660"],
            source_date="2026-06-18",
            run_id="manual-run",
        )
    )

    assert result["ok"] is True
    assert [item["run_id"] for item in result["results"]] == [
        "manual-run-005930",
        "manual-run-000660",
    ]
    assert provider_calls == ["005930", "000660"]

    runs = seeded_session.scalars(
        select(IngestionRun)
        .where(IngestionRun.run_id.in_(["manual-run-005930", "manual-run-000660"]))
        .order_by(IngestionRun.run_id)
    ).all()
    assert len(runs) == 2
    runs_by_ticker = {run.target_scope["ticker"]: run for run in runs}
    assert set(runs_by_ticker) == {"005930", "000660"}
    assert {run.status for run in runs} == {"succeeded"}
    assert runs_by_ticker["005930"].run_id == "manual-run-005930"
    assert runs_by_ticker["000660"].run_id == "manual-run-000660"
    assert runs_by_ticker["005930"].target_scope == {
        "ticker": "005930",
        "source_date": "2026-06-18",
    }
    assert runs_by_ticker["000660"].target_scope == {
        "ticker": "000660",
        "source_date": "2026-06-18",
    }
    assert runs_by_ticker["005930"].input_hash != runs_by_ticker["000660"].input_hash
    assert runs_by_ticker["005930"].result_counts == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }
    assert runs_by_ticker["000660"].result_counts == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }


def test_naver_ingestion_upserts_news_and_source_documents(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_search_news(self, *, ticker: str, company_name: str, display: int = 10):
        return ExternalApiResult(
            provider=NAVER_PROVIDER,
            endpoint="/v1/search/news.json",
            cache_key=f"news:{ticker}:{company_name}:{display}",
            data_status="available",
            status_code=200,
            payload={
                "items": [
                    {
                        "title": "삼성전자 신규 공시 분석",
                        "originallink": "https://news.example/articles/1",
                        "link": "https://news.example/articles/1",
                        "description": "테스트 뉴스",
                        "pubDate": "Thu, 18 Jun 2026 09:00:00 +0900",
                    }
                ]
            },
        )

    monkeypatch.setattr("app.services.ingestion.NaverNewsClient.search_news", fake_search_news)
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(NAVER_CLIENT_ID="id", NAVER_CLIENT_SECRET="secret"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=NAVER_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
            news_display=1,
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["result_counts"] == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }

    news_item = seeded_session.scalars(
        select(NewsItem).where(NewsItem.source_url == "https://news.example/articles/1")
    ).one()
    assert news_item.provider == NAVER_PROVIDER
    assert news_item.summary == "테스트 뉴스"

    source_document = seeded_session.scalars(
        select(SourceDocument).where(
            SourceDocument.source_name == NAVER_PROVIDER,
            SourceDocument.source_url == "https://news.example/articles/1",
        )
    ).one()
    assert source_document.source_type == "news"


def test_provider_fallback_marks_partial_failed_without_persisting_rows(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(self, *, ticker: str, corp_code=None, page_count: int = 10):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key="fallback",
            data_status="fallback",
            payload={"fallback": True, "list": []},
            missing_data=[{"field": "OPENDART_API_KEY", "reason": "missing_api_key"}],
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY=""),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
        )
    )

    assert result["ok"] is False
    assert result["results"][0]["status"] == "partial_failed"
    assert result["results"][0]["result_counts"] == {
        "inserted": 0,
        "updated": 0,
        "skipped": 1,
    }
    assert result["results"][0]["error_summary"]["code"] == "provider_fallback"


def test_persist_failure_rolls_back_normalized_rows_before_marking_failed(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(self, *, ticker: str, corp_code=None, page_count: int = 10):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:mock:{page_count}",
            data_status="available",
            status_code=200,
            payload={
                "list": [
                    {
                        "rcept_no": "202606180001",
                        "report_nm": "반기보고서",
                        "rcept_dt": "20260618",
                        "rm": "정기공시",
                    },
                    {
                        "rcept_no": "202606180002",
                        "report_nm": "정정공시",
                        "rcept_dt": "20260618",
                        "rm": "정정",
                    },
                ]
            },
        )

    original_upsert = ingestion_module.upsert_source_document
    calls = {"count": 0}

    def failing_upsert(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("normalized_write_failed")
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    monkeypatch.setattr("app.services.ingestion.upsert_source_document", failing_upsert)
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY="test-key"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
        )
    )

    assert result["ok"] is False
    assert result["results"][0]["status"] == "failed"

    run = seeded_session.scalars(
        select(IngestionRun).where(
            IngestionRun.run_id == build_run_id(
                provider=OPENDART_PROVIDER,
                source_date="2026-06-18",
                ticker="005930",
            )
        )
    ).one()
    assert run.status == "failed"

    leaked_source = seeded_session.scalars(
        select(SourceDocument).where(
            SourceDocument.source_name == OPENDART_PROVIDER,
            SourceDocument.external_id.in_(["202606180001", "202606180002"]),
        )
    ).all()
    leaked_disclosures = seeded_session.scalars(
        select(Disclosure).where(
            Disclosure.receipt_no.in_(["202606180001", "202606180002"])
        )
    ).all()
    assert leaked_source == []
    assert leaked_disclosures == []


def test_handle_ingestion_event_raises_for_scheduled_failure(monkeypatch) -> None:
    class FakeSessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeProviderIngestionService:
        def __init__(self, session):
            self.session = session

        def run_provider_batch(self, request):
            return {
                "ok": False,
                "provider": request.provider,
                "results": [{"status": "partial_failed"}],
            }

    monkeypatch.setattr("app.services.ingestion.get_session_factory", lambda: FakeSessionFactory())
    monkeypatch.setattr(
        "app.services.ingestion.ProviderIngestionService",
        FakeProviderIngestionService,
    )

    with pytest.raises(RuntimeError, match="ingestion_batch_failed"):
        handle_ingestion_event(
            {
                "stockbrief_operation": "ingest_provider_batch",
                "provider": OPENDART_PROVIDER,
                "tickers": ["005930"],
                "raise_on_failure": True,
            }
        )


def test_hydrate_external_api_settings_reads_external_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ingestion.load_secret_json",
        lambda _secret_arn: {
            "OPENDART_API_KEY": "opendart-secret",
            "NAVER_CLIENT_ID": "naver-id",
            "NAVER_CLIENT_SECRET": "naver-secret",
        },
    )

    settings = hydrate_external_api_settings(
        Settings(EXTERNAL_API_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:external")
    )

    assert settings.opendart_api_key == "opendart-secret"
    assert settings.naver_client_id == "naver-id"
    assert settings.naver_client_secret == "naver-secret"


def test_check_ingestion_readiness_reports_missing_configuration_without_secret_values() -> None:
    result = check_ingestion_readiness(Settings())

    assert result["ok"] is False
    assert result["checks"]["raw_archive"] == {"configured": False}
    assert result["checks"]["external_api_secret"] == {
        "configured": False,
        "loaded": False,
        "error": None,
    }
    assert result["checks"]["providers"] == {
        OPENDART_PROVIDER: {"api_key_configured": False},
        NAVER_PROVIDER: {
            "client_id_configured": False,
            "client_secret_configured": False,
        },
    }
    assert result["checks"]["network"]["outbound_internet_egress_verified"] is False
    assert result["issues"] == [
        {"code": "missing_external_api_secret_arn", "field": "EXTERNAL_API_SECRET_ARN"},
        {"code": "missing_ingestion_raw_bucket", "field": "INGESTION_RAW_BUCKET"},
        {"code": "missing_provider_credential", "field": "OPENDART_API_KEY"},
        {"code": "missing_provider_credential", "field": "NAVER_CLIENT_ID"},
        {"code": "missing_provider_credential", "field": "NAVER_CLIENT_SECRET"},
    ]


def test_check_ingestion_readiness_loads_external_secret_without_exposing_values(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ingestion.load_secret_json",
        lambda _secret_arn: {
            "OPENDART_API_KEY": "opendart-secret",
            "NAVER_CLIENT_ID": "naver-id",
            "NAVER_CLIENT_SECRET": "naver-secret",
        },
    )

    result = check_ingestion_readiness(
        Settings(
            EXTERNAL_API_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:external",
            INGESTION_RAW_BUCKET="stockbrief-dev-raw",
        )
    )

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["checks"]["raw_archive"] == {"configured": True}
    assert result["checks"]["external_api_secret"] == {
        "configured": True,
        "loaded": True,
        "error": None,
    }
    serialized = str(result)
    assert "opendart-secret" not in serialized
    assert "naver-id" not in serialized
    assert "naver-secret" not in serialized


def test_check_ingestion_readiness_returns_secret_load_error(monkeypatch) -> None:
    def fail_secret_load(_secret_arn):
        raise RuntimeError("secret unavailable")

    monkeypatch.setattr("app.services.ingestion.load_secret_json", fail_secret_load)

    result = check_ingestion_readiness(
        Settings(
            EXTERNAL_API_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:external",
            INGESTION_RAW_BUCKET="stockbrief-dev-raw",
        )
    )

    assert result["ok"] is False
    assert result["checks"]["external_api_secret"] == {
        "configured": True,
        "loaded": False,
        "error": {
            "code": "RuntimeError",
            "message": "External API secret could not be loaded.",
        },
    }
    assert "secret unavailable" not in str(result)
    assert {"code": "external_api_secret_load_failed", "field": "EXTERNAL_API_SECRET_ARN"} in result[
        "issues"
    ]


def test_check_raw_archive_write_reports_missing_bucket_configuration() -> None:
    result = check_raw_archive_write(Settings())

    assert result == {
        "ok": False,
        "checks": {"raw_archive": {"configured": False, "write_verified": False}},
        "issues": [{"code": "missing_ingestion_raw_bucket", "field": "INGESTION_RAW_BUCKET"}],
    }


def test_check_raw_archive_write_archives_small_probe_without_secret_values() -> None:
    archiver = RecordingArchiver()

    result = check_raw_archive_write(
        Settings(INGESTION_RAW_BUCKET="stockbrief-dev-raw"),
        archiver=archiver,
    )

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["checks"]["raw_archive"]["configured"] is True
    assert result["checks"]["raw_archive"]["bucket"] == "stockbrief-dev-raw"
    assert result["checks"]["raw_archive"]["write_verified"] is True
    assert result["checks"]["raw_archive"]["raw_archive_uri"].startswith(
        "s3://stockbrief-dev-raw/STOCKBRIEF_PROBE/healthcheck/raw-archive-probe-"
    )
    assert len(archiver.calls) == 1
    assert archiver.calls[0]["provider"] == "STOCKBRIEF_PROBE"
    assert archiver.calls[0]["ticker"] == "healthcheck"
    assert archiver.calls[0]["payload"]["probe"] == "stockbrief-ingestion-raw-archive"
    assert "secret" not in str(result).lower()


def test_check_raw_archive_write_returns_error_code_without_exception_message() -> None:
    result = check_raw_archive_write(
        Settings(INGESTION_RAW_BUCKET="stockbrief-dev-raw"),
        archiver=FailingArchiver(),
    )

    assert result == {
        "ok": False,
        "checks": {
            "raw_archive": {
                "configured": True,
                "bucket": "stockbrief-dev-raw",
                "write_verified": False,
                "error_code": "RuntimeError",
            }
        },
        "issues": [{"code": "raw_archive_write_failed", "field": "INGESTION_RAW_BUCKET"}],
    }
    assert "secret-like token" not in str(result)


def test_check_provider_egress_reports_reachable_provider_endpoints() -> None:
    calls: list[ExternalRequest] = []

    def fake_transport(request: ExternalRequest) -> ExternalResponse:
        calls.append(request)
        return ExternalResponse(status_code=401, payload={})

    result = check_provider_egress(transport=fake_transport)

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["checks"]["providers"][OPENDART_PROVIDER]["reachable"] is True
    assert result["checks"]["providers"][NAVER_PROVIDER]["reachable"] is True
    assert [call.method for call in calls] == ["GET", "GET"]
    assert all(call.headers == {} for call in calls)
    assert all(call.timeout_seconds == 3.0 for call in calls)


def test_check_provider_egress_empty_provider_list_defaults_to_supported_providers() -> None:
    calls: list[ExternalRequest] = []

    def fake_transport(request: ExternalRequest) -> ExternalResponse:
        calls.append(request)
        return ExternalResponse(status_code=401, payload={})

    result = check_provider_egress({"providers": []}, transport=fake_transport)

    assert result["ok"] is True
    assert result["issues"] == []
    assert set(result["checks"]["providers"]) == {OPENDART_PROVIDER, NAVER_PROVIDER}
    assert len(calls) == 2


def test_check_provider_egress_treats_http_error_as_reachable() -> None:
    class FakeHttpError(Exception):
        code = 403

    def fake_transport(_request: ExternalRequest) -> ExternalResponse:
        raise FakeHttpError("forbidden")

    result = check_provider_egress({"provider": OPENDART_PROVIDER}, transport=fake_transport)

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["checks"]["providers"] == {
        OPENDART_PROVIDER: {
            "reachable": True,
            "endpoint": "https://opendart.fss.or.kr/api/list.json",
            "status_code": 403,
            "note": "Provider endpoint returned an HTTP error response.",
        }
    }


def test_check_provider_egress_reports_network_failure_without_secret_values() -> None:
    def fake_transport(_request: ExternalRequest) -> ExternalResponse:
        raise TimeoutError("network timeout with no credentials")

    result = check_provider_egress({"providers": [NAVER_PROVIDER]}, transport=fake_transport)

    assert result["ok"] is False
    assert result["checks"]["providers"][NAVER_PROVIDER] == {
        "reachable": False,
        "endpoint": "https://openapi.naver.com/v1/search/news.json",
        "status_code": None,
        "error_code": "TimeoutError",
        "note": "Provider endpoint could not be reached from this runtime.",
    }
    assert result["issues"] == [
        {
            "code": "provider_egress_unreachable",
            "provider": NAVER_PROVIDER,
            "endpoint": "https://openapi.naver.com/v1/search/news.json",
        }
    ]
    assert "credentials" not in str(result)


def test_check_provider_egress_rejects_unsupported_provider() -> None:
    result = check_provider_egress({"providers": ["UNKNOWN"]}, transport=lambda _request: None)

    assert result == {
        "ok": False,
        "checks": {"providers": {}},
        "issues": [{"code": "unsupported_provider", "provider": "UNKNOWN"}],
    }
