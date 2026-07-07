from __future__ import annotations

import jwt as pyjwt
import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import auth
from app.config import Settings
from app.orm import User


def _request(
    *,
    headers: dict[str, str] | None = None,
    aws_event: dict | None = None,
) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/v1/me",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
    }
    if aws_event is not None:
        scope["aws.event"] = aws_event
    return Request(scope)


def _settings(
    *,
    issuer: str = "https://issuer.example.com",
    client_id: str = "client-1",
) -> Settings:
    return Settings(COGNITO_ISSUER=issuer, COGNITO_APP_CLIENT_ID=client_id)


class _DummySigningKey:
    key = "unused-test-key"


class _DummyJwkClient:
    def __init__(self, url: str, **kwargs: object) -> None:
        self.url = url

    def get_signing_key_from_jwt(self, token: str) -> _DummySigningKey:
        return _DummySigningKey()


# --- _claims_from_authorization_header -------------------------------------


def test_claims_from_authorization_header_returns_none_without_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()

    result = auth._claims_from_authorization_header(request, _settings())

    assert result is None


def test_claims_from_authorization_header_returns_none_for_non_bearer_scheme() -> None:
    request = _request(headers={"Authorization": "Basic abc123"})

    result = auth._claims_from_authorization_header(request, _settings())

    assert result is None


def test_claims_from_authorization_header_returns_none_without_configured_issuer() -> None:
    request = _request(headers={"Authorization": "Bearer some.jwt.token"})

    result = auth._claims_from_authorization_header(
        request, _settings(issuer="", client_id="")
    )

    assert result is None


def test_claims_from_authorization_header_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["issuer"] == "https://issuer.example.com"
        return {
            "sub": "user-sub",
            "client_id": "client-1",
            "email": "user@example.com",
            "email_verified": "true",
            "nickname": "researcher",
        }

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer valid.jwt.token"})

    claims = auth._claims_from_authorization_header(request, _settings())

    assert claims is not None
    assert claims.sub == "user-sub"
    assert claims.email == "user@example.com"
    assert claims.email_verified is True
    assert claims.nickname == "researcher"


def test_claims_from_authorization_header_raises_401_for_expired_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        raise pyjwt.ExpiredSignatureError("token expired")

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer expired.jwt.token"})

    with pytest.raises(Exception) as exc_info:
        auth._claims_from_authorization_header(request, _settings())

    assert getattr(exc_info.value, "status_code", None) == 401


def test_claims_from_authorization_header_raises_401_for_malformed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        raise pyjwt.DecodeError("not enough segments")

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer not-a-jwt"})

    with pytest.raises(Exception) as exc_info:
        auth._claims_from_authorization_header(request, _settings())

    assert getattr(exc_info.value, "status_code", None) == 401


def test_claims_from_authorization_header_rejects_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        return {"sub": "user-sub", "aud": "some-other-client"}

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer wrong.aud.token"})

    with pytest.raises(Exception) as exc_info:
        auth._claims_from_authorization_header(request, _settings(client_id="client-1"))

    assert getattr(exc_info.value, "status_code", None) == 401
    assert "audience" in str(exc_info.value.detail).lower()


def test_claims_from_authorization_header_rejects_wrong_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        return {"sub": "user-sub", "client_id": "some-other-client"}

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer wrong.client.token"})

    with pytest.raises(Exception) as exc_info:
        auth._claims_from_authorization_header(request, _settings(client_id="client-1"))

    assert getattr(exc_info.value, "status_code", None) == 401


def test_claims_from_authorization_header_returns_none_without_sub_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        return {"client_id": "client-1"}

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer no.sub.token"})

    result = auth._claims_from_authorization_header(request, _settings())

    assert result is None


# --- _claims_from_api_gateway_event ------------------------------------


def test_claims_from_api_gateway_event_returns_none_without_event() -> None:
    request = _request()

    assert auth._claims_from_api_gateway_event(request) is None


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"requestContext": "not-a-dict"},
        {"requestContext": {"authorizer": "not-a-dict"}},
        {"requestContext": {"authorizer": {"jwt": "not-a-dict"}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": "not-a-dict"}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": ""}}}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": 123}}}}},
    ],
)
def test_claims_from_api_gateway_event_returns_none_for_malformed_shapes(
    event: dict,
) -> None:
    request = _request(aws_event=event)

    assert auth._claims_from_api_gateway_event(request) is None


def test_claims_from_api_gateway_event_extracts_claims() -> None:
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "gateway-sub",
                        "email": "gateway@example.com",
                        "email_verified": True,
                        "cognito:username": "gateway-user",
                    }
                }
            }
        }
    }
    request = _request(aws_event=event)

    claims = auth._claims_from_api_gateway_event(request)

    assert claims is not None
    assert claims.sub == "gateway-sub"
    assert claims.email == "gateway@example.com"
    assert claims.email_verified is True
    assert claims.nickname == "gateway-user"


# --- get_optional_current_user -----------------------------------------


def test_get_optional_current_user_returns_none_without_any_auth(
    seeded_session: Session,
) -> None:
    request = _request()

    result = auth.get_optional_current_user(
        request, session=seeded_session, settings=_settings(issuer="", client_id="")
    )

    assert result is None


def test_get_optional_current_user_raises_401_for_invalid_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
    seeded_session: Session,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        raise pyjwt.DecodeError("bad token")

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer garbage"})

    with pytest.raises(Exception) as exc_info:
        auth.get_optional_current_user(request, session=seeded_session, settings=_settings())

    assert getattr(exc_info.value, "status_code", None) == 401


def test_get_optional_current_user_raises_401_when_header_present_but_unparseable(
    seeded_session: Session,
) -> None:
    # Authorization header is present (so we don't take the "no auth at all"
    # early return) but uses a non-bearer scheme, so
    # _claims_from_authorization_header returns None and this should 401
    # rather than silently treating the request as anonymous.
    request = _request(headers={"Authorization": "Basic abc123"})

    with pytest.raises(Exception) as exc_info:
        auth.get_optional_current_user(request, session=seeded_session, settings=_settings())

    assert getattr(exc_info.value, "status_code", None) == 401


def test_get_optional_current_user_upserts_user_from_valid_token(
    monkeypatch: pytest.MonkeyPatch,
    seeded_session: Session,
) -> None:
    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        return {"sub": "optional-sub", "client_id": "client-1"}

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _DummyJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    request = _request(headers={"Authorization": "Bearer valid.jwt.token"})

    user = auth.get_optional_current_user(request, session=seeded_session, settings=_settings())

    assert user is not None
    assert user.cognito_sub == "optional-sub"


# --- get_current_user ----------------------------------------------------


def test_get_current_user_raises_401_without_any_claims(seeded_session: Session) -> None:
    request = _request()

    with pytest.raises(Exception) as exc_info:
        auth.get_current_user(
            request, session=seeded_session, settings=_settings(issuer="", client_id="")
        )

    assert getattr(exc_info.value, "status_code", None) == 401


def test_get_current_user_upserts_user_for_valid_gateway_claims(
    seeded_session: Session,
) -> None:
    event = {
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"sub": "gateway-current-user-sub"}}}
        }
    }
    request = _request(aws_event=event)

    user = auth.get_current_user(
        request, session=seeded_session, settings=_settings(issuer="", client_id="")
    )

    assert user.cognito_sub == "gateway-current-user-sub"


def test_upsert_user_from_claims_syncs_existing_user_on_second_call(
    seeded_session: Session,
) -> None:
    from app.auth import _upsert_user_from_claims

    claims = auth.CognitoClaims(
        sub="repeat-login-sub", email="first@example.com", email_verified=False
    )
    first = _upsert_user_from_claims(seeded_session, claims)
    assert first.email == "first@example.com"

    updated_claims = auth.CognitoClaims(
        sub="repeat-login-sub", email="second@example.com", email_verified=True
    )
    second = _upsert_user_from_claims(seeded_session, updated_claims)

    assert second.cognito_sub == "repeat-login-sub"
    assert second.email == "second@example.com"
    assert second.email_verified is True


# --- _sync_user_claims ----------------------------------------------------


def test_sync_user_claims_updates_changed_fields(seeded_session: Session) -> None:
    user = User(
        cognito_sub="sync-sub",
        email="old@example.com",
        email_verified=False,
        nickname="old-name",
    )
    seeded_session.add(user)
    seeded_session.commit()

    updated = auth._sync_user_claims(
        seeded_session,
        user,
        auth.CognitoClaims(
            sub="sync-sub",
            email="new@example.com",
            email_verified=True,
            nickname="new-name",
        ),
    )

    assert updated.email == "new@example.com"
    assert updated.email_verified is True
    assert updated.nickname == "new-name"


def test_sync_user_claims_keeps_existing_nickname_when_claims_nickname_is_empty(
    seeded_session: Session,
) -> None:
    user = User(
        cognito_sub="keep-nickname-sub",
        email="same@example.com",
        email_verified=True,
        nickname="keep-me",
    )
    seeded_session.add(user)
    seeded_session.commit()

    updated = auth._sync_user_claims(
        seeded_session,
        user,
        auth.CognitoClaims(
            sub="keep-nickname-sub",
            email="same@example.com",
            email_verified=True,
            nickname=None,
        ),
    )

    assert updated.nickname == "keep-me"


def test_sync_user_claims_is_a_noop_when_nothing_changed(
    seeded_session: Session,
) -> None:
    user = User(
        cognito_sub="noop-sub",
        email="same@example.com",
        email_verified=True,
        nickname="same-name",
    )
    seeded_session.add(user)
    seeded_session.commit()
    original_updated_at = user.updated_at

    updated = auth._sync_user_claims(
        seeded_session,
        user,
        auth.CognitoClaims(
            sub="noop-sub",
            email="same@example.com",
            email_verified=True,
            nickname="same-name",
        ),
    )

    assert updated.updated_at == original_updated_at


# --- _upsert_user_from_claims raises when rollback loses the race --------


def test_upsert_user_from_claims_reraises_when_concurrent_lookup_finds_nothing(
    seeded_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import IntegrityError

    claims = auth.CognitoClaims(sub="ghost-sub", email="ghost@example.com")

    def failing_commit() -> None:
        raise IntegrityError("insert users", {}, Exception("duplicate"))

    monkeypatch.setattr(seeded_session, "commit", failing_commit)

    with pytest.raises(IntegrityError):
        auth._upsert_user_from_claims(seeded_session, claims)


# --- _bool_claim / _token_matches_client (pure helpers) -------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("false", False),
        ("", False),
        (None, False),
        (1, False),
    ],
)
def test_bool_claim_normalizes_values(value: object, expected: bool) -> None:
    assert auth._bool_claim(value) is expected


def test_token_matches_client_accepts_matching_string_audience() -> None:
    assert auth._token_matches_client({"aud": "client-1"}, "client-1") is True


def test_token_matches_client_accepts_matching_audience_list() -> None:
    assert auth._token_matches_client({"aud": ["other", "client-1"]}, "client-1") is True


def test_token_matches_client_accepts_matching_client_id_claim() -> None:
    assert auth._token_matches_client({"client_id": "client-1"}, "client-1") is True


def test_token_matches_client_rejects_mismatched_claims() -> None:
    assert auth._token_matches_client({"aud": "other", "client_id": "other"}, "client-1") is False


def test_token_matches_client_rejects_missing_claims() -> None:
    assert auth._token_matches_client({}, "client-1") is False


def test_jwk_client_uses_settings_configured_jwks_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls_used: list[str] = []

    class RecordingJwkClient(_DummyJwkClient):
        def __init__(self, url: str, **kwargs: object) -> None:
            urls_used.append(url)
            super().__init__(url, **kwargs)

    def fake_decode(*args: object, **kwargs: object) -> dict[str, object]:
        return {"sub": "jwks-url-sub", "client_id": "client-1"}

    monkeypatch.setattr(auth.jwt, "PyJWKClient", RecordingJwkClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    auth._jwk_client.cache_clear()
    request = _request(headers={"Authorization": "Bearer valid.jwt.token"})

    auth._claims_from_authorization_header(
        request,
        Settings(
            COGNITO_ISSUER="https://issuer.example.com",
            COGNITO_APP_CLIENT_ID="client-1",
            COGNITO_JWKS_URL="https://custom-jwks.example.com/keys.json",
        ),
    )

    assert urls_used == ["https://custom-jwks.example.com/keys.json"]
    auth._jwk_client.cache_clear()
