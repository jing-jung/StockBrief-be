import logging
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import (
    RecommendationCandidateResponse,
    RecommendationReasonResponse,
    ScoreComponentResponse,
    StockEvidenceItemResponse,
)
from app.orm import EvidenceChunk, FinancialStatement, PriceMetric, RecommendationScore
from app.services.chat import (
    ChatProviderInput,
    ChatProviderUnavailable,
    chat_provider_for,
    compose_chat_answer,
)
from app.services.chat.providers import BedrockChatProvider, _validate_answer_citations


PROHIBITED_KOREAN_OUTPUT_TERMS = [
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


def test_chat_allowed_answer_uses_candidate_evidence_and_risks(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "왜 추천됐나요?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "mock Agent 응답을 반환했습니다."
    data = payload["data"]
    assert data["safety"]["policy_action"] == "ALLOW"
    assert data["message_id"] is None
    assert "추천 후보 점수" in data["answer"]
    assert "주요 추천 이유" in data["answer"]
    assert "연결된 근거 요약" in data["answer"]
    assert "리스크/확인 필요 사항" in data["answer"]
    assert data["citations"]
    assert {"id", "source_type", "title", "url", "published_at"}.issubset(
        data["citations"][0]
    )
    assert any(citation["published_at"] for citation in data["citations"])


def test_chat_mock_provider_preserves_existing_contract(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "왜 추천됐나요?"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "mock Agent 응답을 반환했습니다."


def test_chat_bedrock_provider_fails_closed_when_model_is_missing(
    seeded_api_client: TestClient,
) -> None:
    def override_settings() -> Settings:
        return Settings(chat_provider="bedrock", bedrock_chat_model_id="")

    app.dependency_overrides[get_settings] = override_settings
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "BEDROCK_CHAT_MODEL_ID" in payload["error"]["message"]


def test_chat_bedrock_provider_rejects_invalid_max_tokens_before_runtime(
    seeded_api_client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    def fail_if_bedrock_client_is_created(*args, **kwargs):
        raise AssertionError("invalid Bedrock config must fail before runtime client")

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
            bedrock_chat_max_tokens=1,
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        fail_if_bedrock_client_is_created,
    )
    app.dependency_overrides[get_settings] = override_settings
    caplog.set_level(logging.WARNING, logger="app.services.chat.providers")
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "BEDROCK_CHAT_MAX_TOKENS" in payload["error"]["message"]
    assert "provider=bedrock" in caplog.text
    assert "latency_ms=" in caplog.text
    assert "fail_closed_reason=invalid_max_tokens" in caplog.text
    assert "unsafe_output_block=False" in caplog.text


def test_chat_bedrock_provider_logs_runtime_request_failure_reason(
    seeded_api_client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    class FakeBedrockClient:
        def converse(self, **kwargs):
            raise ClientError(
                {
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "model access denied",
                    }
                },
                "Converse",
            )

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: FakeBedrockClient(),
    )
    app.dependency_overrides[get_settings] = override_settings
    caplog.set_level(logging.WARNING, logger="app.services.chat.providers")
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "request failed" in payload["error"]["message"]
    assert "reason=runtime_request_failed" in caplog.text
    assert "provider=bedrock" in caplog.text
    assert "latency_ms=" in caplog.text
    assert "fail_closed_reason=runtime_request_failed" in caplog.text
    assert "citation_guard_failure=False" in caplog.text
    assert "unsafe_output_block=False" in caplog.text
    assert "error_type=ClientError" in caplog.text
    assert "error_code=AccessDeniedException" in caplog.text
    assert "model access denied" not in caplog.text


def test_chat_bedrock_provider_returns_model_answer_with_existing_citations(
    seeded_api_client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    class FakeBedrockClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def converse(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    "삼성전자(005930)는 공개 데이터 기준 추천 후보 점수와 "
                                    "연결 근거가 확인된 검토 대상입니다. "
                                        "[ev_provider_005930_disclosure] 근거를 함께 확인하세요."
                                )
                            }
                        ]
                    }
                }
            }

    fake_client = FakeBedrockClient()

    def fake_boto3_client(service_name: str, **kwargs):
        assert service_name == "bedrock-runtime"
        return fake_client

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr("app.services.chat.providers.boto3.client", fake_boto3_client)
    app.dependency_overrides[get_settings] = override_settings
    caplog.set_level(logging.INFO, logger="app.services.chat.providers")
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "bedrock Agent 응답을 반환했습니다."
    assert "연결 근거가 확인된 검토 대상" in payload["data"]["answer"]
    assert payload["data"]["citations"]
    assert fake_client.calls
    assert fake_client.calls[0]["modelId"] == "apac.amazon.nova-micro-v1:0"
    assert fake_client.calls[0]["inferenceConfig"]["maxTokens"] == 700
    assert "bedrock_chat_provider_result provider=bedrock" in caplog.text
    assert "latency_ms=" in caplog.text
    assert "citation_retry=False" in caplog.text


def test_chat_bedrock_provider_retries_once_when_citations_are_missing(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    class FakeBedrockClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def converse(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "output": {
                        "message": {
                            "content": [
                                {"text": "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다."}
                            ]
                        }
                    }
                }
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다. "
                                        "[ev_provider_005930_disclosure]"
                                )
                            }
                        ]
                    }
                }
            }

    fake_client = FakeBedrockClient()

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: fake_client,
    )
    app.dependency_overrides[get_settings] = override_settings
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "bedrock Agent 응답을 반환했습니다."
    assert len(fake_client.calls) == 2
    retry_prompt = fake_client.calls[1]["messages"][0]["content"][0]["text"]
    assert "Previous answer failed citation validation" in retry_prompt
    assert fake_client.calls[1]["inferenceConfig"]["temperature"] == 0.0


def test_chat_bedrock_provider_fails_closed_when_retry_still_lacks_citations(
    seeded_api_client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    class FakeBedrockClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def converse(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "output": {
                    "message": {
                        "content": [
                            {"text": "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다."}
                        ]
                    }
                }
            }

    fake_client = FakeBedrockClient()

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: fake_client,
    )
    app.dependency_overrides[get_settings] = override_settings
    caplog.set_level(logging.WARNING, logger="app.services.chat.providers")
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "without evidence citations" in payload["error"]["message"]
    assert len(fake_client.calls) == 2
    assert "reason=citation_retry_failed" in caplog.text
    assert "citation_guard_failure=True" in caplog.text
    assert "fail_closed_reason=citation_retry_failed" in caplog.text


def test_chat_bedrock_prompt_only_includes_guard_allowed_evidence() -> None:
    class FakeBedrockClient:
        def __init__(self) -> None:
            self.call: dict[str, Any] | None = None

        def converse(self, **kwargs):
            self.call = kwargs
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    "삼성전자(005930)는 공개 데이터 기준으로 검토할 수 있는 "
                                    "후보입니다. [ev_used_a] [ev_used_a] [ev_used_b]"
                                )
                            }
                        ]
                    }
                }
            }

    fake_client = FakeBedrockClient()
    provider = BedrockChatProvider(
        model_id="apac.amazon.nova-micro-v1:0",
        region_name="ap-northeast-2",
        client=fake_client,
    )
    candidate = RecommendationCandidateResponse(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        sector="반도체",
        recommendation_score=73.2,
        score_components=[
            ScoreComponentResponse(
                name="disclosure_event",
                weight=10,
                raw_score=70.0,
                weighted_score=7.0,
                reason="공시 근거가 확인되었습니다.",
                evidence_ids=["ev_used_a"],
            )
        ],
        recommendation_reasons=[
            RecommendationReasonResponse(
                reason_id="reason-disclosure",
                component="disclosure_event",
                summary="최근 공시와 실적 근거가 연결되었습니다.",
                evidence_ids=["ev_used_a", "ev_used_b", "ev_used_c", "ev_used_d", "ev_unused"],
            )
        ],
        risk_tags=["근거 확인 필요"],
        evidence_level="strong",
        evidence_count=3,
        missing_data=[],
        data_freshness={"as_of": "2026-06-24"},
        disclaimer="이 정보는 투자 조언이 아닙니다.",
    )
    evidence = [
        StockEvidenceItemResponse(
            id="ev_unused",
            type="news",
            title="프롬프트에 들어가면 안 되는 뉴스",
            summary="baseline citation이 아니므로 Bedrock context에서 제외되어야 합니다.",
            source_name="Test News",
            source_url="https://example.com/unused",
            published_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            as_of_date=date(2026, 6, 24),
            data_status="available",
        ),
        StockEvidenceItemResponse(
            id="ev_used_a",
            type="disclosure",
            title="분기보고서",
            summary="공시 근거입니다.",
            source_name="OpenDART",
            source_url="https://example.com/a",
            published_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            as_of_date=date(2026, 6, 24),
            data_status="available",
        ),
        StockEvidenceItemResponse(
            id="ev_used_b",
            type="financial",
            title="재무 요약",
            summary="실적 근거입니다.",
            source_name="OpenDART",
            source_url="https://example.com/b",
            published_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            as_of_date=date(2026, 6, 24),
            data_status="available",
        ),
        StockEvidenceItemResponse(
            id="ev_used_c",
            type="price",
            title="가격 지표",
            summary="거래 지표 근거입니다.",
            source_name="KRX",
            source_url="https://example.com/c",
            published_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            as_of_date=date(2026, 6, 24),
            data_status="available",
        ),
        StockEvidenceItemResponse(
            id="ev_used_d",
            type="news",
            title="시장 뉴스",
            summary="시장 관심도 근거입니다.",
            source_name="NAVER_NEWS",
            source_url="https://example.com/d",
            published_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            as_of_date=date(2026, 6, 24),
            data_status="available",
        ),
    ]

    response = provider.compose(
        ChatProviderInput(
            message="왜 추천됐나요?",
            candidate=candidate,
            evidence=evidence,
        )
    )

    assert response.used_evidence_ids == ["ev_used_a", "ev_used_b", "ev_used_c", "ev_used_d"]
    assert fake_client.call is not None
    prompt = fake_client.call["messages"][0]["content"][0]["text"]
    assert "Allowed citation IDs: ev_used_a, ev_used_b, ev_used_c, ev_used_d" in prompt
    assert "evidence_ids=ev_used_a, ev_used_b, ev_used_c, ev_used_d" in prompt
    assert "ev_used_a" in prompt
    assert "ev_used_b" in prompt
    assert "title=분기보고서" in prompt
    assert "summary=공시 근거입니다." in prompt
    assert prompt.count("id=ev_used_a;") == 1
    assert "title=재무 요약" in prompt
    assert "summary=실적 근거입니다." in prompt
    assert "ev_unused" not in prompt


def test_chat_citation_guard_accepts_labelled_evidence_id_links() -> None:
    _validate_answer_citations(
        answer=(
            "삼성전자 검토 근거입니다. "
            "[증거 ID: ev_used_a](https://example.com/a) "
            "[ev_used_b]"
        ),
        allowed_evidence_ids={"ev_used_a", "ev_used_b"},
    )


def test_chat_bedrock_provider_rejects_unsupported_model_citation(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    class FakeBedrockClient:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다. "
                                    "[ev_fake] 근거를 확인하세요."
                                )
                            }
                        ]
                    }
                }
            }

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: FakeBedrockClient(),
    )
    app.dependency_overrides[get_settings] = override_settings
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "unsupported evidence citations" in payload["error"]["message"]


def test_chat_bedrock_provider_rejects_invented_citation_without_allowed_evidence() -> None:
    class FakeBedrockClient:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다. "
                                    "[ev_fake] 근거를 확인하세요."
                                )
                            }
                        ]
                    }
                }
            }

    provider = BedrockChatProvider(
        model_id="apac.amazon.nova-micro-v1:0",
        region_name="ap-northeast-2",
        client=FakeBedrockClient(),
    )
    candidate = RecommendationCandidateResponse(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        sector="반도체",
        recommendation_score=51.0,
        score_components=[],
        recommendation_reasons=[],
        risk_tags=["근거 확인 필요"],
        evidence_level="weak",
        evidence_count=0,
        missing_data=["evidence"],
        data_freshness={},
        disclaimer="이 정보는 투자 조언이 아닙니다.",
    )

    try:
        provider.compose(
            ChatProviderInput(
                message="왜 추천됐나요?",
                candidate=candidate,
                evidence=[],
            )
        )
    except ChatProviderUnavailable as exc:
        assert "unsupported evidence citations" in str(exc)
    else:
        raise AssertionError("invented citations must fail closed")


def test_chat_bedrock_provider_fails_closed_without_allowed_evidence_ids() -> None:
    class FakeBedrockClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def converse(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "output": {
                    "message": {
                        "content": [
                            {"text": "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다."}
                        ]
                    }
                }
            }

    fake_client = FakeBedrockClient()
    provider = BedrockChatProvider(
        model_id="apac.amazon.nova-micro-v1:0",
        region_name="ap-northeast-2",
        client=fake_client,
    )
    candidate = RecommendationCandidateResponse(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        sector="반도체",
        recommendation_score=51.0,
        score_components=[],
        recommendation_reasons=[],
        risk_tags=["근거 확인 필요"],
        evidence_level="weak",
        evidence_count=0,
        missing_data=["evidence"],
        data_freshness={},
        disclaimer="이 정보는 투자 조언이 아닙니다.",
    )

    try:
        provider.compose(
            ChatProviderInput(
                message="왜 추천됐나요?",
                candidate=candidate,
                evidence=[],
            )
        )
    except ChatProviderUnavailable as exc:
        assert "requires allowed evidence citations" in str(exc)
    else:
        raise AssertionError("Bedrock provider must fail closed without allowed evidence IDs")

    assert len(fake_client.calls) == 2


def test_chat_bedrock_provider_requires_model_citation_when_evidence_exists(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    class FakeBedrockClient:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다."
                            }
                        ]
                    }
                }
            }

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: FakeBedrockClient(),
    )
    app.dependency_overrides[get_settings] = override_settings
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "without evidence citations" in payload["error"]["message"]


def test_chat_bedrock_provider_logs_likely_false_positive_guard_without_raw_answer(
    seeded_api_client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    raw_model_answer = (
        "삼성전자(005930)는 공개 데이터 기준 검토 대상입니다. "
        "매수 권유가 아닙니다. "  # policy-scan: allow model-output-guard-test
        "[ev_provider_005930_disclosure] 근거를 확인하세요."
    )

    class FakeBedrockClient:
        def converse(self, **kwargs):
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": raw_model_answer,
                            }
                        ]
                    }
                }
            }

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: FakeBedrockClient(),
    )
    app.dependency_overrides[get_settings] = override_settings
    caplog.set_level(logging.WARNING, logger="app.services.chat.providers")
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "unsafe answer" in payload["error"]["message"]
    assert "reason=unsafe_output" in caplog.text
    assert "fail_closed_reason=unsafe_output" in caplog.text
    assert "unsafe_output_block=True" in caplog.text
    assert "likely_false_positive=True" in caplog.text
    assert "matched_terms=매수" in caplog.text
    assert raw_model_answer not in caplog.text
    assert "매수 권유가 아닙니다" not in caplog.text


def test_chat_bedrock_provider_logs_empty_answer_guard_reason(
    seeded_api_client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    class FakeBedrockClient:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "   "}]}}}

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        lambda *args, **kwargs: FakeBedrockClient(),
    )
    app.dependency_overrides[get_settings] = override_settings
    caplog.set_level(logging.WARNING, logger="app.services.chat.providers")
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "empty answer" in payload["error"]["message"]
    assert "reason=empty_answer" in caplog.text
    assert "answer_length=0" in caplog.text


def test_chat_bedrock_provider_keeps_policy_redirect_deterministic(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    def fail_if_bedrock_client_is_created(*args, **kwargs):
        raise AssertionError("redirected policy requests must not call Bedrock")

    def override_settings() -> Settings:
        return Settings(
            chat_provider="bedrock",
            bedrock_chat_model_id="apac.amazon.nova-micro-v1:0",
            bedrock_chat_region="ap-northeast-2",
        )

    monkeypatch.setattr(
        "app.services.chat.providers.boto3.client",
        fail_if_bedrock_client_is_created,
    )
    app.dependency_overrides[get_settings] = override_settings
    try:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "이 종목 매수해도 돼?"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "bedrock Agent 응답을 반환했습니다."
    assert payload["data"]["safety"]["policy_action"] == "REDIRECT"


def test_chat_closes_read_session_before_provider_io(
    seeded_api_client: TestClient,
    seeded_session: Session,
    monkeypatch,
) -> None:
    class AssertingProvider:
        name = "mock"

        def compose(self, request):
            assert not seeded_session.in_transaction()
            return compose_chat_answer(
                message=request.message,
                candidate=request.candidate,
                evidence=request.evidence,
            )

    monkeypatch.setattr(
        "app.routes.chat.chat_provider_for",
        lambda *args, **kwargs: AssertingProvider(),
    )

    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "왜 추천됐나요?"},
    )

    assert response.status_code == 200


def test_chat_provider_factory_failure_returns_fail_closed_response(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    def unavailable_provider_factory(name: str, **kwargs):
        raise ChatProviderUnavailable(f"Unsupported chat provider: {name}")

    monkeypatch.setattr(
        "app.routes.chat.chat_provider_for",
        unavailable_provider_factory,
    )

    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "왜 추천됐나요?"},
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert "Unsupported chat provider" in payload["error"]["message"]


def test_chat_provider_factory_rejects_unknown_provider() -> None:
    try:
        chat_provider_for("unknown")
    except ChatProviderUnavailable as exc:
        assert "Unsupported chat provider" in str(exc)
    else:
        raise AssertionError("unknown chat provider should fail closed")


def test_chat_redirects_trade_decision_request(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "이 종목 매수해도 돼?"},
    )

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert data["safety"]["policy_action"] == "REDIRECT"
    assert "직접 답하지 않습니다" in data["answer"]
    assert data["citations"]


def test_chat_redirects_target_entry_and_stop_requests(
    seeded_api_client: TestClient,
) -> None:
    messages = [
        "목표가 알려줘",
        "진입가와 손절가를 정해줘",
    ]

    for message in messages:
        response = seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": message},
        )

        assert response.status_code == 200
        assert response.json()["data"]["safety"]["policy_action"] == "REDIRECT"


def test_chat_blocks_or_redirects_return_certainty_request(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "수익 보장되는지 확실하게 말해줘"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["safety"]["policy_action"] in {"BLOCK", "REDIRECT"}
    assert "답할 수 없습니다" in payload["data"]["answer"]


def test_chat_says_evidence_is_insufficient_when_evidence_is_weak(
    seeded_api_client: TestClient,
    seeded_session: Session,
) -> None:
    score = seeded_session.scalars(
        select(RecommendationScore).where(RecommendationScore.ticker == "005930")
    ).one()
    score.evidence_level = "weak"
    score.evidence_count = 1
    seeded_session.execute(
        delete(FinancialStatement).where(FinancialStatement.ticker == "005930")
    )
    seeded_session.execute(delete(PriceMetric).where(PriceMetric.ticker == "005930"))
    seeded_session.execute(delete(EvidenceChunk).where(EvidenceChunk.ticker == "005930"))
    seeded_session.commit()

    response = seeded_api_client.post(
        "/v1/chat",
        json={"ticker": "005930", "message": "왜 추천됐나요?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["safety"]["policy_action"] == "ALLOW"
    assert "근거가 부족" in payload["data"]["answer"]
    assert payload["data"]["citations"] == []


def test_chat_response_does_not_emit_prohibited_korean_terms(
    seeded_api_client: TestClient,
) -> None:
    responses = [
        seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "왜 추천됐나요?"},
        ).json(),
        seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "목표가 알려줘"},
        ).json(),
        seeded_api_client.post(
            "/v1/chat",
            json={"ticker": "005930", "message": "수익 보장 가능해?"},
        ).json(),
    ]
    text = _flatten_text(responses)

    for term in PROHIBITED_KOREAN_OUTPUT_TERMS:
        assert term not in text


def test_chat_openapi_documents_response_model(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/openapi.json")

    assert response.status_code == 200
    schema = response.json()["paths"]["/v1/chat"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert "ChatContractResponse" in schema["$ref"]
