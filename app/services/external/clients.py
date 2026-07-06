from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.orm import CompanyIdentifier
from app.services.external.cache import ExternalApiCacheService
from app.services.external.logger import ExternalApiCallLogger
from app.services.external.transport import urllib_transport
from app.services.external.types import (
    ExternalApiResult,
    ExternalRequest,
    ExternalResponse,
    ExternalTransport,
    RateLimitPolicy,
)


OPENDART_PROVIDER = "OpenDART"
NAVER_PROVIDER = "NAVER_NEWS"
KRX_PROVIDER = "KRX"


class BaseExternalApiClient:
    def __init__(
        self,
        *,
        settings: Settings,
        session: Session,
        transport: ExternalTransport | None = None,
        rate_limit_policy: RateLimitPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.transport = transport or urllib_transport
        self.rate_limit_policy = rate_limit_policy or RateLimitPolicy()
        self.cache = ExternalApiCacheService(session)
        self.logger = ExternalApiCallLogger(session)

    def _from_cache(
        self,
        *,
        provider: str,
        endpoint: str,
        cache_key: str,
    ) -> ExternalApiResult | None:
        cached = self.cache.get(provider=provider, cache_key=cache_key)
        if cached is None:
            return None
        self.logger.log(
            provider=provider,
            endpoint=endpoint,
            method="CACHE",
            request_params={"cache_key": cache_key},
            status_code=200,
            duration_ms=0,
            error_code=None,
        )
        return _result_from_cached(
            provider=provider,
            endpoint=endpoint,
            cache_key=cache_key,
            cached=cached,
        )

    def _fallback_result(
        self,
        *,
        provider: str,
        endpoint: str,
        cache_key: str,
        payload: dict[str, Any],
        missing_data: list[dict[str, Any]],
        request_params: dict[str, Any],
        error_code: str,
    ) -> ExternalApiResult:
        self.cache.set(
            provider=provider,
            cache_key=cache_key,
            response_payload=_cache_payload(
                payload=payload,
                data_status="fallback",
                missing_data=missing_data,
            ),
            status_code=None,
        )
        self.logger.log(
            provider=provider,
            endpoint=endpoint,
            method="FALLBACK",
            request_params=request_params,
            status_code=None,
            duration_ms=0,
            error_code=error_code,
        )
        return ExternalApiResult(
            provider=provider,
            endpoint=endpoint,
            cache_key=cache_key,
            payload=payload,
            data_status="fallback",
            status_code=None,
            missing_data=missing_data,
        )

    def _request_result(
        self,
        *,
        provider: str,
        endpoint: str,
        cache_key: str,
        request: ExternalRequest,
        request_params: dict[str, Any],
        fallback_payload: dict[str, Any],
        fallback_field: str,
        normalize_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> ExternalApiResult:
        started = time.monotonic()
        status_code: int | None = None
        try:
            response = _request_with_backoff(
                transport=self.transport,
                request=request,
                policy=self.rate_limit_policy,
            )
            status_code = response.status_code
            if status_code != 200:
                raise RuntimeError(f"unexpected_status_{status_code}")
            payload = normalize_payload(response.payload) if normalize_payload else response.payload
            self.cache.set(
                provider=provider,
                cache_key=cache_key,
                response_payload=_cache_payload(
                    payload=payload,
                    data_status="available",
                    missing_data=[],
                ),
                status_code=status_code,
            )
            self.logger.log(
                provider=provider,
                endpoint=endpoint,
                method="GET",
                request_params=request_params,
                status_code=status_code,
                duration_ms=_duration_ms(started),
                error_code=None,
            )
            return ExternalApiResult(
                provider=provider,
                endpoint=endpoint,
                cache_key=cache_key,
                payload=payload,
                data_status="available",
                status_code=status_code,
            )
        except Exception as exc:
            error_code = _error_code(exc)
            missing_data = [
                _missing_data(
                    provider=provider,
                    field=fallback_field,
                    reason=error_code,
                )
            ]
            payload = {**fallback_payload, "fallback": True, "missing_data": missing_data}
            self.cache.set(
                provider=provider,
                cache_key=cache_key,
                response_payload=_cache_payload(
                    payload=payload,
                    data_status="fallback",
                    missing_data=missing_data,
                ),
                status_code=status_code,
            )
            self.logger.log(
                provider=provider,
                endpoint=endpoint,
                method="GET",
                request_params=request_params,
                status_code=status_code,
                duration_ms=_duration_ms(started),
                error_code=error_code,
            )
            return ExternalApiResult(
                provider=provider,
                endpoint=endpoint,
                cache_key=cache_key,
                payload=payload,
                data_status="fallback",
                status_code=status_code,
                missing_data=missing_data,
            )


class OpenDartClient(BaseExternalApiClient):
    base_url = "https://opendart.fss.or.kr/api"

    def resolve_corp_code(self, ticker: str) -> str | None:
        identifier = self.session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.ticker == ticker,
                CompanyIdentifier.provider == OPENDART_PROVIDER,
                CompanyIdentifier.identifier_type == "corp_code",
            )
        ).first()
        return identifier.identifier_value if identifier else None

    def list_disclosures(
        self,
        *,
        ticker: str,
        corp_code: str | None = None,
        page_count: int = 10,
        bgn_de: str | None = None,
        end_de: str | None = None,
    ) -> ExternalApiResult:
        resolved_corp_code = corp_code or self.resolve_corp_code(ticker)
        endpoint = "/list.json"
        cache_key = (
            f"disclosures:{ticker}:{resolved_corp_code or 'missing'}:"
            f"{bgn_de or 'default'}:{end_de or 'default'}:{page_count}"
        )

        cached = self._from_cache(
            provider=OPENDART_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
        )
        if cached:
            return cached

        if not self.settings.opendart_api_key:
            return self._fallback(
                endpoint=endpoint,
                cache_key=cache_key,
                ticker=ticker,
                reason="missing_api_key",
                field="OPENDART_API_KEY",
            )

        if not resolved_corp_code:
            return self._fallback(
                endpoint=endpoint,
                cache_key=cache_key,
                ticker=ticker,
                reason="missing_corp_code",
                field="corp_code",
            )

        params = {
            "crtfc_key": self.settings.opendart_api_key,
            "corp_code": resolved_corp_code,
            "page_count": page_count,
        }
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        safe_request_params = {
            "corp_code": resolved_corp_code,
            "page_count": page_count,
        }
        if bgn_de:
            safe_request_params["bgn_de"] = bgn_de
        if end_de:
            safe_request_params["end_de"] = end_de
        result = self._request(
            endpoint=endpoint,
            cache_key=cache_key,
            params=params,
            request_params=safe_request_params,
            fallback_payload={"ticker": ticker, "corp_code": resolved_corp_code, "list": []},
            fallback_field="OpenDART response",
        )
        result.payload.setdefault("ticker", ticker)
        result.payload.setdefault("corp_code", resolved_corp_code)
        return result

    def list_financial_statements(
        self,
        *,
        ticker: str,
        corp_code: str | None = None,
        business_years: list[int],
        report_code: str = "11011",
    ) -> ExternalApiResult:
        resolved_corp_code = corp_code or self.resolve_corp_code(ticker)
        endpoint = "/fnlttSinglAcntAll.json"
        year_key = ",".join(str(year) for year in business_years)
        cache_key = f"financials:{ticker}:{resolved_corp_code or 'missing'}:{year_key}:{report_code}"

        cached = self._from_cache(
            provider=OPENDART_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
        )
        if cached:
            return cached

        if not self.settings.opendart_api_key:
            return self._fallback(
                endpoint=endpoint,
                cache_key=cache_key,
                ticker=ticker,
                reason="missing_api_key",
                field="OPENDART_API_KEY",
            )

        if not resolved_corp_code:
            return self._fallback(
                endpoint=endpoint,
                cache_key=cache_key,
                ticker=ticker,
                reason="missing_corp_code",
                field="corp_code",
            )

        rows: list[dict[str, Any]] = []
        missing_data: list[dict[str, Any]] = []
        status_code: int | None = None
        for business_year in business_years:
            year_rows, year_status, year_missing = self._financial_statement_rows(
                endpoint=endpoint,
                ticker=ticker,
                corp_code=resolved_corp_code,
                business_year=business_year,
                report_code=report_code,
            )
            rows.extend(year_rows)
            status_code = year_status if year_status is not None else status_code
            missing_data.extend(year_missing)

        payload = {
            "ticker": ticker,
            "corp_code": resolved_corp_code,
            "financial_statements": rows,
            "missing_data": missing_data,
        }
        data_status = "available" if rows else "fallback"
        self.cache.set(
            provider=OPENDART_PROVIDER,
            cache_key=cache_key,
            response_payload=_cache_payload(
                payload=payload,
                data_status=data_status,
                missing_data=missing_data,
            ),
            status_code=status_code,
        )
        return ExternalApiResult(
            provider=OPENDART_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            payload=payload,
            data_status=data_status,
            status_code=status_code,
            missing_data=missing_data,
        )

    def _financial_statement_rows(
        self,
        *,
        endpoint: str,
        ticker: str,
        corp_code: str,
        business_year: int,
        report_code: str,
    ) -> tuple[list[dict[str, Any]], int | None, list[dict[str, Any]]]:
        year_missing: list[dict[str, Any]] = []
        status_code: int | None = None
        for fs_div in ("CFS", "OFS"):
            params = {
                "crtfc_key": self.settings.opendart_api_key,
                "corp_code": corp_code,
                "bsns_year": str(business_year),
                "reprt_code": report_code,
                "fs_div": fs_div,
            }
            result = self._request(
                endpoint=endpoint,
                cache_key=(
                    f"financials:{ticker}:{corp_code}:{business_year}:"
                    f"{report_code}:{fs_div}"
                ),
                params=params,
                request_params={key: value for key, value in params.items() if key != "crtfc_key"},
                fallback_payload={
                    "ticker": ticker,
                    "corp_code": corp_code,
                    "bsns_year": str(business_year),
                    "reprt_code": report_code,
                    "fs_div": fs_div,
                    "list": [],
                },
                fallback_field="OpenDART financial statements",
            )
            status_code = result.status_code if result.status_code is not None else status_code
            rows = _iter_dicts(result.payload.get("list"))
            if _opendart_status_ok(result.payload) and rows:
                return [
                    {
                        **row,
                        "ticker": ticker,
                        "corp_code": corp_code,
                        "bsns_year": str(business_year),
                        "reprt_code": report_code,
                        "fs_div": fs_div,
                    }
                    for row in rows
                ], status_code, []
            year_missing.extend(result.missing_data)

        return [], status_code, year_missing or [
            _missing_data(
                provider=OPENDART_PROVIDER,
                field=f"financial_statements:{business_year}",
                reason="no_financial_statement_rows",
            )
        ]

    def _fallback(
        self,
        *,
        endpoint: str,
        cache_key: str,
        ticker: str,
        reason: str,
        field: str,
    ) -> ExternalApiResult:
        missing_data = [_missing_data(provider=OPENDART_PROVIDER, field=field, reason=reason)]
        payload = {
            "fallback": True,
            "ticker": ticker,
            "list": [],
            "missing_data": missing_data,
        }
        return self._fallback_result(
            provider=OPENDART_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            request_params={"ticker": ticker, "reason": reason},
            error_code=reason,
            payload=payload,
            missing_data=missing_data,
        )

    def _request(
        self,
        *,
        endpoint: str,
        cache_key: str,
        params: dict[str, Any],
        request_params: dict[str, Any],
        fallback_payload: dict[str, Any],
        fallback_field: str,
    ) -> ExternalApiResult:
        return self._request_result(
            provider=OPENDART_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            request=ExternalRequest(
                method="GET",
                url=f"{self.base_url}{endpoint}",
                params=params,
                timeout_seconds=self.rate_limit_policy.timeout_seconds,
            ),
            request_params=request_params,
            fallback_payload=fallback_payload,
            fallback_field=fallback_field,
        )


class NaverNewsClient(BaseExternalApiClient):
    base_url = "https://openapi.naver.com/v1/search/news.json"

    def search_news(
        self,
        *,
        ticker: str,
        company_name: str,
        display: int = 10,
    ) -> ExternalApiResult:
        endpoint = "/v1/search/news.json"
        cache_key = f"news:{ticker}:{company_name}:{display}"
        cached = self._from_cache(
            provider=NAVER_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
        )
        if cached is not None:
            return cached

        if not self.settings.naver_client_id or not self.settings.naver_client_secret:
            return self._fallback(
                endpoint=endpoint,
                cache_key=cache_key,
                ticker=ticker,
                company_name=company_name,
                reason="missing_api_key",
            )

        params = {"query": company_name, "display": display, "sort": "date"}
        return self._request_result(
            provider=NAVER_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            request=ExternalRequest(
                method="GET",
                url=self.base_url,
                params=params,
                headers={
                    "X-Naver-Client-Id": self.settings.naver_client_id,
                    "X-Naver-Client-Secret": self.settings.naver_client_secret,
                },
                timeout_seconds=self.rate_limit_policy.timeout_seconds,
            ),
            request_params=params,
            fallback_payload=_fallback_news_payload(
                ticker=ticker,
                company_name=company_name,
                missing_data=[],
            ),
            fallback_field="NAVER news response",
            normalize_payload=lambda payload: {
                **_normalize_naver_payload(payload),
                "ticker": ticker,
            },
        )

    def _fallback(
        self,
        *,
        endpoint: str,
        cache_key: str,
        ticker: str,
        company_name: str,
        reason: str,
    ) -> ExternalApiResult:
        missing_data = [
            _missing_data(
                provider=NAVER_PROVIDER,
                field="NAVER_CLIENT_ID/NAVER_CLIENT_SECRET",
                reason=reason,
            )
        ]
        payload = _fallback_news_payload(
            ticker=ticker,
            company_name=company_name,
            missing_data=missing_data,
        )
        return self._fallback_result(
            provider=NAVER_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            request_params={"ticker": ticker, "company_name": company_name, "reason": reason},
            error_code=reason,
            payload=payload,
            missing_data=missing_data,
        )


class KrxClient(BaseExternalApiClient):
    def daily_trading(
        self,
        *,
        ticker: str,
        base_date: str,
        market: str = "KOSPI",
        bypass_cache: bool = False,
    ) -> ExternalApiResult:
        market_key = _krx_market_key(market)
        endpoint = self._daily_endpoint(market_key)
        cache_key = f"daily_trading:{market_key}:{base_date}"
        fallback_cache_key = f"{cache_key}:{ticker}"
        if not bypass_cache:
            cached = self._from_cache(
                provider=KRX_PROVIDER,
                endpoint=endpoint or f"missing_krx_{market_key.lower()}_daily_url",
                cache_key=cache_key,
            )
            if cached is not None:
                return cached

        if not endpoint:
            return self._fallback(
                endpoint=f"missing_krx_{market_key.lower()}_daily_url",
                cache_key=fallback_cache_key,
                ticker=ticker,
                base_date=base_date,
                market=market_key,
                reason="missing_daily_url",
                field=self._daily_endpoint_field(market_key),
            )
        if not self.settings.krx_api_key:
            return self._fallback(
                endpoint=endpoint,
                cache_key=fallback_cache_key,
                ticker=ticker,
                base_date=base_date,
                market=market_key,
                reason="missing_api_key",
                field="KRX_API_KEY",
            )

        return self._request_result(
            provider=KRX_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            request=ExternalRequest(
                method="GET",
                url=endpoint,
                params={"basDd": base_date},
                headers={self.settings.krx_api_key_header: self.settings.krx_api_key},
                timeout_seconds=max(self.rate_limit_policy.timeout_seconds, 24.0),
            ),
            request_params={"basDd": base_date, "market": market_key},
            fallback_payload={
                "base_date": base_date,
                "market": market_key,
                "OutBlock_1": [],
            },
            fallback_field="KRX daily trading response",
            normalize_payload=lambda payload: {
                **payload,
                "base_date": base_date,
                "market": market_key,
            },
        )

    def _daily_endpoint(self, market_key: str) -> str:
        if market_key == "KOSDAQ":
            return self.settings.krx_kosdaq_daily_url
        return self.settings.krx_daily_url or self.settings.krx_kospi_daily_url

    @staticmethod
    def _daily_endpoint_field(market_key: str) -> str:
        if market_key == "KOSDAQ":
            return "KRX_KOSDAQ_DAILY_URL"
        return "KRX_DAILY_URL/KRX_KOSPI_DAILY_URL"

    def _fallback(
        self,
        *,
        endpoint: str,
        cache_key: str,
        ticker: str,
        base_date: str,
        market: str,
        reason: str,
        field: str,
    ) -> ExternalApiResult:
        missing_data = [_missing_data(provider=KRX_PROVIDER, field=field, reason=reason)]
        payload = {
            "fallback": True,
            "ticker": ticker,
            "base_date": base_date,
            "market": market,
            "OutBlock_1": [],
            "missing_data": missing_data,
        }
        return self._fallback_result(
            provider=KRX_PROVIDER,
            endpoint=endpoint,
            cache_key=cache_key,
            request_params={
                "ticker": ticker,
                "basDd": base_date,
                "market": market,
                "reason": reason,
            },
            error_code=reason,
            payload=payload,
            missing_data=missing_data,
        )


def _krx_market_key(market: str) -> str:
    normalized = market.strip().upper()
    if normalized in {"KOSDAQ", "KQ"}:
        return "KOSDAQ"
    return "KOSPI"


def _request_with_backoff(
    *,
    transport: ExternalTransport,
    request: ExternalRequest,
    policy: RateLimitPolicy,
) -> ExternalResponse:
    attempts = policy.max_retries + 1
    response: ExternalResponse | None = None
    for index in range(attempts):
        response = transport(request)
        if response.status_code not in policy.retry_status_codes:
            return response
        if index < attempts - 1:
            time.sleep(policy.backoff_seconds * (index + 1))
    return response


def _normalize_naver_payload(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items", [])
    normalized_items = [
        {
            "title": str(item.get("title", "")),
            "originallink": str(item.get("originallink", "")),
            "link": str(item.get("link", "")),
            "description": str(item.get("description", "")),
            "pubDate": str(item.get("pubDate", "")),
        }
        for item in _iter_dicts(items)
    ]
    return {
        "lastBuildDate": payload.get("lastBuildDate"),
        "total": payload.get("total"),
        "start": payload.get("start"),
        "display": payload.get("display"),
        "items": normalized_items,
    }


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _opendart_status_ok(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "000").strip()
    return status in {"", "000"}


def _fallback_news_payload(
    *,
    ticker: str,
    company_name: str,
    missing_data: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "fallback": True,
        "ticker": ticker,
        "query": company_name,
        "items": [],
        "missing_data": missing_data,
    }


def _cache_payload(
    *,
    payload: dict[str, Any],
    data_status: str,
    missing_data: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "payload": payload,
        "data_status": data_status,
        "missing_data": missing_data,
    }


def _result_from_cached(
    *,
    provider: str,
    endpoint: str,
    cache_key: str,
    cached: dict[str, Any],
) -> ExternalApiResult:
    data_status = "fallback" if cached.get("data_status") == "fallback" else "available"
    missing_data = cached.get("missing_data", [])
    if not isinstance(missing_data, list):
        missing_data = []
    payload = cached.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    return ExternalApiResult(
        provider=provider,
        endpoint=endpoint,
        cache_key=cache_key,
        payload=payload,
        data_status=data_status,
        status_code=200,
        missing_data=missing_data,
        from_cache=True,
    )


def _missing_data(*, provider: str, field: str, reason: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "field": field,
        "reason": reason,
        "data_status": "fallback",
    }


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _error_code(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("unexpected_status_"):
        return message
    return exc.__class__.__name__
