"""index chat messages by session id

Revision ID: 0005_chat_messages_session_id_index
Revises: 0005a_alembic_version_width
Create Date: 2026-06-25 00:00:00.000000

Adds an index for chat session detail lookups that load all messages for one
session_id.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0005_chat_messages_session_id_index"
down_revision: str | None = "0005a_alembic_version_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEX_NAME = "ix_chat_messages_session_id"
TABLE_NAME = "chat_messages"


def _index_exists() -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(index["name"] == INDEX_NAME for index in inspector.get_indexes(TABLE_NAME))


def upgrade() -> None:
    if _index_exists():
        return

    context = op.get_context()
    if context.dialect.name == "postgresql":
        with context.autocommit_block():
            op.create_index(
                INDEX_NAME,
                TABLE_NAME,
                ["session_id"],
                postgresql_concurrently=True,
            )
        return

    op.create_index(INDEX_NAME, TABLE_NAME, ["session_id"])


def downgrade() -> None:
    if not _index_exists():
        return

    context = op.get_context()
    if context.dialect.name == "postgresql":
        with context.autocommit_block():
            op.drop_index(
                INDEX_NAME,
                table_name=TABLE_NAME,
                postgresql_concurrently=True,
            )
        return

    op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
