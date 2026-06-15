from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table

from app.orm import EvidenceChunk, FinancialStatement, PriceMetric, RecommendationScore, RiskSignal
from app.services.candidate_service import CandidateService


PROHIBITED_KOREAN_TERMS = [
    "매수",
    "매도",
    "목표가",
    "진입가",
    "손절가",
    "수익 보장",
]


def _statement_references_table(statement, table_name: str) -> bool:
    return any(
        isinstance(node, Table) and node.name == table_name
        for node in visitors.iterate(statement)
    )


def _stock_candidate_aggregate_statements(
    service: CandidateService,
):
    base_statement = service._stock_candidate_base_statement(market=None, sector=None)
    return service._stock_candidate_aggregate_statements(base_statement)


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(_flatten_text(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def test_stock_search_returns_seeded_stocks(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/stocks/search", params={"q": "삼성"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "종목 검색 결과를 반환했습니다."
    assert payload["request_id"].startswith("req_")
    data = payload["data"]
    assert data["pagination"]["total"] >= 2
    assert {"ticker", "name", "market", "sector", "corp_code", "match_reason"}.issubset(
        data["items"][0]
    )
    assert data["items"][0]["match_reason"] in {"name", "ticker", "keyword", "default"}


def test_stock_search_escapes_like_wildcards(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/stocks/search", params={"q": "%"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["items"] == []
    assert payload["data"]["pagination"]["total"] == 0


def test_stock_detail_returns_identifiers(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/stocks/005930")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["stock"]["ticker"] == "005930"
    assert data["stock"]["name"] == "삼성전자"
    assert data["stock"]["corp_code"] == "MOCK00126380"
    assert {"stock", "price", "score", "brief", "evidence_preview"}.issubset(data)
    assert {"total", "grade", "as_of", "version", "breakdown"}.issubset(data["score"])


def test_stock_candidates_respect_risk_profile_sorting(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    for index in range(5):
        seeded_session.add(
            RiskSignal(
                ticker="005930",
                as_of_date=score.as_of_date,
                risk_tag=f"extra_review_risk_{index}",
                severity="medium",
                penalty_points=Decimal("1.00"),
                display_text="추가 리스크 확인이 필요합니다.",
                description="추가 리스크 확인이 필요합니다.",
                evidence_ids=[],
            )
        )
    seeded_session.commit()

    aggressive = seeded_api_client.get(
        "/v1/stocks/candidates",
        params={"risk_profile": "aggressive", "limit": 1},
    )
    conservative = seeded_api_client.get(
        "/v1/stocks/candidates",
        params={"risk_profile": "conservative", "limit": 1},
    )

    assert aggressive.status_code == 200
    assert conservative.status_code == 200
    assert aggressive.json()["data"]["items"][0]["ticker"] == "005930"
    assert conservative.json()["data"]["items"][0]["ticker"] != "005930"


def test_stock_candidates_include_items_without_risk_signals(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    seeded_session.execute(delete(RiskSignal).where(RiskSignal.ticker == "005930"))
    seeded_session.commit()

    response = seeded_api_client.get(
        "/v1/stocks/candidates",
        params={"limit": 100},
    )

    assert response.status_code == 200
    tickers = {item["ticker"] for item in response.json()["data"]["items"]}
    assert "005930" in tickers


def test_stock_candidates_bulk_loads_candidate_related_data(
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
        response = seeded_api_client.get("/v1/stocks/candidates", params={"limit": 20})
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    assert response.json()["data"]["items"]
    assert len(statements) <= 8


def test_stock_candidates_use_database_limit_offset(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    engine = seeded_session.get_bind()
    statements: list[str] = []

    def capture_statement(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.upper())

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = seeded_api_client.get(
            "/v1/stocks/candidates",
            params={"limit": 1, "offset": 1},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert response.json()["data"]["pagination"]["limit"] == 1
    assert response.json()["data"]["pagination"]["offset"] == 1
    assert any(" LIMIT " in statement and " OFFSET " in statement for statement in statements)


def test_stock_candidate_aggregate_queries_skip_price_metric_join(
    seeded_session: Session,
) -> None:
    service = CandidateService(seeded_session)
    count_statement, as_of_statement = _stock_candidate_aggregate_statements(service)

    assert not _statement_references_table(count_statement, PriceMetric.__tablename__)
    assert not _statement_references_table(as_of_statement, PriceMetric.__tablename__)


def test_stock_candidate_score_and_updated_sorts_skip_price_metric_join(
    seeded_session: Session,
) -> None:
    service = CandidateService(seeded_session)
    base_statement = service._stock_candidate_base_statement(market=None, sector=None)

    score_statement = service._order_stock_candidate_statement(
        statement=base_statement,
        sort="score_desc",
        risk_profile="balanced",
    )
    updated_statement = service._order_stock_candidate_statement(
        statement=base_statement,
        sort="updated_desc",
        risk_profile="balanced",
    )

    assert not _statement_references_table(score_statement, PriceMetric.__tablename__)
    assert not _statement_references_table(updated_statement, PriceMetric.__tablename__)


def test_stock_candidate_volume_sort_uses_price_metric_for_global_ordering(
    seeded_session: Session,
) -> None:
    service = CandidateService(seeded_session)
    base_statement = service._stock_candidate_base_statement(market=None, sector=None)
    volume_statement = service._order_stock_candidate_statement(
        statement=base_statement,
        sort="volume_desc",
        risk_profile="balanced",
    )
    count_statement, as_of_statement = _stock_candidate_aggregate_statements(service)

    assert _statement_references_table(volume_statement, PriceMetric.__tablename__)
    assert not _statement_references_table(count_statement, PriceMetric.__tablename__)
    assert not _statement_references_table(as_of_statement, PriceMetric.__tablename__)


def test_invalid_ticker_returns_contract_error(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/stocks/ABC/evidence")

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "INVALID_TICKER"
    assert payload["error"]["details"] == [
        {"field": "ticker", "reason": "invalid_format"}
    ]
    assert payload["request_id"].startswith("req_")


def test_contract_response_request_id_matches_header(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get(
        "/v1/stocks/005930/evidence",
        headers={"x-request-id": "req_test_correlation"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req_test_correlation"
    assert response.json()["request_id"] == "req_test_correlation"


def test_invalid_evidence_date_returns_contract_error(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get(
        "/v1/stocks/005930/evidence",
        params={"from_date": "not-a-date"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "INVALID_REQUEST"
    assert payload["request_id"].startswith("req_")


def test_stock_evidence_returns_all_seeded_evidence_types(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/stocks/005930/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "근거 목록을 반환했습니다."
    data = payload["data"]
    assert data["ticker"] == "005930"
    evidence_types = {item["source_type"] for item in data["items"]}
    assert {"SCORE", "NEWS", "DISCLOSURE"}.issubset(evidence_types)

    for item in data["items"]:
        assert {
            "id",
            "source_type",
            "title",
            "source_name",
            "url",
            "published_at",
            "snippet",
            "metadata",
        }.issubset(item)


def test_stock_evidence_type_filter_and_limit(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get(
        "/v1/stocks/005930/evidence",
        params={"source_type": "NEWS", "limit": 2},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) <= 2
    assert {item["source_type"] for item in data["items"]}.issubset({"NEWS"})


def test_price_evidence_has_source_identifier_when_url_is_missing(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get(
        "/v1/stocks/005930/evidence",
        params={"source_type": "SCORE"},
    )

    assert response.status_code == 200
    evidence = response.json()["data"]["items"]
    assert evidence
    price_items = [
        item
        for item in evidence
        if item["id"].startswith("price_")
    ]
    assert price_items
    assert price_items[0]["url"] is None
    assert price_items[0]["source_name"] == "KRX_FALLBACK_MOCK"
    assert price_items[0]["metadata"]["source_identifier"]
    assert price_items[0]["metadata"]["data_status"] == "fallback"


def test_stock_evidence_empty_result_has_clear_message(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    seeded_session.execute(
        delete(FinancialStatement).where(FinancialStatement.ticker == "005930")
    )
    seeded_session.execute(delete(PriceMetric).where(PriceMetric.ticker == "005930"))
    seeded_session.execute(delete(EvidenceChunk).where(EvidenceChunk.ticker == "005930"))
    seeded_session.commit()

    response = seeded_api_client.get("/v1/stocks/005930/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["items"] == []
    assert payload["data"]["pagination"]["total"] == 0
    assert payload["message"] == "근거 목록을 반환했습니다."


def test_recommendation_reason_evidence_ids_link_to_evidence_api(
    seeded_api_client: TestClient,
) -> None:
    candidate = seeded_api_client.get("/v1/recommendations/candidates/005930").json()
    evidence = seeded_api_client.get("/v1/stocks/005930/evidence").json()

    reason_evidence_ids = {
        evidence_id
        for reason in candidate["recommendation_reasons"]
        for evidence_id in reason["evidence_ids"]
    }
    evidence_api_ids = {item["id"] for item in evidence["data"]["items"]}
    assert reason_evidence_ids
    assert reason_evidence_ids.issubset(evidence_api_ids)


def test_stock_evidence_openapi_documents_response_model(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert (
            "StockSearchContractResponse"
            in paths["/v1/stocks/search"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["$ref"]
    )
    assert (
            "StockDetailContractResponse"
            in paths["/v1/stocks/{ticker}"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["$ref"]
    )
    assert (
            "StockEvidenceContractResponse"
            in paths["/v1/stocks/{ticker}/evidence"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"]
    )


def test_stock_evidence_responses_do_not_emit_prohibited_terms(
    seeded_api_client: TestClient,
) -> None:
    responses = [
        seeded_api_client.get("/v1/stocks/search", params={"q": "삼성"}).json(),
        seeded_api_client.get("/v1/stocks/005930").json(),
        seeded_api_client.get("/v1/stocks/005930/evidence").json(),
    ]
    text = _flatten_text(responses)

    for term in PROHIBITED_KOREAN_TERMS:
        assert term not in text
