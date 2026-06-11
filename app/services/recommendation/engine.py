from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.recommendation.models import (
    EvidenceReference,
    RecommendationReason,
    RecommendationScoreInput,
    RecommendationScoreResult,
    ScoreComponent,
)

COMPONENT_WEIGHTS: dict[str, int] = {
    "financial_stability": 20,
    "profitability": 15,
    "growth": 15,
    "valuation": 10,
    "news_attention": 10,
    "disclosure_event": 10,
    "liquidity": 10,
    "momentum_volatility": 10,
}

COMPONENT_LABELS: dict[str, str] = {
    "financial_stability": "재무 안정성",
    "profitability": "수익성",
    "growth": "성장성",
    "valuation": "가치 지표",
    "news_attention": "뉴스 관심도",
    "disclosure_event": "공시 이벤트",
    "liquidity": "유동성",
    "momentum_volatility": "모멘텀과 변동성",
}


def calculate_recommendation_score(
    score_input: RecommendationScoreInput,
) -> RecommendationScoreResult:
    components = [
        _component(
            "financial_stability",
            score_input,
            _score_financial_stability,
            ["financials.total_liabilities", "financials.total_equity"],
        ),
        _component(
            "profitability",
            score_input,
            _score_profitability,
            ["financials.revenue", "financials.operating_income"],
        ),
        _component(
            "growth",
            score_input,
            _score_growth,
            ["financials.revenue", "previous_financials.revenue"],
        ),
        _component(
            "valuation",
            score_input,
            _score_valuation,
            ["price_metrics.market_cap", "financials.net_income"],
        ),
        _component(
            "news_attention",
            score_input,
            _score_news_attention,
            ["evidence.news"],
        ),
        _component(
            "disclosure_event",
            score_input,
            _score_disclosure_event,
            ["evidence.disclosure"],
        ),
        _component(
            "liquidity",
            score_input,
            _score_liquidity,
            ["price_metrics.volume", "price_metrics.trading_value"],
        ),
        _component(
            "momentum_volatility",
            score_input,
            _score_momentum_volatility,
            ["price_metrics.momentum_20d", "price_metrics.volatility_20d"],
        ),
    ]

    missing_data = sorted(
        {missing for component in components for missing in component.missing_data}
    )
    fallback_data = sorted(
        {component.name for component in components if component.used_fallback}
    )
    risk_penalty = sum(risk.penalty_points for risk in score_input.risks)
    raw_total = sum(component.weighted_score for component in components)
    total_score = _clamp(round(raw_total - risk_penalty, 1), 0, 100)
    evidence_count = len({evidence.evidence_id for evidence in score_input.evidence})

    return RecommendationScoreResult(
        ticker=score_input.ticker,
        as_of_date=score_input.as_of_date,
        total_score=total_score,
        components=components,
        missing_data=missing_data,
        fallback_data=fallback_data,
        risk_penalty=risk_penalty,
        evidence_count=evidence_count,
        evidence_level=_evidence_level(evidence_count, len(missing_data)),
        reasons=_build_reasons(components, score_input.evidence),
    )


def _component(
    name: str,
    score_input: RecommendationScoreInput,
    scorer: Callable[[RecommendationScoreInput], tuple[float | None, bool]],
    required_keys: list[str],
) -> ScoreComponent:
    raw_score, used_fallback = scorer(score_input)
    missing_data = [] if raw_score is not None else [f"{name}.inputs"]
    weighted_score = 0.0
    if raw_score is not None:
        weighted_score = _clamp(raw_score, 0, 100) * COMPONENT_WEIGHTS[name] / 100

    return ScoreComponent(
        name=name,
        weight=COMPONENT_WEIGHTS[name],
        raw_score=None if raw_score is None else _clamp(raw_score, 0, 100),
        weighted_score=weighted_score,
        reason=_component_reason(name, raw_score, used_fallback),
        input_refs=required_keys,
        evidence_ids=_evidence_ids_for(score_input.evidence, name),
        used_fallback=used_fallback,
        missing_data=missing_data,
    )


def _score_financial_stability(
    score_input: RecommendationScoreInput,
) -> tuple[float | None, bool]:
    financials = score_input.financials or {}
    liabilities = _number(financials.get("total_liabilities"))
    equity = _number(financials.get("total_equity"))
    assets = _number(financials.get("total_assets"))
    if liabilities is None or equity is None or equity <= 0:
        return None, False

    debt_to_equity = liabilities / equity
    debt_score = _inverse_ratio_score(debt_to_equity, excellent=0.4, weak=1.8)
    if assets and assets > 0:
        equity_ratio = equity / assets
        ratio_score = _ratio_score(equity_ratio, weak=0.2, excellent=0.7)
        return (debt_score * 0.65 + ratio_score * 0.35), False
    return debt_score, False


def _score_profitability(
    score_input: RecommendationScoreInput,
) -> tuple[float | None, bool]:
    financials = score_input.financials or {}
    revenue = _number(financials.get("revenue"))
    operating_income = _number(financials.get("operating_income"))
    net_income = _number(financials.get("net_income"))
    if revenue is None or revenue <= 0 or operating_income is None:
        return None, False

    operating_margin = operating_income / revenue
    operating_score = _ratio_score(operating_margin, weak=-0.05, excellent=0.2)
    if net_income is None:
        return operating_score, False
    net_margin = net_income / revenue
    net_score = _ratio_score(net_margin, weak=-0.05, excellent=0.15)
    return operating_score * 0.65 + net_score * 0.35, False


def _score_growth(score_input: RecommendationScoreInput) -> tuple[float | None, bool]:
    current = score_input.financials or {}
    previous = score_input.previous_financials or {}
    current_revenue = _number(current.get("revenue"))
    previous_revenue = _number(previous.get("revenue"))
    current_income = _number(current.get("operating_income"))
    previous_income = _number(previous.get("operating_income"))
    if (
        current_revenue is None
        or previous_revenue is None
        or previous_revenue <= 0
        or current_income is None
        or previous_income is None
    ):
        return None, False

    revenue_growth = (current_revenue - previous_revenue) / previous_revenue
    revenue_score = _ratio_score(revenue_growth, weak=-0.15, excellent=0.25)
    if previous_income <= 0:
        income_score = 50 if current_income > 0 else 35
    else:
        income_growth = (current_income - previous_income) / previous_income
        income_score = _ratio_score(income_growth, weak=-0.2, excellent=0.3)
    return revenue_score * 0.6 + income_score * 0.4, False


def _score_valuation(score_input: RecommendationScoreInput) -> tuple[float | None, bool]:
    price_metrics, used_fallback = _price_metrics(score_input)
    financials = score_input.financials or {}
    market_cap = _number(price_metrics.get("market_cap"))
    net_income = _number(financials.get("net_income"))
    equity = _number(financials.get("total_equity"))
    if market_cap is None or market_cap <= 0:
        return None, used_fallback

    scores = []
    if net_income is not None and net_income > 0:
        per = market_cap / net_income
        scores.append(_inverse_ratio_score(per, excellent=8, weak=35))
    if equity is not None and equity > 0:
        pbr = market_cap / equity
        scores.append(_inverse_ratio_score(pbr, excellent=0.8, weak=4))
    if not scores:
        return None, used_fallback
    return sum(scores) / len(scores), used_fallback


def _score_news_attention(
    score_input: RecommendationScoreInput,
) -> tuple[float | None, bool]:
    news = [item for item in score_input.evidence if item.source_type == "news"]
    if not news:
        return None, False
    confidence = sum(item.confidence for item in news) / len(news)
    return min(100, 45 + len(news) * 20 + confidence * 15), False


def _score_disclosure_event(
    score_input: RecommendationScoreInput,
) -> tuple[float | None, bool]:
    disclosures = [
        item for item in score_input.evidence if item.source_type == "disclosure"
    ]
    if not disclosures:
        return None, False
    confidence = sum(item.confidence for item in disclosures) / len(disclosures)
    return min(100, 50 + len(disclosures) * 18 + confidence * 12), False


def _score_liquidity(score_input: RecommendationScoreInput) -> tuple[float | None, bool]:
    price_metrics, used_fallback = _price_metrics(score_input)
    volume = _number(price_metrics.get("volume"))
    trading_value = _number(price_metrics.get("trading_value"))
    market_cap = _number(price_metrics.get("market_cap"))
    if volume is None or trading_value is None:
        return None, used_fallback

    volume_score = _ratio_score(volume, weak=100_000, excellent=5_000_000)
    trading_score = _ratio_score(trading_value, weak=5_000_000_000, excellent=300_000_000_000)
    if market_cap is None:
        return volume_score * 0.45 + trading_score * 0.55, used_fallback
    market_score = _ratio_score(market_cap, weak=100_000_000_000, excellent=20_000_000_000_000)
    return volume_score * 0.3 + trading_score * 0.45 + market_score * 0.25, used_fallback


def _score_momentum_volatility(
    score_input: RecommendationScoreInput,
) -> tuple[float | None, bool]:
    price_metrics, used_fallback = _price_metrics(score_input)
    momentum = _number(price_metrics.get("momentum_20d"))
    volatility = _number(price_metrics.get("volatility_20d"))
    if momentum is None or volatility is None:
        return None, used_fallback

    momentum_score = _ratio_score(momentum, weak=-0.1, excellent=0.15)
    volatility_score = _inverse_ratio_score(volatility, excellent=0.12, weak=0.55)
    return momentum_score * 0.55 + volatility_score * 0.45, used_fallback


def _price_metrics(score_input: RecommendationScoreInput) -> tuple[dict[str, Any], bool]:
    if score_input.price_metrics:
        return score_input.price_metrics, False
    if score_input.fallback_price_metrics:
        return score_input.fallback_price_metrics, True
    return {}, False


def _ratio_score(value: float, *, weak: float, excellent: float) -> float:
    if excellent == weak:
        return 50
    ratio = (value - weak) / (excellent - weak)
    return _clamp(30 + ratio * 60, 30, 90)


def _inverse_ratio_score(value: float, *, excellent: float, weak: float) -> float:
    if weak == excellent:
        return 50
    ratio = (weak - value) / (weak - excellent)
    return _clamp(30 + ratio * 60, 30, 90)


def _evidence_level(evidence_count: int, missing_data_count: int) -> str:
    if evidence_count >= 4 and missing_data_count <= 1:
        return "strong"
    if evidence_count >= 2:
        return "medium"
    return "weak"


def _build_reasons(
    components: list[ScoreComponent],
    evidence: list[EvidenceReference],
) -> list[RecommendationReason]:
    top_components = sorted(
        components,
        key=lambda component: (component.weighted_score, component.weight),
        reverse=True,
    )[:3]
    fallback_evidence_ids = [item.evidence_id for item in evidence[:2]]

    reasons = []
    for component in top_components:
        evidence_ids = component.evidence_ids or fallback_evidence_ids
        reasons.append(
            RecommendationReason(
                component=component.name,
                summary=(
                    f"{COMPONENT_LABELS[component.name]} 항목에서 공개 데이터 기준 "
                    "검토 포인트가 확인됩니다."
                ),
                contribution=component.weighted_score,
                evidence_ids=evidence_ids,
            )
        )
    return reasons


def _component_reason(name: str, raw_score: float | None, used_fallback: bool) -> str:
    label = COMPONENT_LABELS[name]
    if raw_score is None:
        return f"{label} 항목은 입력 데이터가 부족해 확인이 필요합니다."
    if used_fallback:
        return f"{label} 항목은 fallback 데이터를 기준으로 검토했습니다."
    return f"{label} 항목은 공개 데이터 기준으로 계산했습니다."


def _evidence_ids_for(evidence: list[EvidenceReference], component: str) -> list[str]:
    matching = [
        item.evidence_id for item in evidence if item.evidence_type == component
    ]
    if matching:
        return matching
    if component == "news_attention":
        return [item.evidence_id for item in evidence if item.source_type == "news"]
    if component == "disclosure_event":
        return [
            item.evidence_id for item in evidence if item.source_type == "disclosure"
        ]
    return []


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

