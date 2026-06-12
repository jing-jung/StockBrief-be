from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db_session
from app.orm import User


@dataclass(frozen=True)
class CognitoClaims:
    sub: str
    email: str | None = None
    email_verified: bool = False
    nickname: str | None = None


def get_current_user(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User:
    claims = _claims_from_api_gateway_event(request) or _claims_from_authorization_header(request, settings)
    if claims is None:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    return _upsert_user_from_claims(session, claims)


def get_optional_current_user(
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User | None:
    claims = _claims_from_api_gateway_event(request)
    if claims is None:
        authorization = request.headers.get("Authorization")
        if not authorization:
            return None
        claims = _claims_from_authorization_header(request, settings)
    if claims is None:
        raise HTTPException(status_code=401, detail="Authentication is invalid.")
    return _upsert_user_from_claims(session, claims)


def _claims_from_api_gateway_event(request: Request) -> CognitoClaims | None:
    event = request.scope.get("aws.event")
    if not isinstance(event, dict):
        return None
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return None
    authorizer = request_context.get("authorizer")
    if not isinstance(authorizer, dict):
        return None
    jwt = authorizer.get("jwt")
    if not isinstance(jwt, dict):
        return None
    claims = jwt.get("claims")
    if not isinstance(claims, dict):
        return None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    return CognitoClaims(
        sub=sub,
        email=_optional_string(claims.get("email")),
        email_verified=_bool_claim(claims.get("email_verified")),
        nickname=_optional_string(claims.get("nickname") or claims.get("cognito:username")),
    )


def _claims_from_authorization_header(
    request: Request,
    settings: Settings,
) -> CognitoClaims | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token:
        return None
    if not settings.cognito_issuer or not settings.cognito_app_client_id:
        return None

    try:
        jwks_url = settings.cognito_jwks_url or f"{settings.cognito_issuer.rstrip('/')}/.well-known/jwks.json"
        signing_key = _jwk_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Authentication is invalid.") from exc

    if not _token_matches_client(claims, settings.cognito_app_client_id):
        raise HTTPException(status_code=401, detail="Authentication audience is invalid.")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    return CognitoClaims(
        sub=sub,
        email=_optional_string(claims.get("email")),
        email_verified=_bool_claim(claims.get("email_verified")),
        nickname=_optional_string(claims.get("nickname") or claims.get("cognito:username")),
    )


def _upsert_user_from_claims(session: Session, claims: CognitoClaims) -> User:
    user = session.scalars(select(User).where(User.cognito_sub == claims.sub)).first()
    if user is None:
        user = User(
            cognito_sub=claims.sub,
            email=claims.email,
            email_verified=claims.email_verified,
            nickname=claims.nickname,
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            user = session.scalars(select(User).where(User.cognito_sub == claims.sub)).first()
            if user is None:
                raise
            return _sync_user_claims(session, user, claims)
        session.refresh(user)
        return user

    return _sync_user_claims(session, user, claims)


def _sync_user_claims(session: Session, user: User, claims: CognitoClaims) -> User:
    changed = False
    if user.email != claims.email:
        user.email = claims.email
        changed = True
    if user.email_verified != claims.email_verified:
        user.email_verified = claims.email_verified
        changed = True
    if claims.nickname and user.nickname != claims.nickname:
        user.nickname = claims.nickname
        changed = True
    if changed:
        session.commit()
        session.refresh(user)
    return user


@lru_cache
def _jwk_client(jwks_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=600)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool_claim(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.casefold() == "true"
    return False


def _token_matches_client(claims: dict[str, object], app_client_id: str) -> bool:
    audience = claims.get("aud")
    client_id = claims.get("client_id")
    if isinstance(audience, str) and audience == app_client_id:
        return True
    if isinstance(audience, list) and app_client_id in audience:
        return True
    return isinstance(client_id, str) and client_id == app_client_id
