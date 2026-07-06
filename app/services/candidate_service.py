from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CandidateEvidenceSummaryContract,
    RecommendationCandidateListResponse,
    RecommendationCandidateResponse,
    RecommendationReasonResponse,
    RiskProfile,
    ScoreComponentResponse,
    StockBriefContract,
    StockCandidateContractData,
    StockCandidateContractItem,
    StockPriceContract,
    StockScoreBreakdownContract,
    StockScoreContract,
    StockScoreResponse,
)
from app.orm import (
    EvidenceChunk,
    PriceMetric,
    RecommendationReason,
    RecommendationScore,
    RiskSignal,
    SourceDocument,
    Stock,
)
from app.services.response_helpers import pagination
from app.services.recommendation.engine import SCORE_VERSION
from app.ticker import validate_ticker

logger = logging.getLogger(__name__)

DISCLAIMER = "공개 데이터 기반 검토 후보이며 최종 투자 판단은 사용자에게 있습니다."
LEGACY_MOCK_EVIDENCE_PREFIX = "ev_mock_"
EVIDENCE_LEVEL_MAP = {
    "strong": "strong",
    "medium": "medium",
    "moderate": "medium",
    "weak": "weak",
    "limited": "weak",
    "insufficient": "weak",
}


class CandidateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_recommendation_candidates(
        self,
        *,
        risk_profile: RiskProfile,
        market: str | None,
        sector: str | None,
        limit: int,
        score_version: str | None = None,
    ) -> RecommendationCandidateListResponse:
        rows = self._candidate_rows(
            market=market,
            sector=sector,
            score_version=score_version,
        )
        risk_counts = (
            {}
            if risk_profile == "aggressive"
            else self._candidate_risk_counts(rows)
        )
        rows = _sort_candidate_rows(
            rows=rows,
            risk_profile=risk_profile,
            risk_counts=risk_counts,
        )[:limit]
        candidates = self._candidate_responses(rows)
        return RecommendationCandidateListResponse(
            items=candidates,
            count=len(candidates),
            risk_profile=risk_profile,
            disclaimer=DISCLAIMER,
        )

    def get_recommendation_candidate(
        self,
        ticker: str,
        *,
        score_version: str | None = None,
    ) -> RecommendationCandidateResponse:
        stock, score = self.candidate_row(ticker, score_version=score_version)
        return self.candidate_response(stock, score)

    def list_stock_candidates(
        self,
        *,
        risk_profile: RiskProfile,
        market: str | None,
        sector: str | None,
        sort: str,
        limit: int,
        offset: int,
        score_version: str | None = None,
    ) -> StockCandidateContractData:
        base_statement = self._stock_candidate_base_statement(
            market=market,
            sector=sector,
            score_version=score_version,
        )
        count_statement, as_of_statement = self._stock_candidate_aggregate_statements(
            base_statement,
        )
        total = self.session.scalar(count_statement) or 0
        as_of = self.session.scalar(as_of_statement)
        rows = self.session.execute(
            self._order_stock_candidate_statement(
                statement=base_statement,
                sort=sort,
                risk_profile=risk_profile,
            )
            .limit(limit)
            .offset(offset)
        ).all()
        candidate_rows = [(row[0], row[1]) for row in rows]
        items = self._stock_candidate_contract_items(candidate_rows)
        return StockCandidateContractData(
            as_of=as_of or datetime.now(timezone.utc).date(),
            items=items,
            pagination=pagination(limit=limit, offset=offset, total=total),
        )

    def _stock_candidate_base_statement(
        self,
        *,
        market: str | None,
        sector: str | None,
        score_version: str | None = None,
    ):
        selected_scores = self._selected_scores_subquery(score_version)
        risk_counts = (
            select(
                RiskSignal.ticker.label("ticker"),
                RiskSignal.as_of_date.label("as_of_date"),
                func.count(RiskSignal.id).label("risk_count"),
            )
            .group_by(RiskSignal.ticker, RiskSignal.as_of_date)
            .subquery()
        )
        statement = (
            select(
                Stock,
                RecommendationScore,
                risk_counts.c.risk_count,
            )
            .join(RecommendationScore, RecommendationScore.ticker == Stock.ticker)
            .join(selected_scores, selected_scores.c.score_id == RecommendationScore.id)
            .outerjoin(
                risk_counts,
                (risk_counts.c.ticker == Stock.ticker)
                & (risk_counts.c.as_of_date == RecommendationScore.as_of_date),
            )
            .where(
                selected_scores.c.score_rank == 1,
                RecommendationScore.missing_data.is_not(None),
                RecommendationScore.data_freshness.is_not(None),
                RecommendationScore.data_freshness["as_of"].as_string().is_not(None),
            )
        )
        if market:
            statement = statement.where(Stock.market == market)
        if sector:
            statement = statement.where(Stock.sector == sector)
        return statement

    def _stock_candidate_aggregate_statements(self, base_statement):
        candidate_index = (
            base_statement.with_only_columns(
                Stock.ticker.label("ticker"),
                RecommendationScore.as_of_date.label("as_of_date"),
            )
            .order_by(None)
            .subquery()
        )
        return (
            select(func.count()).select_from(candidate_index),
            select(func.max(candidate_index.c.as_of_date)),
        )

    def _latest_price_volume_subquery(self):
        latest_price_dates = (
            select(
                PriceMetric.ticker.label("ticker"),
                func.max(PriceMetric.trade_date).label("trade_date"),
            )
            .group_by(PriceMetric.ticker)
            .subquery()
        )
        return (
            select(
                PriceMetric.ticker.label("ticker"),
                PriceMetric.volume.label("volume"),
            )
            .join(
                latest_price_dates,
                (PriceMetric.ticker == latest_price_dates.c.ticker)
                & (PriceMetric.trade_date == latest_price_dates.c.trade_date),
            )
            .subquery()
        )

    def _order_stock_candidate_statement(
        self,
        *,
        statement,
        sort: str,
        risk_profile: RiskProfile,
    ):
        selected_columns = statement.selected_columns
        risk_count = func.coalesce(selected_columns.risk_count, 0)

        if sort == "volume_desc":
            # Volume is a global ordering key, so the price join must run before
            # LIMIT/OFFSET. The aggregate count/as_of queries still avoid it.
            latest_prices = self._latest_price_volume_subquery()
            latest_volume = func.coalesce(latest_prices.c.volume, 0)
            statement = statement.add_columns(latest_prices.c.volume).outerjoin(
                latest_prices,
                latest_prices.c.ticker == Stock.ticker,
            )
            return statement.order_by(
                latest_volume.desc(),
                RecommendationScore.total_score.desc(),
                Stock.ticker.asc(),
            )
        if sort == "updated_desc":
            return statement.order_by(
                RecommendationScore.as_of_date.desc(),
                RecommendationScore.total_score.desc(),
                Stock.ticker.asc(),
            )
        if risk_profile == "conservative":
            return statement.order_by(
                risk_count.asc(),
                RecommendationScore.total_score.desc(),
                Stock.ticker.asc(),
            )
        if risk_profile == "aggressive":
            return statement.order_by(RecommendationScore.total_score.desc(), Stock.ticker.asc())
        return statement.order_by(
            (RecommendationScore.total_score - risk_count * Decimal("0.5")).desc(),
            RecommendationScore.total_score.desc(),
            Stock.ticker.asc(),
        )

    def stock_score(
        self,
        ticker: str,
        *,
        score_version: str | None = None,
    ) -> StockScoreResponse:
        _, score = self.candidate_row(ticker, score_version=score_version)
        candidate = self.candidate_response_from_score(score)
        return StockScoreResponse(
            ticker=candidate.ticker,
            as_of_date=score.as_of_date,
            recommendation_score=candidate.recommendation_score,
            score_components=candidate.score_components,
            risk_tags=candidate.risk_tags,
            evidence_level=candidate.evidence_level,
            evidence_count=candidate.evidence_count,
            missing_data=candidate.missing_data,
            data_freshness=candidate.data_freshness,
            disclaimer=DISCLAIMER,
        )

    def candidate_response_from_score(
        self,
        score: RecommendationScore,
    ) -> RecommendationCandidateResponse:
        stock = self.stock_or_404(score.ticker)
        return self.candidate_response(stock, score)

    def stock_or_404(self, ticker: str) -> Stock:
        validate_ticker(ticker)
        stock = self.session.get(Stock, ticker)
        if stock is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "STOCK_NOT_FOUND",
                    "message": "Stock was not found.",
                },
            )
        return stock

    def candidate_row(
        self,
        ticker: str,
        *,
        score_version: str | None = None,
    ) -> tuple[Stock, RecommendationScore]:
        validate_ticker(ticker)
        selected_scores = self._selected_scores_subquery(score_version)
        row = self.session.execute(
            select(Stock, RecommendationScore)
            .join(RecommendationScore, RecommendationScore.ticker == Stock.ticker)
            .join(selected_scores, selected_scores.c.score_id == RecommendationScore.id)
            .where(Stock.ticker == ticker, selected_scores.c.score_rank == 1)
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "STOCK_NOT_FOUND",
                    "message": "Recommendation candidate was not found.",
                },
            )
        stock, score = row
        return stock, score

    def candidate_response(
        self,
        stock: Stock,
        score: RecommendationScore,
    ) -> RecommendationCandidateResponse:
        reasons = self.session.scalars(
            select(RecommendationReason)
            .where(RecommendationReason.recommendation_score_id == score.id)
            .order_by(RecommendationReason.created_at.asc())
        ).all()
        risks = self.session.scalars(
            select(RiskSignal)
            .where(
                RiskSignal.ticker == stock.ticker,
                RiskSignal.as_of_date == score.as_of_date,
            )
            .order_by(RiskSignal.created_at.asc())
        ).all()
        evidence_summary = self._candidate_evidence_summaries([stock.ticker]).get(stock.ticker)
        return _candidate_response_from_loaded(
            stock=stock,
            score=score,
            reasons=list(reasons),
            risks=list(risks),
            evidence_summary=evidence_summary,
        )

    def latest_price_contract(self, ticker: str) -> StockPriceContract | None:
        price = self.session.scalars(
            select(PriceMetric)
            .where(PriceMetric.ticker == ticker)
            .order_by(PriceMetric.trade_date.desc())
        ).first()
        if price is None:
            return None
        return StockPriceContract(
            close=_optional_float(price.close_price),
            change_rate=_optional_float(price.change_rate),
            volume=_optional_float(price.volume),
            trade_date=price.trade_date,
        )

    def stock_score_contract(self, score: RecommendationScore) -> StockScoreContract:
        return _stock_score_contract(score)

    def stock_brief_contract(self, *, stock: Stock, score: RecommendationScore) -> StockBriefContract:
        return StockBriefContract(
            summary=(
                f"{stock.company_name}는 공개 데이터 기반 점수와 근거로 "
                "검토 후보에 포함된 종목입니다."
            ),
            risk_notes=[
                "OpenDART, NAVER, KRX 등 연결된 원천 데이터 기준입니다.",
                "투자 판단 전 원문과 최신 데이터를 확인해야 합니다.",
            ],
            as_of=score.as_of_date,
        )

    def _candidate_rows(
        self,
        *,
        market: str | None,
        sector: str | None,
        score_version: str | None = None,
    ) -> list[tuple[Stock, RecommendationScore]]:
        selected_scores = self._selected_scores_subquery(score_version)
        statement = (
            select(Stock, RecommendationScore)
            .join(RecommendationScore, RecommendationScore.ticker == Stock.ticker)
            .join(selected_scores, selected_scores.c.score_id == RecommendationScore.id)
            .where(
                selected_scores.c.score_rank == 1,
            )
        )
        if market:
            statement = statement.where(Stock.market == market)
        if sector:
            statement = statement.where(Stock.sector == sector)

        rows = self.session.execute(statement).all()
        return [
            (stock, score)
            for stock, score in rows
            if _passes_evidence_gate(score)
        ]

    def _selected_scores_subquery(self, score_version: str | None):
        selected_score_version = score_version or SCORE_VERSION
        statement = select(
            RecommendationScore.id.label("score_id"),
            func.row_number()
            .over(
                partition_by=RecommendationScore.ticker,
                order_by=(
                    RecommendationScore.as_of_date.desc(),
                    RecommendationScore.created_at.desc(),
                    RecommendationScore.id.desc(),
                ),
            )
            .label("score_rank"),
        )
        statement = statement.where(RecommendationScore.score_version == selected_score_version)
        return statement.subquery()

    def _candidate_responses(
        self,
        rows: list[tuple[Stock, RecommendationScore]],
    ) -> list[RecommendationCandidateResponse]:
        if not rows:
            return []
        score_ids = [score.id for _, score in rows]
        tickers = [stock.ticker for stock, _ in rows]
        as_of_dates = [score.as_of_date for _, score in rows]

        reasons_by_score_id: dict[object, list[RecommendationReason]] = defaultdict(list)
        reasons = self.session.scalars(
            select(RecommendationReason)
            .where(RecommendationReason.recommendation_score_id.in_(score_ids))
            .order_by(RecommendationReason.created_at.asc())
        ).all()
        for reason in reasons:
            reasons_by_score_id[reason.recommendation_score_id].append(reason)

        risks_by_key: dict[tuple[str, date], list[RiskSignal]] = defaultdict(list)
        risks = self.session.scalars(
            select(RiskSignal)
            .where(
                RiskSignal.ticker.in_(tickers),
                RiskSignal.as_of_date.in_(as_of_dates),
            )
            .order_by(RiskSignal.created_at.asc())
        ).all()
        for risk in risks:
            risks_by_key[(risk.ticker, risk.as_of_date)].append(risk)
        evidence_summaries = self._candidate_evidence_summaries(tickers)

        return [
            _candidate_response_from_loaded(
                stock=stock,
                score=score,
                reasons=reasons_by_score_id.get(score.id, []),
                risks=risks_by_key.get((stock.ticker, score.as_of_date), []),
                evidence_summary=evidence_summaries.get(stock.ticker),
            )
            for stock, score in rows
        ]

    def _stock_candidate_contract_items(
        self,
        rows: list[tuple[Stock, RecommendationScore]],
    ) -> list[StockCandidateContractItem]:
        tickers = [stock.ticker for stock, _ in rows]
        prices = self._latest_price_contracts(tickers)
        evidence_summaries = self._candidate_evidence_summaries(tickers)
        return [
            StockCandidateContractItem(
                ticker=stock.ticker,
                name=stock.company_name,
                market=stock.market,
                sector=stock.sector,
                score=_stock_score_contract(score),
                price=prices.get(stock.ticker),
                evidence_summary=evidence_summaries.get(
                    stock.ticker,
                    CandidateEvidenceSummaryContract(
                        news_count=0,
                        disclosure_count=0,
                        latest_at=None,
                    ),
                ),
            )
            for stock, score in rows
        ]

    def _latest_price_contracts(self, tickers: list[str]) -> dict[str, StockPriceContract]:
        if not tickers:
            return {}
        rows = self.session.scalars(
            select(PriceMetric)
            .where(PriceMetric.ticker.in_(tickers))
            .order_by(PriceMetric.ticker.asc(), PriceMetric.trade_date.desc())
        ).all()
        prices: dict[str, StockPriceContract] = {}
        for row in rows:
            if row.ticker in prices:
                continue
            prices[row.ticker] = StockPriceContract(
                close=_optional_float(row.close_price),
                change_rate=_optional_float(row.change_rate),
                volume=_optional_float(row.volume),
                trade_date=row.trade_date,
            )
        return prices

    def _candidate_evidence_summaries(
        self,
        tickers: list[str],
    ) -> dict[str, CandidateEvidenceSummaryContract]:
        if not tickers:
            return {}
        summaries: dict[str, dict[str, object]] = {
            ticker: {"news": 0, "disclosure": 0, "latest": None}
            for ticker in tickers
        }
        rows = self.session.execute(
            select(EvidenceChunk, SourceDocument)
            .join(SourceDocument, SourceDocument.id == EvidenceChunk.source_document_id)
            .where(
                EvidenceChunk.ticker.in_(tickers),
                SourceDocument.source_type.in_(["news", "disclosure"]),
                ~EvidenceChunk.evidence_id.startswith("ev_mock_", autoescape=True),
            )
        ).all()
        for chunk, source in rows:
            summary = summaries.setdefault(
                chunk.ticker,
                {"news": 0, "disclosure": 0, "latest": None},
            )
            if source.source_type == "news":
                summary["news"] = int(summary["news"]) + 1
            elif source.source_type == "disclosure":
                summary["disclosure"] = int(summary["disclosure"]) + 1
            latest = summary["latest"]
            published_at = chunk.published_at or source.published_at
            if published_at is not None and (latest is None or published_at > latest):
                summary["latest"] = published_at
        return {
            ticker: CandidateEvidenceSummaryContract(
                news_count=int(summary["news"]),
                disclosure_count=int(summary["disclosure"]),
                latest_at=summary["latest"],
            )
            for ticker, summary in summaries.items()
        }

    def _candidate_risk_counts(
        self,
        rows: list[tuple[Stock, RecommendationScore]],
    ) -> dict[tuple[str, date], int]:
        if not rows:
            return {}
        tickers = [stock.ticker for stock, _ in rows]
        as_of_dates = [score.as_of_date for _, score in rows]
        counts = self.session.execute(
            select(RiskSignal.ticker, RiskSignal.as_of_date, func.count())
            .where(
                RiskSignal.ticker.in_(tickers),
                RiskSignal.as_of_date.in_(as_of_dates),
            )
            .group_by(RiskSignal.ticker, RiskSignal.as_of_date)
        ).all()
        return {
            (ticker, as_of_date): int(count)
            for ticker, as_of_date, count in counts
        }


def _candidate_response_from_loaded(
    *,
    stock: Stock,
    score: RecommendationScore,
    reasons: list[RecommendationReason],
    risks: list[RiskSignal],
    evidence_summary: CandidateEvidenceSummaryContract | None = None,
) -> RecommendationCandidateResponse:
    live_evidence_count = 0
    data_freshness = dict(score.data_freshness or {})
    if evidence_summary is not None:
        live_evidence_count = evidence_summary.news_count + evidence_summary.disclosure_count
        if evidence_summary.latest_at is not None:
            data_freshness["live_evidence_latest_at"] = _utc_isoformat(
                evidence_summary.latest_at
            )
    return RecommendationCandidateResponse(
        ticker=stock.ticker,
        name=stock.company_name,
        market=stock.market,
        sector=stock.sector,
        recommendation_score=_float(score.total_score),
        score_components=_score_components(score.component_scores),
        recommendation_reasons=[
            RecommendationReasonResponse(
                reason_id=reason.reason_id,
                component=reason.component,
                summary=reason.summary,
                evidence_ids=_public_evidence_ids(reason.evidence_ids or []),
                source_document_ids=_public_source_document_ids(reason),
            )
            for reason in reasons
        ],
        risk_tags=[risk.risk_tag for risk in risks],
        evidence_level=_evidence_level(score.evidence_level),
        evidence_count=max(score.evidence_count, live_evidence_count),
        missing_data=list(score.missing_data or []),
        data_freshness=data_freshness,
        disclaimer=DISCLAIMER,
    )


def _stock_score_contract(score: RecommendationScore) -> StockScoreContract:
    components = _score_components(score.component_scores)
    component_by_name = {component.name: component.weighted_score for component in components}
    return StockScoreContract(
        total=_float(score.total_score),
        grade=_score_grade(_float(score.total_score)),
        as_of=score.as_of_date,
        version=score.score_version,
        breakdown=StockScoreBreakdownContract(
            momentum=component_by_name.get("momentum_volatility", 0),
            liquidity=component_by_name.get("liquidity", 0),
            disclosure=component_by_name.get("disclosure_event", 0),
            news=component_by_name.get("news_attention", 0),
        ),
    )


def _utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


def _score_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _sort_stock_candidate_contract_items(
    *,
    items: list[StockCandidateContractItem],
    sort: str,
    risk_profile: RiskProfile,
    risk_counts: dict[tuple[str, date], int],
) -> list[StockCandidateContractItem]:
    if sort == "volume_desc":
        return sorted(
            items,
            key=lambda item: item.price.volume if item.price and item.price.volume else 0,
            reverse=True,
        )
    if sort == "updated_desc":
        return sorted(items, key=lambda item: item.score.as_of, reverse=True)
    if risk_profile == "conservative":
        return sorted(
            items,
            key=lambda item: (
                risk_counts.get((item.ticker, item.score.as_of), 0),
                -item.score.total,
            ),
        )
    if risk_profile == "aggressive":
        return sorted(items, key=lambda item: item.score.total, reverse=True)
    return sorted(
        items,
        key=lambda item: (
            item.score.total
            - risk_counts.get((item.ticker, item.score.as_of), 0) * 0.5
        ),
        reverse=True,
    )


def _sort_candidate_rows(
    *,
    rows: list[tuple[Stock, RecommendationScore]],
    risk_profile: RiskProfile,
    risk_counts: dict[tuple[str, date], int],
) -> list[tuple[Stock, RecommendationScore]]:
    if risk_profile == "conservative":
        return sorted(
            rows,
            key=lambda row: (
                risk_counts.get((row[0].ticker, row[1].as_of_date), 0),
                -row[1].total_score,
            ),
        )
    if risk_profile == "aggressive":
        return sorted(rows, key=lambda row: row[1].total_score, reverse=True)
    return sorted(
        rows,
        key=lambda row: (
            row[1].total_score
            - risk_counts.get((row[0].ticker, row[1].as_of_date), 0) * Decimal("0.5")
        ),
        reverse=True,
    )


def _passes_evidence_gate(score: RecommendationScore) -> bool:
    if not isinstance(score.missing_data, list):
        return False
    if not isinstance(score.data_freshness, dict) or not score.data_freshness.get("as_of"):
        return False
    return True


def _score_components(components: list[dict[str, object]]) -> list[ScoreComponentResponse]:
    responses = [
        ScoreComponentResponse(
            name=str(component["name"]),
            weight=int(component["weight"]),
            raw_score=_optional_float(component.get("raw_score")),
            weighted_score=_float(component.get("weighted_score")),
            reason=str(component.get("reason", "공개 데이터 기준 검토 포인트입니다.")),
            input_refs=[str(item) for item in component.get("input_refs", [])],
            evidence_ids=_public_evidence_ids(component.get("evidence_ids", [])),
        )
        for component in components
    ]
    if len(responses) != 8:
        logger.warning(
            "Stored recommendation score has %s components; expected 8.",
            len(responses),
        )
    return responses


def _evidence_level(value: str) -> str:
    return EVIDENCE_LEVEL_MAP.get(value, "weak")


def _public_evidence_ids(evidence_ids: object) -> list[str]:
    return [
        evidence_id
        for evidence_id in [str(item) for item in evidence_ids or []]
        if not evidence_id.startswith(LEGACY_MOCK_EVIDENCE_PREFIX)
    ]


def _public_source_document_ids(reason: RecommendationReason) -> list[str]:
    evidence_ids = [str(item) for item in reason.evidence_ids or []]
    source_document_ids = [str(item) for item in reason.source_document_ids or []]
    if len(evidence_ids) != len(source_document_ids):
        return [] if not _public_evidence_ids(evidence_ids) else source_document_ids
    return [
        source_document_id
        for evidence_id, source_document_id in zip(evidence_ids, source_document_ids, strict=True)
        if not evidence_id.startswith(LEGACY_MOCK_EVIDENCE_PREFIX)
    ]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _float(value)


def _float(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)
