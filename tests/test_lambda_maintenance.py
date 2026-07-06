from app.lambda_handler import handler
from app.maintenance import handle_maintenance_event


def test_lambda_handler_routes_maintenance_events(monkeypatch) -> None:
    calls = []

    def fake_handle(event):
        calls.append(event)
        return {"ok": True}

    monkeypatch.setattr("app.lambda_handler.handle_maintenance_event", fake_handle)

    result = handler({"stockbrief_operation": "migrate"}, None)

    assert result == {"ok": True}
    assert calls == [{"stockbrief_operation": "migrate"}]


def test_maintenance_rejects_unknown_operation() -> None:
    result = handle_maintenance_event({"stockbrief_operation": "unknown"})

    assert result["ok"] is False
    assert result["error"] == "unsupported_operation"
    assert "check_ingestion_readiness" in result["supported_operations"]
    assert "check_raw_archive_write" in result["supported_operations"]
    assert "check_provider_egress" in result["supported_operations"]
    assert "check_ingestion_scheduler_enable_gate" in result["supported_operations"]
    assert "seed_stock_universe" in result["supported_operations"]
    assert "seed_krx_stock_universe" in result["supported_operations"]
    assert "seed_mock_data" not in result["supported_operations"]
    assert "seed_demo_stocks" not in result["supported_operations"]
    assert "ingest_provider_batch" in result["supported_operations"]
    assert "get_ingestion_status" in result["supported_operations"]
    assert "reconcile_stale_ingestion_runs" in result["supported_operations"]


def test_maintenance_routes_ingestion_readiness_operation(monkeypatch) -> None:
    calls = []

    def fake_check(*, providers=None):
        calls.append(providers)
        return {"ok": False, "issues": [{"code": "missing_provider_credential"}]}

    monkeypatch.setattr("app.maintenance.check_ingestion_readiness", fake_check)

    result = handle_maintenance_event(
        {
            "stockbrief_operation": "check_ingestion_readiness",
            "providers": ["OpenDART", "NAVER_NEWS"],
        }
    )

    assert result == {"ok": False, "issues": [{"code": "missing_provider_credential"}]}
    assert calls == [["OpenDART", "NAVER_NEWS"]]


def test_maintenance_routes_raw_archive_write_operation(monkeypatch) -> None:
    def fake_check():
        return {"ok": True, "checks": {"raw_archive": {"write_verified": True}}}

    monkeypatch.setattr("app.maintenance.check_raw_archive_write", fake_check)

    result = handle_maintenance_event({"stockbrief_operation": "check_raw_archive_write"})

    assert result == {"ok": True, "checks": {"raw_archive": {"write_verified": True}}}


def test_maintenance_routes_provider_egress_operation(monkeypatch) -> None:
    calls = []

    def fake_check(event):
        calls.append(event)
        return {"ok": True, "checks": {"providers": {}}}

    monkeypatch.setattr("app.maintenance.check_provider_egress", fake_check)

    event = {"stockbrief_operation": "check_provider_egress", "provider": "OpenDART"}
    result = handle_maintenance_event(event)

    assert result == {"ok": True, "checks": {"providers": {}}}
    assert calls == [event]


def test_maintenance_routes_scheduler_enable_gate_operation(monkeypatch) -> None:
    calls = []

    def fake_check(event):
        calls.append(event)
        return {"ok": False, "scheduler_enable_ready": False, "blockers": []}

    monkeypatch.setattr("app.maintenance.check_ingestion_scheduler_enable_gate", fake_check)

    event = {
        "stockbrief_operation": "check_ingestion_scheduler_enable_gate",
        "providers": ["OpenDART", "NAVER_NEWS"],
        "tickers": ["005930"],
    }
    result = handle_maintenance_event(event)

    assert result == {"ok": False, "scheduler_enable_ready": False, "blockers": []}
    assert calls == [event]


def test_maintenance_routes_stock_universe_seed_operation(monkeypatch) -> None:
    calls = []

    def fake_seed(event):
        calls.append(event)
        return {"ok": True, "operation": "seed_stock_universe"}

    monkeypatch.setattr("app.maintenance.seed_stock_universe_from_event", fake_seed)

    event = {
        "stockbrief_operation": "seed_stock_universe",
        "tickers": ["005930"],
    }
    result = handle_maintenance_event(event)

    assert result == {"ok": True, "operation": "seed_stock_universe"}
    assert calls == [event]


def test_maintenance_routes_krx_stock_universe_seed_operation(monkeypatch) -> None:
    calls = []

    def fake_seed(event):
        calls.append(event)
        return {"ok": True, "operation": "seed_krx_stock_universe"}

    monkeypatch.setattr("app.maintenance.seed_krx_stock_universe_from_event", fake_seed)

    event = {
        "stockbrief_operation": "seed_krx_stock_universe",
        "markets": ["KOSPI", "KOSDAQ"],
    }
    result = handle_maintenance_event(event)

    assert result == {"ok": True, "operation": "seed_krx_stock_universe"}
    assert calls == [event]


def test_maintenance_routes_ingestion_operation(monkeypatch) -> None:
    calls = []

    def fake_handle(event):
        calls.append(event)
        return {"ok": True, "provider": "OpenDART"}

    monkeypatch.setattr("app.maintenance.handle_ingestion_event", fake_handle)

    result = handle_maintenance_event(
        {
            "stockbrief_operation": "ingest_provider_batch",
            "provider": "OpenDART",
            "tickers": ["005930"],
        }
    )

    assert result == {"ok": True, "provider": "OpenDART"}
    assert calls == [
        {
            "stockbrief_operation": "ingest_provider_batch",
            "provider": "OpenDART",
            "tickers": ["005930"],
        }
    ]


def test_maintenance_routes_ingestion_status_operation(monkeypatch) -> None:
    calls = []

    def fake_status(event):
        calls.append(event)
        return {"ok": True, "summary": {"recent_run_count": 1}}

    monkeypatch.setattr("app.maintenance.get_ingestion_status", fake_status)

    event = {
        "stockbrief_operation": "get_ingestion_status",
        "tickers": ["005930"],
        "limit": 5,
    }
    result = handle_maintenance_event(event)

    assert result == {"ok": True, "summary": {"recent_run_count": 1}}
    assert calls == [event]


def test_maintenance_routes_stale_ingestion_reconcile_operation(monkeypatch) -> None:
    calls = []

    def fake_reconcile(event):
        calls.append(event)
        return {"ok": True, "dry_run": True, "stale_count": 1}

    monkeypatch.setattr("app.maintenance.reconcile_stale_ingestion_runs", fake_reconcile)

    event = {
        "stockbrief_operation": "reconcile_stale_ingestion_runs",
        "tickers": ["005930"],
        "max_age_minutes": 60,
        "dry_run": True,
    }
    result = handle_maintenance_event(event)

    assert result == {"ok": True, "dry_run": True, "stale_count": 1}
    assert calls == [event]
