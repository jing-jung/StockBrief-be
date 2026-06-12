from collections import defaultdict
from decimal import Decimal

import uuid
from datetime import date, datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_optional_current_user
from app.config import Settings, get_settings
from app.db import get_db_session
from app.models import (
    ApiErrorResponse,
    CandidateEvidenceSummaryContract,
    ChatCitationContract,
    ChatContractData,
    ChatContractResponse,
    ChatRequest,
    ChatResponse,
    ChatSafetyContract,
    CompanyIdentifierResponse,
    EvidencePreviewContract,
    HealthResponse,
    PaginationResponse,
    RecommendationCandidateListResponse,
    RecommendationCandidateResponse,
    RecommendationReasonResponse,
    RiskProfile,
    ScoreComponentResponse,
    ServicePolicyResponse,
    StockBriefContract,
    StockCandidateContractData,
    StockCandidateContractItem,
    StockCandidateContractResponse,
    StockContractItem,
    StockDetailContractData,
    StockDetailContractResponse,
    StockDetailResponse,
    StockEvidenceContractData,
    StockEvidenceContractItem,
    StockEvidenceContractResponse,
    StockEvidenceItemResponse,
    StockEvidenceResponse,
    StockPriceContract,
    StockScoreBreakdownContract,
    StockScoreContract,
    StockSearchItemResponse,
    StockSearchContractData,
    StockSearchContractItem,
    StockSearchContractResponse,
    StockSearchResponse,
    StockScoreResponse,
)
from app.orm import (
    ChatMessage,
    ChatSession,
    CompanyIdentifier,
    EvidenceChunk,
    FinancialStatement,
    PriceMetric,
    RecommendationReason,
    RecommendationScore,
    RiskSignal,
    SourceDocument,
    Stock,
    User,
)
from app.services.chat import compose_chat_answer

router = APIRouter()

COMMON_ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse, "description": "Request validation failed."},
    404: {"model": ApiErrorResponse, "description": "Resource was not found."},
}

PROHIBITED_OUTPUTS = [
    "buy_instruction",
    "sell_instruction",
    "target_price",
    "guaranteed_return",
    "entry_price",
    "stop_loss",
]

DISCLAIMER = "공개 데이터 기반 검토 후보이며 최종 투자 판단은 사용자에게 있습니다."
EVIDENCE_LEVEL_MAP = {
    "strong": "strong",
    "medium": "medium",
    "moderate": "medium",
    "weak": "weak",
    "limited": "weak",
    "insufficient": "weak",
}


@router.get(
    "/health",
    response_model=HealthResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=settings.service_version,
        environment=settings.app_env,
        time=datetime.now(timezone.utc),
    )


@router.get(
    "/meta/service-policy",
    response_model=ServicePolicyResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def service_policy() -> ServicePolicyResponse:
    return ServicePolicyResponse(
        product_type="evidence_based_stock_candidate_recommendation",
        recommendation_type="review_candidate_not_buy_sell_advice",
        prohibited_outputs=PROHIBITED_OUTPUTS,
        mvp_auth="guest_first",
    )


@router.get(
    "/recommendations/candidates",
    response_model=RecommendationCandidateListResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def list_recommendation_candidates(
    risk_profile: RiskProfile = "balanced",
    market: str | None = Query(default=None, pattern="^(KOSPI|KOSDAQ)$"),
    sector: str | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> RecommendationCandidateListResponse:
    rows = _candidate_rows(session=session, market=market, sector=sector)
    candidates = _candidate_responses(session, rows)
    candidates = _sort_candidates(candidates, risk_profile)[:limit]
    return RecommendationCandidateListResponse(
        items=candidates,
        count=len(candidates),
        risk_profile=risk_profile,
        disclaimer=DISCLAIMER,
    )


@router.get(
    "/recommendations/candidates/{ticker}",
    response_model=RecommendationCandidateResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def get_recommendation_candidate(
    ticker: str,
    session: Session = Depends(get_db_session),
) -> RecommendationCandidateResponse:
    stock, score = _candidate_row(session, ticker)
    return _candidate_response(session, stock, score)


@router.get(
    "/stocks/candidates",
    response_model=StockCandidateContractResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def list_stock_candidates(
    request: Request,
    risk_profile: RiskProfile = "balanced",
    market: str | None = Query(default=None, pattern="^(KOSPI|KOSDAQ)$"),
    sector: str | None = None,
    sort: str = Query(default="score_desc", pattern="^(score_desc|volume_desc|updated_desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> StockCandidateContractResponse:
    rows = _candidate_rows(session=session, market=market, sector=sector)
    items = _stock_candidate_contract_items(rows, session=session)
    risk_counts = _candidate_risk_counts(session, rows)
    items = _sort_stock_candidate_contract_items(
        items=items,
        sort=sort,
        risk_profile=risk_profile,
        risk_counts=risk_counts,
    )
    paged = items[offset : offset + limit]
    as_of = max((item.score.as_of for item in items), default=datetime.now(timezone.utc).date())
    return StockCandidateContractResponse(
        data=StockCandidateContractData(
            as_of=as_of,
            items=paged,
            pagination=_pagination(limit=limit, offset=offset, total=len(items)),
        ),
        message="추천 후보 목록을 반환했습니다.",
        request_id=_request_id(request),
    )


@router.get(
    "/stocks/candidates/{ticker}",
    response_model=RecommendationCandidateResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def get_stock_candidate(
    ticker: str,
    session: Session = Depends(get_db_session),
) -> RecommendationCandidateResponse:
    return get_recommendation_candidate(ticker=ticker, session=session)


@router.get(
    "/stocks/{ticker}/score",
    response_model=StockScoreResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def get_stock_score(
    ticker: str,
    session: Session = Depends(get_db_session),
) -> StockScoreResponse:
    stock, score = _candidate_row(session, ticker)
    candidate = _candidate_response(session, stock, score)
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


@router.get(
    "/stocks/search",
    response_model=StockSearchContractResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def search_stocks(
    request: Request,
    q: str = Query(default="", max_length=100),
    market: str | None = Query(default=None, pattern="^(KOSPI|KOSDAQ)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> StockSearchContractResponse:
    statement = select(Stock)
    if q:
        query = f"%{_escape_like_query(q)}%"
        statement = statement.where(
            (Stock.ticker.like(query, escape="\\")) | (
                Stock.company_name.like(query, escape="\\")
            )
        )
    if market:
        statement = statement.where(Stock.market == market)
        
    total_statement = select(func.count()).select_from(statement.subquery())
    total = session.scalar(total_statement) or 0

    statement = statement.order_by(Stock.ticker.asc()).offset(offset).limit(limit)
    rows = session.scalars(statement).all()
    corp_codes = _corp_codes(session, [stock.ticker for stock in rows])

    items = [
        StockSearchContractItem(
            ticker=stock.ticker,
            name=stock.company_name,
            market=stock.market,
            sector=stock.sector,
            corp_code=corp_codes.get(stock.ticker),
            match_reason=_match_reason(stock, q),
        )
        for stock in rows
    ]
    return StockSearchContractResponse(
        data=StockSearchContractData(
            items=items,
            pagination=_pagination(limit=limit, offset=offset, total=total),
        ),
        message="종목 검색 결과를 반환했습니다.",
        request_id=_request_id(request),
    )


@router.get(
    "/stocks/{ticker}",
    response_model=StockDetailContractResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def get_stock(
    request: Request,
    ticker: str,
    session: Session = Depends(get_db_session),
) -> StockDetailContractResponse:
    stock = _stock_or_404(session, ticker)
    _, score = _candidate_row(session, ticker)
    evidence = _evidence_items(session, ticker, _parse_evidence_types(None))
    return StockDetailContractResponse(
        data=StockDetailContractData(
            stock=_stock_contract_item(session, stock),
            price=_latest_price_contract(session, ticker),
            score=_stock_score_contract(score),
            brief=_stock_brief_contract(stock=stock, score=score),
            evidence_preview=[
                EvidencePreviewContract(
                    id=item.id,
                    source_type=_contract_source_type(item.type),
                    title=item.title,
                    source_name=item.source_name,
                    url=item.source_url,
                    published_at=item.published_at,
                )
                for item in evidence[:3]
            ],
        ),
        message="종목 상세 정보를 반환했습니다.",
        request_id=_request_id(request),
    )


@router.get(
    "/stocks/{ticker}/evidence",
    response_model=StockEvidenceContractResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def get_stock_evidence(
    request: Request,
    ticker: str,
    source_type: str | None = Query(default=None, pattern="^(NEWS|DISCLOSURE|SCORE|CHUNK)$"),
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> StockEvidenceContractResponse:
    _stock_or_404(session, ticker)
    requested_types = _contract_source_type_filter(source_type)
    evidence = _evidence_items(session, ticker, requested_types)
    evidence = _filter_evidence_dates(evidence, from_date=from_date, to_date=to_date)
    limited = evidence[offset : offset + limit]
    return StockEvidenceContractResponse(
        data=StockEvidenceContractData(
            ticker=ticker,
            items=[_stock_evidence_contract_item(item) for item in limited],
            pagination=_pagination(limit=limit, offset=offset, total=len(evidence)),
        ),
        message="근거 목록을 반환했습니다.",
        request_id=_request_id(request),
    )


@router.post(
    "/chat",
    response_model=ChatContractResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def chat(
    http_request: Request,
    request: ChatRequest,
    session: Session = Depends(get_db_session),
    current_user: User | None = Depends(get_optional_current_user),
) -> ChatContractResponse:
    stock, score = _candidate_row(session, request.ticker)
    candidate = _candidate_response(session, stock, score)
    evidence = _evidence_items(session, request.ticker, _parse_evidence_types(None))
    response = compose_chat_answer(
        message=request.message,
        candidate=candidate,
        evidence=evidence,
    )
    if current_user is not None:
        response = _persist_chat_exchange(
            session=session,
            user=current_user,
            request=request,
            response=response,
        )
    return ChatContractResponse(
        data=_chat_contract_data(request=request, response=response),
        message="mock Agent 응답을 반환했습니다.",
        request_id=_request_id(http_request),
    )


def _request_id(request: Request) -> str:
    return (
        getattr(request.state, "request_id", None)
        or request.headers.get("x-request-id")
        or f"req_{uuid.uuid4().hex}"
    )


def _validate_ticker(ticker: str) -> None:
    if re.fullmatch(r"\d{6}", ticker):
        return
    raise HTTPException(
        status_code=400,
        detail={
            "code": "INVALID_TICKER",
            "message": "Ticker must be a 6-digit Korean stock ticker.",
            "details": [{"field": "ticker", "reason": "invalid_format"}],
        },
    )


def _pagination(*, limit: int, offset: int, total: int) -> PaginationResponse:
    return PaginationResponse(
        limit=limit,
        offset=offset,
        total=total,
        has_more=offset + limit < total,
    )


def _stock_contract_item(session: Session, stock: Stock) -> StockContractItem:
    return StockContractItem(
        ticker=stock.ticker,
        name=stock.company_name,
        market=stock.market,
        sector=stock.sector,
        corp_code=_corp_code(session, stock.ticker),
    )


def _corp_code(session: Session, ticker: str) -> str | None:
    identifier = session.scalars(
        select(CompanyIdentifier).where(
            CompanyIdentifier.ticker == ticker,
            CompanyIdentifier.provider == "OpenDART",
            CompanyIdentifier.identifier_type == "corp_code",
        )
    ).first()
    return identifier.identifier_value if identifier else None


def _corp_codes(session: Session, tickers: list[str]) -> dict[str, str]:
    if not tickers:
        return {}
    rows = session.scalars(
        select(CompanyIdentifier).where(
            CompanyIdentifier.ticker.in_(tickers),
            CompanyIdentifier.provider == "OpenDART",
            CompanyIdentifier.identifier_type == "corp_code",
        )
    ).all()
    return {row.ticker: row.identifier_value for row in rows}


def _escape_like_query(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _match_reason(stock: Stock, query: str) -> str:
    if not query:
        return "default"
    if query in stock.ticker:
        return "ticker"
    if query.casefold() in stock.company_name.casefold():
        return "name"
    return "keyword"


def _latest_price_contract(session: Session, ticker: str) -> StockPriceContract | None:
    price = session.scalars(
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


def _score_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _stock_candidate_contract_item(
    *,
    session: Session,
    stock: Stock,
    score: RecommendationScore,
) -> StockCandidateContractItem:
    return StockCandidateContractItem(
        ticker=stock.ticker,
        name=stock.company_name,
        market=stock.market,
        sector=stock.sector,
        score=_stock_score_contract(score),
        price=_latest_price_contract(session, stock.ticker),
        evidence_summary=_candidate_evidence_summary(session, stock.ticker),
    )


def _stock_candidate_contract_items(
    rows: list[tuple[Stock, RecommendationScore]],
    *,
    session: Session,
) -> list[StockCandidateContractItem]:
    tickers = [stock.ticker for stock, _ in rows]
    prices = _latest_price_contracts(session, tickers)
    evidence_summaries = _candidate_evidence_summaries(session, tickers)
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


def _latest_price_contracts(
    session: Session,
    tickers: list[str],
) -> dict[str, StockPriceContract]:
    if not tickers:
        return {}
    rows = session.scalars(
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


def _candidate_evidence_summary(
    session: Session,
    ticker: str,
) -> CandidateEvidenceSummaryContract:
    evidence = _evidence_items(session, ticker, _parse_evidence_types(None))
    news = [item for item in evidence if item.type == "news"]
    disclosures = [item for item in evidence if item.type == "disclosure"]
    latest_values = [
        item.published_at
        for item in evidence
        if item.published_at is not None
    ]
    return CandidateEvidenceSummaryContract(
        news_count=len(news),
        disclosure_count=len(disclosures),
        latest_at=max(latest_values) if latest_values else None,
    )


def _candidate_evidence_summaries(
    session: Session,
    tickers: list[str],
) -> dict[str, CandidateEvidenceSummaryContract]:
    if not tickers:
        return {}
    summaries: dict[str, dict[str, object]] = {
        ticker: {"news": 0, "disclosure": 0, "latest": None}
        for ticker in tickers
    }
    rows = session.execute(
        select(EvidenceChunk, SourceDocument)
        .join(SourceDocument, SourceDocument.id == EvidenceChunk.source_document_id)
        .where(
            EvidenceChunk.ticker.in_(tickers),
            SourceDocument.source_type.in_(["news", "disclosure"]),
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


def _candidate_risk_count(
    session: Session,
    ticker: str,
    as_of_date: date,
) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(RiskSignal).where(
                RiskSignal.ticker == ticker,
                RiskSignal.as_of_date == as_of_date,
            )
        )
        or 0
    )


def _candidate_risk_pairs(
    session: Session,
    rows: list[tuple[Stock, RecommendationScore]],
) -> set[tuple[str, date]]:
    if not rows:
        return set()
    tickers = [stock.ticker for stock, _ in rows]
    as_of_dates = [score.as_of_date for _, score in rows]
    risks = session.execute(
        select(RiskSignal.ticker, RiskSignal.as_of_date).where(
            RiskSignal.ticker.in_(tickers),
            RiskSignal.as_of_date.in_(as_of_dates),
        )
    ).all()
    return {(ticker, as_of_date) for ticker, as_of_date in risks}


def _candidate_risk_counts(
    session: Session,
    rows: list[tuple[Stock, RecommendationScore]],
) -> dict[tuple[str, date], int]:
    if not rows:
        return {}
    tickers = [stock.ticker for stock, _ in rows]
    as_of_dates = [score.as_of_date for _, score in rows]
    counts = session.execute(
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


def _stock_brief_contract(
    *,
    stock: Stock,
    score: RecommendationScore,
) -> StockBriefContract:
    return StockBriefContract(
        summary=(
            f"{stock.company_name}는 공개 데이터 기반 mock 점수와 근거로 "
            "검토 후보에 포함된 종목입니다."
        ),
        risk_notes=[
            "실데이터 연동 전 mock 데이터 기준입니다.",
            "투자 판단 전 원문과 최신 데이터를 확인해야 합니다.",
        ],
        as_of=score.as_of_date,
    )


def _contract_source_type(value: str) -> str:
    mapping = {
        "news": "NEWS",
        "disclosure": "DISCLOSURE",
        "financial": "SCORE",
        "price": "SCORE",
    }
    return mapping.get(value, "CHUNK")


def _contract_source_type_filter(source_type: str | None) -> set[str]:
    if source_type is None:
        return _parse_evidence_types(None)
    mapping = {
        "NEWS": {"news"},
        "DISCLOSURE": {"disclosure"},
        "SCORE": {"financial", "price"},
        "CHUNK": {"news", "disclosure"},
    }
    return mapping[source_type]


def _filter_evidence_dates(
    evidence: list[StockEvidenceItemResponse],
    *,
    from_date: date | None,
    to_date: date | None,
) -> list[StockEvidenceItemResponse]:
    if from_date is None and to_date is None:
        return evidence
    filtered = []
    for item in evidence:
        item_date = item.as_of_date or (item.published_at.date() if item.published_at else None)
        if item_date is None:
            continue
        if from_date and item_date < from_date:
            continue
        if to_date and item_date > to_date:
            continue
        filtered.append(item)
    return filtered


def _stock_evidence_contract_item(
    item: StockEvidenceItemResponse,
) -> StockEvidenceContractItem:
    metadata = {
        "data_status": item.data_status,
    }
    if item.source_identifier:
        metadata["source_identifier"] = item.source_identifier
    if item.as_of_date:
        metadata["as_of_date"] = item.as_of_date.isoformat()
    return StockEvidenceContractItem(
        id=item.id,
        source_type=_contract_source_type(item.type),
        title=item.title,
        source_name=item.source_name,
        url=item.source_url,
        published_at=item.published_at,
        snippet=item.summary,
        metadata=metadata,
    )


def _chat_contract_data(
    *,
    request: ChatRequest,
    response: ChatResponse,
) -> ChatContractData:
    return ChatContractData(
        session_id=response.session_id or request.session_id or f"local-{request.ticker}",
        answer=response.answer,
        citations=[
            ChatCitationContract(
                id=citation.evidence_id,
                source_type=_contract_source_type(citation.type),
                title=citation.title,
                url=citation.source_url,
                published_at=None,
            )
            for citation in response.citations
        ],
        safety=ChatSafetyContract(
            policy_action=_policy_action(response.policy_status),
            disclaimer="이 정보는 투자 조언이 아니며, 투자 판단 전 원문과 최신 데이터를 확인하세요.",
        ),
    )


def _policy_action(policy_status: str) -> str:
    if policy_status == "allowed":
        return "ALLOW"
    if policy_status == "redirected":
        return "REDIRECT"
    return "BLOCK"


def _candidate_rows(
    session: Session,
    market: str | None,
    sector: str | None,
) -> list[tuple[Stock, RecommendationScore]]:
    statement = (
        select(Stock, RecommendationScore)
        .join(RecommendationScore, RecommendationScore.ticker == Stock.ticker)
        .where(RecommendationScore.is_candidate_eligible.is_(True))
    )
    if market:
        statement = statement.where(Stock.market == market)
    if sector:
        statement = statement.where(Stock.sector == sector)

    rows = session.execute(statement).all()
    risk_pairs = _candidate_risk_pairs(session, rows)
    return [
        (stock, score)
        for stock, score in rows
        if _passes_evidence_gate(stock, score, risk_pairs)
    ]


def _persist_chat_exchange(
    *,
    session: Session,
    user: User,
    request: ChatRequest,
    response: ChatResponse,
) -> ChatResponse:
    session_id = request.session_id or f"chat_{uuid.uuid4().hex}"
    chat_session = session.scalars(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user.id,
        )
    ).first()
    if chat_session is None:
        chat_session = ChatSession(
            session_id=session_id,
            user_id=user.id,
            title=request.title,
            ticker=request.ticker,
        )
        session.add(chat_session)
    else:
        chat_session.ticker = request.ticker
        if request.title:
            chat_session.title = request.title
    chat_session.updated_at = datetime.now(timezone.utc)

    user_message_id = f"msg_{uuid.uuid4().hex}"
    assistant_message_id = f"msg_{uuid.uuid4().hex}"
    session.add_all(
        [
            ChatMessage(
                message_id=user_message_id,
                session_id=session_id,
                role="user",
                content=request.message,
                ticker=request.ticker,
                citations=[],
                safety_flags=[],
            ),
            ChatMessage(
                message_id=assistant_message_id,
                session_id=session_id,
                role="assistant",
                content=response.answer,
                ticker=request.ticker,
                citations=[citation.model_dump(mode="json") for citation in response.citations],
                safety_flags=[{"policy_status": response.policy_status}],
            ),
        ]
    )
    session.commit()
    return response.model_copy(
        update={
            "session_id": session_id,
            "message_id": assistant_message_id,
        }
    )


def _stock_or_404(session: Session, ticker: str) -> Stock:
    _validate_ticker(ticker)
    stock = session.get(Stock, ticker)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock was not found.")
    return stock


def _candidate_row(
    session: Session,
    ticker: str,
) -> tuple[Stock, RecommendationScore]:
    _validate_ticker(ticker)
    row = session.execute(
        select(Stock, RecommendationScore)
        .join(RecommendationScore, RecommendationScore.ticker == Stock.ticker)
        .where(Stock.ticker == ticker)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Recommendation candidate was not found.",
        )
    stock, score = row
    return stock, score


def _candidate_response(
    session: Session,
    stock: Stock,
    score: RecommendationScore,
) -> RecommendationCandidateResponse:
    reasons = session.scalars(
        select(RecommendationReason)
        .where(RecommendationReason.recommendation_score_id == score.id)
        .order_by(RecommendationReason.created_at.asc())
    ).all()
    risks = session.scalars(
        select(RiskSignal)
        .where(
            RiskSignal.ticker == stock.ticker,
            RiskSignal.as_of_date == score.as_of_date,
        )
        .order_by(RiskSignal.created_at.asc())
    ).all()

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
                evidence_ids=list(reason.evidence_ids or []),
                source_document_ids=list(reason.source_document_ids or []),
            )
            for reason in reasons
        ],
        risk_tags=[risk.risk_tag for risk in risks],
        evidence_level=_evidence_level(score.evidence_level),
        evidence_count=score.evidence_count,
        missing_data=list(score.missing_data or []),
        data_freshness=dict(score.data_freshness or {}),
        disclaimer=DISCLAIMER,
    )


def _candidate_responses(
    session: Session,
    rows: list[tuple[Stock, RecommendationScore]],
) -> list[RecommendationCandidateResponse]:
    if not rows:
        return []
    score_ids = [score.id for _, score in rows]
    tickers = [stock.ticker for stock, _ in rows]
    as_of_dates = [score.as_of_date for _, score in rows]

    reasons_by_score_id: dict[object, list[RecommendationReason]] = defaultdict(list)
    reasons = session.scalars(
        select(RecommendationReason)
        .where(RecommendationReason.recommendation_score_id.in_(score_ids))
        .order_by(RecommendationReason.created_at.asc())
    ).all()
    for reason in reasons:
        reasons_by_score_id[reason.recommendation_score_id].append(reason)

    risks_by_key: dict[tuple[str, date], list[RiskSignal]] = defaultdict(list)
    risks = session.scalars(
        select(RiskSignal)
        .where(
            RiskSignal.ticker.in_(tickers),
            RiskSignal.as_of_date.in_(as_of_dates),
        )
        .order_by(RiskSignal.created_at.asc())
    ).all()
    for risk in risks:
        risks_by_key[(risk.ticker, risk.as_of_date)].append(risk)

    return [
        _candidate_response_from_loaded(
            stock=stock,
            score=score,
            reasons=reasons_by_score_id.get(score.id, []),
            risks=risks_by_key.get((stock.ticker, score.as_of_date), []),
        )
        for stock, score in rows
    ]


def _candidate_response_from_loaded(
    *,
    stock: Stock,
    score: RecommendationScore,
    reasons: list[RecommendationReason],
    risks: list[RiskSignal],
) -> RecommendationCandidateResponse:
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
                evidence_ids=list(reason.evidence_ids or []),
                source_document_ids=list(reason.source_document_ids or []),
            )
            for reason in reasons
        ],
        risk_tags=[risk.risk_tag for risk in risks],
        evidence_level=_evidence_level(score.evidence_level),
        evidence_count=score.evidence_count,
        missing_data=list(score.missing_data or []),
        data_freshness=dict(score.data_freshness or {}),
        disclaimer=DISCLAIMER,
    )


def _passes_evidence_gate(
    stock: Stock,
    score: RecommendationScore,
    risk_pairs: set[tuple[str, date]],
) -> bool:
    if score.evidence_count < 2:
        return False
    if not isinstance(score.missing_data, list):
        return False
    if not isinstance(score.data_freshness, dict) or not score.data_freshness.get("as_of"):
        return False
    return (stock.ticker, score.as_of_date) in risk_pairs


def _score_components(components: list[dict[str, object]]) -> list[ScoreComponentResponse]:
    responses = [
        ScoreComponentResponse(
            name=str(component["name"]),
            weight=int(component["weight"]),
            raw_score=_optional_float(component.get("raw_score")),
            weighted_score=_float(component.get("weighted_score")),
            reason=str(component.get("reason", "공개 데이터 기준 검토 포인트입니다.")),
            input_refs=[str(item) for item in component.get("input_refs", [])],
            evidence_ids=[str(item) for item in component.get("evidence_ids", [])],
        )
        for component in components
    ]
    if len(responses) != 8:
        raise HTTPException(
            status_code=500,
            detail="Stored recommendation score must contain 8 score components.",
        )
    return responses


def _sort_candidates(
    candidates: list[RecommendationCandidateResponse],
    risk_profile: RiskProfile,
) -> list[RecommendationCandidateResponse]:
    if risk_profile == "conservative":
        return sorted(candidates, key=lambda item: (len(item.risk_tags), -item.recommendation_score))
    if risk_profile == "aggressive":
        return sorted(candidates, key=lambda item: item.recommendation_score, reverse=True)
    return sorted(
        candidates,
        key=lambda item: item.recommendation_score - len(item.risk_tags) * 0.5,
        reverse=True,
    )


def _evidence_level(value: str) -> str:
    return EVIDENCE_LEVEL_MAP.get(value, "weak")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _float(value)


def _float(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _parse_evidence_types(types: str | None) -> set[str]:
    allowed = {"financial", "news", "disclosure", "price"}
    if not types:
        return allowed
    parsed = {item.strip() for item in types.split(",") if item.strip()}
    invalid = parsed - allowed
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported evidence types: {', '.join(sorted(invalid))}.",
        )
    return parsed


def _evidence_items(
    session: Session,
    ticker: str,
    requested_types: set[str],
) -> list[StockEvidenceItemResponse]:
    items: list[StockEvidenceItemResponse] = []
    if "financial" in requested_types:
        items.extend(_financial_evidence(session, ticker))
    if "disclosure" in requested_types or "news" in requested_types:
        items.extend(_chunk_evidence(session, ticker, requested_types))
    if "price" in requested_types:
        items.extend(_price_evidence(session, ticker))
    return sorted(
        items,
        key=lambda item: (item.as_of_date is None, item.as_of_date, item.id),
        reverse=True,
    )


def _financial_evidence(
    session: Session,
    ticker: str,
) -> list[StockEvidenceItemResponse]:
    financials = session.scalars(
        select(FinancialStatement)
        .where(FinancialStatement.ticker == ticker)
        .order_by(FinancialStatement.period_end_date.desc())
    ).all()
    items = []
    for row in financials:
        source = session.get(SourceDocument, row.source_document_id) if row.source_document_id else None
        items.append(
            StockEvidenceItemResponse(
                id=f"financial_{ticker}_{row.fiscal_year}_{row.fiscal_period}",
                type="financial",
                title=f"{row.fiscal_year} {row.fiscal_period} 재무 mock 근거",
                summary="재무제표 mock 데이터의 주요 수치가 검토 근거로 사용됩니다.",
                source_name=source.source_name if source else "FINANCIAL_MOCK",
                source_url=source.source_url if source else None,
                source_identifier=source.external_id if source else f"{ticker}-{row.fiscal_year}-{row.fiscal_period}",
                published_at=source.published_at if source else None,
                as_of_date=row.period_end_date,
                data_status="available",
            )
        )
    return items


def _chunk_evidence(
    session: Session,
    ticker: str,
    requested_types: set[str],
) -> list[StockEvidenceItemResponse]:
    chunks = session.scalars(
        select(EvidenceChunk)
        .where(EvidenceChunk.ticker == ticker)
        .order_by(EvidenceChunk.fetched_at.desc())
    ).all()
    items = []
    for chunk in chunks:
        source = session.get(SourceDocument, chunk.source_document_id)
        evidence_type = _source_type_to_evidence_type(source.source_type if source else "")
        if evidence_type not in requested_types:
            continue
        items.append(
            StockEvidenceItemResponse(
                id=chunk.evidence_id,
                type=evidence_type,
                title=source.title if source else f"{ticker} 근거 데이터",
                summary=chunk.chunk_text,
                source_name=source.source_name if source else "UNKNOWN_SOURCE",
                source_url=chunk.source_url or (source.source_url if source else None),
                source_identifier=source.external_id if source else str(chunk.source_document_id),
                published_at=chunk.published_at or (source.published_at if source else None),
                as_of_date=(chunk.published_at.date() if chunk.published_at else None),
                data_status="available",
            )
        )
    return items


def _price_evidence(
    session: Session,
    ticker: str,
) -> list[StockEvidenceItemResponse]:
    prices = session.scalars(
        select(PriceMetric)
        .where(PriceMetric.ticker == ticker)
        .order_by(PriceMetric.trade_date.desc())
    ).all()
    return [
        StockEvidenceItemResponse(
            id=f"price_{ticker}_{row.trade_date.isoformat()}",
            type="price",
            title=f"{row.trade_date.isoformat()} 가격 지표 fallback mock",
            summary="가격과 유동성 fallback mock 데이터가 검토 근거로 사용됩니다.",
            source_name=row.source,
            source_url=None,
            source_identifier=f"{row.source}:{ticker}:{row.trade_date.isoformat()}",
            published_at=None,
            as_of_date=row.trade_date,
            data_status="fallback" if "FALLBACK" in row.source else "available",
        )
        for row in prices
    ]


def _source_type_to_evidence_type(source_type: str) -> str:
    if source_type == "news":
        return "news"
    if source_type == "disclosure":
        return "disclosure"
    if source_type == "financial":
        return "financial"
    if source_type == "price":
        return "price"
    return "disclosure"
