from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.orm import RecommendationScore, RiskSignal


def test_seeded_recommendation_candidates_pass_evidence_gate(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/recommendations/candidates", params={"limit": 100})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"], "seed should expose recommendation candidates"
    for candidate in payload["items"]:
        assert candidate["recommendation_score"] is not None, (
            f"{candidate['ticker']} missing recommendation score"
        )
        assert isinstance(candidate["risk_tags"], list), (
            f"{candidate['ticker']} risk_tags must be present as an array"
        )
        assert candidate["data_freshness"].get("as_of"), f"{candidate['ticker']} missing data basis date"
        assert isinstance(candidate["missing_data"], list), (
            f"{candidate['ticker']} missing_data must be present as an array"
        )


def test_candidate_list_allows_scored_rows_below_min_evidence_gate(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.evidence_count = 1
    score.evidence_level = "weak"
    seeded_session.commit()

    response = seeded_api_client.get("/v1/recommendations/candidates", params={"limit": 100})

    assert response.status_code == 200
    candidates = response.json()["items"]
    candidate = next(item for item in candidates if item["ticker"] == "005930")
    assert candidate["recommendation_score"] is not None


def test_candidate_list_allows_rows_without_risk_signal_gate(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    seeded_session.execute(delete(RiskSignal).where(RiskSignal.ticker == "005930"))
    seeded_session.commit()

    response = seeded_api_client.get("/v1/recommendations/candidates", params={"limit": 100})

    assert response.status_code == 200
    candidates = {candidate["ticker"]: candidate for candidate in response.json()["items"]}
    assert candidates["005930"]["risk_tags"] == []


def test_candidate_list_allows_scored_rows_without_candidate_eligible_gate(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.is_candidate_eligible = False
    seeded_session.commit()

    response = seeded_api_client.get("/v1/recommendations/candidates", params={"limit": 100})

    assert response.status_code == 200
    candidates = response.json()["items"]
    candidate = next(item for item in candidates if item["ticker"] == "005930")
    assert candidate["recommendation_score"] is not None


def test_candidate_list_excludes_rows_without_data_basis_date_gate(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.data_freshness = {"price_as_of": "2026-06-09"}
    seeded_session.commit()

    response = seeded_api_client.get("/v1/recommendations/candidates", params={"limit": 100})

    assert response.status_code == 200
    tickers = {candidate["ticker"] for candidate in response.json()["items"]}
    assert "005930" not in tickers
