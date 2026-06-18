from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.orm import IngestionRun


class IngestionIdempotencyService:
    """Track ingestion runs so provider jobs can be safely retried."""

    SUCCEEDED_STATUS = "succeeded"
    RESTARTABLE_STATUSES = {"failed", "partial_failed"}

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def compute_input_hash(params: dict[str, Any]) -> str:
        normalized = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def is_duplicate(self, *, run_id: str, input_hash: str) -> bool:
        existing = self.session.scalars(
            select(IngestionRun).where(
                IngestionRun.status == self.SUCCEEDED_STATUS,
                or_(
                    IngestionRun.run_id == run_id,
                    IngestionRun.input_hash == input_hash,
                ),
            )
        ).first()
        return existing is not None

    def start_run(
        self,
        *,
        run_id: str,
        job_type: str,
        provider: str,
        target_scope: dict[str, Any],
        input_hash: str,
    ) -> IngestionRun:
        run = IngestionRun(
            run_id=run_id,
            job_type=job_type,
            provider=provider,
            target_scope=target_scope,
            status="started",
            input_hash=input_hash,
            started_at=datetime.now(timezone.utc),
            result_counts={},
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def start_or_restart_run(
        self,
        *,
        run_id: str,
        job_type: str,
        provider: str,
        target_scope: dict[str, Any],
        input_hash: str,
    ) -> IngestionRun:
        existing = self.session.scalars(
            select(IngestionRun).where(IngestionRun.run_id == run_id).with_for_update()
        ).first()
        if existing is None:
            try:
                return self.start_run(
                    run_id=run_id,
                    job_type=job_type,
                    provider=provider,
                    target_scope=target_scope,
                    input_hash=input_hash,
                )
            except IntegrityError:
                self.session.rollback()
                existing = self.session.scalars(
                    select(IngestionRun)
                    .where(IngestionRun.run_id == run_id)
                    .with_for_update()
                ).first()
                if existing is None:
                    raise

        if existing.status == self.SUCCEEDED_STATUS and existing.input_hash == input_hash:
            return existing
        if existing.status not in self.RESTARTABLE_STATUSES:
            raise ValueError(f"ingestion_run_already_active:{run_id}")

        existing.job_type = job_type
        existing.provider = provider
        existing.target_scope = target_scope
        existing.status = "started"
        existing.input_hash = input_hash
        existing.started_at = datetime.now(timezone.utc)
        existing.completed_at = None
        existing.result_counts = {}
        existing.error_summary = None
        self.session.commit()
        self.session.refresh(existing)
        return existing

    def mark_failed_by_run_id(
        self,
        *,
        run_id: str,
        error_summary: dict[str, Any],
    ) -> IngestionRun:
        run = self.session.scalars(
            select(IngestionRun).where(IngestionRun.run_id == run_id)
        ).first()
        if run is None:
            raise ValueError(f"ingestion_run_not_found:{run_id}")
        return self.mark_failed(run=run, error_summary=error_summary)

    def mark_succeeded(
        self,
        *,
        run: IngestionRun,
        result_counts: dict[str, Any],
    ) -> IngestionRun:
        run.status = self.SUCCEEDED_STATUS
        run.completed_at = datetime.now(timezone.utc)
        run.result_counts = result_counts
        run.error_summary = None
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_partial_failed(
        self,
        *,
        run: IngestionRun,
        result_counts: dict[str, Any],
        error_summary: dict[str, Any],
    ) -> IngestionRun:
        run.status = "partial_failed"
        run.completed_at = datetime.now(timezone.utc)
        run.result_counts = result_counts
        run.error_summary = error_summary
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_failed(
        self,
        *,
        run: IngestionRun,
        error_summary: dict[str, Any],
    ) -> IngestionRun:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.error_summary = error_summary
        self.session.commit()
        self.session.refresh(run)
        return run
