"""index chat messages by session id

Revision ID: 0005_chat_messages_session_id_index
Revises: 0004_ingestion_input_hash_guard
Create Date: 2026-06-25 00:00:00.000000

Adds an index for chat session detail lookups that load all messages for one
session_id.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_chat_messages_session_id_index"
down_revision: str | None = "0004_ingestion_input_hash_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        with context.autocommit_block():
            op.create_index(
                "ix_chat_messages_session_id",
                "chat_messages",
                ["session_id"],
                postgresql_concurrently=True,
            )
        return

    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        with context.autocommit_block():
            op.drop_index(
                "ix_chat_messages_session_id",
                table_name="chat_messages",
                postgresql_concurrently=True,
            )
        return

    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
