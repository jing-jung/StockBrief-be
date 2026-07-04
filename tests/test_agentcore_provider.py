import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.services.candidate_service import CandidateService
from app.services.chat import ChatProviderInput, ChatProviderUnavailable, compose_chat_answer
from app.services.chat.providers import chat_provider_for
from app.services.chat.runtime_provider import AgentCoreChatProvider
from app.services.evidence_service import EvidenceService


def test_agentcore_provider_factory_requires_settings() -> None:
    with pytest.raises(ChatProviderUnavailable):
        chat_provider_for("agentcore")


def test_agentcore_provider_factory_uses_settings() -> None:
    provider = chat_provider_for(
        "agentcore",
        settings=Settings(
            chat_provider="agentcore",
            agentcore_runtime_url="http://127.0.0.1:8080",
            agentcore_runtime_timeout_seconds=3,
        ),
    )

    assert isinstance(provider, AgentCoreChatProvider)
    assert provider.runtime_url == "http://127.0.0.1:8080"
    assert provider.timeout_seconds == 3


def test_agentcore_provider_aws_runtime_invocation_sets_json_content_type() -> None:
    class FakeAgentCoreClient:
        request: dict | None = None

        def invoke_agent_runtime(self, **kwargs):
            self.request = kwargs
            return {
                "response": BytesIO(
                    b'{"status":"success","response":{"answer":"ok"}}'
                )
            }

    client = FakeAgentCoreClient()
    provider = AgentCoreChatProvider(
        runtime_arn="arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test",
        client=client,
    )
    payload = {"input": {"ticker": "005930"}}

    response = provider._invoke_aws_runtime(payload)

    assert response["status"] == "success"
    assert client.request is not None
    assert client.request["contentType"] == "application/json"
    assert client.request["accept"] == "application/json"
    assert json.loads(client.request["payload"].decode("utf-8")) == payload


def test_agentcore_provider_rechecks_runtime_answer(
    seeded_session: Session,
) -> None:
    request = _provider_input(seeded_session)
    baseline = compose_chat_answer(
        message=request.message,
        candidate=request.candidate,
        evidence=request.evidence,
    )
    evidence_id = baseline.used_evidence_ids[0]
    captured_payload: dict = {}

    provider = AgentCoreChatProvider(
        runtime_url="http://runtime.local",
        runtime_invoker=lambda payload: (
            captured_payload.update(payload)
            or {
                "status": "success",
                "response": {
                    "answer": f"저장된 근거 기준 설명입니다. [{evidence_id}]",
                    "trace": {
                        "selected_tools": ["get_candidate"],
                        "tool_calls": [{"name": "get_candidate", "status": "success"}],
                        "citation_ids": [evidence_id],
                    },
                },
            }
        ),
    )

    response = provider.compose(request)

    assert response.answer.endswith(f"[{evidence_id}]")
    assert response.citations == baseline.citations
    assert response.policy_status == "allowed"
    assert {item["id"] for item in captured_payload["input"]["evidence"]} <= set(
        baseline.used_evidence_ids
    )


def test_agentcore_provider_blocks_context_citation_not_returned_to_client(
    seeded_session: Session,
) -> None:
    request = _provider_input(seeded_session)
    baseline = compose_chat_answer(
        message=request.message,
        candidate=request.candidate,
        evidence=request.evidence,
    )
    returned_ids = set(baseline.used_evidence_ids)
    extra_evidence_id = next(
        item.id for item in request.evidence if item.id not in returned_ids
    )
    provider = AgentCoreChatProvider(
        runtime_url="http://runtime.local",
        runtime_invoker=lambda payload: {
            "status": "success",
            "response": {
                "answer": f"후보 context의 추가 근거를 인용했습니다. [{extra_evidence_id}]",
                "trace": {
                    "selected_tools": ["get_candidate"],
                    "citation_ids": [extra_evidence_id],
                },
            },
        },
    )

    with pytest.raises(ChatProviderUnavailable, match="invalid citations"):
        provider.compose(request)


def test_agentcore_provider_blocks_unsafe_runtime_answer(
    seeded_session: Session,
) -> None:
    request = _provider_input(seeded_session)
    baseline = compose_chat_answer(
        message=request.message,
        candidate=request.candidate,
        evidence=request.evidence,
    )
    evidence_id = baseline.used_evidence_ids[0]
    provider = AgentCoreChatProvider(
        runtime_url="http://runtime.local",
        runtime_invoker=lambda payload: {
            "status": "success",
            "response": {
                "answer": f"매수 추천으로 볼 수 있습니다. [{evidence_id}]",
                "trace": {"selected_tools": ["get_candidate"], "citation_ids": [evidence_id]},
            },
        },
    )

    with pytest.raises(ChatProviderUnavailable, match="unsafe answer"):
        provider.compose(request)


def test_agentcore_provider_blocks_broken_citation(
    seeded_session: Session,
) -> None:
    request = _provider_input(seeded_session)
    provider = AgentCoreChatProvider(
        runtime_url="http://runtime.local",
        runtime_invoker=lambda payload: {
            "status": "success",
            "response": {
                "answer": "저장된 근거 기준 설명입니다. [made_up_evidence]",
                "trace": {
                    "selected_tools": ["get_candidate"],
                    "citation_ids": ["made_up_evidence"],
                },
            },
        },
    )

    with pytest.raises(ChatProviderUnavailable, match="invalid citations"):
        provider.compose(request)


def test_agentcore_provider_keeps_policy_redirect_deterministic(
    seeded_session: Session,
) -> None:
    request = _provider_input(seeded_session, message="이 종목 매수해도 돼?")
    provider = AgentCoreChatProvider(
        runtime_url="http://runtime.local",
        runtime_invoker=lambda payload: (_ for _ in ()).throw(
            AssertionError("redirected policy requests must not call AgentCore")
        ),
    )

    response = provider.compose(request)

    assert response.policy_status == "redirected"
    assert "직접 답하지 않습니다" in response.answer


def test_chat_api_agentcore_runtime_failure_fails_closed(
    seeded_api_client: TestClient,
    monkeypatch,
) -> None:
    def override_settings() -> Settings:
        return Settings(
            chat_provider="agentcore",
            agentcore_runtime_url="http://runtime.local",
        )

    def unavailable_runtime(self, payload):
        raise ChatProviderUnavailable("simulated runtime failure")

    monkeypatch.setattr(
        "app.services.chat.runtime_provider.AgentCoreChatProvider._invoke_runtime",
        unavailable_runtime,
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
    assert "simulated runtime failure" in payload["error"]["message"]


def test_agentcore_runtime_dev_invocation_records_tool_trace(
    seeded_session: Session,
    monkeypatch,
) -> None:
    pytest.importorskip("strands")
    from app.agentcore_runtime import app as runtime_app

    request = _provider_input(seeded_session)
    baseline = compose_chat_answer(
        message=request.message,
        candidate=request.candidate,
        evidence=request.evidence,
    )
    monkeypatch.setenv("AGENTCORE_RUNTIME_USE_DEV_MODEL", "true")
    get_settings.cache_clear()
    try:
        client = TestClient(runtime_app)
        ping_response = client.get("/ping")
        response = client.post(
            "/invocations",
            json={
                "input": {
                    "message": request.message,
                    "ticker": request.candidate.ticker,
                    "candidate": request.candidate.model_dump(mode="json"),
                    "evidence": [item.model_dump(mode="json") for item in request.evidence],
                    "baseline": baseline.model_dump(mode="json"),
                }
            },
        )
    finally:
        get_settings.cache_clear()

    assert ping_response.status_code == 200
    assert ping_response.json() == {"status": "Healthy"}
    assert response.status_code == 200
    payload = response.json()
    trace = payload["response"]["trace"]
    assert payload["status"] == "success"
    assert "get_candidate" in trace["selected_tools"]
    assert trace["tool_calls"][0]["status"] == "success"
    assert trace["citation_ids"] == baseline.used_evidence_ids
    assert trace["metrics"]["summary"]["tool_usage"]["get_candidate"]


def _provider_input(
    session: Session,
    *,
    message: str = "왜 추천됐나요?",
) -> ChatProviderInput:
    candidate_service = CandidateService(session)
    stock, score = candidate_service.candidate_row("005930")
    return ChatProviderInput(
        message=message,
        candidate=candidate_service.candidate_response(stock, score),
        evidence=EvidenceService(session).items("005930"),
    )
