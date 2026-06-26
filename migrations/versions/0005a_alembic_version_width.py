"""widen alembic version table before long revision ids

Revision ID: 0005a_alembic_version_width
Revises: 0004_ingestion_input_hash_guard
Create Date: 2026-06-26 00:00:00.000000

Alembic creates alembic_version.version_num as VARCHAR(32) by default. The next
revision id is longer than that, so widen the column before Alembic records it.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005a_alembic_version_width"
down_revision: str | None = "0004_ingestion_input_hash_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=255),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
