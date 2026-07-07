"""Ingestion readiness, raw archive, and provider egress checks."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET
import zipfile

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.orm import CompanyIdentifier, Disclosure, EvidenceChunk, SourceDocument
from app.seed.stock_universe import STOCK_UNIVERSE
from app.services.external.clients import KRX_PROVIDER, NAVER_PROVIDER, OPENDART_PROVIDER
from app.services.external.transport import urllib_transport
from app.services.external.types import ExternalRequest, ExternalTransport
from app.services.ingestion.archiver import PayloadArchiver, S3PayloadArchiver
from app.services.ingestion.event_helpers import (
    _event_providers,
    _event_tickers,
    _first_secret_value,
)
from app.services.ingestion.request import SUPPORTED_PROVIDERS
from app.services.ingestion.status import _stale_run_max_age_minutes, _status_limit


PROVIDER_EGRESS_ENDPOINTS = {
    OPENDART_PROVIDER: "https://opendart.fss.or.kr/api/list.json",
    NAVER_PROVIDER: "https://openapi.naver.com/v1/search/news.json",
}

PROVIDER_EGRESS_TIMEOUT_SECONDS = 3.0

RAW_ARCHIVE_PROBE_PROVIDER = "STOCKBRIEF_PROBE"

RAW_ARCHIVE_PROBE_TICKER = "healthcheck"

def check_opendart_corp_code_alignment(
    event: dict[str, object] | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    request = event or {}
    tickers = _event_tickers(request) or [item.ticker for item in STOCK_UNIVERSE]
    base_settings = settings or get_settings()
    hydrated_settings = hydrate_external_api_settings(base_settings)
    if not hydrated_settings.opendart_api_key:
        return {
            "ok": False,
            "error": "missing_provider_credential",
            "field": "OPENDART_API_KEY",
        }

    actual_codes = _opendart_corp_codes_by_stock_code(hydrated_settings.opendart_api_key)
    seed_by_ticker = {item.ticker: item for item in STOCK_UNIVERSE}
    with get_session_factory()() as session:
        identifier_rows = session.scalars(
            select(CompanyIdentifier).where(
                CompanyIdentifier.ticker.in_(tickers),
                CompanyIdentifier.provider == OPENDART_PROVIDER,
                CompanyIdentifier.identifier_type == "corp_code",
            )
        ).all()
    db_codes = {row.ticker: row.identifier_value for row in identifier_rows}

    rows = []
    for ticker in tickers:
        seed_item = seed_by_ticker.get(ticker)
        actual = actual_codes.get(ticker)
        seed_corp_code = seed_item.corp_code if seed_item else None
        db_corp_code = db_codes.get(ticker)
        actual_corp_code = actual["corp_code"] if actual else None
        rows.append(
            {
                "ticker": ticker,
                "seed_name": seed_item.company_name if seed_item else None,
                "seed_corp_code": seed_corp_code,
                "db_corp_code": db_corp_code,
                "actual_name": actual["corp_name"] if actual else None,
                "actual_corp_code": actual_corp_code,
                "seed_matches": seed_corp_code == actual_corp_code,
                "db_matches": db_corp_code == actual_corp_code,
            }
        )

    mismatches = [
        row
        for row in rows
        if not row["seed_matches"] or not row["db_matches"]
    ]
    return {
        "ok": not mismatches,
        "operation": "check_opendart_corp_code_alignment",
        "rows": rows,
        "mismatches": mismatches,
    }

def reconcile_opendart_evidence_tickers(event: dict[str, object] | None = None) -> dict[str, Any]:
    request = event or {}
    tickers = _event_tickers(request)
    dry_run = bool(request.get("dry_run", True))
    with get_session_factory()() as session:
        statement = (
            select(EvidenceChunk, Disclosure)
            .join(SourceDocument, EvidenceChunk.source_document_id == SourceDocument.id)
            .join(
                Disclosure,
                (Disclosure.provider == OPENDART_PROVIDER)
                & (Disclosure.receipt_no == SourceDocument.external_id),
            )
            .where(
                EvidenceChunk.evidence_type == "disclosure",
                SourceDocument.source_name == OPENDART_PROVIDER,
            )
        )
        if tickers:
            statement = statement.where(EvidenceChunk.ticker.in_(tickers))
        pairs = session.execute(statement).all()
        stale_chunks = [
            chunk
            for chunk, disclosure in pairs
            if chunk.ticker != disclosure.ticker
        ]
        removed = [
            {
                "evidence_id": chunk.evidence_id,
                "ticker": chunk.ticker,
                "source_document_id": str(chunk.source_document_id),
            }
            for chunk in stale_chunks
        ]
        if not dry_run:
            for chunk in stale_chunks:
                session.delete(chunk)
            session.commit()
    return {
        "ok": True,
        "operation": "reconcile_opendart_evidence_tickers",
        "dry_run": dry_run,
        "stale_count": len(stale_chunks),
        "removed": removed,
    }

def check_ingestion_scheduler_enable_gate(event: dict[str, object] | None = None) -> dict[str, Any]:
    request = event or {}
    providers = _event_providers(request) or list(SUPPORTED_PROVIDERS)
    tickers = _event_tickers(request) or ["005930"]
    status_limit = _status_limit(request.get("limit"))
    stale_max_age_minutes = _stale_run_max_age_minutes(request.get("max_age_minutes"))

    # Lazy lookups keep the check functions patchable on the package namespace
    # (tests monkeypatch app.services.ingestion.check_ingestion_readiness etc).
    from app.services import ingestion as _ingestion_pkg

    readiness = _ingestion_pkg.check_ingestion_readiness(providers=providers)
    raw_archive = _ingestion_pkg.check_raw_archive_write()
    provider_egress = _ingestion_pkg.check_provider_egress({"providers": providers})
    status = _ingestion_pkg.get_ingestion_status(
        {
            "tickers": tickers,
            "providers": providers,
            "limit": status_limit,
        }
    )
    stale_runs = _ingestion_pkg.reconcile_stale_ingestion_runs(
        {
            "tickers": tickers,
            "providers": providers,
            "max_age_minutes": stale_max_age_minutes,
            "dry_run": True,
        }
    )

    checks = {
        "readiness": readiness,
        "raw_archive": raw_archive,
        "provider_egress": provider_egress,
        "status": status,
        "stale_runs": stale_runs,
    }
    blockers = _scheduler_enable_gate_blockers(
        checks,
        providers=providers,
        tickers=tickers,
    )

    return {
        "ok": not blockers,
        "scheduler_enable_ready": not blockers,
        "providers": providers,
        "tickers": tickers,
        "checks": checks,
        "blockers": blockers,
    }

def check_ingestion_readiness(
    settings: Settings | None = None,
    *,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    base_settings = settings or get_settings()
    selected_providers, provider_selection_issues = _provider_egress_selection(
        {} if providers is None else {"providers": providers}
    )
    issues: list[dict[str, str]] = list(provider_selection_issues)
    secret_load_error: dict[str, str] | None = None
    hydrated_settings = base_settings

    if base_settings.external_api_secret_arn:
        try:
            hydrated_settings = hydrate_external_api_settings(base_settings)
        except Exception as exc:
            secret_load_error = {
                "code": exc.__class__.__name__,
                "message": "External API secret could not be loaded.",
            }
            issues.append(
                {
                    "code": "external_api_secret_load_failed",
                    "field": "EXTERNAL_API_SECRET_ARN",
                }
            )
    else:
        issues.append(
            {
                "code": "missing_external_api_secret_arn",
                "field": "EXTERNAL_API_SECRET_ARN",
            }
        )

    if not hydrated_settings.ingestion_raw_bucket:
        issues.append(
            {
                "code": "missing_ingestion_raw_bucket",
                "field": "INGESTION_RAW_BUCKET",
            }
        )
    provider_checks = {
        OPENDART_PROVIDER: {
            "api_key_configured": bool(hydrated_settings.opendart_api_key),
        },
        NAVER_PROVIDER: {
            "client_id_configured": bool(hydrated_settings.naver_client_id),
            "client_secret_configured": bool(hydrated_settings.naver_client_secret),
        },
        KRX_PROVIDER: {
            "api_key_configured": bool(hydrated_settings.krx_api_key),
            "kospi_daily_url_configured": bool(
                hydrated_settings.krx_daily_url or hydrated_settings.krx_kospi_daily_url
            ),
            "kosdaq_daily_url_configured": bool(hydrated_settings.krx_kosdaq_daily_url),
        },
    }

    if OPENDART_PROVIDER in selected_providers and not hydrated_settings.opendart_api_key:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "OPENDART_API_KEY",
            }
        )
    if NAVER_PROVIDER in selected_providers and not hydrated_settings.naver_client_id:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "NAVER_CLIENT_ID",
            }
        )
    if NAVER_PROVIDER in selected_providers and not hydrated_settings.naver_client_secret:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "NAVER_CLIENT_SECRET",
            }
        )
    if KRX_PROVIDER in selected_providers and not hydrated_settings.krx_api_key:
        issues.append(
            {
                "code": "missing_provider_credential",
                "field": "KRX_API_KEY",
            }
        )
    if KRX_PROVIDER in selected_providers and not _krx_daily_endpoints_configured(hydrated_settings):
        issues.append(
            {
                "code": "missing_provider_endpoint",
                "field": "KRX_KOSPI_DAILY_URL/KRX_KOSDAQ_DAILY_URL",
            }
        )

    return {
        "ok": not issues,
        "checks": {
            "raw_archive": {
                "configured": bool(hydrated_settings.ingestion_raw_bucket),
            },
            "external_api_secret": {
                "configured": bool(base_settings.external_api_secret_arn),
                "loaded": bool(base_settings.external_api_secret_arn) and secret_load_error is None,
                "error": secret_load_error,
            },
            "providers": {
                provider: provider_checks[provider]
                for provider in selected_providers
                if provider in provider_checks
            },
            "network": {
                "outbound_internet_egress_verified": False,
                "note": "This check does not call external provider APIs.",
            },
        },
        "issues": issues,
    }

def check_raw_archive_write(
    settings: Settings | None = None,
    *,
    archiver: PayloadArchiver | None = None,
) -> dict[str, Any]:
    base_settings = settings or get_settings()
    if not base_settings.ingestion_raw_bucket:
        return {
            "ok": False,
            "checks": {"raw_archive": {"configured": False, "write_verified": False}},
            "issues": [{"code": "missing_ingestion_raw_bucket", "field": "INGESTION_RAW_BUCKET"}],
        }

    probe_created_at = datetime.now(timezone.utc)
    probe_run_id = f"raw-archive-probe-{probe_created_at.strftime('%Y%m%dT%H%M%SZ')}"
    probe_payload = {
        "probe": "stockbrief-ingestion-raw-archive",
        "created_at": probe_created_at.isoformat(),
    }
    archive_writer = archiver or S3PayloadArchiver(bucket=base_settings.ingestion_raw_bucket)

    try:
        raw_archive_uri = archive_writer.archive(
            run_id=probe_run_id,
            provider=RAW_ARCHIVE_PROBE_PROVIDER,
            ticker=RAW_ARCHIVE_PROBE_TICKER,
            payload=probe_payload,
        )
        if raw_archive_uri is None:
            raise RuntimeError("raw archive probe did not return a URI")
    except Exception as exc:
        return {
            "ok": False,
            "checks": {
                "raw_archive": {
                    "configured": True,
                    "bucket": base_settings.ingestion_raw_bucket,
                    "write_verified": False,
                    "error_code": exc.__class__.__name__,
                }
            },
            "issues": [{"code": "raw_archive_write_failed", "field": "INGESTION_RAW_BUCKET"}],
        }

    return {
        "ok": True,
        "checks": {
            "raw_archive": {
                "configured": True,
                "bucket": base_settings.ingestion_raw_bucket,
                "write_verified": True,
                "raw_archive_uri": raw_archive_uri,
            }
        },
        "issues": [],
    }

def check_provider_egress(
    event: dict[str, object] | None = None,
    *,
    transport: ExternalTransport | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    selected_providers, provider_issues = _provider_egress_selection(event or {})
    checks: dict[str, dict[str, Any]] = {}
    issues = list(provider_issues)
    transport_fn = transport or urllib_transport
    base_settings = settings or get_settings()

    for provider in selected_providers:
        targets = _provider_egress_targets(provider, base_settings)
        target_checks: dict[str, dict[str, Any]] = {}
        for target in targets:
            label = target["label"]
            endpoint = target["endpoint"]
            if not endpoint:
                target_checks[label] = {
                    "reachable": False,
                    "endpoint": None,
                    "status_code": None,
                    "error_code": "missing_provider_endpoint",
                    "note": "Provider endpoint is not configured.",
                }
                issues.append(
                    {
                        "code": "missing_provider_endpoint",
                        "provider": provider,
                        "field": target["field"],
                    }
                )
                continue
            check = _check_provider_endpoint_egress(
                provider=provider,
                endpoint=endpoint,
                transport=transport_fn,
            )
            target_checks[label] = check
            if not check["reachable"]:
                issue = {
                    "code": "provider_egress_unreachable",
                    "provider": provider,
                    "endpoint": endpoint,
                }
                if provider == KRX_PROVIDER:
                    issue["market"] = label
                issues.append(issue)
        if provider == KRX_PROVIDER:
            checks[provider] = {
                "reachable": all(check["reachable"] for check in target_checks.values()),
                "markets": target_checks,
            }
        else:
            checks[provider] = next(iter(target_checks.values()))

    return {
        "ok": not issues,
        "checks": {
            "providers": checks,
        },
        "issues": issues,
    }

def _provider_egress_selection(event: dict[str, object]) -> tuple[list[str], list[dict[str, str]]]:
    raw_providers = event.get("providers") or event.get("provider")
    if raw_providers is None:
        return list(SUPPORTED_PROVIDERS), []
    if isinstance(raw_providers, str):
        requested = [raw_providers]
    elif isinstance(raw_providers, list):
        requested = [str(provider) for provider in raw_providers]
    else:
        return [], [{"code": "invalid_provider_selection", "field": "providers"}]

    selected: list[str] = []
    issues: list[dict[str, str]] = []
    for provider in requested:
        if provider not in SUPPORTED_PROVIDERS:
            issues.append(
                {
                    "code": "unsupported_provider",
                    "provider": provider,
                }
            )
            continue
        if provider not in selected:
            selected.append(provider)
    return selected, issues

def _provider_egress_targets(provider: str, settings: Settings) -> list[dict[str, str]]:
    if provider == KRX_PROVIDER:
        return [
            {
                "label": "KOSPI",
                "endpoint": settings.krx_daily_url or settings.krx_kospi_daily_url,
                "field": "KRX_DAILY_URL/KRX_KOSPI_DAILY_URL",
            },
            {
                "label": "KOSDAQ",
                "endpoint": settings.krx_kosdaq_daily_url,
                "field": "KRX_KOSDAQ_DAILY_URL",
            },
        ]
    return [
        {
            "label": provider,
            "endpoint": PROVIDER_EGRESS_ENDPOINTS[provider],
            "field": "endpoint",
        }
    ]

def _scheduler_enable_gate_blockers(
    checks: dict[str, dict[str, Any]],
    *,
    providers: list[str],
    tickers: list[str],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for check_name in ("readiness", "raw_archive", "provider_egress", "status", "stale_runs"):
        result = checks[check_name]
        if result.get("ok") is not True:
            blockers.append(
                {
                    "code": f"{check_name}_not_ready",
                    "check": check_name,
                    "issues": result.get("issues", []),
                }
            )

    status = checks["status"]
    if status.get("ok") is True:
        missing_smoke_runs = _missing_successful_manual_smoke_runs(
            status.get("recent_runs"),
            providers=providers,
            tickers=tickers,
        )
        if missing_smoke_runs:
            blockers.append(
                {
                    "code": "manual_ingestion_smoke_missing",
                    "check": "status",
                    "missing_runs": missing_smoke_runs,
                }
            )

    stale_runs = checks["stale_runs"]
    if stale_runs.get("ok") is True and stale_runs.get("stale_count", 0) > 0:
        blockers.append(
            {
                "code": "stale_ingestion_runs_present",
                "check": "stale_runs",
                "stale_count": stale_runs.get("stale_count", 0),
            }
        )
    return blockers

def _missing_successful_manual_smoke_runs(
    recent_runs: object,
    *,
    providers: list[str],
    tickers: list[str],
) -> list[dict[str, str]]:
    expected = [
        {"provider": provider, "ticker": ticker}
        for provider in providers
        for ticker in tickers
    ]
    if not isinstance(recent_runs, list):
        return expected

    succeeded = {
        (str(run.get("provider")), str(run.get("ticker")))
        for run in recent_runs
        if isinstance(run, dict)
        if run.get("status") == "succeeded"
    }
    return [run for run in expected if (run["provider"], run["ticker"]) not in succeeded]

def _check_provider_endpoint_egress(
    *,
    provider: str,
    endpoint: str,
    transport: ExternalTransport,
) -> dict[str, Any]:
    request = ExternalRequest(
        method="GET",
        url=endpoint,
        params={},
        timeout_seconds=PROVIDER_EGRESS_TIMEOUT_SECONDS,
    )
    try:
        response = transport(request)
        return {
            "reachable": True,
            "endpoint": endpoint,
            "status_code": response.status_code,
            "note": "Provider endpoint returned an HTTP response.",
        }
    except json.JSONDecodeError:
        return {
            "reachable": True,
            "endpoint": endpoint,
            "status_code": None,
            "error_code": "JSONDecodeError",
            "note": "Provider endpoint returned a non-JSON HTTP response.",
        }
    except Exception as exc:
        status_code = getattr(exc, "code", None)
        if isinstance(status_code, int):
            return {
                "reachable": True,
                "endpoint": endpoint,
                "status_code": status_code,
                "note": "Provider endpoint returned an HTTP error response.",
            }
        return {
            "reachable": False,
            "endpoint": endpoint,
            "status_code": None,
            "error_code": exc.__class__.__name__,
            "note": "Provider endpoint could not be reached from this runtime.",
        }

def hydrate_external_api_settings(settings: Settings) -> Settings:
    if (
        settings.opendart_api_key
        and settings.naver_client_id
        and settings.naver_client_secret
        and settings.krx_api_key
        and _krx_daily_endpoints_configured(settings)
    ):
        return settings
    if not settings.external_api_secret_arn:
        return settings
    # Lazy lookup keeps app.services.ingestion.load_secret_json patchable.
    from app.services import ingestion as _ingestion_pkg

    secret = _ingestion_pkg.load_secret_json(settings.external_api_secret_arn)
    return settings.model_copy(
        update={
            "opendart_api_key": (
                settings.opendart_api_key
                or _first_secret_value(secret, "OPENDART_API_KEY", "opendart_api_key")
            ),
            "naver_client_id": (
                settings.naver_client_id
                or _first_secret_value(secret, "NAVER_CLIENT_ID", "naver_client_id")
            ),
            "naver_client_secret": (
                settings.naver_client_secret
                or _first_secret_value(secret, "NAVER_CLIENT_SECRET", "naver_client_secret")
            ),
            "krx_api_key": (
                settings.krx_api_key
                or _first_secret_value(secret, "KRX_API_KEY", "krx_api_key")
            ),
            "krx_daily_url": (
                settings.krx_daily_url
                or _first_secret_value(secret, "KRX_DAILY_URL", "krx_daily_url")
            ),
            "krx_kospi_daily_url": (
                _first_secret_value(secret, "KRX_KOSPI_DAILY_URL", "krx_kospi_daily_url")
                or settings.krx_kospi_daily_url
            ),
            "krx_kosdaq_daily_url": (
                _first_secret_value(secret, "KRX_KOSDAQ_DAILY_URL", "krx_kosdaq_daily_url")
                or settings.krx_kosdaq_daily_url
            ),
            "krx_api_key_header": (
                _first_secret_value(secret, "KRX_API_KEY_HEADER", "krx_api_key_header")
                or settings.krx_api_key_header
            ),
        }
    )

def _opendart_corp_codes_by_stock_code(api_key: str) -> dict[str, dict[str, str]]:
    url = "https://opendart.fss.or.kr/api/corpCode.xml?" + urlencode({"crtfc_key": api_key})
    with urlopen(url, timeout=30) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    root = ET.fromstring(archive.read(archive.namelist()[0]))
    rows: dict[str, dict[str, str]] = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if not stock_code:
            continue
        rows[stock_code] = {
            "corp_code": (item.findtext("corp_code") or "").strip(),
            "corp_name": (item.findtext("corp_name") or "").strip(),
        }
    return rows

def _krx_daily_endpoints_configured(settings: Settings) -> bool:
    return bool(
        (settings.krx_daily_url or settings.krx_kospi_daily_url)
        and settings.krx_kosdaq_daily_url
    )
