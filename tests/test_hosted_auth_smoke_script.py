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
                            "cognito_sub": "user-sub",
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
        "response_shape": "me",
        "contract_ok": True,
        "authenticated": True,
        "email_present": True,
        "email_verified": True,
        "nickname_present": True,
    }
    assert result["checks"]["auth_api:/v1/me/preferences"]["summary"] == {
        "response_shape": "preferences",
        "contract_ok": True,
        "preference_keys": ["notifications", "risk_profile"]
    }
    assert result["checks"]["auth_api:/v1/me/watchlist"]["summary"] == {
        "response_shape": "watchlist",
        "contract_ok": True,
        "item_count": 1,
    }
    assert result["checks"]["auth_api:/v1/me/chat-sessions"]["summary"] == {
        "response_shape": "chat_sessions",
        "contract_ok": True,
        "count": 2,
    }
    auth_headers = [headers for _, headers, _ in fetcher.calls[3:]]
    assert all(headers.get("Authorization") == "Bearer secret-token" for headers in auth_headers)


def test_hosted_auth_smoke_reads_token_file_without_printing_it(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STOCKBRIEF_AUTH_BEARER_TOKEN", raising=False)
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-secret-token\n", encoding="utf-8")
    fetcher = FakeFetcher()

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        token_file=str(token_file),
        fetch=fetcher,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert "file-secret-token" not in serialized
    assert str(token_file) not in serialized
    auth_headers = [headers for _, headers, _ in fetcher.calls[3:]]
    assert all(headers.get("Authorization") == "Bearer file-secret-token" for headers in auth_headers)


def test_hosted_auth_smoke_prefers_explicit_token_file_over_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "env-secret-token")
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-secret-token\n", encoding="utf-8")
    fetcher = FakeFetcher()

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        token_file=str(token_file),
        fetch=fetcher,
    )

    assert result["ok"] is True
    auth_headers = [headers for _, headers, _ in fetcher.calls[3:]]
    assert all(headers.get("Authorization") == "Bearer file-secret-token" for headers in auth_headers)


def test_hosted_auth_smoke_reports_missing_token_file_without_path(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STOCKBRIEF_AUTH_BEARER_TOKEN", raising=False)
    token_file = tmp_path / "missing-token.txt"

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        token_file=str(token_file),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is False
    assert {"code": "missing_auth_token_file"} in result["blockers"]
    assert str(token_file) not in serialized


def test_hosted_auth_smoke_reports_empty_token_file_without_path(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STOCKBRIEF_AUTH_BEARER_TOKEN", raising=False)
    token_file = tmp_path / "empty-token.txt"
    token_file.write_text("\n", encoding="utf-8")

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        token_file=str(token_file),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is False
    assert {"code": "empty_auth_token_file"} in result["blockers"]
    assert str(token_file) not in serialized


def test_hosted_auth_smoke_accepts_top_level_protected_api_responses(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")

    def fetch(url: str, headers: dict[str, str], timeout_seconds: float):
        if url.endswith("/me"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "cognito_sub": "user-sub",
                        "email": "user@example.com",
                        "email_verified": True,
                        "nickname": "새별",
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/me/preferences"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"preferences": {"risk_profile": "balanced"}}).encode("utf-8"),
            )
        if url.endswith("/me/watchlist"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "count": 1,
                        "items": [
                            {
                                "ticker": "005930",
                                "name": "삼성전자",
                                "memo": "비공개 관심종목 메모",
                            }
                        ],
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/me/chat-sessions"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"count": 0, "items": []}).encode("utf-8"),
            )
        return smoke.HttpResponse(status_code=200, body=b"<html>ok</html>")

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        fetch=fetch,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert "secret-token" not in serialized
    assert "user@example.com" not in serialized
    assert "005930" not in serialized
    assert "삼성전자" not in serialized
    assert "비공개 관심종목 메모" not in serialized
    assert "비공개 대화" not in serialized
    assert result["checks"]["auth_api:/v1/me"]["summary"] == {
        "response_shape": "me",
        "contract_ok": True,
        "authenticated": True,
        "email_present": True,
        "email_verified": True,
        "nickname_present": True,
    }
    assert result["checks"]["auth_api:/v1/me/preferences"]["summary"] == {
        "response_shape": "preferences",
        "contract_ok": True,
        "preference_keys": ["risk_profile"]
    }
    assert result["checks"]["auth_api:/v1/me/watchlist"]["summary"] == {
        "response_shape": "watchlist",
        "contract_ok": True,
        "item_count": 1,
    }
    assert result["checks"]["auth_api:/v1/me/chat-sessions"]["summary"] == {
        "response_shape": "chat_sessions",
        "contract_ok": True,
        "count": 0,
    }


def test_hosted_auth_smoke_rejects_unrecognized_success_shapes(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")

    def fetch(url: str, headers: dict[str, str], timeout_seconds: float):
        if url.endswith("/me"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"email": "user@example.com"}).encode("utf-8"),
            )
        if url.endswith("/me/preferences"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"arbitrary": {"risk_profile": "balanced"}}).encode("utf-8"),
            )
        if url.endswith("/me/watchlist"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"items": "not-a-list"}).encode("utf-8"),
            )
        if url.endswith("/me/chat-sessions"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps({"count": "0"}).encode("utf-8"),
            )
        return smoke.HttpResponse(status_code=200, body=b"<html>ok</html>")

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        fetch=fetch,
    )

    assert result["ok"] is False
    assert result["checks"]["auth_api:/v1/me"]["summary"]["authenticated"] is False
    assert result["checks"]["auth_api:/v1/me"]["summary"]["contract_ok"] is False
    assert result["checks"]["auth_api:/v1/me/preferences"]["summary"] == {
        "response_shape": "unknown",
        "contract_ok": False,
    }
    assert result["checks"]["auth_api:/v1/me/watchlist"]["summary"] == {
        "response_shape": "watchlist",
        "contract_ok": False,
        "item_count": None,
    }
    assert result["checks"]["auth_api:/v1/me/chat-sessions"]["summary"] == {
        "response_shape": "chat_sessions",
        "contract_ok": False,
        "count": None,
    }
    assert {
        "check": "auth_api:/v1/me",
        "status_code": 200,
        "error_code": "check_failed",
    } in result["blockers"]


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
