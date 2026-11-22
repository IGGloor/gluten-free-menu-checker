"""Create the analyses table.

Revision ID: 0001
Revises:
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("menu_url", sa.Text(), nullable=False),
        sa.Column("preferences", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("other", sa.Text(), nullable=True),
        sa.Column("restaurant_name", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("dishes", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_cache_key_created_at", "analyses", ["cache_key", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_analyses_cache_key_created_at", table_name="analyses")
    op.drop_table("analyses")
