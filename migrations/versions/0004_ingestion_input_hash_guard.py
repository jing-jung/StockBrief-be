"""guard active ingestion runs by input hash

Revision ID: 0004_ingestion_input_hash_guard
Revises: 0003_ingestion_runs
Create Date: 2026-06-19 00:00:00.000000

Adds a partial unique index so only one active or succeeded ingestion run can
own a normalized input hash at a time.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_ingestion_input_hash_guard"
down_revision: str | None = "0003_ingestion_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicate_rows = op.get_bind().execute(
        sa.text(
            """
            select input_hash, count(*) as duplicate_count
            from ingestion_runs
            where status in ('started', 'succeeded')
            group by input_hash
            having count(*) > 1
            limit 5
            """
        )
    ).fetchall()
    if duplicate_rows:
        sample = ", ".join(f"{row[0]}:{row[1]}" for row in duplicate_rows)
        raise RuntimeError(
            "Cannot create uq_ingestion_runs_active_input_hash while duplicate "
            "started/succeeded ingestion_runs.input_hash rows exist. "
            f"Resolve duplicates before migration. Sample input_hash counts: {sample}"
        )

    op.create_index(
        "uq_ingestion_runs_active_input_hash",
        "ingestion_runs",
        ["input_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('started', 'succeeded')"),
    )


def downgrade() -> None:
    op.drop_index("uq_ingestion_runs_active_input_hash", table_name="ingestion_runs")
