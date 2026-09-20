"""What makes the offer catalogue searchable.

Creates the pg_trgm extension and the two indexes the autocomplete compiles against.

**Neither index is ever edited through an autogenerate diff, and both are here by hand.**
Measured against alembic 1.20: it compares neither an index's access method nor its operator
class, and it reduces every cast of a column to the bare column name — so a plain btree
where a trigram GIN belongs, and `CAST(id AS VARCHAR)` where `CAST(id AS TEXT)` belongs, both
autogenerate as an empty migration. The guards are in
tests/infrastructure/test_db_offer_models.py.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # First, and in this same revision: autogenerate has no concept of an extension, and
    # without it the GIN index below fails with "operator class gin_trgm_ops does not exist".
    # pg_trgm is a trusted extension on PostgreSQL 13+, so the database owner may create it
    # without being a superuser.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Serves `name ILIKE '%...%'` — the autocomplete's name arm. A btree cannot, in any
    # collation and with any operator class.
    op.create_index(
        "ix_offers_name_trgm",
        "offers",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    # Serves the id-prefix arm — the video's «11104». Two measured traps: the cast must be
    # to TEXT and never to VARCHAR, because LIKE then adds a second coercion the planner
    # sees as a different expression; and text_pattern_ops is required rather than
    # decorative, because this database's collation is not C.
    op.create_index(
        "ix_offers_id_as_text",
        "offers",
        [sa.literal_column("CAST(id AS TEXT)").label("id_as_text")],
        unique=False,
        postgresql_ops={"id_as_text": "text_pattern_ops"},
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index("ix_offers_id_as_text", table_name="offers")
    op.drop_index("ix_offers_name_trgm", table_name="offers")
    # The extension is deliberately not dropped: another object in this database may already
    # depend on it, and taking it away is not this revision's business.
