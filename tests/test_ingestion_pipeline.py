from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.orm import (
    Disclosure,
    EvidenceChunk,
    FinancialStatement,
    IngestionRun,
    NewsItem,
    PriceMetric,
    RecommendationScore,
    SourceDocument,
    Stock,
)
from app.services.external.clients import KRX_PROVIDER, NAVER_PROVIDER, OPENDART_PROVIDER
from app.services.external.types import ExternalApiResult, ExternalRequest, ExternalResponse
from app.services import ingestion as ingestion_module
from app.services.recommendation.engine import SCORE_VERSION
from app.services.ingestion import (
    check_ingestion_readiness,
    check_ingestion_scheduler_enable_gate,
    check_provider_egress,
    NoopPayloadArchiver,
    ProviderIngestionRequest,
    ProviderIngestionService,
    _opendart_financial_years,
    build_request_hash,
    build_run_id,
    check_raw_archive_write,
    handle_refresh_score_snapshots_event,
    reconcile_stale_started_runs,
    summarize_ingestion_status,
    hydrate_external_api_settings,
    handle_ingestion_event,
    persist_krx_stock_master,
    upsert_evidence_chunk,
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


@pytest.fixture(autouse=True)
def default_empty_opendart_financials(monkeypatch):
    def fake_list_financial_statements(
        self,
        *,
        ticker: str,
        corp_code=None,
        business_years: list[int],
        report_code: str = "11011",
    ):
        if not self.settings.opendart_api_key:
            return ExternalApiResult(
                provider=OPENDART_PROVIDER,
                endpoint="/fnlttSinglAcntAll.json",
                cache_key=f"financials:{ticker}:missing",
                data_status="fallback",
                payload={"ticker": ticker, "financial_statements": []},
                missing_data=[
                    {"field": "OPENDART_API_KEY", "reason": "missing_api_key"}
                ],
            )
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/fnlttSinglAcntAll.json",
            cache_key=f"financials:{ticker}:empty",
            data_status="available",
            status_code=200,
            payload={"ticker": ticker, "financial_statements": []},
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_financial_statements",
        fake_list_financial_statements,
    )


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


def test_opendart_financial_years_waits_until_q2_for_latest_fy() -> None:
    assert _opendart_financial_years("2026-03-31") == [2024, 2023]
    assert _opendart_financial_years("2026-04-01") == [2025, 2024]


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
    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:sample:{page_count}",
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
    assert replay_with_different_run_id["results"][0]["status"] == "succeeded"
    assert replay_with_different_run_id["results"][0]["run_id"] == "manual-rerun-005930"
    assert replay_with_different_run_id["results"][0]["result_counts"] == {
        "inserted": 0,
        "updated": 1,
        "skipped": 0,
    }
    assert len(archiver.calls) == 2

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

    evidence_chunk = seeded_session.scalars(
        select(EvidenceChunk).where(EvidenceChunk.evidence_id == "ev_opendart_005930_202606180001")
    ).one()
    assert evidence_chunk.source_document_id == source_document.id
    assert evidence_chunk.evidence_type == "disclosure"
    assert evidence_chunk.chunk_text == "반기보고서"
    assert evidence_chunk.source_url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202606180001"

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


def test_opendart_ingestion_upserts_financial_statements_for_score_inputs(
    monkeypatch,
    seeded_session: Session,
) -> None:
    ticker = "456789"
    if seeded_session.get(Stock, ticker) is None:
        seeded_session.add(
            Stock(
                ticker=ticker,
                company_name="테스트재무",
                market="KOSPI",
                is_active=True,
            )
        )
    seeded_session.add(
        PriceMetric(
            ticker=ticker,
            trade_date=date(2026, 7, 3),
            close_price=Decimal("10000"),
            volume=Decimal("1000000"),
            trading_value=Decimal("10000000000"),
            market_cap=Decimal("140000000000"),
            source=KRX_PROVIDER,
        )
    )
    seeded_session.commit()

    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:empty",
            data_status="available",
            status_code=200,
            payload={"list": []},
        )

    def fake_list_financial_statements(
        self,
        *,
        ticker: str,
        corp_code=None,
        business_years: list[int],
        report_code: str = "11011",
    ):
        rows = []
        for business_year, revenue, operating_income in [
            (2025, "100000000000", "12000000000"),
            (2024, "80000000000", "9000000000"),
        ]:
            rows.extend(
                [
                    {
                        "ticker": ticker,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": "CFS",
                        "account_nm": "매출액",
                        "thstrm_amount": revenue,
                    },
                    {
                        "ticker": ticker,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": "CFS",
                        "account_nm": "영업이익",
                        "thstrm_amount": operating_income,
                    },
                    {
                        "ticker": ticker,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": "CFS",
                        "account_nm": "당기순이익",
                        "thstrm_amount": "10000000000",
                    },
                    {
                        "ticker": ticker,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": "CFS",
                        "account_nm": "자산총계",
                        "thstrm_amount": "200000000000",
                    },
                    {
                        "ticker": ticker,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": "CFS",
                        "account_nm": "부채총계",
                        "thstrm_amount": "60000000000",
                    },
                    {
                        "ticker": ticker,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": "CFS",
                        "account_nm": "자본총계",
                        "thstrm_amount": "140000000000",
                    },
                ]
            )
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/fnlttSinglAcntAll.json",
            cache_key=f"financials:{ticker}",
            data_status="available",
            status_code=200,
            payload={"financial_statements": rows},
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_financial_statements",
        fake_list_financial_statements,
    )
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY="test-key"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=[ticker],
            source_date="2026-07-03",
        )
    )
    refresh = ingestion_module.refresh_score_snapshots(
        seeded_session,
        {
            "stockbrief_operation": "refresh_score_snapshots",
            "source_date": "2026-07-03",
            "tickers": [ticker],
        },
    )

    assert result["ok"] is True
    assert result["results"][0]["result_counts"] == {
        "inserted": 2,
        "updated": 0,
        "skipped": 0,
    }
    assert refresh["refresh"]["processed"] == 1
    financials = seeded_session.scalars(
        select(FinancialStatement)
        .where(FinancialStatement.ticker == ticker)
        .order_by(FinancialStatement.fiscal_year.desc())
    ).all()
    assert [row.fiscal_year for row in financials] == [2025, 2024]

    score = seeded_session.scalars(
        select(RecommendationScore).where(
            RecommendationScore.ticker == ticker,
            RecommendationScore.as_of_date == date(2026, 7, 3),
            RecommendationScore.score_version == SCORE_VERSION,
        )
    ).one()
    assert "financial_stability.inputs" not in score.missing_data
    assert "profitability.inputs" not in score.missing_data
    assert "growth.inputs" not in score.missing_data
    assert "valuation.inputs" not in score.missing_data


def test_evidence_chunk_upsert_recovers_from_concurrent_insert_conflict(
    caplog,
    monkeypatch,
    seeded_session: Session,
) -> None:
    source_document = seeded_session.scalars(select(SourceDocument)).first()
    assert source_document is not None
    assert (
        seeded_session.scalars(
            select(EvidenceChunk).where(EvidenceChunk.evidence_id == "ev_concurrent")
        ).first()
        is None
    )

    original_scalars = seeded_session.scalars
    original_flush = seeded_session.flush
    evidence_lookup = {"count": 0}
    conflict = {"done": False}

    seeded_session.execute(
        insert(EvidenceChunk).values(
            evidence_id="ev_concurrent",
            ticker="005930",
            source_document_id=source_document.id,
            evidence_type="news",
            chunk_text="old text",
            source_url="https://example.com/old",
            published_at=None,
            fetched_at=datetime.now(timezone.utc),
            confidence=0.9,
            metadata_={"provider": "race"},
        )
    )

    class RaceMissResult:
        def __init__(self, result):
            self.result = result

        def first(self):
            self.result.first()
            return None

        def __getattr__(self, name: str):
            return getattr(self.result, name)

    def scalars_with_concurrent_insert(statement, *args, **kwargs):
        result = original_scalars(statement, *args, **kwargs)
        if "evidence_chunks" in str(statement):
            evidence_lookup["count"] += 1
            if evidence_lookup["count"] == 1:
                return RaceMissResult(result)
        return result

    monkeypatch.setattr(
        seeded_session,
        "scalars",
        scalars_with_concurrent_insert,
    )

    def flush_with_integrity_conflict(*args, **kwargs):
        has_conflicting_new_chunk = any(
            isinstance(item, EvidenceChunk) and item.evidence_id == "ev_concurrent"
            for item in seeded_session.new
        )
        if has_conflicting_new_chunk and not conflict["done"]:
            conflict["done"] = True
            raise IntegrityError(
                "insert into evidence_chunks",
                {},
                Exception("UNIQUE constraint failed: evidence_chunks.evidence_id"),
            )
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(seeded_session, "flush", flush_with_integrity_conflict)
    caplog.set_level(logging.WARNING, logger="app.services.ingestion")

    chunk = upsert_evidence_chunk(
        seeded_session,
        source_document=source_document,
        ticker="005930",
        evidence_id="ev_concurrent",
        evidence_type="disclosure",
        chunk_text="new text",
        source_url="https://example.com/new",
        published_at=None,
        metadata={"provider": OPENDART_PROVIDER},
    )

    assert chunk.evidence_id == "ev_concurrent"
    assert chunk.evidence_type == "disclosure"
    assert chunk.chunk_text == "new text"
    assert chunk.source_url == "https://example.com/new"
    assert chunk.metadata_ == {"provider": OPENDART_PROVIDER}
    assert conflict["done"] is True
    assert len(
        seeded_session.scalars(
            select(EvidenceChunk).where(EvidenceChunk.evidence_id == "ev_concurrent")
        ).all()
    ) == 1
    records = [
        record
        for record in caplog.records
        if record.name == "app.services.ingestion"
        and "evidence_chunk_upsert_conflict_recovered" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "evidence_id=ev_concurrent" in records[0].getMessage()
    assert "ticker=005930" in records[0].getMessage()
    assert f"source_document_id={source_document.id}" in records[0].getMessage()
    assert OPENDART_PROVIDER not in records[0].getMessage()


def test_explicit_run_id_is_scoped_per_ticker_in_batch(
    monkeypatch,
    seeded_session: Session,
) -> None:
    provider_calls: list[str] = []

    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
        provider_calls.append(ticker)
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:sample:{page_count}",
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
                        "title": "<b>삼성전자</b> 신규 &amp; 공시 분석",
                        "originallink": "https://news.example/articles/1",
                        "link": "https://news.example/articles/1",
                        "description": "<b>테스트</b> &amp; 뉴스",
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
    assert news_item.title == "삼성전자 신규 & 공시 분석"
    assert news_item.summary == "<b>테스트</b> &amp; 뉴스"

    source_document = seeded_session.scalars(
        select(SourceDocument).where(
            SourceDocument.source_name == NAVER_PROVIDER,
            SourceDocument.source_url == "https://news.example/articles/1",
        )
    ).one()
    assert source_document.source_type == "news"
    assert source_document.title == "삼성전자 신규 & 공시 분석"

    evidence_chunk = seeded_session.scalars(
        select(EvidenceChunk).where(
            EvidenceChunk.evidence_id.startswith("ev_naver_news_005930_")
        )
    ).one()
    assert evidence_chunk.source_document_id == source_document.id
    assert evidence_chunk.evidence_type == "news"
    assert evidence_chunk.chunk_text == "테스트 & 뉴스"
    assert evidence_chunk.source_url == "https://news.example/articles/1"


def test_ingestion_status_summarizes_recent_runs_and_latest_evidence(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:sample:{page_count}",
            data_status="available",
            status_code=200,
            payload={
                "list": [
                    {
                        "rcept_no": f"20260618{ticker}",
                        "report_nm": f"{ticker} 상태 확인용 공시",
                        "rcept_dt": "20260618",
                        "rm": "정기공시",
                    }
                ]
            },
        )

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
                        "title": f"{ticker} 신규 뉴스",
                        "originallink": f"https://news.example/status-check/{ticker}",
                        "link": f"https://news.example/status-check/{ticker}",
                        "description": f"{ticker} 상태 확인용 뉴스",
                        "pubDate": "Thu, 18 Jun 2026 09:00:00 +0900",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    monkeypatch.setattr("app.services.ingestion.NaverNewsClient.search_news", fake_search_news)
    naver_service = ProviderIngestionService(
        seeded_session,
        settings=Settings(NAVER_CLIENT_ID="id", NAVER_CLIENT_SECRET="secret"),
        archiver=NoopPayloadArchiver(),
    )
    opendart_service = ProviderIngestionService(
        seeded_session,
        settings=Settings(OPENDART_API_KEY="test-key"),
        archiver=NoopPayloadArchiver(),
    )
    ingest_result = naver_service.run_provider_batch(
        ProviderIngestionRequest(
            provider=NAVER_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
            news_display=1,
        )
    )
    other_ticker_result = naver_service.run_provider_batch(
        ProviderIngestionRequest(
            provider=NAVER_PROVIDER,
            tickers=["000660"],
            source_date="2026-06-19",
            news_display=1,
        )
    )
    other_provider_result = opendart_service.run_provider_batch(
        ProviderIngestionRequest(
            provider=OPENDART_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
            page_count=1,
        )
    )

    status = summarize_ingestion_status(
        seeded_session,
        tickers=["005930"],
        providers=[NAVER_PROVIDER],
        limit=5,
    )

    assert ingest_result["ok"] is True
    assert other_ticker_result["ok"] is True
    assert other_provider_result["ok"] is True
    assert status["ok"] is True
    assert status["summary"]["ticker_filter"] == ["005930"]
    assert status["summary"]["provider_filter"] == [NAVER_PROVIDER]
    assert status["summary"]["run_status_counts"]["succeeded"] >= 1
    assert status["summary"]["recent_run_count"] >= 1
    assert status["summary"]["latest_evidence_count"] >= 1
    assert {item["ticker"] for item in status["recent_runs"]} == {"005930"}
    assert all(item["provider"] == NAVER_PROVIDER for item in status["recent_runs"])
    assert all(item["source_date"] == "2026-06-18" for item in status["recent_runs"])
    assert all(item["completed_at"] is not None for item in status["recent_runs"])
    latest_news = [
        item
        for item in status["latest_evidence"]
        if item["evidence_id"].startswith("ev_naver_news_005930_")
    ]
    assert latest_news
    assert {item["ticker"] for item in status["latest_evidence"]} == {"005930"}
    assert latest_news[0]["source_name"] == NAVER_PROVIDER
    assert latest_news[0]["source_type"] == "news"
    assert latest_news[0]["published_at"] is not None
    assert latest_news[0]["fetched_at"] is not None
    assert all(item["source_name"] == NAVER_PROVIDER for item in status["latest_evidence"])


def test_reconcile_stale_started_runs_defaults_to_dry_run_and_filters_scope(
    seeded_session: Session,
) -> None:
    now = datetime(2026, 6, 23, 9, 0, tzinfo=timezone.utc)
    stale_run = IngestionRun(
        run_id="stale-naver-005930",
        job_type="news",
        provider=NAVER_PROVIDER,
        target_scope={"ticker": "005930", "source_date": "2026-06-22"},
        status="started",
        input_hash="stale-naver-005930-hash",
        started_at=now - timedelta(hours=3),
        result_counts={},
    )
    fresh_run = IngestionRun(
        run_id="fresh-naver-005930",
        job_type="news",
        provider=NAVER_PROVIDER,
        target_scope={"ticker": "005930", "source_date": "2026-06-23"},
        status="started",
        input_hash="fresh-naver-005930-hash",
        started_at=now - timedelta(minutes=10),
        result_counts={},
    )
    other_ticker_stale_run = IngestionRun(
        run_id="stale-naver-000660",
        job_type="news",
        provider=NAVER_PROVIDER,
        target_scope={"ticker": "000660", "source_date": "2026-06-22"},
        status="started",
        input_hash="stale-naver-000660-hash",
        started_at=now - timedelta(hours=3),
        result_counts={},
    )
    seeded_session.add_all([stale_run, fresh_run, other_ticker_stale_run])
    seeded_session.commit()

    dry_run = reconcile_stale_started_runs(
        seeded_session,
        max_age_minutes=60,
        tickers=["005930"],
        providers=[NAVER_PROVIDER],
        now=now,
    )

    assert dry_run["ok"] is True
    assert dry_run["dry_run"] is True
    assert dry_run["stale_count"] == 1
    assert dry_run["updated_count"] == 0
    assert dry_run["stale_runs"][0]["run_id"] == "stale-naver-005930"
    assert dry_run["stale_runs"][0]["age_seconds"] == 10800
    seeded_session.refresh(stale_run)
    assert stale_run.status == "started"
    assert stale_run.completed_at is None
    assert stale_run.error_summary is None

    applied = reconcile_stale_started_runs(
        seeded_session,
        max_age_minutes=60,
        tickers=["005930"],
        providers=[NAVER_PROVIDER],
        dry_run=False,
        now=now,
    )

    assert applied["dry_run"] is False
    assert applied["stale_count"] == 1
    assert applied["updated_count"] == 1
    assert applied["stale_runs"][0]["status"] == "failed"
    seeded_session.refresh(stale_run)
    seeded_session.refresh(fresh_run)
    seeded_session.refresh(other_ticker_stale_run)
    assert stale_run.status == "failed"
    assert stale_run.completed_at is not None
    assert stale_run.completed_at.replace(tzinfo=timezone.utc) == now
    assert stale_run.error_summary == {
        "code": "stale_started_run_reconciled",
        "max_age_minutes": 60,
        "reconciled_at": "2026-06-23T09:00:00+00:00",
    }
    assert fresh_run.status == "started"
    assert other_ticker_stale_run.status == "started"


def test_provider_fallback_marks_partial_failed_without_persisting_rows(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
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


def test_opendart_financial_fallback_marks_partial_failed(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:available",
            data_status="available",
            status_code=200,
            payload={
                "ticker": ticker,
                "list": [
                    {
                        "rcept_no": "202606180099",
                        "report_nm": "주요사항보고서",
                        "rcept_dt": "20260618",
                    }
                ],
            },
        )

    def fake_list_financial_statements(
        self,
        *,
        ticker: str,
        corp_code=None,
        business_years: list[int],
        report_code: str = "11011",
    ):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/fnlttSinglAcntAll.json",
            cache_key=f"financials:{ticker}:fallback",
            data_status="fallback",
            status_code=200,
            payload={"ticker": ticker, "financial_statements": []},
            missing_data=[
                {
                    "provider": OPENDART_PROVIDER,
                    "field": "financial_statements",
                    "reason": "no_financial_statement_rows",
                }
            ],
        )

    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_disclosures",
        fake_list_disclosures,
    )
    monkeypatch.setattr(
        "app.services.ingestion.OpenDartClient.list_financial_statements",
        fake_list_financial_statements,
    )
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
    assert result["results"][0]["status"] == "partial_failed"
    assert result["results"][0]["result_counts"] == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }
    assert (
        result["results"][0]["error_summary"]["code"]
        == "opendart_partial_provider_fallback"
    )
    assert result["results"][0]["error_summary"]["missing_data"] == [
        {
            "provider": OPENDART_PROVIDER,
            "field": "financial_statements",
            "reason": "no_financial_statement_rows",
        }
    ]


def test_krx_ingestion_uses_short_code_when_isu_cd_is_isin(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_daily_trading(self, *, ticker: str, base_date: str, market: str = "KOSPI"):
        return ExternalApiResult(
            provider=KRX_PROVIDER,
            endpoint="/daily",
            cache_key=f"krx:{ticker}:{base_date}",
            data_status="available",
            status_code=200,
            payload={
                "ticker": ticker,
                "base_date": base_date,
                "OutBlock_1": [
                    {
                        "BAS_DD": base_date,
                        "ISU_CD": "KR7005930003",
                        "ISU_SRT_CD": ticker,
                        "TDD_CLSPRC": "71,000",
                        "ACC_TRDVOL": "1,234,567",
                        "ACC_TRDVAL": "87,654,321,000",
                        "MKTCAP": "420,000,000,000,000",
                        "FLUC_RT": "1.50",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.services.ingestion.KrxClient.daily_trading",
        fake_daily_trading,
    )
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(KRX_API_KEY="krx-secret"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=KRX_PROVIDER,
            tickers=["005930"],
            source_date="2026-06-18",
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "succeeded"
    assert result["results"][0]["result_counts"] == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }

    price = seeded_session.scalars(
        select(PriceMetric).where(
            PriceMetric.ticker == "005930",
            PriceMetric.trade_date == datetime(2026, 6, 18, tzinfo=timezone.utc).date(),
        )
    ).one()
    assert price.close_price == 71000
    assert price.source == KRX_PROVIDER


def test_persist_krx_stock_master_upserts_market_stock_rows(
    seeded_session: Session,
) -> None:
    seeded_session.query(Stock).filter(Stock.ticker == "123456").delete()
    seeded_session.commit()

    result = persist_krx_stock_master(
        seeded_session,
        market="KOSDAQ",
        payload={
            "base_date": "20260703",
            "OutBlock_1": [
                {
                    "BAS_DD": "20260703",
                    "ISU_SRT_CD": "123456",
                    "ISU_ABBRV": "테스트바이오",
                    "ISU_ENG_NM": "Test Bio",
                    "MKT_NM": "KOSDAQ",
                    "LIST_DD": "20240115",
                    "SECUGRP_NM": "주권",
                    "TDD_CLSPRC": "12,300",
                    "ACC_TRDVOL": "1,000",
                    "ACC_TRDVAL": "12,300,000",
                    "MKTCAP": "123,000,000,000",
                    "FLUC_RT": "2.5",
                }
            ]
        },
    )

    assert result == {"inserted": 1, "updated": 0, "skipped": 0}
    stock = seeded_session.get(Stock, "123456")
    assert stock is not None
    assert stock.company_name == "테스트바이오"
    assert stock.company_name_en == "Test Bio"
    assert stock.market == "KOSDAQ"
    assert stock.listing_date is not None
    assert stock.listing_date.isoformat() == "2024-01-15"
    price = seeded_session.scalars(
        select(PriceMetric).where(
            PriceMetric.ticker == "123456",
            PriceMetric.trade_date == datetime(2026, 7, 3, tzinfo=timezone.utc).date(),
        )
    ).one()
    assert price.close_price == 12300
    assert price.volume == 1000
    assert price.source == KRX_PROVIDER

    update_result = persist_krx_stock_master(
        seeded_session,
        market="KOSDAQ",
        payload={
            "base_date": "20260703",
            "OutBlock_1": [
                {
                    "BAS_DD": "20260703",
                    "ISU_CD": "123456",
                    "ISU_NM": "테스트바이오2",
                    "TDD_CLSPRC": "12,800",
                }
            ],
        },
    )

    assert update_result == {"inserted": 0, "updated": 1, "skipped": 0}
    assert seeded_session.get(Stock, "123456").company_name == "테스트바이오2"
    seeded_session.refresh(price)
    assert price.close_price == 12800


def test_persist_krx_stock_master_calculates_latest_20_day_price_metrics(
    seeded_session: Session,
) -> None:
    seeded_session.query(PriceMetric).filter(PriceMetric.ticker == "234567").delete()
    seeded_session.query(Stock).filter(Stock.ticker == "234567").delete()
    seeded_session.commit()

    for offset in range(21):
        trade_date = datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=offset)
        persist_krx_stock_master(
            seeded_session,
            market="KOSDAQ",
            payload={
                "base_date": trade_date.strftime("%Y%m%d"),
                "OutBlock_1": [
                    {
                        "BAS_DD": trade_date.strftime("%Y%m%d"),
                        "ISU_SRT_CD": "234567",
                        "ISU_ABBRV": "테스트지표",
                        "MKT_NM": "KOSDAQ",
                        "TDD_CLSPRC": str(100 + offset),
                        "ACC_TRDVOL": "1,000",
                        "ACC_TRDVAL": "100,000",
                        "MKTCAP": "1,000,000,000",
                    }
                ],
            },
        )

    latest = seeded_session.scalars(
        select(PriceMetric)
        .where(PriceMetric.ticker == "234567")
        .order_by(PriceMetric.trade_date.desc())
    ).first()

    assert latest is not None
    assert float(latest.momentum_20d) == pytest.approx(0.2)
    assert latest.volatility_20d is not None
    assert float(latest.volatility_20d) > 0


def test_provider_krx_prices_calculate_latest_20_day_price_metrics(
    seeded_session: Session,
) -> None:
    seeded_session.query(PriceMetric).filter(PriceMetric.ticker == "345678").delete()
    seeded_session.query(Stock).filter(Stock.ticker == "345678").delete()
    seeded_session.add(
        Stock(
            ticker="345678",
            company_name="테스트가격",
            market="KOSPI",
            is_active=True,
        )
    )
    seeded_session.commit()
    service = ProviderIngestionService(seeded_session)

    for offset in range(21):
        trade_date = datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=offset)
        service._persist_krx_prices(
            ticker="345678",
            result=ExternalApiResult(
                provider=KRX_PROVIDER,
                endpoint="/daily",
                cache_key=f"krx:345678:{trade_date:%Y%m%d}",
                data_status="available",
                status_code=200,
                payload={
                    "base_date": trade_date.strftime("%Y%m%d"),
                    "OutBlock_1": [
                        {
                            "BAS_DD": trade_date.strftime("%Y%m%d"),
                            "ISU_SRT_CD": "345678",
                            "TDD_CLSPRC": str(100 + offset),
                            "ACC_TRDVOL": "1,000",
                            "ACC_TRDVAL": "100,000",
                            "MKTCAP": "1,000,000,000",
                        }
                    ],
                },
            ),
            raw_archive_uri=None,
        )

    latest = seeded_session.scalars(
        select(PriceMetric)
        .where(PriceMetric.ticker == "345678")
        .order_by(PriceMetric.trade_date.desc())
    ).first()

    assert latest is not None
    assert float(latest.momentum_20d) == pytest.approx(0.2)
    assert latest.volatility_20d is not None
    assert float(latest.volatility_20d) > 0


def test_seed_krx_stock_universe_accepts_multiple_source_dates(
    monkeypatch,
    seeded_session: Session,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_daily_trading(
        self,
        *,
        ticker: str,
        base_date: str,
        market: str = "KOSPI",
        bypass_cache: bool = False,
    ):
        calls.append((base_date, market))
        return ExternalApiResult(
            provider=KRX_PROVIDER,
            endpoint="/daily",
            cache_key=f"krx:{market}:{base_date}",
            data_status="available",
            status_code=200,
            payload={
                "base_date": base_date,
                "OutBlock_1": [
                    {
                        "BAS_DD": base_date,
                        "ISU_SRT_CD": "345678",
                        "ISU_ABBRV": "테스트백필",
                        "MKT_NM": market,
                        "TDD_CLSPRC": "1,000",
                    }
                ],
            },
        )

    class ExistingSessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return seeded_session

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr("app.services.ingestion.KrxClient.daily_trading", fake_daily_trading)
    monkeypatch.setattr(
        "app.services.ingestion.get_session_factory",
        lambda: ExistingSessionFactory(),
    )

    result = ingestion_module.seed_krx_stock_universe_from_event(
        {
            "stockbrief_operation": "seed_krx_stock_universe",
            "source_dates": ["2026-07-01", "20260702", "20260702"],
            "markets": ["KOSPI"],
        }
    )

    assert result["ok"] is True
    assert result["source_dates"] == ["20260701", "20260702"]
    assert calls == [("20260701", "KOSPI"), ("20260702", "KOSPI")]


def test_krx_ingestion_marks_partial_failed_when_no_price_rows_persist(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_daily_trading(self, *, ticker: str, base_date: str, market: str = "KOSPI"):
        return ExternalApiResult(
            provider=KRX_PROVIDER,
            endpoint="/daily",
            cache_key=f"krx:{ticker}:{base_date}",
            data_status="available",
            status_code=200,
            payload={
                "ticker": ticker,
                "base_date": base_date,
                "OutBlock_1": [
                    {
                        "BAS_DD": base_date,
                        "ISU_CD": "KR7999990000",
                        "ISU_SRT_CD": "999999",
                        "TDD_CLSPRC": "1,000",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.services.ingestion.KrxClient.daily_trading",
        fake_daily_trading,
    )
    service = ProviderIngestionService(
        seeded_session,
        settings=Settings(KRX_API_KEY="krx-secret"),
        archiver=NoopPayloadArchiver(),
    )

    result = service.run_provider_batch(
        ProviderIngestionRequest(
            provider=KRX_PROVIDER,
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
    assert result["results"][0]["error_summary"] == {
        "code": "krx_price_rows_not_persisted",
        "result_counts": {
            "inserted": 0,
            "updated": 0,
            "skipped": 1,
        },
    }

    run = seeded_session.scalars(
        select(IngestionRun).where(
            IngestionRun.provider == KRX_PROVIDER,
            IngestionRun.target_scope["ticker"].as_string() == "005930",
        )
    ).one()
    assert run.status == "partial_failed"


def test_refresh_score_snapshots_uses_successful_krx_tickers_and_marks_partial_freshness(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_daily_trading(self, *, ticker: str, base_date: str, market: str = "KOSPI"):
        if ticker == "000660":
            return ExternalApiResult(
                provider=KRX_PROVIDER,
                endpoint="/daily",
                cache_key=f"krx:{ticker}:{base_date}",
                data_status="fallback",
                payload={"fallback": True, "ticker": ticker, "base_date": base_date, "OutBlock_1": []},
                missing_data=[
                    {
                        "provider": KRX_PROVIDER,
                        "field": "KRX_API_KEY",
                        "reason": "missing_api_key",
                        "data_status": "fallback",
                    }
                ],
            )
        return ExternalApiResult(
            provider=KRX_PROVIDER,
            endpoint="/daily",
            cache_key=f"krx:{ticker}:{base_date}",
            data_status="available",
            status_code=200,
            payload={
                "ticker": ticker,
                "base_date": base_date,
                "OutBlock_1": [
                    {
                        "BAS_DD": base_date,
                        "ISU_CD": ticker,
                        "TDD_CLSPRC": "70,000",
                        "ACC_TRDVOL": "1,234,567",
                        "ACC_TRDVAL": "86,419,690,000",
                        "MKTCAP": "417,000,000,000,000",
                        "FLUC_RT": "1.25",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.services.ingestion.KrxClient.daily_trading",
        fake_daily_trading,
    )

    class ExistingSessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return seeded_session

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "app.services.ingestion.get_session_factory",
        lambda: ExistingSessionFactory(),
    )

    result = handle_refresh_score_snapshots_event(
        {
            "stockbrief_operation": "refresh_score_snapshots",
            "provider": KRX_PROVIDER,
            "tickers": ["005930", "000660"],
            "source_date": "2026-06-09",
        },
    )

    assert result["ok"] is False
    assert result["provider_status"] == "partial_failed"
    assert result["successful_tickers"] == ["005930"]
    assert result["failed_tickers"] == ["000660"]
    assert result["refresh"]["processed"] == 1
    assert result["refresh"]["provider_freshness_annotated"] == 1

    price = seeded_session.scalars(
        select(PriceMetric).where(
            PriceMetric.ticker == "005930",
            PriceMetric.trade_date == datetime(2026, 6, 9, tzinfo=timezone.utc).date(),
        )
    ).one()
    assert price.source == KRX_PROVIDER
    assert price.close_price == 70000

    score = seeded_session.scalars(
        select(RecommendationScore).where(
            RecommendationScore.ticker == "005930",
            RecommendationScore.as_of_date == datetime(2026, 6, 9, tzinfo=timezone.utc).date(),
            RecommendationScore.score_version == SCORE_VERSION,
        )
    ).one()
    provider_freshness = score.data_freshness["providers"][KRX_PROVIDER]
    assert provider_freshness["status"] == "partial_failed"
    assert provider_freshness["successful_tickers"] == ["005930"]
    assert provider_freshness["failed_tickers"] == ["000660"]


def test_refresh_score_snapshots_batches_active_market_stocks(
    seeded_session: Session,
) -> None:
    for ticker, name in [("035900", "JYP Ent."), ("086520", "에코프로")]:
        if seeded_session.get(Stock, ticker) is None:
            seeded_session.add(
                Stock(
                    ticker=ticker,
                    company_name=name,
                    market="KOSDAQ",
                    is_active=True,
                )
            )
    seeded_session.commit()

    result = ingestion_module.refresh_score_snapshots(
        seeded_session,
        {
            "stockbrief_operation": "refresh_score_snapshots",
            "source_date": "2026-07-03",
            "markets": ["KOSDAQ"],
            "stock_limit": 1,
            "stock_offset": 0,
        },
    )

    assert result["ok"] is True
    assert result["batch"]["markets"] == ["KOSDAQ"]
    assert result["batch"]["selected_count"] == 1
    assert result["refresh"]["processed"] == 1
    selected_ticker = result["successful_tickers"][0]
    score = seeded_session.scalars(
        select(RecommendationScore).where(
            RecommendationScore.ticker == selected_ticker,
            RecommendationScore.as_of_date == datetime(2026, 7, 3, tzinfo=timezone.utc).date(),
            RecommendationScore.score_version == SCORE_VERSION,
        )
    ).one()
    assert score.total_score is not None


def test_persist_failure_rolls_back_normalized_rows_before_marking_failed(
    monkeypatch,
    seeded_session: Session,
) -> None:
    def fake_list_disclosures(
        self,
        *,
        ticker: str,
        corp_code=None,
        page_count: int = 10,
        bgn_de=None,
        end_de=None,
    ):
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint="/list.json",
            cache_key=f"disclosures:{ticker}:sample:{page_count}",
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
            "KRX_API_KEY": "krx-secret",
            "KRX_KOSPI_DAILY_URL": "https://krx.example/kospi",
            "KRX_KOSDAQ_DAILY_URL": "https://krx.example/kosdaq",
            "KRX_API_KEY_HEADER": "X-KRX-KEY",
        },
    )

    settings = hydrate_external_api_settings(
        Settings(EXTERNAL_API_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-2:123:secret:external")
    )

    assert settings.opendart_api_key == "opendart-secret"
    assert settings.naver_client_id == "naver-id"
    assert settings.naver_client_secret == "naver-secret"
    assert settings.krx_api_key == "krx-secret"
    assert settings.krx_kospi_daily_url == "https://krx.example/kospi"
    assert settings.krx_kosdaq_daily_url == "https://krx.example/kosdaq"
    assert settings.krx_api_key_header == "X-KRX-KEY"


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
        KRX_PROVIDER: {
            "api_key_configured": False,
            "kospi_daily_url_configured": True,
            "kosdaq_daily_url_configured": True,
        },
    }
    assert result["checks"]["network"]["outbound_internet_egress_verified"] is False
    assert result["issues"] == [
        {"code": "missing_external_api_secret_arn", "field": "EXTERNAL_API_SECRET_ARN"},
        {"code": "missing_ingestion_raw_bucket", "field": "INGESTION_RAW_BUCKET"},
        {"code": "missing_provider_credential", "field": "OPENDART_API_KEY"},
        {"code": "missing_provider_credential", "field": "NAVER_CLIENT_ID"},
        {"code": "missing_provider_credential", "field": "NAVER_CLIENT_SECRET"},
        {"code": "missing_provider_credential", "field": "KRX_API_KEY"},
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
            "KRX_API_KEY": "krx-secret",
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
    assert "krx-secret" not in serialized


def test_check_ingestion_readiness_scopes_provider_requirements(monkeypatch) -> None:
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
        ),
        providers=[OPENDART_PROVIDER, NAVER_PROVIDER],
    )

    assert result["ok"] is True
    assert result["issues"] == []
    assert set(result["checks"]["providers"]) == {OPENDART_PROVIDER, NAVER_PROVIDER}
    assert KRX_PROVIDER not in result["checks"]["providers"]


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

    result = check_provider_egress(
        transport=fake_transport,
        settings=Settings(
            KRX_KOSPI_DAILY_URL="https://krx.example/kospi",
            KRX_KOSDAQ_DAILY_URL="https://krx.example/kosdaq",
        ),
    )

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["checks"]["providers"][OPENDART_PROVIDER]["reachable"] is True
    assert result["checks"]["providers"][NAVER_PROVIDER]["reachable"] is True
    assert result["checks"]["providers"][KRX_PROVIDER]["reachable"] is True
    assert result["checks"]["providers"][KRX_PROVIDER]["markets"]["KOSPI"]["endpoint"] == (
        "https://krx.example/kospi"
    )
    assert result["checks"]["providers"][KRX_PROVIDER]["markets"]["KOSDAQ"]["endpoint"] == (
        "https://krx.example/kosdaq"
    )
    assert [call.method for call in calls] == ["GET", "GET", "GET", "GET"]
    assert [call.url for call in calls][-2:] == [
        "https://krx.example/kospi",
        "https://krx.example/kosdaq",
    ]
    assert all(call.headers == {} for call in calls)
    assert all(call.timeout_seconds == 3.0 for call in calls)


def test_check_provider_egress_empty_provider_list_defaults_to_supported_providers() -> None:
    calls: list[ExternalRequest] = []

    def fake_transport(request: ExternalRequest) -> ExternalResponse:
        calls.append(request)
        return ExternalResponse(status_code=401, payload={})

    result = check_provider_egress(
        {"providers": []},
        transport=fake_transport,
        settings=Settings(
            KRX_KOSPI_DAILY_URL="https://krx.example/kospi",
            KRX_KOSDAQ_DAILY_URL="https://krx.example/kosdaq",
        ),
    )

    assert result["ok"] is True
    assert result["issues"] == []
    assert set(result["checks"]["providers"]) == {OPENDART_PROVIDER, NAVER_PROVIDER, KRX_PROVIDER}
    assert len(calls) == 4


def test_check_provider_egress_requires_kosdaq_krx_endpoint() -> None:
    result = check_provider_egress(
        {"providers": [KRX_PROVIDER]},
        transport=lambda _request: ExternalResponse(status_code=401, payload={}),
        settings=Settings(
            KRX_KOSPI_DAILY_URL="https://krx.example/kospi",
            KRX_KOSDAQ_DAILY_URL="",
        ),
    )

    assert result["ok"] is False
    assert result["checks"]["providers"][KRX_PROVIDER]["reachable"] is False
    assert result["checks"]["providers"][KRX_PROVIDER]["markets"]["KOSPI"]["reachable"] is True
    assert result["checks"]["providers"][KRX_PROVIDER]["markets"]["KOSDAQ"] == {
        "reachable": False,
        "endpoint": None,
        "status_code": None,
        "error_code": "missing_provider_endpoint",
        "note": "Provider endpoint is not configured.",
    }
    assert result["issues"] == [
        {
            "code": "missing_provider_endpoint",
            "provider": KRX_PROVIDER,
            "field": "KRX_KOSDAQ_DAILY_URL",
        }
    ]


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


def test_check_provider_egress_treats_non_json_response_as_reachable() -> None:
    def fake_transport(_request: ExternalRequest) -> ExternalResponse:
        raise json.JSONDecodeError("Expecting value", "<html>", 0)

    result = check_provider_egress({"provider": OPENDART_PROVIDER}, transport=fake_transport)

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["checks"]["providers"] == {
        OPENDART_PROVIDER: {
            "reachable": True,
            "endpoint": "https://opendart.fss.or.kr/api/list.json",
            "status_code": None,
            "error_code": "JSONDecodeError",
            "note": "Provider endpoint returned a non-JSON HTTP response.",
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


def test_check_ingestion_scheduler_enable_gate_blocks_until_all_checks_pass(monkeypatch) -> None:
    readiness_calls = []

    def fake_readiness(*, providers=None):
        readiness_calls.append(providers)
        return {"ok": False, "issues": [{"code": "missing_provider_credential"}]}

    monkeypatch.setattr(
        "app.services.ingestion.check_ingestion_readiness",
        fake_readiness,
    )
    monkeypatch.setattr(
        "app.services.ingestion.check_raw_archive_write",
        lambda: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "app.services.ingestion.check_provider_egress",
        lambda event: {"ok": False, "issues": [{"code": "provider_egress_unreachable"}]},
    )
    monkeypatch.setattr(
        "app.services.ingestion.get_ingestion_status",
        lambda event: {"ok": True, "summary": {"recent_run_count": 0}, "recent_runs": []},
    )
    monkeypatch.setattr(
        "app.services.ingestion.reconcile_stale_ingestion_runs",
        lambda event: {"ok": True, "dry_run": True, "stale_count": 1, "issues": []},
    )

    result = check_ingestion_scheduler_enable_gate(
        {"providers": [OPENDART_PROVIDER], "tickers": ["005930"], "limit": 3}
    )

    assert result["ok"] is False
    assert result["scheduler_enable_ready"] is False
    assert result["providers"] == [OPENDART_PROVIDER]
    assert result["tickers"] == ["005930"]
    assert readiness_calls == [[OPENDART_PROVIDER]]
    assert result["blockers"] == [
        {
            "code": "readiness_not_ready",
            "check": "readiness",
            "issues": [{"code": "missing_provider_credential"}],
        },
        {
            "code": "provider_egress_not_ready",
            "check": "provider_egress",
            "issues": [{"code": "provider_egress_unreachable"}],
        },
        {
            "code": "manual_ingestion_smoke_missing",
            "check": "status",
            "missing_runs": [{"provider": OPENDART_PROVIDER, "ticker": "005930"}],
        },
        {
            "code": "stale_ingestion_runs_present",
            "check": "stale_runs",
            "stale_count": 1,
        },
    ]


def test_check_ingestion_scheduler_enable_gate_requires_successful_manual_smoke(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ingestion.check_ingestion_readiness",
        lambda **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "app.services.ingestion.check_raw_archive_write",
        lambda: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "app.services.ingestion.check_provider_egress",
        lambda event: {"ok": True, "issues": [], "checks": {"providers": {}}},
    )
    monkeypatch.setattr(
        "app.services.ingestion.get_ingestion_status",
        lambda event: {
            "ok": True,
            "summary": {"recent_run_count": 1},
            "recent_runs": [
                {
                    "provider": OPENDART_PROVIDER,
                    "ticker": "005930",
                    "status": "partial_failed",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.ingestion.reconcile_stale_ingestion_runs",
        lambda event: {"ok": True, "dry_run": True, "stale_count": 0, "issues": []},
    )

    result = check_ingestion_scheduler_enable_gate(
        {"providers": [OPENDART_PROVIDER], "tickers": ["005930"]}
    )

    assert result["ok"] is False
    assert result["scheduler_enable_ready"] is False
    assert result["blockers"] == [
        {
            "code": "manual_ingestion_smoke_missing",
            "check": "status",
            "missing_runs": [{"provider": OPENDART_PROVIDER, "ticker": "005930"}],
        }
    ]


def test_check_ingestion_scheduler_enable_gate_passes_when_all_checks_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ingestion.check_ingestion_readiness",
        lambda **_kwargs: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "app.services.ingestion.check_raw_archive_write",
        lambda: {"ok": True, "issues": []},
    )
    monkeypatch.setattr(
        "app.services.ingestion.check_provider_egress",
        lambda event: {"ok": True, "issues": [], "checks": {"providers": {}}},
    )
    monkeypatch.setattr(
        "app.services.ingestion.get_ingestion_status",
        lambda event: {
            "ok": True,
            "summary": {"recent_run_count": 2},
            "recent_runs": [
                {
                    "provider": OPENDART_PROVIDER,
                    "ticker": "005930",
                    "status": "succeeded",
                },
                {
                    "provider": NAVER_PROVIDER,
                    "ticker": "005930",
                    "status": "succeeded",
                },
                {
                    "provider": KRX_PROVIDER,
                    "ticker": "005930",
                    "status": "succeeded",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.ingestion.reconcile_stale_ingestion_runs",
        lambda event: {"ok": True, "dry_run": True, "stale_count": 0, "issues": []},
    )

    result = check_ingestion_scheduler_enable_gate({})

    assert result["ok"] is True
    assert result["scheduler_enable_ready"] is True
    assert result["providers"] == [OPENDART_PROVIDER, NAVER_PROVIDER, KRX_PROVIDER]
    assert result["tickers"] == ["005930"]
    assert result["blockers"] == []
