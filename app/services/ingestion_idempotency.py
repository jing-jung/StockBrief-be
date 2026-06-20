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
    ACTIVE_STATUSES = {"started"}

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
            duplicate_by_input = self._find_duplicate_by_input_hash(input_hash=input_hash)
            if duplicate_by_input is not None:
                if duplicate_by_input.status == self.SUCCEEDED_STATUS:
                    return duplicate_by_input
                if duplicate_by_input.status in self.ACTIVE_STATUSES:
                    raise ValueError(f"ingestion_run_already_active:{duplicate_by_input.run_id}")
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
                existing = self._find_existing_after_integrity_error(
                    run_id=run_id,
                    input_hash=input_hash,
                )
                if existing is None:
                    raise
                if existing.run_id != run_id:
                    if existing.status == self.SUCCEEDED_STATUS:
                        return existing
                    if existing.status in self.ACTIVE_STATUSES:
                        raise ValueError(f"ingestion_run_already_active:{existing.run_id}")

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

    def _find_duplicate_by_input_hash(self, *, input_hash: str) -> IngestionRun | None:
        return self.session.scalars(
            select(IngestionRun)
            .where(
                IngestionRun.input_hash == input_hash,
                IngestionRun.status.in_([self.SUCCEEDED_STATUS, *self.ACTIVE_STATUSES]),
            )
            .order_by(IngestionRun.started_at.desc())
            .with_for_update()
        ).first()

    def _find_existing_after_integrity_error(
        self,
        *,
        run_id: str,
        input_hash: str,
    ) -> IngestionRun | None:
        existing_by_run_id = self.session.scalars(
            select(IngestionRun).where(IngestionRun.run_id == run_id).with_for_update()
        ).first()
        if existing_by_run_id is not None:
            return existing_by_run_id
        return self._find_duplicate_by_input_hash(input_hash=input_hash)

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
