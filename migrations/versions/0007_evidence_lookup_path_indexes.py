"""index evidence lookup query paths

Revision ID: 0007_evidence_lookup_path_indexes
Revises: 0006_news_disclosure_ticker_published_at_index
Create Date: 2026-07-07 00:00:00.000000

Adds composite indexes for the actual candidate evidence summary and evidence
listing paths, which join evidence_chunks to source_documents and filter by
ticker/source_type while ordering or aggregating published_at.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0007_evidence_lookup_path_indexes"
down_revision: str | None = "0006_news_disclosure_ticker_published_at_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVIDENCE_CHUNKS_INDEX_NAME = "ix_evidence_chunks_ticker_published_at"
EVIDENCE_CHUNKS_TABLE_NAME = "evidence_chunks"
SOURCE_DOCUMENTS_INDEX_NAME = "ix_source_documents_source_type_id_published_at"
SOURCE_DOCUMENTS_TABLE_NAME = "source_documents"


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _create_index(table_name: str, index_name: str, columns: list[str]) -> None:
    if _index_exists(table_name, index_name):
        return

    context = op.get_context()
    if context.dialect.name == "postgresql":
        with context.autocommit_block():
            op.create_index(
                index_name,
                table_name,
                columns,
                postgresql_concurrently=True,
            )
        return

    op.create_index(index_name, table_name, columns)


def _drop_index(table_name: str, index_name: str) -> None:
    if not _index_exists(table_name, index_name):
        return

    context = op.get_context()
    if context.dialect.name == "postgresql":
        with context.autocommit_block():
            op.drop_index(
                index_name,
                table_name=table_name,
                postgresql_concurrently=True,
            )
        return

    op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _create_index(
        EVIDENCE_CHUNKS_TABLE_NAME,
        EVIDENCE_CHUNKS_INDEX_NAME,
        ["ticker", "published_at"],
    )
    _create_index(
        SOURCE_DOCUMENTS_TABLE_NAME,
        SOURCE_DOCUMENTS_INDEX_NAME,
        ["source_type", "id", "published_at"],
    )


def downgrade() -> None:
    _drop_index(SOURCE_DOCUMENTS_TABLE_NAME, SOURCE_DOCUMENTS_INDEX_NAME)
    _drop_index(EVIDENCE_CHUNKS_TABLE_NAME, EVIDENCE_CHUNKS_INDEX_NAME)
