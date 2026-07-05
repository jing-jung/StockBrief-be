#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_API_BASE_URL = "http://localhost:8000/v1"
DEFAULT_API_ENV = "STOCKBRIEF_API_BASE_URL"
INTERNAL_EVIDENCE_SOURCE_TYPES = {"SCORE"}
EXPECTED_SCORE_COMPONENT_WEIGHTS = {
    "financial_stability": 20,
    "profitability": 15,
    "growth": 15,
    "valuation": 10,
    "news_attention": 10,
    "disclosure_event": 10,
    "liquidity": 10,
    "momentum_volatility": 10,
}

Fetch = Callable[[str, float], "HttpResponse"]


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
    blockers: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "target": self.target,
            "status_code": self.status_code,
            "summary": self.summary,
            "blockers": self.blockers,
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_smoke(
        api_base_url=args.api_base_url,
        ticker=args.ticker,
        expected_tickers=args.expected_ticker,
        limit=args.limit,
        max_detail_tickers=args.max_detail_tickers,
        min_evidence_count=args.min_evidence_count,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a redacted smoke check for recommendation candidate quality."
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get(DEFAULT_API_ENV, DEFAULT_API_BASE_URL),
        help="API base URL. Both https://... and https://.../v1 are accepted.",
    )
    parser.add_argument("--ticker", default="")
    parser.add_argument(
        "--expected-ticker",
        action="append",
        default=[],
        help="Ticker that must appear in the candidate list. Repeat for multiple tickers.",
    )
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--max-detail-tickers", type=int, default=3)
    parser.add_argument("--min-evidence-count", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser.parse_args(argv)


def run_smoke(
    *,
    api_base_url: str,
    ticker: str = "",
    expected_tickers: list[str] | tuple[str, ...] | None = None,
    limit: int = 3,
    max_detail_tickers: int = 3,
    min_evidence_count: int = 2,
    timeout_seconds: float = 10.0,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    normalized_base_url = normalize_api_base_url(api_base_url)
    if not normalized_base_url:
        return {
            "ok": False,
            "api_base_url_configured": False,
            "selected_ticker": "",
            "selected_tickers": [],
            "checks": {},
            "blockers": [{"code": "missing_api_base_url", "env": DEFAULT_API_ENV}],
        }

    fetcher = fetch or fetch_url
    checks: dict[str, dict[str, Any]] = {}
    required_tickers = normalize_tickers(expected_tickers or [])

    list_result = check_candidate_list(
        base_url=normalized_base_url,
        limit=limit,
        min_evidence_count=min_evidence_count,
        expected_tickers=required_tickers,
        timeout_seconds=timeout_seconds,
        fetch=fetcher,
    )
    checks[list_result.name] = list_result.as_dict()

    selected_tickers = select_detail_tickers(
        explicit_ticker=ticker,
        listed_tickers=list_result.summary.get("tickers", []),
        expected_tickers=required_tickers,
        max_detail_tickers=max_detail_tickers,
    )
    if selected_tickers:
        use_ticker_suffix = len(selected_tickers) > 1
        for selected_ticker in selected_tickers:
            detail_result = check_candidate_detail(
                base_url=normalized_base_url,
                ticker=selected_ticker,
                min_evidence_count=min_evidence_count,
                timeout_seconds=timeout_seconds,
                fetch=fetcher,
            )
            checks[check_name(detail_result.name, selected_ticker, use_ticker_suffix)] = (
                detail_result.as_dict()
            )

            evidence_result = check_stock_evidence(
                base_url=normalized_base_url,
                ticker=selected_ticker,
                min_evidence_count=min_evidence_count,
                timeout_seconds=timeout_seconds,
                fetch=fetcher,
            )
            checks[check_name(evidence_result.name, selected_ticker, use_ticker_suffix)] = (
                evidence_result.as_dict()
            )
    else:
        checks["candidate_detail"] = CheckResult(
            ok=False,
            name="candidate_detail",
            target="/v1/recommendations/candidates/{ticker}",
            status_code=None,
            summary={"ticker_selected": False},
            blockers=[{"code": "missing_candidate_ticker"}],
        ).as_dict()

    blockers = collect_blockers(checks)
    return {
        "ok": bool(checks) and not blockers,
        "api_base_url_configured": True,
        "selected_ticker": selected_tickers[0] if selected_tickers else "",
        "selected_tickers": selected_tickers,
        "checks": checks,
        "blockers": blockers,
    }


def check_candidate_list(
    *,
    base_url: str,
    limit: int,
    min_evidence_count: int,
    expected_tickers: list[str],
    timeout_seconds: float,
    fetch: Fetch,
) -> CheckResult:
    path = f"/recommendations/candidates?limit={limit}"
    response, payload = get_json(base_url, path, timeout_seconds, fetch)
    if response.error_code or response.status_code != 200 or not isinstance(payload, dict):
        return failed_http_check("candidate_list", path, response)

    data = response_payload(payload)
    items = data.get("items", []) if isinstance(data, dict) else []
    blockers: list[dict[str, Any]] = []
    if not items:
        blockers.append({"code": "candidate_list_empty"})

    weak_items = []
    for item in items if isinstance(items, list) else []:
        evidence_summary = item.get("evidence_summary", {}) if isinstance(item, dict) else {}
        evidence_count = int_or_zero(item.get("evidence_count")) or evidence_total(evidence_summary)
        if evidence_count < min_evidence_count:
            weak_items.append(
                {
                    "ticker": item.get("ticker"),
                    "evidence_count": evidence_count,
                    "min_evidence_count": min_evidence_count,
                }
            )
        data_freshness = item.get("data_freshness", {}) if isinstance(item, dict) else {}
        latest_at = evidence_summary.get("latest_at") or (
            data_freshness.get("live_evidence_latest_at")
            if isinstance(data_freshness, dict)
            else None
        )
        if not latest_at:
            blockers.append({"code": "missing_candidate_latest_at", "ticker": item.get("ticker")})

    if weak_items:
        blockers.append({"code": "candidate_evidence_below_minimum", "items": weak_items})

    item_tickers = candidate_tickers(items)
    missing_expected_tickers = [
        ticker for ticker in expected_tickers if ticker not in item_tickers
    ]
    if missing_expected_tickers:
        blockers.append(
            {
                "code": "expected_candidate_ticker_missing",
                "tickers": missing_expected_tickers,
            }
        )

    first_item = items[0] if items else {}
    first_freshness = (
        first_item.get("data_freshness", {}) if isinstance(first_item, dict) else {}
    )
    return CheckResult(
        ok=not blockers,
        name="candidate_list",
        target=path,
        status_code=response.status_code,
        summary={
            "count": len(items) if isinstance(items, list) else 0,
            "first_ticker": first_item.get("ticker") if isinstance(first_item, dict) else "",
            "tickers": item_tickers,
            "expected_tickers": expected_tickers,
            "missing_expected_tickers": missing_expected_tickers,
            "as_of": data.get("as_of")
            or (
                first_freshness.get("as_of")
                if isinstance(first_freshness, dict)
                else None
            ),
        },
        blockers=blockers,
    )


def check_candidate_detail(
    *,
    base_url: str,
    ticker: str,
    min_evidence_count: int,
    timeout_seconds: float,
    fetch: Fetch,
) -> CheckResult:
    path = f"/recommendations/candidates/{urllib.parse.quote(ticker)}"
    response, payload = get_json(base_url, path, timeout_seconds, fetch)
    if response.error_code or response.status_code != 200 or not isinstance(payload, dict):
        return failed_http_check("candidate_detail", path, response)

    blockers: list[dict[str, Any]] = []
    evidence_count = int_or_zero(payload.get("evidence_count"))
    risk_tags = payload.get("risk_tags")
    missing_data = payload.get("missing_data")
    data_freshness = payload.get("data_freshness", {})
    reasons = payload.get("recommendation_reasons", [])
    components = payload.get("score_components", [])
    component_blockers, component_summary = validate_score_components(components)
    blockers.extend(component_blockers)

    if evidence_count < min_evidence_count:
        blockers.append(
            {
                "code": "detail_evidence_below_minimum",
                "evidence_count": evidence_count,
                "min_evidence_count": min_evidence_count,
            }
        )
    if "risk_tags" not in payload:
        blockers.append({"code": "missing_risk_tags"})
    elif not isinstance(risk_tags, list):
        blockers.append({"code": "risk_tags_not_array"})
    if not isinstance(missing_data, list):
        blockers.append({"code": "missing_data_not_array"})
    if not isinstance(data_freshness, dict) or not data_freshness.get("as_of"):
        blockers.append({"code": "missing_data_freshness_as_of"})
    if not isinstance(reasons, list) or not reasons:
        blockers.append({"code": "missing_recommendation_reasons"})

    return CheckResult(
        ok=not blockers,
        name="candidate_detail",
        target=path,
        status_code=response.status_code,
        summary={
            "ticker": payload.get("ticker"),
            "evidence_level": payload.get("evidence_level"),
            "evidence_count": evidence_count,
            "risk_tag_count": len(risk_tags) if isinstance(risk_tags, list) else 0,
            "missing_data_count": len(missing_data) if isinstance(missing_data, list) else None,
            "as_of": data_freshness.get("as_of") if isinstance(data_freshness, dict) else None,
            "reason_count": len(reasons) if isinstance(reasons, list) else 0,
            **component_summary,
        },
        blockers=blockers,
    )


def check_stock_evidence(
    *,
    base_url: str,
    ticker: str,
    min_evidence_count: int,
    timeout_seconds: float,
    fetch: Fetch,
) -> CheckResult:
    path = f"/stocks/{urllib.parse.quote(ticker)}/evidence"
    response, payload = get_json(base_url, path, timeout_seconds, fetch)
    if response.error_code or response.status_code != 200 or not isinstance(payload, dict):
        return failed_http_check("stock_evidence", path, response)

    data = payload.get("data", {})
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    items = raw_items if isinstance(raw_items, list) else []
    blockers: list[dict[str, Any]] = []
    if len(items) < min_evidence_count:
        blockers.append(
            {
                "code": "evidence_items_below_minimum",
                "evidence_count": len(items),
                "min_evidence_count": min_evidence_count,
            }
        )

    source_types: set[str] = set()
    items_with_source_type = 0
    items_with_source_name = 0
    items_with_url = 0
    items_with_published_at = 0
    provider_evidence_count = 0
    provider_items_with_url = 0
    provider_items_with_published_at = 0
    internal_evidence_count = 0
    internal_items_with_source_identifier = 0
    internal_items_with_as_of_date = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            blockers.append({"code": "evidence_item_not_object", "item_index": index})
            continue

        source_type = str(item.get("source_type") or "").upper()
        metadata = item.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if source_type:
            source_types.add(source_type)
            items_with_source_type += 1
        if item.get("source_name"):
            items_with_source_name += 1
        if item.get("url"):
            items_with_url += 1
        if item.get("published_at"):
            items_with_published_at += 1

        missing_fields = [
            field for field in ("source_type", "source_name") if not item.get(field)
        ]
        if source_type in INTERNAL_EVIDENCE_SOURCE_TYPES:
            internal_evidence_count += 1
            if metadata.get("source_identifier"):
                internal_items_with_source_identifier += 1
            else:
                missing_fields.append("metadata.source_identifier")
            if metadata.get("as_of_date"):
                internal_items_with_as_of_date += 1
            else:
                missing_fields.append("metadata.as_of_date")
        else:
            provider_evidence_count += 1
            if item.get("url"):
                provider_items_with_url += 1
            else:
                missing_fields.append("url")
            if item.get("published_at"):
                provider_items_with_published_at += 1
            else:
                missing_fields.append("published_at")

        if missing_fields:
            blockers.append(
                {
                    "code": "evidence_item_missing_source_metadata",
                    "item_index": index,
                    "evidence_id": item.get("id"),
                    "missing_fields": missing_fields,
                }
            )

    return CheckResult(
        ok=not blockers,
        name="stock_evidence",
        target=path,
        status_code=response.status_code,
        summary={
            "ticker": data.get("ticker") if isinstance(data, dict) else ticker,
            "evidence_count": len(items),
            "source_types": sorted(source_types),
            "items_with_source_type": items_with_source_type,
            "items_with_source_name": items_with_source_name,
            "items_with_url": items_with_url,
            "items_with_published_at": items_with_published_at,
            "provider_evidence_count": provider_evidence_count,
            "provider_items_with_url": provider_items_with_url,
            "provider_items_with_published_at": provider_items_with_published_at,
            "internal_evidence_count": internal_evidence_count,
            "internal_items_with_source_identifier": (
                internal_items_with_source_identifier
            ),
            "internal_items_with_as_of_date": internal_items_with_as_of_date,
        },
        blockers=blockers,
    )


def get_json(
    base_url: str,
    path: str,
    timeout_seconds: float,
    fetch: Fetch,
) -> tuple[HttpResponse, Any]:
    response = fetch(f"{base_url}{path}", timeout_seconds)
    if response.error_code or response.status_code != 200:
        return response, None
    try:
        return response, json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (
            HttpResponse(
                status_code=response.status_code,
                body=b"",
                error_code="invalid_json",
                error_message="Response body was not valid JSON.",
            ),
            None,
        )


def fetch_url(url: str, timeout_seconds: float) -> HttpResponse:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return HttpResponse(status_code=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(
            status_code=exc.code,
            body=b"",
            error_code="HTTPError",
            error_message=f"HTTP {exc.code}",
        )
    except urllib.error.URLError as exc:
        return HttpResponse(
            status_code=None,
            body=b"",
            error_code="URLError",
            error_message=str(exc.reason),
        )


def failed_http_check(name: str, target: str, response: HttpResponse) -> CheckResult:
    error_code = response.error_code or f"http_{response.status_code}"
    return CheckResult(
        ok=False,
        name=name,
        target=target,
        status_code=response.status_code,
        summary={"reachable": False},
        blockers=[{"code": error_code, "status_code": response.status_code}],
    )


def collect_blockers(checks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for check_name, check in checks.items():
        for blocker in check.get("blockers", []):
            blockers.append({"check": check_name, **blocker})
    return blockers


def validate_score_components(components: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(components, list):
        return [{"code": "score_components_not_array"}], {
            "component_count": None,
            "component_weight_sum": None,
        }

    component_names: list[str] = []
    weight_sum = 0
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            blockers.append({"code": "score_component_not_object", "component_index": index})
            continue

        name = str(component.get("name") or "").strip()
        component_names.append(name)
        weight = int_or_zero(component.get("weight"))
        weight_sum += weight

        expected_weight = EXPECTED_SCORE_COMPONENT_WEIGHTS.get(name)
        if expected_weight is None:
            blockers.append(
                {
                    "code": "unexpected_score_component",
                    "component_index": index,
                    "component": name,
                }
            )
        elif weight != expected_weight:
            blockers.append(
                {
                    "code": "score_component_weight_mismatch",
                    "component": name,
                    "weight": weight,
                    "expected_weight": expected_weight,
                }
            )

        if not numeric_or_none(component.get("raw_score")):
            blockers.append({"code": "score_component_raw_score_invalid", "component": name})
        if not numeric_value(component.get("weighted_score")):
            blockers.append({"code": "score_component_weighted_score_invalid", "component": name})
        if not isinstance(component.get("reason"), str) or not component["reason"].strip():
            blockers.append({"code": "score_component_reason_missing", "component": name})
        if not isinstance(component.get("input_refs"), list):
            blockers.append({"code": "score_component_input_refs_not_array", "component": name})
        if not isinstance(component.get("evidence_ids"), list):
            blockers.append({"code": "score_component_evidence_ids_not_array", "component": name})

    missing_components = sorted(set(EXPECTED_SCORE_COMPONENT_WEIGHTS) - set(component_names))
    duplicate_components = sorted(
        {name for name in component_names if name and component_names.count(name) > 1}
    )
    if len(components) != len(EXPECTED_SCORE_COMPONENT_WEIGHTS):
        blockers.append(
            {
                "code": "score_component_count_mismatch",
                "component_count": len(components),
                "expected_count": len(EXPECTED_SCORE_COMPONENT_WEIGHTS),
            }
        )
    if missing_components:
        blockers.append(
            {"code": "score_components_missing", "components": missing_components}
        )
    if duplicate_components:
        blockers.append(
            {"code": "score_components_duplicate", "components": duplicate_components}
        )
    if weight_sum != 100:
        blockers.append(
            {"code": "score_component_weight_sum_mismatch", "weight_sum": weight_sum}
        )

    return blockers, {
        "component_count": len(components),
        "component_weight_sum": weight_sum,
        "component_names": sorted(name for name in component_names if name),
    }


def candidate_tickers(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []

    tickers: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip()
        if ticker:
            tickers.append(ticker)
    return tickers


def normalize_tickers(raw_tickers: list[str] | tuple[str, ...]) -> list[str]:
    tickers: list[str] = []
    for raw_ticker in raw_tickers:
        ticker = str(raw_ticker or "").strip()
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def select_detail_tickers(
    *,
    explicit_ticker: str,
    listed_tickers: Any,
    expected_tickers: list[str],
    max_detail_tickers: int,
) -> list[str]:
    ticker = explicit_ticker.strip()
    if ticker:
        return [ticker]
    if max_detail_tickers <= 0 or not isinstance(listed_tickers, list):
        return []

    selected: list[str] = []
    listed_lookup = {str(listed_ticker or "").strip() for listed_ticker in listed_tickers}
    for expected_ticker in expected_tickers:
        ticker = str(expected_ticker or "").strip()
        if ticker and ticker in listed_lookup and ticker not in selected:
            selected.append(ticker)

    limit = max(max_detail_tickers, len(selected))
    for listed_ticker in listed_tickers:
        if len(selected) >= limit:
            break
        ticker = str(listed_ticker or "").strip()
        if ticker and ticker not in selected:
            selected.append(ticker)
    return selected


def check_name(base_name: str, ticker: str, use_ticker_suffix: bool) -> str:
    return f"{base_name}:{ticker}" if use_ticker_suffix else base_name


def normalize_api_base_url(api_base_url: str) -> str:
    normalized = api_base_url.strip().rstrip("/")
    if not normalized:
        return ""
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


def evidence_total(evidence_summary: Any) -> int:
    if not isinstance(evidence_summary, dict):
        return 0
    return sum(
        int_or_zero(evidence_summary.get(key))
        for key in ("news_count", "disclosure_count", "score_count", "chunk_count")
    )


def int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def numeric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_or_none(value: Any) -> bool:
    return value is None or numeric_value(value)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
