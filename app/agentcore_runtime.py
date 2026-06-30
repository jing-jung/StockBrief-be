from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from typing import Any

from botocore.config import Config as BotocoreConfig
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError
from strands import Agent, tool
from strands.models import BedrockModel, Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from app.config import Settings, get_settings
from app.models import (
    ChatResponse,
    RecommendationCandidateResponse,
    StockEvidenceItemResponse,
)
from app.services.chat.composer import evaluate_policy

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

RUNTIME_SYSTEM_PROMPT = (
    "You are StockBrief's evidence explanation runtime. Answer in Korean using "
    "only the registered read-only tools and the provided stored score/evidence. "
    "Do not recalculate scores, mutate data, or provide trading instructions, "
    "target prices, entry prices, stop-loss prices, guaranteed returns, or "
    "portfolio allocation advice. Cite exact evidence IDs in square brackets."
)

app = FastAPI(title="StockBrief AgentCore Runtime", version="0.1.0")


class InvocationRequest(BaseModel):
    input: dict[str, Any]


class InvocationResponse(BaseModel):
    response: dict[str, Any]
    status: str = "success"


class RuntimeContext(BaseModel):
    message: str
    ticker: str
    candidate: RecommendationCandidateResponse
    evidence: list[StockEvidenceItemResponse]
    baseline: ChatResponse


@dataclass
class RuntimeTrace:
    selected_tools: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def record_tool(
        self,
        *,
        name: str,
        started_at: float,
        status: str,
        error_type: str = "",
    ) -> None:
        if name not in self.selected_tools:
            self.selected_tools.append(name)
        self.tool_calls.append(
            {
                "name": name,
                "latency_ms": _elapsed_ms(started_at),
                "status": status,
                "error_type": error_type,
            }
        )


class StockBriefDevModel(Model):
    def __init__(self) -> None:
        self.config: dict[str, Any] = {"model_id": "stockbrief-dev-tool-model"}

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self.config)

    async def structured_output(
        self,
        output_model: type[Any],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        raise NotImplementedError("StockBriefDevModel does not support structured output.")
        yield {}

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[dict[str, Any]] | None = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        if not _messages_include_tool_result(messages):
            tool_name = _first_tool_name(tool_specs) or "get_candidate"
            yield {"messageStart": {"role": "assistant"}}
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {
                        "toolUse": {
                            "name": tool_name,
                            "toolUseId": "tooluse_stockbrief_dev_1",
                        }
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": "{}"}},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            yield _metadata_event(input_tokens=64, output_tokens=8, latency_ms=1)
            return

        citation_ids = (invocation_state or {}).get("citation_ids") or ["evidence"]
        first_citation_id = str(citation_ids[0])
        answer = (
            "저장된 점수와 근거 기준으로 검토 후보 사유를 설명합니다. "
            f"핵심 근거는 [{first_citation_id}]에서 확인된 공개 데이터이며, "
            "누락 데이터와 최신성은 별도 확인이 필요합니다."
        )
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"text": answer},
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield _metadata_event(input_tokens=72, output_tokens=42, latency_ms=1)


@app.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@app.post("/invocations", response_model=InvocationResponse)
async def invoke_agent(request: InvocationRequest) -> InvocationResponse:
    try:
        context = RuntimeContext.model_validate(request.input)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid invocation input.") from exc

    trace = RuntimeTrace()
    settings = get_settings()
    agent = Agent(
        model=_runtime_model(settings),
        tools=_runtime_tools(context=context, trace=trace),
        system_prompt=RUNTIME_SYSTEM_PROMPT,
        callback_handler=None,
    )
    try:
        result = agent(
            _runtime_prompt(context),
            invocation_state={"citation_ids": context.baseline.used_evidence_ids},
            limits={"turns": settings.agentcore_runtime_max_turns},
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Agent processing failed.") from exc

    answer = str(result).strip()
    metrics_summary = _metrics_summary(result)
    response_trace = {
        "selected_tools": trace.selected_tools,
        "tool_calls": trace.tool_calls,
        "tool_errors": sum(1 for call in trace.tool_calls if call["status"] == "error"),
        "metrics": {"summary": metrics_summary},
        "policy_status": context.baseline.policy_status,
        "citation_ids": context.baseline.used_evidence_ids,
    }
    _log_runtime_trace(response_trace)
    return InvocationResponse(
        response={
            "answer": answer,
            "policy_status": context.baseline.policy_status,
            "used_evidence_ids": context.baseline.used_evidence_ids,
            "trace": response_trace,
        }
    )


def _runtime_model(settings: Settings) -> Model:
    if settings.agentcore_runtime_use_dev_model:
        return StockBriefDevModel()
    return BedrockModel(
        model_id=settings.bedrock_chat_model_id,
        region_name=settings.bedrock_chat_region or None,
        temperature=settings.bedrock_chat_temperature,
        max_tokens=settings.bedrock_chat_max_tokens,
        boto_client_config=BotocoreConfig(
            connect_timeout=settings.bedrock_chat_timeout_seconds,
            read_timeout=settings.bedrock_chat_timeout_seconds,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _runtime_tools(
    *,
    context: RuntimeContext,
    trace: RuntimeTrace,
) -> list[Any]:
    @tool
    def get_candidate() -> dict[str, Any]:
        """Return the current stored recommendation candidate snapshot."""

        return _recorded_tool(
            trace=trace,
            name="get_candidate",
            call=lambda: context.candidate.model_dump(mode="json"),
        )

    @tool
    def get_score() -> dict[str, Any]:
        """Return stored score fields for the current candidate."""

        def call() -> dict[str, Any]:
            candidate = context.candidate
            return {
                "ticker": candidate.ticker,
                "recommendation_score": candidate.recommendation_score,
                "score_components": [
                    component.model_dump(mode="json")
                    for component in candidate.score_components
                ],
                "recommendation_reasons": [
                    reason.model_dump(mode="json")
                    for reason in candidate.recommendation_reasons
                ],
                "evidence_level": candidate.evidence_level,
                "evidence_count": candidate.evidence_count,
                "missing_data": candidate.missing_data,
                "data_freshness": candidate.data_freshness,
                "risk_tags": candidate.risk_tags,
            }

        return _recorded_tool(trace=trace, name="get_score", call=call)

    @tool
    def get_evidence(evidence_id: str = "") -> dict[str, Any]:
        """Return DB-filtered evidence for the current candidate.

        Args:
            evidence_id: Optional evidence ID to select one evidence item.
        """

        def call() -> dict[str, Any]:
            items = context.evidence
            if evidence_id:
                items = [item for item in items if item.id == evidence_id]
            return {"items": [item.model_dump(mode="json") for item in items[:6]]}

        return _recorded_tool(trace=trace, name="get_evidence", call=call)

    @tool
    def rag_search(query: str = "") -> dict[str, Any]:
        """Search DB-filtered evidence summaries for the current candidate.

        Args:
            query: Search text from the user's question.
        """

        def call() -> dict[str, Any]:
            normalized = query.casefold().strip()
            rows = []
            for item in context.evidence:
                haystack = " ".join(
                    [
                        item.id,
                        item.title,
                        item.summary,
                        item.source_name,
                    ]
                ).casefold()
                if not normalized or normalized in haystack:
                    rows.append(item)
            return {"items": [item.model_dump(mode="json") for item in rows[:4]]}

        return _recorded_tool(trace=trace, name="rag_search", call=call)

    @tool
    def policy_check(message: str = "") -> dict[str, Any]:
        """Evaluate StockBrief chat policy for the message.

        Args:
            message: Message text to evaluate. Defaults to current user message.
        """

        def call() -> dict[str, Any]:
            decision = evaluate_policy(message or context.message)
            return {"status": decision.status, "category": decision.category}

        return _recorded_tool(trace=trace, name="policy_check", call=call)

    return [get_candidate, get_score, get_evidence, rag_search, policy_check]


def _recorded_tool(
    *,
    trace: RuntimeTrace,
    name: str,
    call: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started_at = time.monotonic()
    try:
        result = call()
    except Exception as exc:
        trace.record_tool(
            name=name,
            started_at=started_at,
            status="error",
            error_type=type(exc).__name__,
        )
        return {
            "status": "error",
            "content": [{"text": f"{name} failed"}],
        }
    trace.record_tool(name=name, started_at=started_at, status="success")
    return {"status": "success", "content": [{"json": result}]}


def _runtime_prompt(context: RuntimeContext) -> str:
    citation_ids = ", ".join(context.baseline.used_evidence_ids) or "none"
    return "\n".join(
        [
            f"User question: {context.message}",
            f"Ticker: {context.ticker}",
            f"Allowed citation IDs: {citation_ids}",
            "Use read-only tools before answering. Keep the answer concise.",
        ]
    )


def _messages_include_tool_result(messages: Messages) -> bool:
    for message in messages:
        content = message.get("content", [])
        for block in content:
            if isinstance(block, dict) and "toolResult" in block:
                return True
    return False


def _first_tool_name(tool_specs: list[ToolSpec] | None) -> str | None:
    for tool_spec in tool_specs or []:
        if not isinstance(tool_spec, dict):
            continue
        name = tool_spec.get("name")
        if isinstance(name, str):
            return name
        nested = tool_spec.get("toolSpec")
        if isinstance(nested, dict) and isinstance(nested.get("name"), str):
            return nested["name"]
    return None


def _metadata_event(
    *,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
) -> StreamEvent:
    return {
        "metadata": {
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": input_tokens + output_tokens,
            },
            "metrics": {"latencyMs": latency_ms},
        }
    }


def _metrics_summary(result: Any) -> dict[str, Any]:
    metrics = getattr(result, "metrics", None)
    if metrics is None or not hasattr(metrics, "get_summary"):
        return {}
    summary = metrics.get_summary()
    return summary if isinstance(summary, dict) else {}


def _log_runtime_trace(trace: dict[str, Any]) -> None:
    token_usage = (
        trace.get("metrics", {})
        .get("summary", {})
        .get("accumulated_usage", {})
    )
    logger.info(
        "agentcore_runtime_trace selected_tools=%s tool_errors=%s citation_ids=%s policy_status=%s token_usage=%s",
        ",".join(trace["selected_tools"]),
        trace["tool_errors"],
        ",".join(trace["citation_ids"]),
        trace["policy_status"],
        token_usage,
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))
