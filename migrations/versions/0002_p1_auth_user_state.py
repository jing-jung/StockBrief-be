"""P1 auth user state

Revision ID: 0002_p1_auth_user_state
Revises: 0001_initial_mvp_schema
Create Date: 2026-06-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_p1_auth_user_state"
down_revision: str | None = "0001_initial_mvp_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_col(nullable: bool = False) -> sa.Column:
    return sa.Column("id", sa.Uuid(as_uuid=True), nullable=nullable)


def created_at_col() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def updated_at_col() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        uuid_col(),
        sa.Column("cognito_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("nickname", sa.Text(), nullable=True),
        created_at_col(),
        updated_at_col(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cognito_sub", name="uq_users_cognito_sub"),
    )

    op.create_table(
        "user_preferences",
        uuid_col(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        created_at_col(),
        updated_at_col(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
    )

    op.create_table(
        "watchlists",
        uuid_col(),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(length=6), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("market", sa.String(length=20), nullable=False),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        created_at_col(),
        updated_at_col(),
        sa.ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ticker", name="uq_watchlists_user_ticker"),
    )
    op.create_index("ix_watchlists_user_saved_at", "watchlists", ["user_id", "saved_at"])

    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("title", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_chat_sessions_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_chat_sessions_user_updated_at", "chat_sessions", ["user_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_user_updated_at", table_name="chat_sessions")
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_constraint("fk_chat_sessions_user_id_users", type_="foreignkey")
        batch_op.drop_column("title")
        batch_op.drop_column("user_id")
    op.drop_index("ix_watchlists_user_saved_at", table_name="watchlists")
    op.drop_table("watchlists")
    op.drop_table("user_preferences")
    op.drop_table("users")
