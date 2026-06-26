from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.orm import EvidenceChunk, RecommendationScore, SourceDocument


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
