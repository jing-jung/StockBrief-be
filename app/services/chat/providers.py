from __future__ import annotations

import hashlib
import logging
import math
import re
import time
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
MIN_BEDROCK_MAX_TOKENS = 64
MAX_BEDROCK_MAX_TOKENS = 1200
MIN_BEDROCK_TIMEOUT_SECONDS = 1.0
MAX_BEDROCK_TIMEOUT_SECONDS = 30.0
EVIDENCE_ID_REFERENCE_PATTERN = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9_.:-]{2,})\]")
LIKELY_FALSE_POSITIVE_PATTERNS = (
    re.compile(r"(매수|매도)\s*(권유|추천|조언|의견)\s*(?:가|은|는)?\s*아닙니다"),
    re.compile(r"(목표가|진입가|손절가)\s*(?:를|은|는)?\s*(제시|제공|산정)\s*하지\s*않"),  # policy-scan: allow model-output-guard
)


@dataclass(frozen=True)
class ChatProviderInput:
    message: str
    candidate: RecommendationCandidateResponse
    evidence: list[StockEvidenceItemResponse]


@dataclass(frozen=True)
class OutputGuardResult:
    matched_terms: tuple[str, ...]
    likely_false_positive: bool = False

    @property
    def blocked(self) -> bool:
        return bool(self.matched_terms)


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
        self.model_id = model_id.strip()
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
        started_at = time.monotonic()
        self._validate_configuration(started_at=started_at)

        baseline = compose_chat_answer(
            message=request.message,
            candidate=request.candidate,
            evidence=request.evidence,
        )
        if baseline.policy_status != "allowed":
            _log_bedrock_provider_result(
                started_at=started_at,
                policy_status=baseline.policy_status,
                citation_retry=False,
            )
            return baseline

        citation_retry = False
        answer = self._request_answer(
            request=request,
            baseline=baseline,
            started_at=started_at,
        )
        try:
            _validate_answer_citations(
                answer=answer,
                allowed_evidence_ids=set(baseline.used_evidence_ids),
            )
        except ChatProviderUnavailable as exc:
            _log_bedrock_guard_failure(
                reason="citation_guard_failed",
                model_id=self.model_id,
                region_name=self.region_name,
                answer=answer,
                started_at=started_at,
                citation_guard_failure=True,
                details=str(exc),
            )
            answer = self._request_answer(
                request=request,
                baseline=baseline,
                citation_retry=True,
                started_at=started_at,
            )
            citation_retry = True
            try:
                _validate_answer_citations(
                    answer=answer,
                    allowed_evidence_ids=set(baseline.used_evidence_ids),
                )
            except ChatProviderUnavailable as retry_exc:
                _log_bedrock_guard_failure(
                    reason="citation_retry_failed",
                    model_id=self.model_id,
                    region_name=self.region_name,
                    answer=answer,
                    started_at=started_at,
                    citation_guard_failure=True,
                    details=str(retry_exc),
                )
                raise retry_exc from exc

        _log_bedrock_provider_result(
            started_at=started_at,
            policy_status=baseline.policy_status,
            citation_retry=citation_retry,
        )
        return ChatResponse(
            answer=answer,
            citations=baseline.citations,
            policy_status=baseline.policy_status,
            used_evidence_ids=baseline.used_evidence_ids,
        )

    def _validate_configuration(self, *, started_at: float) -> None:
        if not self.model_id:
            _log_bedrock_configuration_failure(
                reason="missing_model_id",
                started_at=started_at,
                model_id_configured=False,
                region_configured=bool(self.region_name),
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider requires BEDROCK_CHAT_MODEL_ID or an "
                "inference profile id."
            )
        if not (
            MIN_BEDROCK_MAX_TOKENS <= self.max_tokens <= MAX_BEDROCK_MAX_TOKENS
        ):
            _log_bedrock_configuration_failure(
                reason="invalid_max_tokens",
                started_at=started_at,
                model_id_configured=True,
                region_configured=bool(self.region_name),
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider requires BEDROCK_CHAT_MAX_TOKENS between 64 and 1200."
            )
        if not (
            math.isfinite(self.temperature) and 0.0 <= self.temperature <= 1.0
        ):
            _log_bedrock_configuration_failure(
                reason="invalid_temperature",
                started_at=started_at,
                model_id_configured=True,
                region_configured=bool(self.region_name),
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider requires BEDROCK_CHAT_TEMPERATURE between 0.0 and 1.0."
            )
        if (
            not math.isfinite(self.timeout_seconds)
            or not MIN_BEDROCK_TIMEOUT_SECONDS
            <= self.timeout_seconds
            <= MAX_BEDROCK_TIMEOUT_SECONDS
        ):
            _log_bedrock_configuration_failure(
                reason="invalid_timeout_seconds",
                started_at=started_at,
                model_id_configured=True,
                region_configured=bool(self.region_name),
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider requires BEDROCK_CHAT_TIMEOUT_SECONDS between 1 and 30."
            )

    def _request_answer(
        self,
        *,
        request: ChatProviderInput,
        baseline: ChatResponse,
        started_at: float,
        citation_retry: bool = False,
    ) -> str:
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
                                    citation_retry=citation_retry,
                                )
                            }
                        ],
                    }
                ],
                inferenceConfig={
                    "maxTokens": self.max_tokens,
                    "temperature": 0.0 if citation_retry else self.temperature,
                },
            )
        except (BotoCoreError, ClientError) as exc:
            logger.warning(
                "bedrock_chat_provider_fail_closed provider=bedrock latency_ms=%s reason=runtime_request_failed fail_closed_reason=runtime_request_failed citation_guard_failure=False unsafe_output_block=False model_id=%s region_name=%s error_type=%s error_code=%s",
                _elapsed_ms(started_at),
                self.model_id,
                self.region_name,
                type(exc).__name__,
                _bedrock_error_code(exc),
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider request failed."
            ) from exc

        answer = _extract_bedrock_text(response)
        if not answer:
            _log_bedrock_guard_failure(
                reason="empty_answer",
                model_id=self.model_id,
                region_name=self.region_name,
                answer="",
                started_at=started_at,
            )
            raise ChatProviderUnavailable("Bedrock chat provider returned an empty answer.")
        guard_result = _evaluate_prohibited_output(answer)
        if guard_result.blocked:
            _log_bedrock_guard_failure(
                reason="unsafe_output",
                model_id=self.model_id,
                region_name=self.region_name,
                answer=answer,
                started_at=started_at,
                guard_result=guard_result,
                unsafe_output_block=True,
            )
            raise ChatProviderUnavailable(
                "Bedrock chat provider returned an unsafe answer."
            )
        return answer


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
    if name == "agentcore":
        if settings is None:
            raise ChatProviderUnavailable("Runtime chat provider requires settings.")
        from app.services.chat.runtime_provider import (
            runtime_chat_provider_from_settings,
        )

        return runtime_chat_provider_from_settings(settings)
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


def _user_prompt(
    *,
    request: ChatProviderInput,
    baseline: ChatResponse,
    citation_retry: bool = False,
) -> str:
    candidate = request.candidate
    citable_evidence = _citable_evidence(request=request, baseline=baseline)
    allowed_citation_ids = set(_citation_ids(baseline.citations))
    evidence_lines = [
        (
            f"- id={item.id}; type={item.type}; title={item.title}; "
            f"summary={item.summary}; source={item.source_name}; "
            f"published_at={item.published_at}; as_of_date={item.as_of_date}"
        )
        for item in citable_evidence
    ]
    reason_lines = [
        (
            f"- component={reason.component}; summary={reason.summary}; "
            f"evidence_ids={_reason_evidence_ids(reason.evidence_ids, allowed_citation_ids)}"
        )
        for reason in candidate.recommendation_reasons[:4]
    ]
    citation_hint = ", ".join(_citation_ids(baseline.citations)) or "none"

    lines = [
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
        f"Allowed citation IDs: {citation_hint}",
    ]
    if citation_retry:
        lines.append(
            "Previous answer failed citation validation. Rewrite the answer using only exact allowed citation IDs."
        )
    lines.append(
        "Draft a concise Korean explanation in 4-7 sentences. "
        "Focus on evidence-based review points and avoid unsupported conclusions. "
        "Every factual sentence must include one or more exact allowed citation IDs in square brackets. "
        "Do not use [1], source names, titles, or invented IDs as citations. "
        "Cite only the allowed citation IDs shown above."
    )
    return "\n".join(lines)


def _citable_evidence(
    *,
    request: ChatProviderInput,
    baseline: ChatResponse,
) -> list[StockEvidenceItemResponse]:
    evidence_by_id = {item.id: item for item in request.evidence}
    seen: set[str] = set()
    citable_evidence: list[StockEvidenceItemResponse] = []
    for citation in baseline.citations:
        evidence_id = citation.evidence_id
        if evidence_id not in evidence_by_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        citable_evidence.append(evidence_by_id[evidence_id])
    return citable_evidence


def _reason_evidence_ids(
    evidence_ids: list[str],
    allowed_citation_ids: set[str],
) -> str:
    filtered = [
        evidence_id for evidence_id in evidence_ids if evidence_id in allowed_citation_ids
    ]
    return ", ".join(filtered) or "none"


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


def _evaluate_prohibited_output(value: str) -> OutputGuardResult:
    normalized = value.casefold()
    matched_terms = tuple(
        term for term in PROHIBITED_MODEL_OUTPUT_TERMS if term.casefold() in normalized
    )
    return OutputGuardResult(
        matched_terms=matched_terms,
        likely_false_positive=bool(matched_terms)
        and any(pattern.search(value) for pattern in LIKELY_FALSE_POSITIVE_PATTERNS),
    )


def _contains_prohibited_output(value: str) -> bool:
    return _evaluate_prohibited_output(value).blocked


def _log_bedrock_guard_failure(
    *,
    reason: str,
    model_id: str,
    region_name: str | None,
    answer: str,
    started_at: float,
    guard_result: OutputGuardResult | None = None,
    citation_guard_failure: bool = False,
    unsafe_output_block: bool = False,
    details: str = "",
) -> None:
    fingerprint = (
        hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16] if answer else ""
    )
    logger.warning(
        "bedrock_chat_provider_fail_closed provider=bedrock latency_ms=%s reason=%s fail_closed_reason=%s citation_guard_failure=%s unsafe_output_block=%s model_id=%s region_name=%s answer_length=%s answer_sha256_prefix=%s matched_terms=%s likely_false_positive=%s details=%s",
        _elapsed_ms(started_at),
        reason,
        reason,
        citation_guard_failure,
        unsafe_output_block,
        model_id,
        region_name,
        len(answer),
        fingerprint,
        ",".join(guard_result.matched_terms) if guard_result else "",
        guard_result.likely_false_positive if guard_result else False,
        details,
    )


def _log_bedrock_configuration_failure(
    *,
    reason: str,
    started_at: float,
    model_id_configured: bool,
    region_configured: bool,
) -> None:
    logger.warning(
        "bedrock_chat_provider_fail_closed provider=bedrock latency_ms=%s reason=%s fail_closed_reason=%s citation_guard_failure=False unsafe_output_block=False model_id_configured=%s region_configured=%s",
        _elapsed_ms(started_at),
        reason,
        reason,
        model_id_configured,
        region_configured,
    )


def _log_bedrock_provider_result(
    *,
    started_at: float,
    policy_status: str,
    citation_retry: bool,
) -> None:
    logger.info(
        "bedrock_chat_provider_result provider=bedrock latency_ms=%s policy_status=%s citation_retry=%s fail_closed_reason=none citation_guard_failure=False unsafe_output_block=False",
        _elapsed_ms(started_at),
        policy_status,
        citation_retry,
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _bedrock_error_code(exc: BotoCoreError | ClientError) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", ""))
    return ""


def _validate_answer_citations(
    *,
    answer: str,
    allowed_evidence_ids: set[str],
) -> None:
    cited_evidence_ids = set(EVIDENCE_ID_REFERENCE_PATTERN.findall(answer))
    unexpected_evidence_ids = cited_evidence_ids - allowed_evidence_ids
    if unexpected_evidence_ids:
        raise ChatProviderUnavailable(
            "Bedrock chat provider returned unsupported evidence citations."
        )
    if not allowed_evidence_ids:
        raise ChatProviderUnavailable(
            "Bedrock chat provider requires allowed evidence citations."
        )

    if not cited_evidence_ids:
        raise ChatProviderUnavailable(
            "Bedrock chat provider returned an answer without evidence citations."
        )
