from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.orm import (
    EvidenceChunk,
    FinancialStatement,
    PriceMetric,
    RecommendationReason,
    RecommendationScore,
    RiskSignal,
    SourceDocument,
    Stock,
)
from app.services.recommendation.engine import SCORE_VERSION, calculate_recommendation_score
from app.services.recommendation.models import (
    EvidenceReference,
    RecommendationScoreInput,
    RecommendationScoreResult,
    RiskPenaltyInput,
)


def materialize_recommendation_scores(
    session: Session,
    *,
    as_of_date: date,
    tickers: list[str] | None = None,
) -> dict[str, int | str]:
    stocks = _stocks(session, tickers)
    created = 0
    updated = 0
    reason_count = 0
    risk_count = 0

    for stock in stocks:
        score_input, evidence_source_ids = _score_input(session, stock.ticker, as_of_date)
        result = calculate_recommendation_score(score_input)
        score, was_created = _upsert_score(session, result)
        created += int(was_created)
        updated += int(not was_created)
        reason_count += _replace_reasons(session, score, result, evidence_source_ids)
        risk_count += _replace_risks(session, result, score_input.risks)

    session.flush()
    return {
        "score_version": SCORE_VERSION,
        "processed": len(stocks),
        "created": created,
        "updated": updated,
        "reasons": reason_count,
        "risk_signals": risk_count,
    }


def _stocks(session: Session, tickers: list[str] | None) -> list[Stock]:
    statement = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker.asc())
    if tickers is not None:
        statement = statement.where(Stock.ticker.in_(tickers))
    return list(session.scalars(statement).all())


def _score_input(
    session: Session,
    ticker: str,
    as_of_date: date,
) -> tuple[RecommendationScoreInput, dict[str, list[str]]]:
    financials = _financials(session, ticker, as_of_date)
    price = _price_metrics(session, ticker, as_of_date)
    evidence, evidence_source_ids = _evidence(session, ticker, as_of_date)
    risks = _risks(session, ticker, as_of_date)
    data_freshness = _data_freshness(as_of_date, financials[0], price, evidence)

    price_metrics = _metric_dict(price)
    fallback_price_metrics = None
    if price is not None and "FALLBACK" in price.source.upper():
        fallback_price_metrics = price_metrics
        price_metrics = None

    return (
        RecommendationScoreInput(
            ticker=ticker,
            as_of_date=as_of_date,
            financials=_financial_statement_dict(financials[0]),
            previous_financials=_financial_statement_dict(financials[1]),
            price_metrics=price_metrics,
            fallback_price_metrics=fallback_price_metrics,
            data_freshness=data_freshness,
            evidence=evidence,
            risks=risks,
        ),
        evidence_source_ids,
    )


def _financials(
    session: Session,
    ticker: str,
    as_of_date: date,
) -> tuple[FinancialStatement | None, FinancialStatement | None]:
    rows = [
        financial
        for financial, source in session.execute(
            select(FinancialStatement, SourceDocument)
            .outerjoin(SourceDocument, SourceDocument.id == FinancialStatement.source_document_id)
            .where(
                FinancialStatement.ticker == ticker,
                FinancialStatement.period_end_date <= as_of_date,
            )
            .order_by(FinancialStatement.period_end_date.desc())
        ).all()
        if not _is_mock_source_document(source)
    ][:2]
    current = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    return current, previous


def _price_metrics(
    session: Session,
    ticker: str,
    as_of_date: date,
) -> PriceMetric | None:
    rows = session.scalars(
        select(PriceMetric)
        .where(PriceMetric.ticker == ticker, PriceMetric.trade_date <= as_of_date)
        .order_by(PriceMetric.trade_date.desc())
    )
    for row in rows:
        if not _is_mock_or_fallback_provider(row.source):
            return row
    return None


def _evidence(
    session: Session,
    ticker: str,
    as_of_date: date,
) -> tuple[list[EvidenceReference], dict[str, list[str]]]:
    cutoff = datetime.combine(as_of_date, time.max, tzinfo=timezone.utc)
    rows = session.execute(
        select(EvidenceChunk, SourceDocument)
        .join(SourceDocument, SourceDocument.id == EvidenceChunk.source_document_id)
        .where(
            EvidenceChunk.ticker == ticker,
            or_(
                EvidenceChunk.fetched_at <= cutoff,
                EvidenceChunk.published_at <= cutoff,
            ),
            ~EvidenceChunk.evidence_id.startswith("ev_mock_", autoescape=True),
        )
        .order_by(EvidenceChunk.fetched_at.desc(), EvidenceChunk.evidence_id.asc())
    ).all()
    evidence = [
        EvidenceReference(
            evidence_id=chunk.evidence_id,
            evidence_type=chunk.evidence_type,
            source_type=source.source_type,
            confidence=float(chunk.confidence),
        )
        for chunk, source in rows
    ]
    source_ids: dict[str, list[str]] = {
        chunk.evidence_id: [str(chunk.source_document_id)]
        for chunk, _ in rows
    }
    return evidence, source_ids


def _risks(session: Session, ticker: str, as_of_date: date) -> list[RiskPenaltyInput]:
    rows = session.scalars(
        select(RiskSignal)
        .where(RiskSignal.ticker == ticker, RiskSignal.as_of_date == as_of_date)
        .order_by(RiskSignal.risk_tag.asc())
    ).all()
    return [
        RiskPenaltyInput(
            risk_tag=row.risk_tag,
            penalty_points=float(row.penalty_points),
            display_text=row.display_text,
            evidence_ids=list(row.evidence_ids or []),
        )
        for row in rows
    ]


def _upsert_score(
    session: Session,
    result: RecommendationScoreResult,
) -> tuple[RecommendationScore, bool]:
    score = session.scalars(
        select(RecommendationScore).where(
            RecommendationScore.ticker == result.ticker,
            RecommendationScore.as_of_date == result.as_of_date,
            RecommendationScore.score_version == result.score_version,
        )
    ).one_or_none()
    values = {
        "total_score": Decimal(str(result.total_score)).quantize(Decimal("0.01")),
        "evidence_level": result.evidence_level,
        "component_scores": [component.model_dump() for component in result.components],
        "evidence_count": result.evidence_count,
        "missing_data": list(result.missing_data),
        "data_freshness": {
            **result.data_freshness,
            "fallback_data": list(result.fallback_data),
            "risk_penalty": result.risk_penalty,
        },
        "is_candidate_eligible": result.evidence_count >= 2,
    }
    if score is None:
        score = RecommendationScore(
            ticker=result.ticker,
            as_of_date=result.as_of_date,
            score_version=result.score_version,
            **values,
        )
        session.add(score)
        session.flush()
        return score, True

    for key, value in values.items():
        setattr(score, key, value)
    session.flush()
    return score, False


def _replace_reasons(
    session: Session,
    score: RecommendationScore,
    result: RecommendationScoreResult,
    source_ids: dict[str, list[str]],
) -> int:
    session.execute(
        delete(RecommendationReason).where(
            RecommendationReason.recommendation_score_id == score.id
        )
    )
    session.flush()
    for index, reason in enumerate(result.reasons, start=1):
        session.add(
            RecommendationReason(
                reason_id=(
                    f"rsn_{result.ticker}_{result.as_of_date.isoformat()}_"
                    f"{result.score_version}_{index}"
                ),
                recommendation_score_id=score.id,
                ticker=result.ticker,
                component=reason.component,
                summary=reason.summary,
                evidence_ids=list(reason.evidence_ids),
                source_document_ids=_source_document_ids(reason.evidence_ids, source_ids),
            )
        )
    return len(result.reasons)


def _replace_risks(
    session: Session,
    result: RecommendationScoreResult,
    risks: list[RiskPenaltyInput],
) -> int:
    session.execute(
        delete(RiskSignal).where(
            RiskSignal.ticker == result.ticker,
            RiskSignal.as_of_date == result.as_of_date,
        )
    )
    session.flush()
    for risk in risks:
        session.add(
            RiskSignal(
                ticker=result.ticker,
                as_of_date=result.as_of_date,
                risk_tag=risk.risk_tag,
                severity=_severity(risk.penalty_points),
                penalty_points=Decimal(str(risk.penalty_points)).quantize(Decimal("0.01")),
                display_text=risk.display_text,
                description=risk.display_text,
                evidence_ids=list(risk.evidence_ids),
            )
        )
    return len(risks)


def _financial_statement_dict(row: FinancialStatement | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "revenue": row.revenue,
        "operating_income": row.operating_income,
        "net_income": row.net_income,
        "total_assets": row.total_assets,
        "total_liabilities": row.total_liabilities,
        "total_equity": row.total_equity,
    }


def _metric_dict(row: PriceMetric | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "market_cap": row.market_cap,
        "volume": row.volume,
        "trading_value": row.trading_value,
        "momentum_20d": row.momentum_20d,
        "volatility_20d": row.volatility_20d,
    }


def _data_freshness(
    as_of_date: date,
    financials: FinancialStatement | None,
    price: PriceMetric | None,
    evidence: list[EvidenceReference],
) -> dict[str, Any]:
    freshness: dict[str, Any] = {"as_of": as_of_date.isoformat()}
    if financials is not None:
        freshness["financials_as_of"] = financials.period_end_date.isoformat()
    if price is not None:
        freshness["price_as_of"] = price.trade_date.isoformat()
    if evidence:
        freshness["evidence_count"] = len(evidence)
    return freshness


def _source_document_ids(
    evidence_ids: list[str],
    source_ids: dict[str, list[str]],
) -> list[str]:
    return sorted(
        {
            source_id
            for evidence_id in evidence_ids
            for source_id in source_ids.get(evidence_id, [])
        }
    )


def _is_mock_source_document(source: SourceDocument | None) -> bool:
    if source is None:
        return False
    values = [
        source.source_name,
        source.external_id,
        source.title,
        str(source.metadata_ or ""),
    ]
    return any(_is_mock_or_fallback_provider(value) for value in values)


def _is_mock_or_fallback_provider(value: object) -> bool:
    normalized = str(value or "").upper()
    return "MOCK" in normalized or "FALLBACK" in normalized


def _severity(penalty_points: float) -> str:
    if penalty_points >= 5:
        return "high"
    if penalty_points > 0:
        return "medium"
    return "low"
