from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    service: str = Field(examples=["stockbrief-api"])
    version: str = Field(examples=["0.1.0"])
    environment: str = Field(examples=["local"])
    time: datetime


class ServicePolicyResponse(BaseModel):
    product_type: str = Field(
        examples=["evidence_based_stock_candidate_recommendation"]
    )
    recommendation_type: str = Field(
        examples=["review_candidate_not_buy_sell_advice"]
    )
    prohibited_outputs: list[str] = Field(
        examples=[
            [
                "buy_instruction",
                "sell_instruction",
                "target_price",
                "guaranteed_return",
                "entry_price",
                "stop_loss",
            ]
        ]
    )
    mvp_auth: str = Field(examples=["guest_first"])


class ErrorDetail(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["The requested resource was not found."])
    details: dict[str, object] | list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
    request_id: str


class PaginationResponse(BaseModel):
    limit: int
    offset: int
    total: int
    has_more: bool


class StockSearchContractItem(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str | None
    corp_code: str | None
    match_reason: str


class StockSearchContractData(BaseModel):
    items: list[StockSearchContractItem]
    pagination: PaginationResponse


class StockSearchContractResponse(BaseModel):
    success: Literal[True] = True
    data: StockSearchContractData
    message: str
    request_id: str


class StockContractItem(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str | None
    corp_code: str | None


class StockPriceContract(BaseModel):
    close: float | None
    change_rate: float | None
    volume: float | None
    trade_date: date | None


class StockScoreBreakdownContract(BaseModel):
    momentum: float
    liquidity: float
    disclosure: float
    news: float


class StockScoreContract(BaseModel):
    total: float
    grade: str
    as_of: date
    version: str
    breakdown: StockScoreBreakdownContract


class CandidateEvidenceSummaryContract(BaseModel):
    news_count: int
    disclosure_count: int
    latest_at: datetime | None


class StockCandidateContractItem(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str | None
    score: StockScoreContract
    price: StockPriceContract | None
    evidence_summary: CandidateEvidenceSummaryContract


class StockCandidateContractData(BaseModel):
    as_of: date
    items: list[StockCandidateContractItem]
    pagination: PaginationResponse


class StockCandidateContractResponse(BaseModel):
    success: Literal[True] = True
    data: StockCandidateContractData
    message: str
    request_id: str


class StockBriefContract(BaseModel):
    summary: str
    risk_notes: list[str]
    as_of: date


class EvidencePreviewContract(BaseModel):
    id: str
    source_type: str
    title: str
    source_name: str
    url: str | None = None
    published_at: datetime | None = None


class StockDetailContractData(BaseModel):
    stock: StockContractItem
    price: StockPriceContract | None
    score: StockScoreContract
    brief: StockBriefContract
    evidence_preview: list[EvidencePreviewContract]


class StockDetailContractResponse(BaseModel):
    success: Literal[True] = True
    data: StockDetailContractData
    message: str
    request_id: str


class StockEvidenceContractItem(BaseModel):
    id: str
    source_type: str
    title: str
    source_name: str
    url: str | None = None
    published_at: datetime | None = None
    snippet: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StockEvidenceContractData(BaseModel):
    ticker: str
    items: list[StockEvidenceContractItem]
    pagination: PaginationResponse


class StockEvidenceContractResponse(BaseModel):
    success: Literal[True] = True
    data: StockEvidenceContractData
    message: str
    request_id: str


class ChatCitationContract(BaseModel):
    id: str
    source_type: str
    title: str
    url: str | None = None
    published_at: datetime | None = None


class ChatSafetyContract(BaseModel):
    policy_action: str
    disclaimer: str


class ChatContractData(BaseModel):
    session_id: str
    answer: str
    citations: list[ChatCitationContract]
    safety: ChatSafetyContract


class ChatContractResponse(BaseModel):
    success: Literal[True] = True
    data: ChatContractData
    message: str
    request_id: str


RiskProfile = Literal["conservative", "balanced", "aggressive"]
EvidenceLevel = Literal["strong", "medium", "weak"]
EvidenceType = Literal["news", "disclosure", "financial", "price"]
DataStatus = Literal["available", "fallback", "missing"]
PolicyStatus = Literal["allowed", "redirected", "blocked"]


class ScoreComponentResponse(BaseModel):
    name: str
    weight: int
    raw_score: float | None
    weighted_score: float
    reason: str
    input_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RecommendationReasonResponse(BaseModel):
    reason_id: str
    component: str
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)


class RecommendationCandidateResponse(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str | None
    recommendation_score: float = Field(ge=0, le=100)
    score_components: list[ScoreComponentResponse] = Field(max_length=8)
    recommendation_reasons: list[RecommendationReasonResponse]
    risk_tags: list[str]
    evidence_level: EvidenceLevel
    evidence_count: int = Field(ge=0)
    missing_data: list[Any] = Field(default_factory=list)
    data_freshness: dict[str, Any]
    disclaimer: str


class RecommendationCandidateListResponse(BaseModel):
    items: list[RecommendationCandidateResponse]
    count: int
    risk_profile: RiskProfile
    disclaimer: str


class StockScoreResponse(BaseModel):
    ticker: str
    as_of_date: date
    recommendation_score: float = Field(ge=0, le=100)
    score_components: list[ScoreComponentResponse] = Field(max_length=8)
    risk_tags: list[str]
    evidence_level: EvidenceLevel
    evidence_count: int = Field(ge=0)
    missing_data: list[Any] = Field(default_factory=list)
    data_freshness: dict[str, Any]
    disclaimer: str


class StockSearchItemResponse(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str | None
    industry: str | None


class StockSearchResponse(BaseModel):
    query: str
    count: int
    items: list[StockSearchItemResponse]


class CompanyIdentifierResponse(BaseModel):
    provider: str
    identifier_type: str
    identifier_value: str
    is_primary: bool


class StockDetailResponse(BaseModel):
    ticker: str
    name: str
    name_en: str | None
    market: str
    sector: str | None
    industry: str | None
    listing_date: date | None
    is_active: bool
    identifiers: list[CompanyIdentifierResponse]


class StockEvidenceItemResponse(BaseModel):
    id: str
    type: EvidenceType
    title: str
    summary: str
    source_name: str
    source_url: str | None = None
    source_identifier: str | None = None
    published_at: datetime | None = None
    as_of_date: date | None = None
    data_status: DataStatus


class StockEvidenceResponse(BaseModel):
    ticker: str
    evidence: list[StockEvidenceItemResponse]
    message: str | None = None


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictBaseModel):
    session_id: str | None = Field(default=None, max_length=120)
    ticker: str = Field(min_length=6, max_length=6, examples=["005930"])
    message: str = Field(min_length=1, max_length=1000, examples=["왜 추천됐나요?"])
    title: str | None = Field(default=None, max_length=120)


class ChatCitation(BaseModel):
    evidence_id: str
    type: EvidenceType
    title: str
    source_name: str
    source_url: str | None = None
    as_of_date: date | None = None


class ChatResponse(BaseModel):
    session_id: str | None = None
    message_id: str | None = None
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)
    policy_status: PolicyStatus
    used_evidence_ids: list[str] = Field(default_factory=list)


class MeResponse(BaseModel):
    id: str
    cognito_sub: str
    email: str | None
    email_verified: bool
    nickname: str | None


class MeUpdateRequest(StrictBaseModel):
    nickname: str | None = Field(default=None, max_length=80)


class UserPreferencesResponse(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)


class UserPreferencesUpdateRequest(StrictBaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)


class ServerWatchlistItemRequest(StrictBaseModel):
    ticker: str = Field(min_length=6, max_length=6)
    name: str = Field(min_length=1, max_length=200)
    market: str = Field(min_length=1, max_length=20)
    sector: str | None = Field(default=None, max_length=100)
    memo: str | None = Field(default=None, max_length=1000)


class ServerWatchlistItemUpdateRequest(StrictBaseModel):
    memo: str | None = Field(default=None, max_length=1000)


class ServerWatchlistItemResponse(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str | None = None
    memo: str | None = None
    saved_at: datetime


class ServerWatchlistResponse(BaseModel):
    items: list[ServerWatchlistItemResponse]
    count: int


class ServerWatchlistImportRequest(StrictBaseModel):
    items: list[ServerWatchlistItemRequest] = Field(default_factory=list, max_length=200)


class ServerWatchlistImportResponse(BaseModel):
    imported_count: int
    skipped_existing_count: int
    items: list[ServerWatchlistItemResponse]


class UserChatSessionCreateRequest(StrictBaseModel):
    session_id: str | None = Field(default=None, max_length=120)
    ticker: str | None = Field(default=None, min_length=6, max_length=6)
    title: str | None = Field(default=None, max_length=120)


class UserChatSessionResponse(BaseModel):
    session_id: str
    ticker: str | None = None
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class UserChatSessionListResponse(BaseModel):
    items: list[UserChatSessionResponse]
    count: int
