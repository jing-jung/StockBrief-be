from fastapi.testclient import TestClient


EXPECTED_API_PATHS = {
    "/v1/health": ["get"],
    "/v1/meta/service-policy": ["get"],
    "/v1/stocks/search": ["get"],
    "/v1/recommendations/candidates": ["get"],
    "/v1/recommendations/candidates/{ticker}": ["get"],
    "/v1/stocks/candidates": ["get"],
    "/v1/stocks/candidates/{ticker}": ["get"],
    "/v1/stocks/{ticker}/score": ["get"],
    "/v1/stocks/{ticker}": ["get"],
    "/v1/stocks/{ticker}/evidence": ["get"],
    "/v1/chat": ["post"],
    "/v1/me": ["get", "patch"],
    "/v1/me/preferences": ["get", "put"],
    "/v1/me/watchlist": ["get", "post"],
    "/v1/me/watchlist/import": ["post"],
    "/v1/me/watchlist/{ticker}": ["delete", "patch"],
    "/v1/me/chat-sessions": ["get", "post"],
}

RECOMMENDATION_CANDIDATE_REQUIRED_FIELDS = {
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
}

CHAT_RESPONSE_REQUIRED_FIELDS = {
    "success",
    "data",
    "message",
    "request_id",
}


def test_openapi_path_snapshot(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    for path, methods in EXPECTED_API_PATHS.items():
        assert path in paths, f"API contract missing path: {path}"
        for method in methods:
            assert method in paths[path], f"API contract missing method: {method.upper()} {path}"


def test_recommendation_candidate_schema_required_fields_snapshot(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/openapi.json")

    assert response.status_code == 200
    schema = response.json()["components"]["schemas"]["RecommendationCandidateResponse"]
    assert set(schema["properties"]) >= RECOMMENDATION_CANDIDATE_REQUIRED_FIELDS
    assert set(schema["required"]) >= (
        RECOMMENDATION_CANDIDATE_REQUIRED_FIELDS - {"missing_data"}
    )
    assert "minItems" not in schema["properties"]["score_components"]
    assert schema["properties"]["score_components"]["maxItems"] == 8
    assert schema["properties"]["recommendation_score"]["minimum"] == 0
    assert schema["properties"]["recommendation_score"]["maximum"] == 100


def test_chat_response_schema_required_fields_snapshot(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/openapi.json")

    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    schema = schemas["ChatContractResponse"]
    assert set(schema["properties"]) >= CHAT_RESPONSE_REQUIRED_FIELDS
    assert set(schema["required"]) >= {"data", "message", "request_id"}
    data_schema = schemas["ChatContractData"]
    assert set(data_schema["properties"]) >= {
        "session_id",
        "answer",
        "citations",
        "safety",
    }
