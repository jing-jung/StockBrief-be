from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import (
    ChatCitation,
    ChatResponse,
    PolicyStatus,
    RecommendationCandidateResponse,
    StockEvidenceItemResponse,
)


@dataclass(frozen=True)
class PolicyDecision:
    status: PolicyStatus
    category: str | None = None


TRADE_DECISION_TERMS = (  # policy-scan: allow prohibited-input-guard
    "매수",
    "매도",
    "buy",
    "sell",
    "사도",
    "팔아",
    "거래 판단",
)
TARGET_PRICE_TERMS = ("목표가", "target price", "가격 목표")  # policy-scan: allow prohibited-input-guard
ENTRY_STOP_TERMS = ("진입가", "손절가", "entry", "stop loss", "stop-loss")  # policy-scan: allow prohibited-input-guard
CERTAINTY_TERMS = ("수익 보장", "보장", "확실", "무조건", "guaranteed", "guarantee", "certain")  # policy-scan: allow prohibited-input-guard
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(https?://[^)\s]+\)")
MARKDOWN_BOLD_PATTERN = re.compile(r"\*\*([^*\n]+)\*\*")
BARE_URL_PATTERN = re.compile(r"<?https?://\S+>?")


def compose_chat_answer(
    *,
    message: str,
    candidate: RecommendationCandidateResponse,
    evidence: list[StockEvidenceItemResponse],
) -> ChatResponse:
    decision = evaluate_policy(message)
    citations = _citations(candidate, evidence)
    used_evidence_ids = [citation.evidence_id for citation in citations]

    if decision.status == "blocked":
        return ChatResponse(
            answer=(
                "확정적 결과나 성과를 전제로 한 요청에는 답할 수 없습니다. "
                "공개 데이터 기반 검토 포인트와 확인 필요 사항만 설명할 수 있습니다."
            ),
            citations=citations,
            policy_status=decision.status,
            used_evidence_ids=used_evidence_ids,
        )

    if decision.status == "redirected":
        return ChatResponse(
            answer=(
                "해당 요청은 거래 실행 판단이나 특정 가격 지점 산정으로 이어질 수 있어 "
                "직접 답하지 않습니다. 대신 공개 데이터 기준의 추천 후보 점수, 추천 이유, "
                "근거, 리스크를 설명하겠습니다.\n\n"
                f"{_candidate_summary(candidate)}\n"
                f"{_reason_summary(candidate)}\n"
                f"{_evidence_summary(candidate, evidence)}\n"
                f"{_risk_summary(candidate)}"
            ),
            citations=citations,
            policy_status=decision.status,
            used_evidence_ids=used_evidence_ids,
        )

    return ChatResponse(
        answer=(
            f"{_candidate_summary(candidate)}\n"
            f"{_reason_summary(candidate)}\n"
            f"{_evidence_summary(candidate, evidence)}\n"
            f"{_risk_summary(candidate)}\n"
            f"{_freshness_summary(candidate)}"
        ),
        citations=citations,
        policy_status=decision.status,
        used_evidence_ids=used_evidence_ids,
    )


def evaluate_policy(message: str) -> PolicyDecision:
    normalized = message.casefold()
    if _contains_any(normalized, CERTAINTY_TERMS):
        return PolicyDecision(status="blocked", category="certainty_or_outcome")
    if _contains_any(normalized, TRADE_DECISION_TERMS):
        return PolicyDecision(status="redirected", category="trade_decision")
    if _contains_any(normalized, TARGET_PRICE_TERMS):
        return PolicyDecision(status="redirected", category="target_price")
    if _contains_any(normalized, ENTRY_STOP_TERMS):
        return PolicyDecision(status="redirected", category="entry_or_risk_price")
    return PolicyDecision(status="allowed")


def normalize_chat_answer(answer: str) -> str:
    normalized = MARKDOWN_LINK_PATTERN.sub(r"[\1]", answer)
    normalized = MARKDOWN_BOLD_PATTERN.sub(r"\1", normalized)
    normalized = BARE_URL_PATTERN.sub("", normalized)
    lines = [line.rstrip() for line in normalized.splitlines()]
    return "\n".join(lines).strip()


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term.casefold() in value for term in terms)


def _candidate_summary(candidate: RecommendationCandidateResponse) -> str:
    evidence_note = ""
    if candidate.evidence_level == "weak" or candidate.evidence_count < 2:
        evidence_note = " 다만 현재 근거가 부족한 후보로 분류되어 추가 확인이 필요합니다."

    return (
        f"{candidate.name}({candidate.ticker})는 공개 데이터 기준 추천 후보 점수 "
        f"{candidate.recommendation_score:.1f}점으로 계산되어 검토 후보에 포함되었습니다."
        f"{evidence_note}"
    )


def _reason_summary(candidate: RecommendationCandidateResponse) -> str:
    if not candidate.recommendation_reasons:
        return "추천 이유 데이터가 충분하지 않아 세부 기여 요소 설명은 제한됩니다."

    reasons = [
        f"- {reason.summary}"
        for reason in candidate.recommendation_reasons[:3]
    ]
    return "주요 추천 이유는 다음과 같습니다.\n" + "\n".join(reasons)


def _evidence_summary(
    candidate: RecommendationCandidateResponse,
    evidence: list[StockEvidenceItemResponse],
) -> str:
    if candidate.evidence_level == "weak" or candidate.evidence_count < 2 or len(evidence) < 2:
        return "근거가 부족하다: 현재 확인 가능한 근거가 제한적이므로 추가 공개 데이터 확인이 필요합니다."

    summaries = [
        f"- [{item.id}] {item.type}: {item.summary}"
        for item in evidence[:4]
    ]
    return "연결된 근거 요약입니다.\n" + "\n".join(summaries)


def _risk_summary(candidate: RecommendationCandidateResponse) -> str:
    if not candidate.risk_tags:
        return "리스크 태그는 현재 응답에 표시되지 않았지만, 누락 데이터와 최신 기준일 확인은 필요합니다."
    return "리스크/확인 필요 사항: " + ", ".join(candidate.risk_tags[:5])


def _freshness_summary(candidate: RecommendationCandidateResponse) -> str:
    as_of = candidate.data_freshness.get("as_of")
    if not as_of:
        return "데이터 기준일은 응답에서 확인되지 않았습니다."
    return f"데이터 기준일은 {as_of}입니다."


def _citations(
    candidate: RecommendationCandidateResponse,
    evidence: list[StockEvidenceItemResponse],
) -> list[ChatCitation]:
    reason_ids = [
        evidence_id
        for reason in candidate.recommendation_reasons
        for evidence_id in reason.evidence_ids
    ]
    ordered_ids = list(dict.fromkeys(reason_ids))
    evidence_by_id = {item.id: item for item in evidence}

    selected = [
        evidence_by_id[evidence_id]
        for evidence_id in ordered_ids
        if evidence_id in evidence_by_id
    ]
    if len(selected) < 2:
        selected.extend(item for item in evidence if item.id not in {row.id for row in selected})

    return [
        ChatCitation(
            evidence_id=item.id,
            type=item.type,
            title=item.title,
            source_name=item.source_name,
            source_url=item.source_url,
            published_at=item.published_at,
            as_of_date=item.as_of_date,
        )
        for item in selected[:4]
    ]
