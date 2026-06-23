from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.db import get_session_factory
from app.seed.seed_mock_data import seed_mock_data
from app.services.ingestion import (
    check_ingestion_readiness,
    check_provider_egress,
    check_raw_archive_write,
    get_ingestion_status,
    handle_ingestion_event,
)


def handle_maintenance_event(event: dict[str, object]) -> dict[str, object]:
    operation = event.get("stockbrief_operation")
    if operation == "migrate_and_seed":
        return migrate_and_seed()
    if operation == "migrate":
        return migrate()
    if operation == "seed_mock_data":
        return seed()
    if operation == "check_ingestion_readiness":
        return check_ingestion_readiness()
    if operation == "check_raw_archive_write":
        return check_raw_archive_write()
    if operation == "check_provider_egress":
        return check_provider_egress(event)
    if operation == "ingest_provider_batch":
        return handle_ingestion_event(event)
    if operation == "get_ingestion_status":
        return get_ingestion_status(event)
    return {
        "ok": False,
        "error": "unsupported_operation",
        "supported_operations": [
            "migrate",
            "seed_mock_data",
            "migrate_and_seed",
            "check_ingestion_readiness",
            "check_raw_archive_write",
            "check_provider_egress",
            "ingest_provider_batch",
            "get_ingestion_status",
        ],
    }


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
        result = seed_mock_data(session)
    return {"ok": True, "result": result}
