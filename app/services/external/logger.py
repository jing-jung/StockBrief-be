from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.orm import ExternalApiCallLog


SENSITIVE_KEYS = (
    "key",
    "secret",
    "token",
    "password",
    "authorization",
    "client-id",
    "client_id",
    "crtfc_key",
    "client_secret",
)


class ExternalApiCallLogger:
    def __init__(self, session: Session) -> None:
        self.session = session

    def log(
        self,
        *,
        provider: str,
        endpoint: str,
        method: str,
        request_params: dict[str, Any] | None,
        status_code: int | None,
        duration_ms: int | None,
        error_code: str | None,
    ) -> ExternalApiCallLog:
        entry = ExternalApiCallLog(
            provider=provider,
            endpoint=endpoint,
            method=method,
            request_params=_sanitize(request_params or {}),
            status_code=status_code,
            duration_ms=duration_ms,
            error_code=error_code,
            called_at=datetime.now(timezone.utc),
        )
        self.session.add(entry)
        self.session.commit()
        return entry


def _sanitize(value: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if any(sensitive in key.casefold() for sensitive in SENSITIVE_KEYS):
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = _sanitize_value(item)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value
