from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.orm import (
    EvidenceChunk,
    RecommendationReason,
    RecommendationScore,
    RiskSignal,
    SourceDocument,
)
from app.services.candidate_service import CandidateService
from app.services.recommendation.materializer import materialize_recommendation_scores


PROHIBITED_KOREAN_TERMS = [
    "매수",
    "매도",
    "목표가",
    "진입가",
    "손절가",
    "수익 보장",
]


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(_flatten_text(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def _assert_candidate_shape(candidate: dict[str, Any]) -> None:
    assert {
        "ticker",
        "name",
        "market",
        "sector",
        "recommendation_score",
        "score_components",
        "recommendation_reasons",
        "risk_tags",
        "evidence_level",
        "evidence_count",
        "missing_data",
        "data_freshness",
        "disclaimer",
    }.issubset(candidate)
    assert len(candidate["score_components"]) == 8
    assert 0 <= candidate["recommendation_score"] <= 100
    assert candidate["evidence_count"] >= 2
    assert candidate["evidence_level"] in {"strong", "medium", "weak"}
    assert candidate["disclaimer"] == "공개 데이터 기반 검토 후보이며 최종 투자 판단은 사용자에게 있습니다."


def _replace_live_evidence_chunks(
    seeded_session: Session,
    *,
    ticker: str,
    published_at: datetime,
) -> int:
    seeded_session.execute(delete(EvidenceChunk).where(EvidenceChunk.ticker == ticker))
    live_sources = [
        {
            "source_type": "news",
            "source_name": "NAVER_NEWS",
            "external_id": f"live-news-{ticker}",
            "evidence_id": f"ev_live_news_{ticker}",
            "source_url": "https://news.example/live",
            "evidence_type": "news",
        },
        {
            "source_type": "disclosure",
            "source_name": "OpenDART",
            "external_id": f"live-disclosure-{ticker}",
            "evidence_id": f"ev_live_disclosure_{ticker}",
            "source_url": "https://dart.example/live",
            "evidence_type": "disclosure",
        },
    ]
    for item in live_sources:
        source = SourceDocument(
            ticker=ticker,
            source_type=item["source_type"],
            source_name=item["source_name"],
            source_url=item["source_url"],
            external_id=item["external_id"],
            title=f"{item['evidence_type']} live evidence",
            published_at=published_at,
            fetched_at=published_at,
            content_hash=item["external_id"],
            raw_content="{}",
            metadata_={"provider": item["source_name"]},
        )
        seeded_session.add(source)
        seeded_session.flush()
        seeded_session.add(
            EvidenceChunk(
                evidence_id=item["evidence_id"],
                ticker=ticker,
                source_document_id=source.id,
                evidence_type=item["evidence_type"],
                chunk_text="live evidence summary",
                source_url=source.source_url,
                published_at=published_at,
                fetched_at=published_at,
                confidence=Decimal("0.9000"),
                metadata_={"provider": item["source_name"]},
            )
        )
    return len(live_sources)


def test_list_recommendation_candidates_from_seed(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/recommendations/candidates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_profile"] == "balanced"
    assert payload["count"] == 10
    assert len(payload["items"]) == 10
    _assert_candidate_shape(payload["items"][0])


def test_recommendation_candidates_bulk_loads_related_data(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    engine = seeded_session.get_bind()
    statements: list[str] = []

    def count_statement(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        response = seeded_api_client.get(
            "/v1/recommendations/candidates",
            params={"limit": 20},
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    assert response.json()["items"]
    assert len(statements) <= 5


def test_recommendation_candidates_hydrates_only_limited_rows(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    import app.services.candidate_service as candidate_module

    original = candidate_module._candidate_response_from_loaded
    loaded_tickers: list[str] = []

    def wrapped_response_from_loaded(**kwargs: Any) -> Any:
        loaded_tickers.append(kwargs["stock"].ticker)
        return original(**kwargs)

    monkeypatch.setattr(
        candidate_module,
        "_candidate_response_from_loaded",
        wrapped_response_from_loaded,
    )

    response = seeded_api_client.get(
        "/v1/recommendations/candidates",
        params={"limit": 3},
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 3
    assert len(loaded_tickers) == 3


def test_list_recommendation_candidates_does_not_require_risk_signal(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    seeded_session.execute(delete(RiskSignal).where(RiskSignal.ticker == "005930"))
    seeded_session.commit()

    response = seeded_api_client.get(
        "/v1/recommendations/candidates",
        params={"limit": 100},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert any(item["ticker"] == "005930" for item in items)


def test_list_recommendation_candidates_requires_live_evidence(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    seeded_session.execute(delete(EvidenceChunk).where(EvidenceChunk.ticker == "005930"))
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.evidence_count = 120
    score.is_candidate_eligible = True
    seeded_session.commit()

    response = seeded_api_client.get(
        "/v1/recommendations/candidates",
        params={"limit": 100},
    )

    assert response.status_code == 200
    tickers = [item["ticker"] for item in response.json()["items"]]
    assert "005930" not in tickers


def test_list_recommendation_candidates_filters_and_limits(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get(
        "/v1/recommendations/candidates",
        params={
            "risk_profile": "conservative",
            "market": "KOSPI",
            "sector": "반도체",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_profile"] == "conservative"
    assert payload["count"] == 1
    assert payload["items"][0]["market"] == "KOSPI"
    assert payload["items"][0]["sector"] == "반도체"


def test_get_recommendation_candidate_detail(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/recommendations/candidates/005930")

    assert response.status_code == 200
    candidate = response.json()
    _assert_candidate_shape(candidate)
    assert candidate["ticker"] == "005930"
    assert candidate["name"] == "삼성전자"
    assert candidate["recommendation_reasons"]
    assert candidate["risk_tags"]


def test_candidate_api_defaults_to_current_score_version(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    current = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    seeded_session.add(
        RecommendationScore(
            ticker="005930",
            as_of_date=date(2026, 6, 10),
            score_version="selection-test",
            total_score=Decimal("12.34"),
            evidence_level="medium",
            component_scores=current.component_scores,
            evidence_count=2,
            missing_data=["selection.inputs"],
            data_freshness={"as_of": "2026-06-10"},
            is_candidate_eligible=True,
        )
    )
    seeded_session.add(
        RiskSignal(
            ticker="005930",
            as_of_date=date(2026, 6, 10),
            risk_tag="selection_risk",
            severity="low",
            penalty_points=Decimal("0.00"),
            display_text="선택 규칙 확인용 리스크입니다.",
            description="선택 규칙 확인용 리스크입니다.",
            evidence_ids=[],
        )
    )
    seeded_session.commit()

    detail_response = seeded_api_client.get("/v1/recommendations/candidates/005930")
    list_response = seeded_api_client.get(
        "/v1/recommendations/candidates",
        params={"limit": 100},
    )

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["recommendation_score"] == float(current.total_score)
    assert detail["data_freshness"]["as_of"] == current.data_freshness["as_of"]

    assert list_response.status_code == 200
    items = list_response.json()["items"]
    selected = [item for item in items if item["ticker"] == "005930"]
    assert len(selected) == 1
    assert selected[0]["recommendation_score"] == float(current.total_score)


def test_candidate_service_prefers_requested_score_version(
    seeded_session: Session,
) -> None:
    current = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    seeded_session.add(
        RecommendationScore(
            ticker="005930",
            as_of_date=date(2026, 6, 10),
            score_version="selection-test-version",
            total_score=Decimal("12.34"),
            evidence_level=current.evidence_level,
            component_scores=current.component_scores,
            evidence_count=current.evidence_count,
            missing_data=current.missing_data,
            data_freshness={"as_of": "2026-06-10"},
            is_candidate_eligible=True,
        )
    )
    seeded_session.commit()

    _, selected = CandidateService(seeded_session).candidate_row(
        "005930",
        score_version=current.score_version,
    )

    assert selected.id == current.id


def test_candidate_api_exposes_weak_materialized_score_details(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    seeded_session.execute(delete(EvidenceChunk).where(EvidenceChunk.ticker == "005930"))
    materialize_recommendation_scores(
        seeded_session,
        as_of_date=date(2026, 6, 10),
        tickers=["005930"],
    )
    seeded_session.commit()

    response = seeded_api_client.get("/v1/recommendations/candidates/005930")

    assert response.status_code == 200
    candidate = response.json()
    assert candidate["evidence_level"] == "weak"
    assert candidate["evidence_count"] == 0
    assert "news_attention.inputs" in candidate["missing_data"]
    assert "disclosure_event.inputs" in candidate["missing_data"]
    assert candidate["data_freshness"]["as_of"] == "2026-06-10"


def test_get_stock_score(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/stocks/005930/score")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "005930"
    assert len(payload["score_components"]) == 8
    assert 0 <= payload["recommendation_score"] <= 100
    assert payload["evidence_level"] == "medium"


def test_recommendation_and_score_overlay_live_evidence_freshness(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    published_at = datetime(2026, 6, 22, 6, 16, tzinfo=timezone.utc)
    live_count = _replace_live_evidence_chunks(
        seeded_session,
        ticker="005930",
        published_at=published_at,
    )
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.evidence_count = 1
    seeded_session.commit()

    candidate_response = seeded_api_client.get("/v1/recommendations/candidates/005930")
    score_response = seeded_api_client.get("/v1/stocks/005930/score")

    assert candidate_response.status_code == 200
    assert score_response.status_code == 200
    for payload in [candidate_response.json(), score_response.json()]:
        assert payload["evidence_count"] == live_count
        assert payload["data_freshness"]["live_evidence_latest_at"] == published_at.isoformat()


def test_recommendation_and_evidence_api_hide_legacy_mock_evidence(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    published_at = datetime(2026, 6, 22, 6, 16, tzinfo=timezone.utc)
    mock_published_at = datetime(2026, 6, 23, 6, 16, tzinfo=timezone.utc)
    live_count = _replace_live_evidence_chunks(
        seeded_session,
        ticker="005930",
        published_at=published_at,
    )
    mock_source = SourceDocument(
        ticker="005930",
        source_type="news",
        source_name="NAVER_NEWS",
        source_url="https://news.example/mock",
        external_id="legacy-mock-news-api",
        title="legacy mock evidence",
        published_at=mock_published_at,
        fetched_at=mock_published_at,
        content_hash="legacy-mock-news-api",
        raw_content="{}",
        metadata_={"provider": "NAVER_NEWS"},
    )
    seeded_session.add(mock_source)
    seeded_session.flush()
    seeded_session.add(
        EvidenceChunk(
            evidence_id="ev_mock_005930_news_api",
            ticker="005930",
            source_document_id=mock_source.id,
            evidence_type="news",
            chunk_text="legacy mock evidence should stay hidden",
            source_url=mock_source.source_url,
            published_at=mock_published_at,
            fetched_at=mock_published_at,
            confidence=Decimal("0.9900"),
            metadata_={"provider": "NAVER_NEWS"},
        )
    )
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.evidence_count = 1
    component_scores = [dict(component) for component in score.component_scores]
    component_scores[0] = {
        **component_scores[0],
        "evidence_ids": ["ev_mock_005930_news_api", "ev_live_news_005930"],
    }
    score.component_scores = component_scores
    seeded_session.add(
        RecommendationReason(
            reason_id="rsn_005930_legacy_mock_api",
            recommendation_score_id=score.id,
            ticker="005930",
            component="news_attention",
            summary="legacy mock references should stay hidden",
            evidence_ids=["ev_mock_005930_news_api", "ev_live_news_005930"],
            source_document_ids=[str(mock_source.id), "live-source"],
        )
    )
    seeded_session.commit()

    candidate_response = seeded_api_client.get("/v1/recommendations/candidates/005930")
    evidence_response = seeded_api_client.get(
        "/v1/stocks/005930/evidence",
        params={"source_type": "NEWS", "limit": 20},
    )

    assert candidate_response.status_code == 200
    candidate = candidate_response.json()
    assert candidate["evidence_count"] == live_count
    assert candidate["data_freshness"]["live_evidence_latest_at"] == published_at.isoformat()
    assert "ev_mock_005930_news_api" not in _flatten_text(candidate)
    assert "ev_live_news_005930" in _flatten_text(candidate)

    assert evidence_response.status_code == 200
    evidence_ids = [item["id"] for item in evidence_response.json()["data"]["items"]]
    assert "ev_mock_005930_news_api" not in evidence_ids


def test_recommendation_endpoints_do_not_emit_prohibited_korean_terms(
    seeded_api_client: TestClient,
) -> None:
    responses = [
        seeded_api_client.get("/v1/recommendations/candidates").json(),
        seeded_api_client.get("/v1/recommendations/candidates/005930").json(),
        seeded_api_client.get("/v1/stocks/005930/score").json(),
    ]
    text = _flatten_text(responses)

    for term in PROHIBITED_KOREAN_TERMS:
        assert term not in text


def test_recommendation_openapi_documents_response_models(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert (
        "RecommendationCandidateListResponse"
        in paths["/v1/recommendations/candidates"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"]
    )
    assert (
        "RecommendationCandidateResponse"
        in paths["/v1/recommendations/candidates/{ticker}"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"]
    )
    assert (
        "StockScoreResponse"
        in paths["/v1/stocks/{ticker}/score"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
    )


def test_unknown_candidate_returns_common_error_response(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/recommendations/candidates/999999")

    assert response.status_code == 404
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "STOCK_NOT_FOUND"


def test_missing_score_components_degrade_without_500(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.component_scores = score.component_scores[:2]
    seeded_session.commit()

    response = seeded_api_client.get("/v1/recommendations/candidates/005930")

    assert response.status_code == 200
    assert len(response.json()["score_components"]) == 2
