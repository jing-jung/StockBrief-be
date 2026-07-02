#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse


DEFAULT_HOSTED_PATHS = ("/", "/account", "/auth/callback")
DEFAULT_AUTH_API_PATHS = (
    "/v1/me",
    "/v1/me/preferences",
    "/v1/me/watchlist",
    "/v1/me/chat-sessions",
)
DEFAULT_TOKEN_ENV = "STOCKBRIEF_AUTH_BEARER_TOKEN"

Fetch = Callable[[str, dict[str, str], float], "HttpResponse"]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int | None
    body: bytes
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    target: str
    status_code: int | None
    summary: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "target": self.target,
            "status_code": self.status_code,
            "summary": self.summary,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_smoke(
        hosted_url=args.hosted_url,
        api_base_url=args.api_base_url,
        token_env=args.token_env,
        token_file=args.token_file,
        check_pages=not args.skip_pages,
        check_auth_api=not args.skip_auth_api,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run redacted hosted Cognito smoke checks for the dev stack."
    )
    parser.add_argument(
        "--hosted-url",
        default=os.environ.get("STOCKBRIEF_HOSTED_URL", ""),
        help="Amplify hosted base URL, such as https://main.example.amplifyapp.com.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("STOCKBRIEF_API_BASE_URL", ""),
        help="API base URL. Both https://... and https://.../v1 are accepted.",
    )
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument(
        "--token-file",
        default="",
        help="Read the short-lived bearer token from a local file instead of an environment variable.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--skip-pages", action="store_true")
    parser.add_argument("--skip-auth-api", action="store_true")
    return parser.parse_args(argv)


def run_smoke(
    *,
    hosted_url: str,
    api_base_url: str,
    token_env: str = DEFAULT_TOKEN_ENV,
    token_file: str = "",
    check_pages: bool = True,
    check_auth_api: bool = True,
    timeout_seconds: float = 10.0,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    checks: dict[str, dict[str, Any]] = {}
    normalized_hosted_url = normalize_base_url(hosted_url)
    normalized_api_base_url = normalize_api_base_url(api_base_url)
    fetcher = fetch or fetch_url

    if check_pages and not normalized_hosted_url:
        blockers.append({"code": "missing_hosted_url"})
    if check_auth_api and not normalized_api_base_url:
        blockers.append({"code": "missing_api_base_url"})

    token, token_blockers = read_auth_token(
        token_env=token_env,
        token_file=token_file,
        check_auth_api=check_auth_api,
    )
    blockers.extend(token_blockers)

    if blockers:
        return {
            "ok": False,
            "hosted_url_configured": bool(normalized_hosted_url),
            "api_base_url_configured": bool(normalized_api_base_url),
            "auth_token_configured": bool(token),
            "checks": checks,
            "blockers": blockers,
        }

    if check_pages:
        for path in DEFAULT_HOSTED_PATHS:
            result = check_page(
                base_url=normalized_hosted_url,
                path=path,
                timeout_seconds=timeout_seconds,
                fetch=fetcher,
            )
            checks[result.name] = result.as_dict()

    if check_auth_api:
        for path in DEFAULT_AUTH_API_PATHS:
            result = check_auth_api_endpoint(
                base_url=normalized_api_base_url,
                path=path,
                token=token,
                timeout_seconds=timeout_seconds,
                fetch=fetcher,
            )
            checks[result.name] = result.as_dict()

    return {
        "ok": bool(checks) and all(check["ok"] for check in checks.values()),
        "hosted_url_configured": bool(normalized_hosted_url),
        "api_base_url_configured": bool(normalized_api_base_url),
        "auth_token_configured": bool(token),
        "checks": checks,
        "blockers": collect_blockers(checks),
    }


def check_page(
    *,
    base_url: str,
    path: str,
    timeout_seconds: float,
    fetch: Fetch,
) -> CheckResult:
    response = fetch(urljoin(base_url, path.lstrip("/")), {}, timeout_seconds)
    ok = response.error_code is None and response.status_code is not None and 200 <= response.status_code < 400
    return CheckResult(
        ok=ok,
        name=f"hosted_page:{path}",
        target=path,
        status_code=response.status_code,
        summary={"reachable": ok},
        error_code=response.error_code,
        error_message=response.error_message,
    )


def check_auth_api_endpoint(
    *,
    base_url: str,
    path: str,
    token: str,
    timeout_seconds: float,
    fetch: Fetch,
) -> CheckResult:
    response = fetch(
        urljoin(base_url, path.removeprefix("/v1/").lstrip("/")),
        {"Authorization": f"Bearer {token}"},
        timeout_seconds,
    )
    body = parse_json_body(response.body)
    summary = summarize_api_response(path, body)
    ok = (
        response.error_code is None
        and response.status_code == 200
        and summary.get("contract_ok") is True
    )
    return CheckResult(
        ok=ok,
        name=f"auth_api:{path}",
        target=path,
        status_code=response.status_code,
        summary=summary,
        error_code=response.error_code or extract_error_code(body),
        error_message=response.error_message,
    )


def fetch_url(url: str, headers: dict[str, str], timeout_seconds: float) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return HttpResponse(
                status_code=response.status,
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        return HttpResponse(status_code=exc.code, body=exc.read())
    except urllib.error.URLError as exc:
        return HttpResponse(
            status_code=None,
            body=b"",
            error_code=type(exc.reason).__name__ if exc.reason else type(exc).__name__,
            error_message=str(exc.reason),
        )
    except TimeoutError as exc:
        return HttpResponse(
            status_code=None,
            body=b"",
            error_code="TimeoutError",
            error_message=str(exc),
        )


def normalize_base_url(value: str) -> str:
    stripped = value.strip().rstrip("/")
    if not stripped:
        return ""
    parsed = urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{stripped}/"


def normalize_api_base_url(value: str) -> str:
    base_url = normalize_base_url(value)
    if not base_url:
        return ""
    return base_url if base_url.rstrip("/").endswith("/v1") else urljoin(base_url, "v1/")


def parse_json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_auth_token(
    *,
    token_env: str,
    token_file: str,
    check_auth_api: bool,
) -> tuple[str, list[dict[str, str]]]:
    if not check_auth_api:
        return "", []

    token_path = token_file.strip()
    if token_path:
        try:
            token = Path(token_path).read_text(encoding="utf-8").strip()
        except OSError:
            return "", [{"code": "missing_auth_token_file"}]
        if not token:
            return "", [{"code": "empty_auth_token_file"}]
        return token, []

    token = os.environ.get(token_env, "").strip()
    if token:
        return token, []
    return "", [{"code": "missing_auth_token", "env": token_env}]


def summarize_api_response(path: str, body: dict[str, Any]) -> dict[str, Any]:
    data = response_payload(body)
    if path == "/v1/me" and isinstance(data, dict):
        authenticated = (
            isinstance(data.get("cognito_sub"), str)
            and bool(data.get("cognito_sub"))
        ) or (isinstance(data.get("sub"), str) and bool(data.get("sub")))
        return {
            "response_shape": "me",
            "contract_ok": authenticated,
            "authenticated": authenticated,
            "email_present": isinstance(data.get("email"), str) and bool(data.get("email")),
            "email_verified": data.get("email_verified") is True,
            "nickname_present": isinstance(data.get("nickname"), str) and bool(data.get("nickname")),
        }
    if path == "/v1/me/preferences" and isinstance(data, dict):
        preferences = data.get("preferences")
        if isinstance(preferences, dict):
            safe_keys = sorted(
                key
                for key in preferences
                if key in {"markets", "notifications", "risk_profile", "sectors"}
            )
            return {
                "response_shape": "preferences",
                "contract_ok": True,
                "preference_keys": safe_keys,
            }
    if path == "/v1/me/watchlist" and isinstance(data, dict):
        item_count = count_from_response(data)
        return {
            "response_shape": "watchlist",
            "contract_ok": item_count is not None,
            "item_count": item_count,
        }
    if path == "/v1/me/chat-sessions" and isinstance(data, dict):
        count = number_or_none(data.get("count"))
        return {
            "response_shape": "chat_sessions",
            "contract_ok": count is not None,
            "count": count,
        }
    return {"response_shape": "unknown", "contract_ok": False}


def collect_blockers(checks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    blockers = []
    for name, check in checks.items():
        if check["ok"]:
            continue
        blockers.append(
            {
                "check": name,
                "status_code": check["status_code"],
                "error_code": check["error_code"] or "check_failed",
            }
        )
    return blockers


def extract_error_code(body: dict[str, Any]) -> str | None:
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return None


def response_payload(body: dict[str, Any]) -> Any:
    data = body.get("data")
    return data if isinstance(data, dict) else body


def number_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def count_from_response(data: dict[str, Any]) -> int | None:
    count = number_or_none(data.get("count"))
    if count is not None:
        return count
    items = data.get("items")
    return len(items) if isinstance(items, list) else None


if __name__ == "__main__":
    sys.exit(main())
