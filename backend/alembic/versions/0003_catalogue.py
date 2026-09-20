"""The local mirror of the tracker's offer catalogue.

The table only; its two search indexes and the extension one of them needs are 0004.
PLAN-BACKEND §7 also asks for a `raw jsonb` column, which is absent: the admin port hands
this layer a domain `Offer`, so there is no wire payload here to put in one.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

MIRROR_STATE = sa.Enum(
    "present", "absent", name="mirror_state", native_enum=False, create_constraint=True, length=8
)


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "offers",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "country",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("affiliate_network", sa.Text(), nullable=True),
        sa.Column("preview_path", sa.Text(), nullable=True),
        sa.Column(
            "mirror_state", MIRROR_STATE, server_default=sa.text("'present'"), nullable=False
        ),
        sa.Column("absent_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(mirror_state = 'absent') = (absent_since IS NOT NULL)",
            name=op.f("ck_offers_absent_since_matches_mirror_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("offers")
