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
    assert "ingest_provider_batch" in result["supported_operations"]


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
