from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.db import get_session_factory
from app.seed.seed_stock_universe import seed_stock_universe, seed_stock_universe_from_event
from app.services.ingestion import (
    check_ingestion_readiness,
    check_provider_egress,
    check_raw_archive_write,
    check_ingestion_scheduler_enable_gate,
    get_ingestion_status,
    handle_ingestion_event,
    handle_refresh_score_snapshots_event,
    reconcile_stale_ingestion_runs,
    seed_krx_stock_universe_from_event,
)


def handle_maintenance_event(event: dict[str, object]) -> dict[str, object]:
    operation = event.get("stockbrief_operation")
    if operation == "migrate_and_seed":
        return migrate_and_seed()
    if operation == "migrate":
        return migrate()
    if operation == "seed_stock_universe":
        return seed_stock_universe_from_event(event)
    if operation == "seed_krx_stock_universe":
        return seed_krx_stock_universe_from_event(event)
    if operation == "check_ingestion_readiness":
        return check_ingestion_readiness(
            providers=_provider_selection(event),
        )
    if operation == "check_raw_archive_write":
        return check_raw_archive_write()
    if operation == "check_provider_egress":
        return check_provider_egress(event)
    if operation == "check_ingestion_scheduler_enable_gate":
        return check_ingestion_scheduler_enable_gate(event)
    if operation == "ingest_provider_batch":
        return handle_ingestion_event(event)
    if operation == "refresh_score_snapshots":
        return handle_refresh_score_snapshots_event(event)
    if operation == "get_ingestion_status":
        return get_ingestion_status(event)
    if operation == "reconcile_stale_ingestion_runs":
        return reconcile_stale_ingestion_runs(event)
    return {
        "ok": False,
        "error": "unsupported_operation",
        "supported_operations": [
            "migrate",
            "seed_stock_universe",
            "seed_krx_stock_universe",
            "migrate_and_seed",
            "check_ingestion_readiness",
            "check_raw_archive_write",
            "check_provider_egress",
            "check_ingestion_scheduler_enable_gate",
            "ingest_provider_batch",
            "refresh_score_snapshots",
            "get_ingestion_status",
            "reconcile_stale_ingestion_runs",
        ],
    }


def _provider_selection(event: dict[str, object]) -> list[str] | None:
    if "providers" in event:
        providers = event["providers"]
        if isinstance(providers, list):
            return [str(provider) for provider in providers]
        if isinstance(providers, str):
            return [providers]
    provider = event.get("provider")
    if isinstance(provider, str) and provider.strip():
        return [provider]
    return None


def migrate_and_seed() -> dict[str, object]:
    migration_result = migrate()
    seed_result = seed()
    return {
        "ok": migration_result["ok"] and seed_result["ok"],
        "migration": migration_result,
        "seed": seed_result,
    }


def migrate() -> dict[str, object]:
    alembic_config = Config("alembic.ini")
    command.upgrade(alembic_config, "head")
    return {"ok": True, "revision": "head"}


def seed() -> dict[str, object]:
    with get_session_factory()() as session:
        result = seed_stock_universe(session)
    return {"ok": True, "result": result}
