"""ingestion runs idempotency table

Revision ID: 0003_ingestion_runs
Revises: 0002_p1_auth_user_state
Create Date: 2026-06-17 00:00:00.000000

Adds `ingestion_runs` for ingestion idempotency tracking.
Every ingestion job writes a row here keyed by `run_id`.
Replay detection is done by checking `status` and `input_hash` before re-running.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_ingestion_runs"
down_revision: str | None = "0002_p1_auth_user_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        # Public stable identifier for this run (e.g. "opendart-2026-06-17-005930")
        sa.Column("run_id", sa.Text(), nullable=False),
        # Ingestion job type (e.g. "disclosure", "news", "price", "financial", "score")
        sa.Column("job_type", sa.Text(), nullable=False),
        # Data provider (e.g. "OpenDART", "NAVER", "KRX")
        sa.Column("provider", sa.Text(), nullable=False),
        # Scope of the run: ticker, date range, etc.
        sa.Column(
            "target_scope",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        # started | succeeded | partial_failed | failed | replayed
        sa.Column("status", sa.Text(), nullable=False),
        # SHA-256 hash of the normalized input parameters for replay detection
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # {"inserted": 12, "updated": 3, "skipped": 1}
        sa.Column(
            "result_counts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        # {"error_type": "rate_limited", "message": "..."} — null on success
        sa.Column("error_summary", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_ingestion_runs_run_id"),
    )

    op.create_index(
        "ix_ingestion_runs_job_type_provider_status",
        "ingestion_runs",
        ["job_type", "provider", "status"],
    )
    op.create_index(
        "ix_ingestion_runs_started_at",
        "ingestion_runs",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_index(
        "ix_ingestion_runs_job_type_provider_status", table_name="ingestion_runs"
    )
    op.drop_table("ingestion_runs")
