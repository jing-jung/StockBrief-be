from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth
from app.auth import CognitoClaims, _upsert_user_from_claims, get_current_user, get_optional_current_user
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.orm import ChatMessage, User, Watchlist


def _auth_user(session: Session, sub: str = "cognito-sub-1") -> User:
    user = session.scalars(select(User).where(User.cognito_sub == sub)).first()
    if user:
        return user
    user = User(
        cognito_sub=sub,
        email=f"{sub}@example.com",
        email_verified=True,
        nickname="tester",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _authenticated_client(seeded_session: Session, sub: str = "cognito-sub-1") -> TestClient:
    user = _auth_user(seeded_session, sub)

    def override_current_user() -> User:
        return user

    def override_db_session():
        yield seeded_session

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def test_public_recommendation_api_still_works_without_auth(
    seeded_api_client: TestClient,
) -> None:
    response = seeded_api_client.get("/v1/recommendations/candidates")

    assert response.status_code == 200


def test_protected_me_requires_auth(seeded_api_client: TestClient) -> None:
    response = seeded_api_client.get("/v1/me")

    assert response.status_code == 401
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_cognito_claims_upsert_user_without_password_storage(seeded_session: Session) -> None:
    user = _upsert_user_from_claims(
        seeded_session,
        CognitoClaims(
            sub="cognito-sub-from-jwt",
            email="verified@example.com",
            email_verified=True,
            nickname="verified-user",
        ),
    )

    assert user.cognito_sub == "cognito-sub-from-jwt"
    assert user.email == "verified@example.com"
    assert user.email_verified is True
    assert user.nickname == "verified-user"
    assert not hasattr(user, "password")


def test_cognito_claims_upsert_recovers_from_concurrent_insert(
    seeded_session: Session,
    monkeypatch,
) -> None:
    claims = CognitoClaims(
        sub="race-sub",
        email="race@example.com",
        email_verified=True,
        nickname="race-user",
    )
    original_commit = seeded_session.commit
    calls = {"count": 0}

    def commit_once_with_race() -> None:
        if calls["count"] == 0:
            calls["count"] += 1
            raise IntegrityError("insert users", {}, Exception("duplicate"))
        original_commit()

    def rollback_with_concurrent_user() -> None:
        seeded_session.expunge_all()
        original_commit()
        concurrent_user = User(
            cognito_sub=claims.sub,
            email=claims.email,
            email_verified=claims.email_verified,
            nickname=claims.nickname,
        )
        seeded_session.add(concurrent_user)
        original_commit()

    monkeypatch.setattr(seeded_session, "commit", commit_once_with_race)
    monkeypatch.setattr(seeded_session, "rollback", rollback_with_concurrent_user)

    user = _upsert_user_from_claims(seeded_session, claims)

    assert user.cognito_sub == "race-sub"
    assert user.email == "race@example.com"


def test_jwk_client_is_cached_per_url(monkeypatch) -> None:
    auth._jwk_client.cache_clear()
    created: list[str] = []

    class DummyJwkClient:
        def __init__(self, url: str, **kwargs) -> None:
            created.append(url)
            self.url = url
            self.kwargs = kwargs

    monkeypatch.setattr(auth.jwt, "PyJWKClient", DummyJwkClient)

    first = auth._jwk_client("https://issuer.example.com/.well-known/jwks.json")
    second = auth._jwk_client("https://issuer.example.com/.well-known/jwks.json")

    assert first is second
    assert created == ["https://issuer.example.com/.well-known/jwks.json"]
    assert first.kwargs["cache_jwk_set"] is True
    assert first.kwargs["lifespan"] == 600
    auth._jwk_client.cache_clear()


def test_protected_api_accepts_valid_cognito_bearer_token(
    seeded_session: Session,
    monkeypatch,
) -> None:
    class DummySigningKey:
        key = "unused-test-key"

    class DummyJwkClient:
        def __init__(self, url: str, **kwargs) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> DummySigningKey:
            assert token == "valid.jwt.token"
            return DummySigningKey()

    def fake_decode(*args, **kwargs):
        assert kwargs["issuer"] == "https://issuer.example.com"
        return {
            "sub": "bearer-sub",
            "client_id": "client-1",
            "email": "bearer@example.com",
            "email_verified": "true",
        }

    def override_db_session():
        yield seeded_session

    def override_settings() -> Settings:
        return Settings(
            COGNITO_ISSUER="https://issuer.example.com",
            COGNITO_APP_CLIENT_ID="client-1",
        )

    monkeypatch.setattr(auth.jwt, "PyJWKClient", DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = override_settings
    try:
        response = TestClient(app).get(
            "/v1/me",
            headers={"Authorization": "Bearer valid.jwt.token"},
        )

        assert response.status_code == 200
        assert response.json()["cognito_sub"] == "bearer-sub"
    finally:
        app.dependency_overrides.clear()


def test_patch_me_rejects_client_supplied_user_id(
    seeded_session: Session,
) -> None:
    client = _authenticated_client(seeded_session)
    try:
        response = client.patch(
            "/v1/me",
            json={"nickname": "researcher", "user_id": "malicious-user-id"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"
    finally:
        app.dependency_overrides.clear()


def test_get_and_patch_me_uses_cognito_sub_from_auth_context(
    seeded_session: Session,
) -> None:
    client = _authenticated_client(seeded_session)
    try:
        response = client.patch("/v1/me", json={"nickname": "researcher"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["cognito_sub"] == "cognito-sub-1"
        assert payload["nickname"] == "researcher"
        user = seeded_session.scalars(select(User).where(User.cognito_sub == "cognito-sub-1")).one()
        assert user.nickname == "researcher"
    finally:
        app.dependency_overrides.clear()


def test_preferences_round_trip_for_authenticated_user(seeded_session: Session) -> None:
    client = _authenticated_client(seeded_session)
    try:
        response = client.put(
            "/v1/me/preferences",
            json={"preferences": {"risk_profile": "balanced", "markets": ["KOSPI"]}},
        )

        assert response.status_code == 200
        assert response.json()["preferences"]["risk_profile"] == "balanced"

        get_response = client.get("/v1/me/preferences")
        assert get_response.status_code == 200
        assert get_response.json()["preferences"]["markets"] == ["KOSPI"]
    finally:
        app.dependency_overrides.clear()


def test_server_watchlist_add_dedup_delete(seeded_session: Session) -> None:
    client = _authenticated_client(seeded_session)
    try:
        body = {
            "ticker": "005930",
            "name": "삼성전자",
            "market": "KOSPI",
            "sector": "반도체",
            "memo": "public data review",
        }
        first = client.post("/v1/me/watchlist", json=body)
        second = client.post("/v1/me/watchlist", json=body)

        assert first.status_code == 200
        assert second.status_code == 200
        list_response = client.get("/v1/me/watchlist")
        assert list_response.status_code == 200
        assert list_response.json()["count"] == 1

        delete_response = client.delete("/v1/me/watchlist/005930")
        assert delete_response.status_code == 204
        assert client.get("/v1/me/watchlist").json()["count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_server_watchlist_memo_patch(seeded_session: Session) -> None:
    client = _authenticated_client(seeded_session)
    try:
        client.post(
            "/v1/me/watchlist",
            json={
                "ticker": "005930",
                "name": "삼성전자",
                "market": "KOSPI",
                "sector": "반도체",
            },
        )
        response = client.patch("/v1/me/watchlist/005930", json={"memo": "review memo"})

        assert response.status_code == 200
        assert response.json()["memo"] == "review memo"
    finally:
        app.dependency_overrides.clear()


def test_watchlist_import_merges_without_duplicate_tickers(
    seeded_session: Session,
) -> None:
    client = _authenticated_client(seeded_session)
    try:
        client.post(
            "/v1/me/watchlist",
            json={
                "ticker": "005930",
                "name": "삼성전자",
                "market": "KOSPI",
                "sector": "반도체",
            },
        )
        response = client.post(
            "/v1/me/watchlist/import",
            json={
                "items": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "market": "KOSPI",
                        "sector": "반도체",
                    },
                    {
                        "ticker": "000660",
                        "name": "SK하이닉스",
                        "market": "KOSPI",
                        "sector": "반도체",
                    },
                    {
                        "ticker": "000660",
                        "name": "SK하이닉스",
                        "market": "KOSPI",
                        "sector": "반도체",
                    },
                ]
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["imported_count"] == 1
        assert payload["skipped_existing_count"] == 1
        assert {item["ticker"] for item in payload["items"]} == {"005930", "000660"}

        user = seeded_session.scalars(select(User).where(User.cognito_sub == "cognito-sub-1")).one()
        rows = seeded_session.scalars(select(Watchlist).where(Watchlist.user_id == user.id)).all()
        assert len(rows) == 2
    finally:
        app.dependency_overrides.clear()


def test_chat_sessions_are_user_scoped(seeded_session: Session) -> None:
    first_client = _authenticated_client(seeded_session, "cognito-sub-1")
    try:
        created = first_client.post(
            "/v1/me/chat-sessions",
            json={"ticker": "005930", "title": "삼성전자 설명"},
        )
        assert created.status_code == 200
        assert created.json()["ticker"] == "005930"
        assert first_client.get("/v1/me/chat-sessions").json()["count"] == 1
    finally:
        app.dependency_overrides.clear()

    second_client = _authenticated_client(seeded_session, "cognito-sub-2")
    try:
        assert second_client.get("/v1/me/chat-sessions").json()["count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_authenticated_chat_persists_session_and_messages(seeded_session: Session) -> None:
    user = _auth_user(seeded_session, "cognito-sub-chat")

    def override_current_user() -> User:
        return user

    def override_db_session():
        yield seeded_session

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_optional_current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    client = TestClient(app)
    try:
        response = client.post(
            "/v1/chat",
            json={
                "ticker": "005930",
                "message": "왜 추천됐나요?",
                "title": "삼성전자 설명",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["data"]["session_id"]
        assert client.get("/v1/me/chat-sessions").json()["count"] == 1
        messages = seeded_session.scalars(
            select(ChatMessage).where(ChatMessage.session_id == payload["data"]["session_id"])
        ).all()
        assert [message.role for message in messages] == ["user", "assistant"]
    finally:
        app.dependency_overrides.clear()
