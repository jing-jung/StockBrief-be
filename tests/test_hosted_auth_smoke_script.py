from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts/check_hosted_auth_smoke.py"


spec = importlib.util.spec_from_file_location("check_hosted_auth_smoke", SCRIPT_PATH)
assert spec is not None
smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


class FakeFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout_seconds: float):
        self.calls.append((url, headers, timeout_seconds))
        if url.endswith("/me"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "data": {
                            "email": "user@example.com",
                            "email_verified": True,
                            "nickname": "새별",
                        }
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/me/preferences"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "data": {
                            "preferences": {
                                "risk_profile": "balanced",
                                "notifications": {"email_enabled": True},
                                "private_note": "should-not-print",
                            }
                        }
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/me/watchlist"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "data": {
                            "count": 1,
                            "items": [
                                {
                                    "ticker": "005930",
                                    "name": "삼성전자",
                                    "memo": "비공개 관심종목 메모",
                                }
                            ],
                        }
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/me/chat-sessions"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"data": {"count": 2, "items": [{"title": "비공개 대화"}]}}).encode(
                    "utf-8"
                ),
            )
        return smoke.HttpResponse(status_code=200, body=b"<html>ok</html>")


def test_hosted_auth_smoke_requires_auth_token_for_api_checks(monkeypatch) -> None:
    monkeypatch.delenv("STOCKBRIEF_AUTH_BEARER_TOKEN", raising=False)

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com/v1",
    )

    assert result["ok"] is False
    assert {"code": "missing_auth_token", "env": "STOCKBRIEF_AUTH_BEARER_TOKEN"} in result[
        "blockers"
    ]


def test_hosted_auth_smoke_redacts_token_email_and_raw_response(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")
    fetcher = FakeFetcher()

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        fetch=fetcher,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert "secret-token" not in serialized
    assert "user@example.com" not in serialized
    assert "should-not-print" not in serialized
    assert "005930" not in serialized
    assert "삼성전자" not in serialized
    assert "비공개 관심종목 메모" not in serialized
    assert "비공개 대화" not in serialized
    assert result["checks"]["auth_api:/v1/me"]["summary"] == {
        "authenticated": True,
        "email_present": True,
        "email_verified": True,
        "nickname_present": True,
    }
    assert result["checks"]["auth_api:/v1/me/preferences"]["summary"] == {
        "preference_keys": ["notifications", "risk_profile"]
    }
    assert result["checks"]["auth_api:/v1/me/watchlist"]["summary"] == {"item_count": 1}
    assert result["checks"]["auth_api:/v1/me/chat-sessions"]["summary"] == {"count": 2}
    auth_headers = [headers for _, headers, _ in fetcher.calls[3:]]
    assert all(headers.get("Authorization") == "Bearer secret-token" for headers in auth_headers)


def test_hosted_auth_smoke_can_run_pages_only_without_token(monkeypatch) -> None:
    monkeypatch.delenv("STOCKBRIEF_AUTH_BEARER_TOKEN", raising=False)
    fetcher = FakeFetcher()

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="",
        check_auth_api=False,
        fetch=fetcher,
    )

    assert result["ok"] is True
    assert set(result["checks"]) == {
        "hosted_page:/",
        "hosted_page:/account",
        "hosted_page:/auth/callback",
    }
    assert all(not headers for _, headers, _ in fetcher.calls)


def test_hosted_auth_smoke_reports_protected_api_error_without_raw_body(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")

    def fetch(url: str, headers: dict[str, str], timeout_seconds: float):
        if url.endswith("/me"):
            return smoke.HttpResponse(
                status_code=401,
                body=json.dumps(
                    {
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "Bearer secret-token expired for user@example.com",
                        }
                    }
                ).encode("utf-8"),
            )
        return smoke.HttpResponse(status_code=200, body=json.dumps({"data": {}}).encode("utf-8"))

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com/v1",
        fetch=fetch,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is False
    assert "secret-token" not in serialized
    assert "user@example.com" not in serialized
    assert {
        "check": "auth_api:/v1/me",
        "status_code": 401,
        "error_code": "UNAUTHORIZED",
    } in result["blockers"]
