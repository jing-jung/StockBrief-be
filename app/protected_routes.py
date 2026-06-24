from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db_session
from app.models import (
    MeResponse,
    MeUpdateRequest,
    ServerWatchlistImportRequest,
    ServerWatchlistImportResponse,
    ServerWatchlistItemRequest,
    ServerWatchlistItemResponse,
    ServerWatchlistItemUpdateRequest,
    ServerWatchlistResponse,
    UserChatSessionCreateRequest,
    UserChatSessionDetailResponse,
    UserChatSessionListResponse,
    UserChatMessageResponse,
    UserChatSessionResponse,
    UserPreferencesResponse,
    UserPreferencesUpdateRequest,
)
from app.orm import ChatMessage, ChatSession, Stock, User, UserPreference, Watchlist
from app.ticker import validate_ticker

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeResponse)
def get_me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return _me_response(current_user)


@router.patch("", response_model=MeResponse)
def update_me(
    request: MeUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    if "nickname" in request.model_fields_set:
        current_user.nickname = request.nickname
    session.commit()
    session.refresh(current_user)
    return _me_response(current_user)


@router.get("/preferences", response_model=UserPreferencesResponse)
def get_preferences(
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    preferences = _preference_row(session, current_user)
    return UserPreferencesResponse(preferences=dict(preferences.preferences or {}))


@router.put("/preferences", response_model=UserPreferencesResponse)
def put_preferences(
    request: UserPreferencesUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    preferences = _preference_row(session, current_user)
    preferences.preferences = request.preferences
    session.commit()
    session.refresh(preferences)
    return UserPreferencesResponse(preferences=dict(preferences.preferences or {}))


@router.get("/watchlist", response_model=ServerWatchlistResponse)
def get_watchlist(
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ServerWatchlistResponse:
    items = _watchlist_rows(session, current_user)
    return ServerWatchlistResponse(items=[_watchlist_response(item) for item in items], count=len(items))


@router.post("/watchlist", response_model=ServerWatchlistItemResponse)
def add_watchlist_item(
    request: ServerWatchlistItemRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ServerWatchlistItemResponse:
    _stock_or_404(session, request.ticker)
    existing = session.scalars(
        select(Watchlist).where(
            Watchlist.user_id == current_user.id,
            Watchlist.ticker == request.ticker,
        )
    ).first()
    if existing:
        return _watchlist_response(existing)

    item = Watchlist(
        user_id=current_user.id,
        ticker=request.ticker,
        name=request.name,
        market=request.market,
        sector=request.sector,
        memo=request.memo,
    )
    session.add(item)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalars(
            select(Watchlist).where(
                Watchlist.user_id == current_user.id,
                Watchlist.ticker == request.ticker,
            )
        ).first()
        if existing:
            return _watchlist_response(existing)
        raise
    session.refresh(item)
    return _watchlist_response(item)


@router.patch("/watchlist/{ticker}", response_model=ServerWatchlistItemResponse)
def update_watchlist_item(
    ticker: str,
    request: ServerWatchlistItemUpdateRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ServerWatchlistItemResponse:
    validate_ticker(ticker)
    item = session.scalars(
        select(Watchlist).where(
            Watchlist.user_id == current_user.id,
            Watchlist.ticker == ticker,
        )
    ).first()
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "WATCHLIST_ITEM_NOT_FOUND",
                "message": "Watchlist item was not found.",
            },
        )
    if "memo" in request.model_fields_set:
        item.memo = request.memo
    session.commit()
    session.refresh(item)
    return _watchlist_response(item)


@router.delete("/watchlist/{ticker}", status_code=204, response_class=Response)
def delete_watchlist_item(
    ticker: str,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    validate_ticker(ticker)
    item = session.scalars(
        select(Watchlist).where(
            Watchlist.user_id == current_user.id,
            Watchlist.ticker == ticker,
        )
    ).first()
    if item is None:
        return Response(status_code=204)
    session.delete(item)
    session.commit()
    return Response(status_code=204)


@router.post("/watchlist/import", response_model=ServerWatchlistImportResponse)
def import_watchlist(
    request: ServerWatchlistImportRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ServerWatchlistImportResponse:
    existing_tickers = {
        row.ticker for row in _watchlist_rows(session, current_user)
    }
    seen_input: set[str] = set()
    imported_count = 0
    skipped_existing_count = 0

    for item in request.items:
        if item.ticker in seen_input:
            continue
        seen_input.add(item.ticker)
        if item.ticker in existing_tickers:
            skipped_existing_count += 1
            continue
        _stock_or_404(session, item.ticker)
        session.add(
            Watchlist(
                user_id=current_user.id,
                ticker=item.ticker,
                name=item.name,
                market=item.market,
                sector=item.sector,
                memo=item.memo,
            )
        )
        existing_tickers.add(item.ticker)
        imported_count += 1

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    items = _watchlist_rows(session, current_user)
    return ServerWatchlistImportResponse(
        imported_count=imported_count,
        skipped_existing_count=skipped_existing_count,
        items=[_watchlist_response(item) for item in items],
    )


@router.get("/chat-sessions", response_model=UserChatSessionListResponse)
def get_chat_sessions(
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> UserChatSessionListResponse:
    rows = session.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
    ).all()
    return UserChatSessionListResponse(
        items=[_chat_session_response(row) for row in rows],
        count=len(rows),
    )


@router.post("/chat-sessions", response_model=UserChatSessionResponse)
def create_chat_session(
    request: UserChatSessionCreateRequest,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> UserChatSessionResponse:
    if request.ticker:
        _stock_or_404(session, request.ticker)
    row = ChatSession(
        session_id=request.session_id or f"chat_{uuid.uuid4().hex}",
        user_id=current_user.id,
        title=request.title,
        ticker=request.ticker,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _chat_session_response(row)


@router.get("/chat-sessions/{session_id}", response_model=UserChatSessionDetailResponse)
def get_chat_session_detail(
    session_id: str,
    session: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> UserChatSessionDetailResponse:
    row = session.scalars(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == current_user.id,
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CHAT_SESSION_NOT_FOUND",
                "message": "Chat session was not found.",
            },
        )

    role_order = case(
        (ChatMessage.role == "user", 0),
        (ChatMessage.role == "assistant", 1),
        else_=2,
    )
    messages = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == row.session_id)
        .order_by(
            ChatMessage.created_at.asc(),
            role_order.asc(),
            ChatMessage.message_id.asc(),
        )
    ).all()
    return UserChatSessionDetailResponse(
        session=_chat_session_response(row),
        messages=[_chat_message_response(message) for message in messages],
    )


def _me_response(user: User) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        cognito_sub=user.cognito_sub,
        email=user.email,
        email_verified=user.email_verified,
        nickname=user.nickname,
    )


def _preference_row(session: Session, user: User) -> UserPreference:
    row = session.scalars(select(UserPreference).where(UserPreference.user_id == user.id)).first()
    if row:
        return row
    row = UserPreference(user_id=user.id, preferences={})
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _watchlist_rows(session: Session, user: User) -> list[Watchlist]:
    return list(
        session.scalars(
            select(Watchlist)
            .where(Watchlist.user_id == user.id)
            .order_by(Watchlist.saved_at.desc(), Watchlist.ticker.asc())
        ).all()
    )


def _watchlist_response(item: Watchlist) -> ServerWatchlistItemResponse:
    return ServerWatchlistItemResponse(
        ticker=item.ticker,
        name=item.name,
        market=item.market,
        sector=item.sector,
        memo=item.memo,
        saved_at=item.saved_at,
    )


def _chat_session_response(row: ChatSession) -> UserChatSessionResponse:
    return UserChatSessionResponse(
        session_id=row.session_id,
        ticker=row.ticker,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _chat_message_response(row: ChatMessage) -> UserChatMessageResponse:
    return UserChatMessageResponse(
        message_id=row.message_id,
        role=row.role,
        content=row.content,
        ticker=row.ticker,
        citations=list(row.citations or []),
        safety_flags=list(row.safety_flags or []),
        created_at=row.created_at,
    )


def _stock_or_404(session: Session, ticker: str) -> Stock:
    validate_ticker(ticker)
    stock = session.get(Stock, ticker)
    if stock is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STOCK_NOT_FOUND",
                "message": "Stock was not found.",
            },
        )
    return stock
