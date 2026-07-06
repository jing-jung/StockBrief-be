from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    EvidencePreviewContract,
    StockBriefContract,
    StockContractItem,
    StockDetailContractData,
    StockScoreBreakdownContract,
    StockScoreContract,
    StockSearchContractData,
    StockSearchContractItem,
)
from app.orm import CompanyIdentifier, Stock
from app.services.candidate_service import CandidateService
from app.services.evidence_service import EvidenceService, contract_source_type
from app.services.response_helpers import pagination


class StockService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.candidates = CandidateService(session)
        self.evidence = EvidenceService(session)

    def search(
        self,
        *,
        q: str,
        market: str | None,
        limit: int,
        offset: int,
    ) -> StockSearchContractData:
        raw_query = q.strip()
        statement = select(Stock)
        if raw_query:
            query = f"%{escape_like_query(raw_query)}%"
            compact_query = compact_search_text(raw_query)
            conditions = [
                Stock.ticker.like(query, escape="\\"),
                Stock.company_name.like(query, escape="\\"),
                Stock.company_name_en.ilike(query, escape="\\"),
            ]
            if compact_query != raw_query.casefold():
                compact_like = f"%{escape_like_query(compact_query)}%"
                conditions.extend(
                    [
                        func.replace(func.lower(Stock.ticker), " ", "").like(compact_like, escape="\\"),
                        func.replace(func.lower(Stock.company_name), " ", "").like(compact_like, escape="\\"),
                        func.replace(func.lower(Stock.company_name_en), " ", "").like(compact_like, escape="\\"),
                    ]
                )
            statement = statement.where(or_(*conditions))
        if market:
            statement = statement.where(Stock.market == market)

        total_statement = select(func.count()).select_from(statement.subquery())
        total = self.session.scalar(total_statement) or 0

        statement = statement.order_by(Stock.ticker.asc()).offset(offset).limit(limit)
        rows = self.session.scalars(statement).all()
        corp_codes = self.corp_codes([stock.ticker for stock in rows])

        return StockSearchContractData(
            items=[
                StockSearchContractItem(
                    ticker=stock.ticker,
                    name=stock.company_name,
                    market=stock.market,
                    sector=stock.sector,
                    corp_code=corp_codes.get(stock.ticker),
                    match_reason=match_reason(stock, raw_query),
                )
                for stock in rows
            ],
            pagination=pagination(limit=limit, offset=offset, total=total),
        )

    def detail(self, ticker: str) -> StockDetailContractData:
        stock = self.candidates.stock_or_404(ticker)
        try:
            _, score = self.candidates.candidate_row(ticker)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            score = None
        evidence = self.evidence.items(ticker)
        return StockDetailContractData(
            stock=self.contract_item(stock),
            price=self.candidates.latest_price_contract(ticker),
            score=(
                self.candidates.stock_score_contract(score)
                if score is not None
                else self.unscored_stock_score_contract()
            ),
            brief=(
                self.candidates.stock_brief_contract(stock=stock, score=score)
                if score is not None
                else self.unscored_stock_brief_contract(stock)
            ),
            evidence_preview=[
                EvidencePreviewContract(
                    id=item.id,
                    source_type=contract_source_type(item.type),
                    title=item.title,
                    source_name=item.source_name,
                    url=item.source_url,
                    published_at=item.published_at,
                )
                for item in evidence[:3]
            ],
        )

    def contract_item(self, stock: Stock) -> StockContractItem:
        return StockContractItem(
            ticker=stock.ticker,
            name=stock.company_name,
            market=stock.market,
            sector=stock.sector,
            corp_code=self.corp_code(stock.ticker),
        )

    def unscored_stock_score_contract(self) -> StockScoreContract:
        return StockScoreContract(
            total=0.0,
            grade="확인 필요",
            as_of=date.today(),
            version="unscored",
            breakdown=StockScoreBreakdownContract(
                momentum=0.0,
                liquidity=0.0,
                disclosure=0.0,
                news=0.0,
            ),
        )

    def unscored_stock_brief_contract(self, stock: Stock) -> StockBriefContract:
        return StockBriefContract(
            summary=f"{stock.company_name}는 아직 추천 후보 점수가 산정되지 않았습니다.",
            risk_notes=[
                "기본 종목 정보와 연결된 공개 근거를 먼저 확인해 주세요.",
                "점수 산정 전에는 추천 후보로 해석하지 않아야 합니다.",
            ],
            as_of=date.today(),
        )

    def corp_code(self, ticker: str) -> str | None:
        identifier = self.session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.ticker == ticker,
                CompanyIdentifier.provider == "OpenDART",
                CompanyIdentifier.identifier_type == "corp_code",
            )
        ).first()
        return identifier.identifier_value if identifier else None

    def corp_codes(self, tickers: list[str]) -> dict[str, str]:
        if not tickers:
            return {}
        rows = self.session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.ticker.in_(tickers),
                CompanyIdentifier.provider == "OpenDART",
                CompanyIdentifier.identifier_type == "corp_code",
            )
        ).all()
        return {row.ticker: row.identifier_value for row in rows}


def escape_like_query(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def compact_search_text(query: str) -> str:
    return "".join(query.split()).casefold()


def match_reason(stock: Stock, query: str) -> str:
    if not query:
        return "default"
    compact_query = compact_search_text(query)
    if compact_query in compact_search_text(stock.ticker):
        return "ticker"
    if compact_query in compact_search_text(stock.company_name):
        return "name"
    if stock.company_name_en and compact_query in compact_search_text(stock.company_name_en):
        return "name"
    return "keyword"
