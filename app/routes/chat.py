import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_optional_current_user
from app.config import Settings, get_settings
from app.db import get_db_session
from app.models import (
    ChatCitationContract,
    ChatContractData,
    ChatContractResponse,
    ChatRequest,
    ChatResponse,
    ChatSafetyContract,
)
from app.orm import ChatMessage, ChatSession, User
from app.routes.common import COMMON_ERROR_RESPONSES, request_id
from app.services.candidate_service import CandidateService
from app.services.chat import ChatProviderInput, ChatProviderUnavailable, chat_provider_for
from app.services.chat.composer import normalize_chat_answer
from app.services.evidence_service import EvidenceService, contract_source_type

router = APIRouter()


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
    settings: Settings = Depends(get_settings),
) -> ChatContractResponse:
    candidate_service = CandidateService(session)
    stock, score = candidate_service.candidate_row(request.ticker)
    candidate = candidate_service.candidate_response(stock, score)
    evidence = EvidenceService(session).items(request.ticker)
    current_user_id = current_user.id if current_user is not None else None
    session.close()

    try:
        provider = chat_provider_for(settings.chat_provider, settings=settings)
        response = provider.compose(
            ChatProviderInput(
                message=request.message,
                candidate=candidate,
                evidence=evidence,
            )
        )
        response = response.model_copy(
            update={"answer": normalize_chat_answer(response.answer)}
        )
    except ChatProviderUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CHAT_PROVIDER_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc
    if current_user_id is not None:
        response = _persist_chat_exchange(
            session=session,
            user_id=current_user_id,
            request=request,
            response=response,
        )
    return ChatContractResponse(
        data=_chat_contract_data(request=request, response=response),
        message=f"{provider.name} Agent 응답을 반환했습니다.",
        request_id=request_id(http_request),
    )


def _chat_contract_data(
    *,
    request: ChatRequest,
    response: ChatResponse,
) -> ChatContractData:
    return ChatContractData(
        session_id=response.session_id or request.session_id or f"local-{request.ticker}",
        message_id=response.message_id,
        answer=response.answer,
        citations=[
            ChatCitationContract(
                id=citation.evidence_id,
                source_type=contract_source_type(citation.type),
                title=citation.title,
                url=citation.source_url,
                published_at=citation.published_at,
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


def _persist_chat_exchange(
    *,
    session: Session,
    user_id: uuid.UUID,
    request: ChatRequest,
    response: ChatResponse,
) -> ChatResponse:
    session_id = request.session_id or f"chat_{uuid.uuid4().hex}"
    chat_session = session.scalars(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user_id,
        )
    ).first()
    if chat_session is None:
        chat_session = ChatSession(
            session_id=session_id,
            user_id=user_id,
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
