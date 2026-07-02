from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts/check_recommendation_quality_smoke.py"


spec = importlib.util.spec_from_file_location("check_recommendation_quality_smoke", SCRIPT_PATH)
assert spec is not None
smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


class FakeFetcher:
    def __init__(
        self,
        *,
        tickers: tuple[str, ...] = ("005930",),
        weak_detail: bool = False,
        missing_evidence_source_metadata: bool = False,
        score_evidence_without_url: bool = False,
        score_evidence_without_metadata: bool = False,
    ) -> None:
        self.tickers = tickers
        self.weak_detail = weak_detail
        self.missing_evidence_source_metadata = missing_evidence_source_metadata
        self.score_evidence_without_url = score_evidence_without_url
        self.score_evidence_without_metadata = score_evidence_without_metadata
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_seconds: float):
        self.calls.append((url, timeout_seconds))
        if "/stocks/candidates?" in url:
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "success": True,
                        "data": {
                            "as_of": "2026-06-09",
                            "items": [
                                {
                                    "ticker": ticker,
                                    "name": f"테스트 종목 {ticker}",
                                    "evidence_summary": {
                                        "news_count": 2,
                                        "disclosure_count": 1,
                                        "latest_at": "2026-06-26T03:48:00Z",
                                    },
                                }
                                for ticker in self.tickers
                            ],
                        },
                    }
                ).encode("utf-8"),
            )
        ticker = self._ticker_from_url(url, "/stocks/candidates/")
        if ticker:
            evidence_count = 1 if self.weak_detail else 3
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "ticker": ticker,
                        "evidence_level": "medium",
                        "evidence_count": evidence_count,
                        "risk_tags": ["sector_cycle"],
                        "missing_data": [],
                        "data_freshness": {"as_of": "2026-06-09"},
                        "recommendation_reasons": [
                            {
                                "reason_id": "rsn_1",
                                "summary": "공개 데이터 기준 검토 포인트가 확인됩니다.",
                            }
                        ],
                    }
                ).encode("utf-8"),
            )
        ticker = self._ticker_from_evidence_url(url)
        if ticker:
            second_item = {
                "id": "ev_2",
                "source_type": "DISCLOSURE",
                "source_name": "OpenDART",
                "url": "https://provider.example/disclosure/private-title",
                "published_at": "2026-06-26T03:40:00Z",
                "title": "두 번째 원문 제목",
                "snippet": "두 번째 원문 요약",
            }
            if self.missing_evidence_source_metadata:
                second_item = {
                    "id": "ev_2",
                    "source_type": "",
                    "title": "두 번째 원문 제목",
                    "snippet": "두 번째 원문 요약",
                }
            if self.score_evidence_without_url:
                second_item = {
                    "id": "price_005930_2026-06-09",
                    "source_type": "SCORE",
                    "source_name": "KRX_FALLBACK_MOCK",
                    "url": None,
                    "published_at": None,
                    "title": "가격 지표 fallback mock",
                    "snippet": "가격 지표 요약",
                    "metadata": {
                        "source_identifier": "KRX_FALLBACK_MOCK:005930:2026-06-09",
                        "as_of_date": "2026-06-09",
                        "data_status": "fallback",
                    },
                }
            if self.score_evidence_without_metadata:
                second_item = {
                    "id": "price_005930_2026-06-09",
                    "source_type": "SCORE",
                    "source_name": "KRX_FALLBACK_MOCK",
                    "url": None,
                    "published_at": None,
                    "title": "가격 지표 fallback mock",
                    "snippet": "가격 지표 요약",
                    "metadata": {"data_status": "fallback"},
                }
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "success": True,
                        "data": {
                            "ticker": ticker,
                            "items": [
                                {
                                    "id": "ev_1",
                                    "source_type": "NEWS",
                                    "source_name": "NAVER_NEWS",
                                    "url": "https://provider.example/news/private-title",
                                    "published_at": "2026-06-26T03:48:00Z",
                                    "title": "원문 제목은 smoke 결과에 남기지 않습니다.",
                                    "snippet": "원문 요약도 smoke 결과에 남기지 않습니다.",
                                },
                                second_item,
                            ],
                        },
                    }
                ).encode("utf-8"),
            )
        return smoke.HttpResponse(status_code=404, body=b"{}")

    @staticmethod
    def _ticker_from_url(url: str, marker: str) -> str:
        if marker not in url:
            return ""
        return url.rsplit(marker, 1)[1].split("?", 1)[0].strip("/")

    @staticmethod
    def _ticker_from_evidence_url(url: str) -> str:
        marker = "/stocks/"
        if marker not in url or not url.endswith("/evidence"):
            return ""
        return url.rsplit(marker, 1)[1].removesuffix("/evidence")


def test_recommendation_quality_smoke_passes_with_list_detail_and_evidence() -> None:
    fetcher = FakeFetcher()

    result = smoke.run_smoke(
        api_base_url="https://api.example.com",
        ticker="",
        limit=3,
        max_detail_tickers=3,
        min_evidence_count=2,
        timeout_seconds=2,
        fetch=fetcher,
    )

    assert result["ok"] is True
    assert result["selected_ticker"] == "005930"
    assert result["selected_tickers"] == ["005930"]
    assert result["checks"]["candidate_list"]["summary"] == {
        "count": 1,
        "first_ticker": "005930",
        "tickers": ["005930"],
        "as_of": "2026-06-09",
    }
    assert result["checks"]["candidate_detail"]["summary"]["evidence_count"] == 3
    assert result["checks"]["stock_evidence"]["summary"] == {
        "ticker": "005930",
        "evidence_count": 2,
        "source_types": ["DISCLOSURE", "NEWS"],
        "items_with_source_type": 2,
        "items_with_source_name": 2,
        "items_with_url": 2,
        "items_with_published_at": 2,
        "provider_evidence_count": 2,
        "provider_items_with_url": 2,
        "provider_items_with_published_at": 2,
        "internal_evidence_count": 0,
        "internal_items_with_source_identifier": 0,
        "internal_items_with_as_of_date": 0,
    }
    assert [url for url, _ in fetcher.calls] == [
        "https://api.example.com/v1/stocks/candidates?limit=3",
        "https://api.example.com/v1/stocks/candidates/005930",
        "https://api.example.com/v1/stocks/005930/evidence",
    ]


def test_recommendation_quality_smoke_checks_multiple_listed_tickers() -> None:
    fetcher = FakeFetcher(tickers=("005930", "000660", "035420"))

    result = smoke.run_smoke(
        api_base_url="https://api.example.com",
        ticker="",
        limit=3,
        max_detail_tickers=2,
        min_evidence_count=2,
        timeout_seconds=2,
        fetch=fetcher,
    )

    assert result["ok"] is True
    assert result["selected_ticker"] == "005930"
    assert result["selected_tickers"] == ["005930", "000660"]
    assert result["checks"]["candidate_list"]["summary"]["tickers"] == [
        "005930",
        "000660",
        "035420",
    ]
    assert result["checks"]["candidate_detail:005930"]["summary"]["ticker"] == "005930"
    assert result["checks"]["stock_evidence:005930"]["summary"]["ticker"] == "005930"
    assert result["checks"]["candidate_detail:000660"]["summary"]["ticker"] == "000660"
    assert result["checks"]["stock_evidence:000660"]["summary"]["ticker"] == "000660"
    assert [url for url, _ in fetcher.calls] == [
        "https://api.example.com/v1/stocks/candidates?limit=3",
        "https://api.example.com/v1/stocks/candidates/005930",
        "https://api.example.com/v1/stocks/005930/evidence",
        "https://api.example.com/v1/stocks/candidates/000660",
        "https://api.example.com/v1/stocks/000660/evidence",
    ]


def test_recommendation_quality_smoke_reports_structured_blockers() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        ticker="005930",
        limit=3,
        max_detail_tickers=3,
        min_evidence_count=2,
        timeout_seconds=2,
        fetch=FakeFetcher(weak_detail=True),
    )

    assert result["ok"] is False
    assert {
        "check": "candidate_detail",
        "code": "detail_evidence_below_minimum",
        "evidence_count": 1,
        "min_evidence_count": 2,
    } in result["blockers"]


def test_recommendation_quality_smoke_fails_when_evidence_source_metadata_is_partial() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        ticker="005930",
        limit=3,
        max_detail_tickers=3,
        min_evidence_count=2,
        timeout_seconds=2,
        fetch=FakeFetcher(missing_evidence_source_metadata=True),
    )

    assert result["ok"] is False
    assert {
        "check": "stock_evidence",
        "code": "evidence_item_missing_source_metadata",
        "item_index": 1,
        "evidence_id": "ev_2",
        "missing_fields": ["source_type", "source_name", "url", "published_at"],
    } in result["blockers"]
    assert result["checks"]["stock_evidence"]["summary"] == {
        "ticker": "005930",
        "evidence_count": 2,
        "source_types": ["NEWS"],
        "items_with_source_type": 1,
        "items_with_source_name": 1,
        "items_with_url": 1,
        "items_with_published_at": 1,
        "provider_evidence_count": 2,
        "provider_items_with_url": 1,
        "provider_items_with_published_at": 1,
        "internal_evidence_count": 0,
        "internal_items_with_source_identifier": 0,
        "internal_items_with_as_of_date": 0,
    }


def test_recommendation_quality_smoke_accepts_score_evidence_without_public_url() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        ticker="005930",
        limit=3,
        max_detail_tickers=3,
        min_evidence_count=2,
        timeout_seconds=2,
        fetch=FakeFetcher(score_evidence_without_url=True),
    )

    assert result["ok"] is True
    assert result["checks"]["stock_evidence"]["summary"] == {
        "ticker": "005930",
        "evidence_count": 2,
        "source_types": ["NEWS", "SCORE"],
        "items_with_source_type": 2,
        "items_with_source_name": 2,
        "items_with_url": 1,
        "items_with_published_at": 1,
        "provider_evidence_count": 1,
        "provider_items_with_url": 1,
        "provider_items_with_published_at": 1,
        "internal_evidence_count": 1,
        "internal_items_with_source_identifier": 1,
        "internal_items_with_as_of_date": 1,
    }


def test_recommendation_quality_smoke_requires_score_evidence_metadata() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com/v1",
        ticker="005930",
        limit=3,
        max_detail_tickers=3,
        min_evidence_count=2,
        timeout_seconds=2,
        fetch=FakeFetcher(score_evidence_without_metadata=True),
    )

    assert result["ok"] is False
    assert {
        "check": "stock_evidence",
        "code": "evidence_item_missing_source_metadata",
        "item_index": 1,
        "evidence_id": "price_005930_2026-06-09",
        "missing_fields": ["metadata.source_identifier", "metadata.as_of_date"],
    } in result["blockers"]


def test_recommendation_quality_smoke_does_not_print_raw_provider_text() -> None:
    result = smoke.run_smoke(
        api_base_url="https://api.example.com",
        ticker="005930",
        fetch=FakeFetcher(),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "원문 제목" not in serialized
    assert "원문 요약" not in serialized
    assert "provider.example" not in serialized
