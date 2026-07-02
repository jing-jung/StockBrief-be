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


class WatchlistWriteRequester:
    def __init__(
        self,
        *,
        preexisting: bool = False,
        concurrent_existing_on_post: bool = False,
        fail_delete: bool = False,
    ) -> None:
        self.items = {"005930"} if preexisting else set()
        self.concurrent_existing_on_post = concurrent_existing_on_post
        self.fail_delete = fail_delete
        self.calls: list[tuple[str, str, dict[str, str], dict[str, object] | None, float]] = []

    def __call__(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: dict[str, object] | None,
        timeout_seconds: float,
    ):
        self.calls.append((url, method, headers, body, timeout_seconds))
        if method == "GET" and url.endswith("/me/watchlist"):
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "data": {
                            "count": len(self.items),
                            "items": [
                                {
                                    "ticker": ticker,
                                    "name": "비공개 관심종목",
                                    "memo": "비공개 메모",
                                }
                                for ticker in sorted(self.items)
                            ],
                        }
                    }
                ).encode("utf-8"),
            )
        if method == "POST" and url.endswith("/me/watchlist"):
            ticker = str(body["ticker"]) if body else ""
            if self.concurrent_existing_on_post:
                self.items.add(ticker)
                return smoke.HttpResponse(
                    status_code=200,
                    body=json.dumps(
                        {
                            "data": {
                                "ticker": ticker,
                                "name": "기존 관심종목",
                                "market": "KOSPI",
                                "sector": "기존 섹터",
                                "memo": "기존 메모",
                            }
                        }
                    ).encode("utf-8"),
                )
            self.items.add(ticker)
            return smoke.HttpResponse(
                status_code=200,
                body=json.dumps(
                    {
                        "data": {
                            "ticker": ticker,
                            "name": body["name"],
                            "market": body["market"],
                            "sector": body["sector"],
                            "memo": body["memo"],
                        }
                    }
                ).encode("utf-8"),
            )
        if method == "PATCH" and "/me/watchlist/" in url:
            ticker = url.rsplit("/", 1)[-1]
            status_code = 200 if ticker in self.items else 404
            return smoke.HttpResponse(
                status_code=status_code,
                body=json.dumps({"data": {"ticker": ticker, "memo": "updated memo"}}).encode(
                    "utf-8"
                ),
            )
        if method == "DELETE" and "/me/watchlist/" in url:
            ticker = url.rsplit("/", 1)[-1]
            if self.fail_delete:
                return smoke.HttpResponse(
                    status_code=500,
                    body=json.dumps({"error": {"code": "DELETE_FAILED"}}).encode("utf-8"),
                )
            self.items.discard(ticker)
            return smoke.HttpResponse(status_code=204, body=b"")
        return smoke.HttpResponse(status_code=500, body=b"{}")


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


def test_hosted_auth_smoke_can_run_redacted_watchlist_write_cycle(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")
    requester = WatchlistWriteRequester()

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        check_watchlist_write=True,
        fetch=FakeFetcher(),
        request_json=requester,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    write_check = result["checks"]["auth_api:/v1/me/watchlist:write_cycle"]
    assert result["ok"] is True
    assert write_check["summary"] == {
        "response_shape": "watchlist_write_cycle",
        "contract_ok": True,
        "preexisting_item": False,
        "created": True,
        "updated": True,
        "deleted": True,
        "cleanup_confirmed": True,
    }
    assert "secret-token" not in serialized
    assert "005930" not in serialized
    assert "created memo" not in serialized
    assert "updated memo" not in serialized
    assert [method for _, method, _, _, _ in requester.calls] == [
        "GET",
        "POST",
        "PATCH",
        "DELETE",
        "GET",
    ]


def test_hosted_auth_smoke_does_not_mutate_preexisting_watchlist_item(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")
    requester = WatchlistWriteRequester(preexisting=True)

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        check_watchlist_write=True,
        fetch=FakeFetcher(),
        request_json=requester,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    write_check = result["checks"]["auth_api:/v1/me/watchlist:write_cycle"]
    assert result["ok"] is False
    assert write_check["error_code"] == "preexisting_watchlist_item"
    assert write_check["summary"]["preexisting_item"] is True
    assert "005930" not in serialized
    assert [method for _, method, _, _, _ in requester.calls] == ["GET"]


def test_hosted_auth_smoke_stops_when_post_returns_concurrent_existing_item(monkeypatch) -> None:
    monkeypatch.setenv("STOCKBRIEF_AUTH_BEARER_TOKEN", "secret-token")
    requester = WatchlistWriteRequester(concurrent_existing_on_post=True)

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        check_watchlist_write=True,
        fetch=FakeFetcher(),
        request_json=requester,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    write_check = result["checks"]["auth_api:/v1/me/watchlist:write_cycle"]
    assert result["ok"] is False
    assert write_check["error_code"] == "concurrent_watchlist_item_detected"
    assert write_check["summary"]["created"] is False
    assert "005930" not in serialized
    assert "기존 메모" not in serialized
    assert [method for _, method, _, _, _ in requester.calls] == ["GET", "POST"]


def test_hosted_auth_smoke_requires_auth_api_for_watchlist_write(monkeypatch) -> None:
    monkeypatch.delenv("STOCKBRIEF_AUTH_BEARER_TOKEN", raising=False)

    result = smoke.run_smoke(
        hosted_url="https://main.example.amplifyapp.com",
        api_base_url="https://api.example.com",
        check_auth_api=False,
        check_watchlist_write=True,
        fetch=FakeFetcher(),
        request_json=WatchlistWriteRequester(),
    )

    assert result["ok"] is False
    assert {"code": "watchlist_write_requires_auth_api"} in result["blockers"]
    assert result["checks"] == {}


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
