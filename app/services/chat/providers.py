from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.models import (
    ChatCitation,
    ChatResponse,
    RecommendationCandidateResponse,
    StockEvidenceItemResponse,
)
from app.services.chat.composer import compose_chat_answer

logger = logging.getLogger(__name__)

PROHIBITED_MODEL_OUTPUT_TERMS = (
    "매수",  # policy-scan: allow model-output-guard
    "매도",  # policy-scan: allow model-output-guard
    "목표가",  # policy-scan: allow model-output-guard
    "진입가",  # policy-scan: allow model-output-guard
    "손절가",  # policy-scan: allow model-output-guard
    "수익 보장",  # policy-scan: allow model-output-guard
)
EVIDENCE_ID_REFERENCE_PATTERN = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9_.:-]{2,})\]")


@dataclass(frozen=True)
class ChatProviderInput:
    message: str
    candidate: RecommendationCandidateResponse
    evidence: list[StockEvidenceItemResponse]


class ChatProviderUnavailable(RuntimeError):
    pass


class ChatProvider(Protocol):
    name: str

    def compose(self, request: ChatProviderInput) -> ChatResponse:
        raise NotImplementedError


class MockChatProvider:
    name = "mock"

    def compose(self, request: ChatProviderInput) -> ChatResponse:
        return compose_chat_answer(
            message=request.message,
            candidate=request.candidate,
            evidence=request.evidence,
        )


class BedrockChatProvider:
    name = "bedrock"

    def __init__(
        self,
        *,
        model_id: str,
        region_name: str | None = None,
        max_tokens: int = 700,
        temperature: float = 0.2,
        timeout_seconds: float = 8.0,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id
        self.region_name = region_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.client = client

    def _client(self):
        if self.client is not None:
            return self.client
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=self.region_name or None,
            config=Config(
                connect_timeout=self.timeout_seconds,
                read_timeout=self.timeout_seconds,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        return self.client

    def compose(self, request: ChatProviderInput) -> ChatResponse:
        if not self.model_id:
            raise ChatProviderUnavailable(
                "Bedrock chat provider requires BEDROCK_CHAT_MODEL_ID."
            )

        baseline = compose_chat_answer(
            message=request.message,
            candidate=request.candidate,
            evidence=request.evidence,
        )
        if baseline.policy_status != "allowed":
            return baseline

        try:
            response = self._client().converse(
                modelId=self.model_id,
                system=[{"text": _system_prompt()}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": _user_prompt(
                                    request=request,
                                    baseline=baseline,
                                )
                            }
                        ],
                    }
                ],
                inferenceConfig={
                    "maxTokens": self.max_tokens,
                    "temperature": self.temperature,
                },
            )
        except (BotoCoreError, ClientError) as exc:
            logger.warning(
                "bedrock_chat_provider_request_failed model_id=%s region_name=%s error_type=%s error_message=%s",
                self.model_id,
                self.region_name,
                type(exc).__name__,
                str(exc),
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider request failed."
            ) from exc

        answer = _extract_bedrock_text(response)
        if not answer:
            raise ChatProviderUnavailable("Bedrock chat provider returned an empty answer.")
        if _contains_prohibited_output(answer):
            raise ChatProviderUnavailable(
                "Bedrock chat provider returned an unsafe answer."
            )
        _validate_answer_citations(
            answer=answer,
            allowed_evidence_ids=set(baseline.used_evidence_ids),
        )

        return ChatResponse(
            answer=answer,
            citations=baseline.citations,
            policy_status=baseline.policy_status,
            used_evidence_ids=baseline.used_evidence_ids,
        )


def chat_provider_for(name: str, *, settings: Settings | None = None) -> ChatProvider:
    if name == "mock":
        return MockChatProvider()
    if name == "bedrock":
        if settings is None:
            raise ChatProviderUnavailable("Bedrock chat provider requires settings.")
        return BedrockChatProvider(
            model_id=settings.bedrock_chat_model_id,
            region_name=settings.bedrock_chat_region or None,
            max_tokens=settings.bedrock_chat_max_tokens,
            temperature=settings.bedrock_chat_temperature,
            timeout_seconds=settings.bedrock_chat_timeout_seconds,
        )
    raise ChatProviderUnavailable(f"Unsupported chat provider: {name}")


def _system_prompt() -> str:
    return (
        "You are StockBrief's evidence explanation assistant. "
        "Answer in Korean. Use only the provided candidate, scores, reasons, "
        "evidence, freshness, missing data, and risk tags. Do not invent facts, "
        "recalculate scores, or provide trading instructions, target prices, "
        "entry prices, stop-loss prices, guaranteed returns, or portfolio allocation advice. "
        "Cite evidence IDs in brackets when making factual claims."
    )


def _user_prompt(*, request: ChatProviderInput, baseline: ChatResponse) -> str:
    candidate = request.candidate
    evidence_lines = [
        (
            f"- id={item.id}; type={item.type}; title={item.title}; "
            f"summary={item.summary}; source={item.source_name}; "
            f"published_at={item.published_at}; as_of_date={item.as_of_date}"
        )
        for item in request.evidence[:6]
    ]
    reason_lines = [
        (
            f"- component={reason.component}; summary={reason.summary}; "
            f"evidence_ids={', '.join(reason.evidence_ids)}"
        )
        for reason in candidate.recommendation_reasons[:4]
    ]
    citation_hint = ", ".join(_citation_ids(baseline.citations)) or "none"

    return "\n".join(
        [
            f"User question: {request.message}",
            f"Policy status: {baseline.policy_status}",
            f"Candidate: {candidate.name}({candidate.ticker}), market={candidate.market}, sector={candidate.sector}",
            f"Recommendation score: {candidate.recommendation_score}",
            f"Evidence level/count: {candidate.evidence_level}/{candidate.evidence_count}",
            f"Risk tags: {', '.join(candidate.risk_tags) or 'none'}",
            f"Missing data: {candidate.missing_data}",
            f"Data freshness: {candidate.data_freshness}",
            "Recommendation reasons:",
            "\n".join(reason_lines) or "- none",
            "Evidence:",
            "\n".join(evidence_lines) or "- none",
            f"Required citation IDs to prefer: {citation_hint}",
            "Draft a concise Korean explanation in 4-7 sentences. "
            "Focus on evidence-based review points and avoid unsupported conclusions.",
        ]
    )


def _citation_ids(citations: list[ChatCitation]) -> list[str]:
    return [citation.evidence_id for citation in citations]


def _extract_bedrock_text(response: dict[str, Any]) -> str:
    content = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )
    if not isinstance(content, list):
        return ""
    text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
    return "\n".join(part.strip() for part in text_parts if part.strip()).strip()


def _contains_prohibited_output(value: str) -> bool:
    normalized = value.casefold()
    return any(term.casefold() in normalized for term in PROHIBITED_MODEL_OUTPUT_TERMS)


def _validate_answer_citations(
    *,
    answer: str,
    allowed_evidence_ids: set[str],
) -> None:
    if not allowed_evidence_ids:
        return

    cited_evidence_ids = set(EVIDENCE_ID_REFERENCE_PATTERN.findall(answer))
    if not cited_evidence_ids:
        raise ChatProviderUnavailable(
            "Bedrock chat provider returned an answer without evidence citations."
        )

    unexpected_evidence_ids = cited_evidence_ids - allowed_evidence_ids
    if unexpected_evidence_ids:
        raise ChatProviderUnavailable(
            "Bedrock chat provider returned unsupported evidence citations."
        )
