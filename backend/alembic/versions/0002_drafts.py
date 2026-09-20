"""The editor's staged edits and the audit of every push.

Creates stream_drafts, stream_draft_rows and push_attempts. The partial unique index is
created here rather than in 0004 with the search indexes, as PLAN-BACKEND's own file list
suggests: it is what makes two live drafts on one flow unrepresentable, and a window between
two revisions where they are representable is the state it exists to prevent.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

DRAFT_STATUS = sa.Enum(
    "open",
    "pushing",
    "pushed",
    "discarded",
    name="draft_status",
    native_enum=False,
    create_constraint=True,
    length=10,
)
PUSH_OUTCOME = sa.Enum(
    "in_flight",
    "applied",
    "conflict",
    "failed",
    "indeterminate",
    "mismatched",
    name="push_outcome",
    native_enum=False,
    create_constraint=True,
    length=14,
)
LIVE_DRAFT = "status IN ('open', 'pushing')"


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "stream_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.BigInteger(), nullable=False),
        sa.Column("status", DRAFT_STATUS, server_default=sa.text("'open'"), nullable=False),
        sa.Column("base_snapshot_hash", sa.Text(), nullable=False),
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
            "base_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_stream_drafts_base_snapshot_hash_is_a_sha256_digest"),
        ),
        sa.ForeignKeyConstraint(
            ["stream_id"],
            ["streams.keitaro_stream_id"],
            name=op.f("fk_stream_drafts_stream_id_streams"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stream_drafts")),
    )
    # A partial unique has to be an Index in PostgreSQL; the naming convention's `ix_`
    # template can say neither "unique" nor "partial", so the name is given by hand.
    op.create_index(
        "uq_stream_drafts_live_draft_per_stream",
        "stream_drafts",
        ["stream_id"],
        unique=True,
        postgresql_where=sa.text(LIVE_DRAFT),
    )
    op.create_table(
        "stream_draft_rows",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.Integer(), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False),
        sa.Column("removed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint(
            "seq > 0 AND activated_at > 0", name=op.f("ck_stream_draft_rows_ordinals_are_positive")
        ),
        sa.CheckConstraint(
            "share BETWEEN 0 AND 100", name=op.f("ck_stream_draft_rows_share_is_a_percentage")
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["stream_drafts.id"],
            name=op.f("fk_stream_draft_rows_draft_id_stream_drafts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("draft_id", "offer_id", name=op.f("pk_stream_draft_rows")),
        # Exactly one row can be the most recently activated: a tie would hand the rounding
        # remainder to seq, which is a different rule from the one the video established.
        sa.UniqueConstraint(
            "draft_id", "activated_at", name=op.f("uq_stream_draft_rows_draft_id_activated_at")
        ),
        sa.UniqueConstraint("draft_id", "seq", name=op.f("uq_stream_draft_rows_draft_id_seq")),
    )
    op.create_table(
        "push_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        # NOT NULL and RESTRICT, where PLAN-BACKEND §7 asked for ON DELETE SET NULL: a draft
        # is closed softly and never deleted, so a nullable column would describe nothing.
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", PUSH_OUTCOME, server_default=sa.text("'in_flight'"), nullable=False),
        sa.Column("desired_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
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
            "jsonb_typeof(desired_state) = 'array'",
            name=op.f("ck_push_attempts_desired_state_is_an_array"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["stream_drafts.id"],
            name=op.f("fk_push_attempts_draft_id_stream_drafts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_attempts")),
    )
    op.create_index(
        op.f("ix_push_attempts_draft_id_created_at"),
        "push_attempts",
        ["draft_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index(op.f("ix_push_attempts_draft_id_created_at"), table_name="push_attempts")
    op.drop_table("push_attempts")
    op.drop_table("stream_draft_rows")
    op.drop_index(
        "uq_stream_drafts_live_draft_per_stream",
        table_name="stream_drafts",
        postgresql_where=sa.text(LIVE_DRAFT),
    )
    op.drop_table("stream_drafts")
