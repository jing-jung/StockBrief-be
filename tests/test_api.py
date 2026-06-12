from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "stockbrief-api"
    assert payload["version"] == "0.1.0"
    assert payload["environment"] == "local"
    assert payload["time"]


def test_service_policy() -> None:
    response = client.get("/v1/meta/service-policy")

    assert response.status_code == 200
    assert response.json() == {
        "product_type": "evidence_based_stock_candidate_recommendation",
        "recommendation_type": "review_candidate_not_buy_sell_advice",
        "prohibited_outputs": [
            "buy_instruction",
            "sell_instruction",
            "target_price",
            "guaranteed_return",
            "entry_price",
            "stop_loss",
        ],
        "mvp_auth": "guest_first",
    }


def test_not_found_uses_common_error_response() -> None:
    response = client.get("/v1/missing")

    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False
    assert payload["request_id"].startswith("req_")
    assert payload["error"] == {
        "code": "RESOURCE_NOT_FOUND",
        "message": "Not Found",
        "details": None,
    }


def test_cors_preflight_allows_configured_origin() -> None:
    response = client.options(
        "/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_preflight_allows_mutation_methods() -> None:
    response = client.options(
        "/v1/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,x-request-id",
        },
    )

    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "Authorization" in response.headers["access-control-allow-headers"]
    assert "x-request-id" in response.headers["access-control-allow-headers"]


def test_openapi_documents_common_error_response() -> None:
    response = client.get("/v1/openapi.json")

    assert response.status_code == 200
    health_responses = response.json()["paths"]["/v1/health"]["get"]["responses"]
    assert "ApiErrorResponse" in health_responses["404"]["content"]["application/json"][
        "schema"
    ]["$ref"]
