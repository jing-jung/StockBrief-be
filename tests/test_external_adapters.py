from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.orm import ApiCacheEntry, ExternalApiCallLog
from app.services.external import NaverNewsClient, OpenDartClient
from app.services.external.clients import BaseExternalApiClient
from app.services.external.types import ExternalRequest, ExternalResponse, RateLimitPolicy


def test_external_clients_share_base_template_methods() -> None:
    assert issubclass(OpenDartClient, BaseExternalApiClient)
    assert issubclass(NaverNewsClient, BaseExternalApiClient)


def test_opendart_fallback_without_api_key_does_not_call_external_api(
    seeded_session: Session,
) -> None:
    def transport(_request: ExternalRequest) -> ExternalResponse:
        raise AssertionError("transport should not be called without API key")

    client = OpenDartClient(
        settings=Settings(OPENDART_API_KEY=""),
        session=seeded_session,
        transport=transport,
    )

    result = client.list_disclosures(ticker="005930")

    assert result.data_status == "fallback"
    assert result.payload["fallback"] is True
    assert result.missing_data[0]["field"] == "OPENDART_API_KEY"
    assert result.missing_data[0]["data_status"] == "fallback"

    cache_entry = seeded_session.scalars(
        select(ApiCacheEntry).where(ApiCacheEntry.provider == "OpenDART")
    ).first()
    assert cache_entry is not None
    assert cache_entry.response_payload["data_status"] == "fallback"

    log = seeded_session.scalars(
        select(ExternalApiCallLog)
        .where(ExternalApiCallLog.provider == "OpenDART")
        .order_by(ExternalApiCallLog.called_at.desc())
    ).first()
    assert log is not None
    assert log.method == "FALLBACK"
    assert log.error_code == "missing_api_key"


def test_opendart_success_uses_corp_code_mapping_cache_and_secret_redaction(
    seeded_session: Session,
) -> None:
    calls: list[ExternalRequest] = []

    def transport(request: ExternalRequest) -> ExternalResponse:
        calls.append(request)
        return ExternalResponse(
            status_code=200,
            payload={
                "status": "000",
                "message": "OK",
                "list": [{"corp_code": request.params["corp_code"], "report_nm": "mock"}],
            },
        )

    settings = Settings(OPENDART_API_KEY="opendart-secret")
    client = OpenDartClient(
        settings=settings,
        session=seeded_session,
        transport=transport,
    )

    result = client.list_disclosures(ticker="005930")
    cached_result = OpenDartClient(
        settings=settings,
        session=seeded_session,
        transport=lambda _request: (_ for _ in ()).throw(
            AssertionError("cache should avoid transport")
        ),
    ).list_disclosures(ticker="005930")

    assert result.data_status == "available"
    assert result.payload["list"][0]["corp_code"] == "MOCK00126380"
    assert calls[0].params["corp_code"] == "MOCK00126380"
    assert cached_result.from_cache is True

    logs = seeded_session.scalars(
        select(ExternalApiCallLog).where(ExternalApiCallLog.provider == "OpenDART")
    ).all()
    request_params = [log.request_params for log in logs if log.method == "GET"]
    assert request_params
    assert request_params[-1]["crtfc_key"] == "[REDACTED]"
    assert "opendart-secret" not in str(request_params)


def test_naver_news_fallback_without_credentials_has_news_item_shape(
    seeded_session: Session,
) -> None:
    client = NaverNewsClient(
        settings=Settings(NAVER_CLIENT_ID="", NAVER_CLIENT_SECRET=""),
        session=seeded_session,
        transport=lambda _request: (_ for _ in ()).throw(
            AssertionError("transport should not be called without credentials")
        ),
    )

    result = client.search_news(ticker="005930", company_name="삼성전자")

    assert result.data_status == "fallback"
    item = result.payload["items"][0]
    assert {"title", "originallink", "link", "description", "pubDate"}.issubset(item)
    assert result.missing_data[0]["field"] == "NAVER_CLIENT_ID/NAVER_CLIENT_SECRET"


def test_naver_news_success_normalizes_payload_and_does_not_log_secrets(
    seeded_session: Session,
) -> None:
    captured_headers: list[dict[str, str]] = []

    def transport(request: ExternalRequest) -> ExternalResponse:
        captured_headers.append(dict(request.headers))
        return ExternalResponse(
            status_code=200,
            payload={
                "lastBuildDate": "Tue, 09 Jun 2026 10:00:00 +0900",
                "total": 1,
                "start": 1,
                "display": 1,
                "items": [
                    {
                        "title": "mock title",
                        "originallink": "https://news.example/original",
                        "link": "https://news.example/link",
                        "description": "mock description",
                        "pubDate": "Tue, 09 Jun 2026 09:00:00 +0900",
                        "extra": "ignored",
                    }
                ],
            },
        )

    client = NaverNewsClient(
        settings=Settings(NAVER_CLIENT_ID="naver-id", NAVER_CLIENT_SECRET="naver-secret"),
        session=seeded_session,
        transport=transport,
    )

    result = client.search_news(ticker="005930", company_name="삼성전자", display=1)

    assert result.data_status == "available"
    assert captured_headers[0]["X-Naver-Client-Id"] == "naver-id"
    assert captured_headers[0]["X-Naver-Client-Secret"] == "naver-secret"
    assert result.payload["items"] == [
        {
            "title": "mock title",
            "originallink": "https://news.example/original",
            "link": "https://news.example/link",
            "description": "mock description",
            "pubDate": "Tue, 09 Jun 2026 09:00:00 +0900",
        }
    ]

    logs = seeded_session.scalars(
        select(ExternalApiCallLog).where(ExternalApiCallLog.provider == "NAVER_NEWS")
    ).all()
    assert logs
    assert "naver-secret" not in str([log.request_params for log in logs])
    assert "naver-id" not in str([log.request_params for log in logs])


def test_external_api_failure_returns_fallback_instead_of_raising(
    seeded_session: Session,
) -> None:
    def transport(_request: ExternalRequest) -> ExternalResponse:
        return ExternalResponse(status_code=503, payload={"error": "temporary"})

    client = NaverNewsClient(
        settings=Settings(NAVER_CLIENT_ID="naver-id", NAVER_CLIENT_SECRET="naver-secret"),
        session=seeded_session,
        transport=transport,
        rate_limit_policy=RateLimitPolicy(max_retries=0, backoff_seconds=0),
    )

    result = client.search_news(ticker="005930", company_name="삼성전자")

    assert result.data_status == "fallback"
    assert result.status_code == 503
    assert result.missing_data[0]["reason"] == "unexpected_status_503"


def test_rate_limit_policy_retries_retryable_status(
    seeded_session: Session,
) -> None:
    status_codes = [429, 200]
    calls: list[dict[str, Any]] = []

    def transport(request: ExternalRequest) -> ExternalResponse:
        calls.append(request.params)
        status_code = status_codes.pop(0)
        return ExternalResponse(
            status_code=status_code,
            payload={"items": []},
        )

    client = NaverNewsClient(
        settings=Settings(NAVER_CLIENT_ID="naver-id", NAVER_CLIENT_SECRET="naver-secret"),
        session=seeded_session,
        transport=transport,
        rate_limit_policy=RateLimitPolicy(max_retries=1, backoff_seconds=0),
    )

    result = client.search_news(ticker="005930", company_name="삼성전자")

    assert result.data_status == "available"
    assert len(calls) == 2
