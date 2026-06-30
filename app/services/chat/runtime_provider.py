from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.models import ChatResponse
from app.services.chat.composer import compose_chat_answer
from app.services.chat.providers import (
    ChatProviderInput,
    ChatProviderUnavailable,
    OutputGuardResult,
    _elapsed_ms,
    _evaluate_prohibited_output,
    _extract_bedrock_text,
    _validate_answer_citations,
)

logger = logging.getLogger(__name__)

MIN_AGENTCORE_TIMEOUT_SECONDS = 1.0
MAX_AGENTCORE_TIMEOUT_SECONDS = 30.0


class AgentCoreChatProvider:
    name = "agentcore"

    def __init__(
        self,
        *,
        runtime_url: str = "",
        runtime_arn: str = "",
        region_name: str | None = None,
        qualifier: str = "DEFAULT",
        timeout_seconds: float = 8.0,
        client: Any | None = None,
        runtime_invoker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.runtime_url = runtime_url.strip().rstrip("/")
        self.runtime_arn = runtime_arn.strip()
        self.region_name = region_name
        self.qualifier = qualifier.strip() or "DEFAULT"
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.runtime_invoker = runtime_invoker

    def _client(self):
        if self.client is not None:
            return self.client
        self.client = boto3.client(
            "bedrock-agentcore",
            region_name=self.region_name or None,
            config=Config(
                connect_timeout=self.timeout_seconds,
                read_timeout=self.timeout_seconds,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        return self.client

    def compose(self, request: ChatProviderInput) -> ChatResponse:
        started_at = time.monotonic()
        self._validate_configuration(started_at=started_at)
        baseline = compose_chat_answer(
            message=request.message,
            candidate=request.candidate,
            evidence=request.evidence,
        )
        if baseline.policy_status != "allowed":
            _log_agentcore_provider_result(
                started_at=started_at,
                policy_status=baseline.policy_status,
                selected_tools=[],
                tool_errors=0,
                citation_ids=baseline.used_evidence_ids,
            )
            return baseline

        runtime_response = self._invoke_runtime(
            _agentcore_runtime_payload(request=request, baseline=baseline)
        )
        answer = _extract_agentcore_answer(runtime_response)
        trace = _extract_agentcore_trace(runtime_response)
        selected_tools = _trace_selected_tools(trace)
        tool_errors = _trace_tool_error_count(trace)
        if not answer:
            _log_agentcore_guard_failure(
                reason="empty_answer",
                started_at=started_at,
                answer="",
                trace=trace,
            )
            raise ChatProviderUnavailable(
                "AgentCore chat provider returned an empty answer."
            )
        guard_result = _evaluate_prohibited_output(answer)
        if guard_result.blocked:
            _log_agentcore_guard_failure(
                reason="unsafe_output",
                started_at=started_at,
                answer=answer,
                trace=trace,
                guard_result=guard_result,
                unsafe_output_block=True,
            )
            raise ChatProviderUnavailable(
                "AgentCore chat provider returned an unsafe answer."
            )
        try:
            _validate_answer_citations(
                answer=answer,
                allowed_evidence_ids=set(baseline.used_evidence_ids),
            )
        except ChatProviderUnavailable as exc:
            _log_agentcore_guard_failure(
                reason="citation_guard_failed",
                started_at=started_at,
                answer=answer,
                trace=trace,
                citation_guard_failure=True,
                details=str(exc),
            )
            raise ChatProviderUnavailable(
                "AgentCore chat provider returned an answer with invalid citations."
            ) from exc

        _log_agentcore_provider_result(
            started_at=started_at,
            policy_status=baseline.policy_status,
            selected_tools=selected_tools,
            tool_errors=tool_errors,
            citation_ids=baseline.used_evidence_ids,
        )
        return ChatResponse(
            answer=answer,
            citations=baseline.citations,
            policy_status=baseline.policy_status,
            used_evidence_ids=baseline.used_evidence_ids,
        )

    def _validate_configuration(self, *, started_at: float) -> None:
        if not self.runtime_url and not self.runtime_arn:
            logger.warning(
                "agentcore_chat_provider_fail_closed provider=agentcore latency_ms=%s reason=missing_runtime_target fail_closed_reason=missing_runtime_target runtime_url_configured=False runtime_arn_configured=False",
                _elapsed_ms(started_at),
            )
            raise ChatProviderUnavailable(
                "AgentCore chat provider requires AGENTCORE_RUNTIME_URL or AGENTCORE_RUNTIME_ARN."
            )
        if (
            not math.isfinite(self.timeout_seconds)
            or not MIN_AGENTCORE_TIMEOUT_SECONDS
            <= self.timeout_seconds
            <= MAX_AGENTCORE_TIMEOUT_SECONDS
        ):
            logger.warning(
                "agentcore_chat_provider_fail_closed provider=agentcore latency_ms=%s reason=invalid_timeout_seconds fail_closed_reason=invalid_timeout_seconds runtime_url_configured=%s runtime_arn_configured=%s",
                _elapsed_ms(started_at),
                bool(self.runtime_url),
                bool(self.runtime_arn),
            )
            raise ChatProviderUnavailable(
                "AgentCore chat provider requires AGENTCORE_RUNTIME_TIMEOUT_SECONDS between 1 and 30."
            )

    def _invoke_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.runtime_invoker is not None:
            return self.runtime_invoker(payload)
        if self.runtime_url:
            return self._invoke_http_runtime(payload)
        return self._invoke_aws_runtime(payload)

    def _invoke_http_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = UrlRequest(
            f"{self.runtime_url}/invocations",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ChatProviderUnavailable(
                "AgentCore chat provider request failed."
            ) from exc

    def _invoke_aws_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client().invoke_agent_runtime(
                agentRuntimeArn=self.runtime_arn,
                runtimeSessionId=_agentcore_runtime_session_id(payload),
                payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                qualifier=self.qualifier,
            )
            body = response["response"].read()
            return json.loads(body.decode("utf-8"))
        except (BotoCoreError, ClientError, KeyError, OSError, json.JSONDecodeError) as exc:
            raise ChatProviderUnavailable(
                "AgentCore chat provider request failed."
            ) from exc


def runtime_chat_provider_from_settings(settings: Settings) -> AgentCoreChatProvider:
    return AgentCoreChatProvider(
        runtime_url=settings.agentcore_runtime_url,
        runtime_arn=settings.agentcore_runtime_arn,
        region_name=settings.agentcore_runtime_region
        or settings.bedrock_chat_region
        or None,
        qualifier=settings.agentcore_runtime_qualifier,
        timeout_seconds=settings.agentcore_runtime_timeout_seconds,
    )


def _agentcore_runtime_payload(
    *,
    request: ChatProviderInput,
    baseline: ChatResponse,
) -> dict[str, Any]:
    return {
        "input": {
            "message": request.message,
            "ticker": request.candidate.ticker,
            "candidate": request.candidate.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in request.evidence],
            "baseline": baseline.model_dump(mode="json"),
        }
    }


def _agentcore_runtime_session_id(payload: dict[str, Any]) -> str:
    ticker = str(payload.get("input", {}).get("ticker", "stockbrief"))
    return f"stockbrief-{ticker}-{uuid.uuid4().hex}"


def _extract_agentcore_answer(response: dict[str, Any]) -> str:
    if response.get("status") not in (None, "success"):
        return ""
    payload = response.get("response") or response.get("output") or response
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    answer = payload.get("answer") or payload.get("response")
    if isinstance(answer, str):
        return answer.strip()
    message = payload.get("message")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        return _extract_bedrock_text({"output": {"message": message}})
    return ""


def _extract_agentcore_trace(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("response") or response.get("output") or {}
    if isinstance(payload, dict) and isinstance(payload.get("trace"), dict):
        return payload["trace"]
    trace = response.get("trace")
    return trace if isinstance(trace, dict) else {}


def _trace_selected_tools(trace: dict[str, Any]) -> list[str]:
    selected_tools = trace.get("selected_tools")
    if isinstance(selected_tools, list):
        return [str(tool) for tool in selected_tools if tool]
    metrics = trace.get("metrics")
    if not isinstance(metrics, dict):
        return []
    summary = metrics.get("summary")
    if not isinstance(summary, dict):
        return []
    tool_usage = summary.get("tool_usage")
    if isinstance(tool_usage, dict):
        return [str(name) for name in tool_usage]
    return []


def _trace_tool_error_count(trace: dict[str, Any]) -> int:
    tool_errors = trace.get("tool_errors")
    if isinstance(tool_errors, int):
        return tool_errors
    tool_calls = trace.get("tool_calls")
    if isinstance(tool_calls, list):
        return sum(
            1
            for call in tool_calls
            if isinstance(call, dict) and call.get("status") == "error"
        )
    metrics = trace.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    summary = metrics.get("summary")
    if not isinstance(summary, dict):
        return 0
    tool_usage = summary.get("tool_usage")
    if not isinstance(tool_usage, dict):
        return 0
    return sum(
        int(stats.get("execution_stats", {}).get("error_count", 0))
        for stats in tool_usage.values()
        if isinstance(stats, dict)
    )


def _log_agentcore_guard_failure(
    *,
    reason: str,
    started_at: float,
    answer: str,
    trace: dict[str, Any],
    guard_result: OutputGuardResult | None = None,
    citation_guard_failure: bool = False,
    unsafe_output_block: bool = False,
    details: str = "",
) -> None:
    fingerprint = (
        hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16] if answer else ""
    )
    logger.warning(
        "agentcore_chat_provider_fail_closed provider=agentcore latency_ms=%s reason=%s fail_closed_reason=%s citation_guard_failure=%s unsafe_output_block=%s answer_length=%s answer_sha256_prefix=%s matched_terms=%s likely_false_positive=%s selected_tools=%s tool_errors=%s citation_ids=%s details=%s",
        _elapsed_ms(started_at),
        reason,
        reason,
        citation_guard_failure,
        unsafe_output_block,
        len(answer),
        fingerprint,
        ",".join(guard_result.matched_terms) if guard_result else "",
        guard_result.likely_false_positive if guard_result else False,
        ",".join(_trace_selected_tools(trace)),
        _trace_tool_error_count(trace),
        ",".join(str(item) for item in trace.get("citation_ids", []) or []),
        details,
    )


def _log_agentcore_provider_result(
    *,
    started_at: float,
    policy_status: str,
    selected_tools: list[str],
    tool_errors: int,
    citation_ids: list[str],
) -> None:
    logger.info(
        "agentcore_chat_provider_result provider=agentcore latency_ms=%s policy_status=%s selected_tools=%s tool_errors=%s citation_ids=%s fail_closed_reason=none citation_guard_failure=False unsafe_output_block=False",
        _elapsed_ms(started_at),
        policy_status,
        ",".join(selected_tools),
        tool_errors,
        ",".join(citation_ids),
    )
