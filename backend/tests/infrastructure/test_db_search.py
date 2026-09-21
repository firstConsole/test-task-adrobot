"""That the autocomplete's two indexes are used, and not merely present — and how it ranks.

PLAN-BACKEND §7 asks for this by name: an EXPLAIN is a more convincing artifact of
"PostgreSQL optimisation" than the index itself. It is also the only thing that can catch
these two — alembic compares neither an index's access method nor its operator class, and it
reduces every cast of a column to the bare column name, so a plain btree where a trigram GIN
belongs and a VARCHAR cast where TEXT belongs each autogenerate as an empty migration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from adrobot.domain.ids import OfferId
from adrobot.domain.offer import Offer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from adrobot.application.ports.persistence import UnitOfWork

# Enough that the planner's choice is a decision and not an accident. Measured on this
# schema: the id index is already chosen at 5 000 rows, while the trigram index is not chosen
# at 20 000 and is at 50 000 — for every pattern alike, so it is the table's size that decides
# and not the query's. Seeding costs 0.27 s.
CATALOGUE_SIZE = 50_000


@pytest.fixture
async def catalogue(db: AsyncConnection) -> AsyncConnection:
    """A catalogue large enough for the planner to prefer an index, and its statistics.

    ANALYZE inside the test's own transaction, so the numbers the planner reads are the ones
    this fixture wrote; without it the table looks empty and every plan is a sequential scan.
    """
    await db.execute(
        text(
            "INSERT INTO offers (id, name, state, country) "
            "SELECT g, CASE mod(g, 3) WHEN 0 THEN 'Oxys ' WHEN 1 THEN 'Miaflow ' "
            "ELSE 'Keramin ' END || g, 'active', ARRAY['pl','-'] "
            "FROM generate_series(10000, :last) g"
        ),
        {"last": 10_000 + CATALOGUE_SIZE},
    )
    await db.execute(text("ANALYZE offers"))
    return db


async def plan_for(connection: AsyncConnection, predicate: str) -> str:
    rows = await connection.execute(
        # The predicate is a literal written in this module; no value here comes from
        # anywhere a test does not control.
        text(f"EXPLAIN (COSTS OFF) SELECT id, name FROM offers WHERE {predicate}")  # noqa: S608
    )
    return "\n".join(rows.scalars().all())


async def test_typing_an_offer_id_uses_the_expression_index(
    catalogue: AsyncConnection,
) -> None:
    # The video's «11104». The index NAME and an Index Cond, never the bare word "Index": a
    # wrong operator class, a VARCHAR cast and a missing index can all leave a plan that still
    # contains it.
    plan = await plan_for(catalogue, "CAST(id AS TEXT) LIKE '11104%'")
    assert "ix_offers_id_as_text" in plan, plan
    assert "Index Cond" in plan, plan


async def test_the_same_predicate_cast_to_varchar_would_scan(
    catalogue: AsyncConnection,
) -> None:
    # What `cast(DbOffer.id, String)` compiles to. LIKE adds a second coercion, so the planner
    # sees `((id)::character varying)::text` — a different expression, which no index on
    # `(id::text)` can match. This is the regression the repository's own cast prevents.
    plan = await plan_for(catalogue, "CAST(id AS VARCHAR) LIKE '11104%'")
    assert "ix_offers_id_as_text" not in plan, plan
    assert "Seq Scan" in plan, plan


async def test_typing_part_of_a_name_uses_the_trigram_index(
    catalogue: AsyncConnection,
) -> None:
    plan = await plan_for(catalogue, "name ILIKE '%iaflow 145%'")
    assert "ix_offers_name_trgm" in plan, plan
    assert "Index Cond" in plan, plan


async def test_a_name_query_shorter_than_a_trigram_scans_and_that_is_the_limit(
    catalogue: AsyncConnection,
) -> None:
    # Documented rather than fixed: pg_trgm extracts no full trigram below three characters,
    # so the first two keystrokes of a name search are a scan whatever index exists. It is why
    # the id arm, which works from one character, carries the early keystrokes.
    plan = await plan_for(catalogue, "name ILIKE '%ox%'")
    assert "ix_offers_name_trgm" not in plan, plan


async def test_both_arms_of_one_search_use_both_indexes(
    catalogue: AsyncConnection,
) -> None:
    # The argument PLAN-BACKEND does not make for the id index: a BitmapOr needs every branch
    # indexable, so without it the trigram index buys nothing either and the whole search
    # scans.
    plan = await plan_for(catalogue, "CAST(id AS TEXT) LIKE '11104%' OR name ILIKE '%iaflow 145%'")
    assert "BitmapOr" in plan, plan
    assert "ix_offers_id_as_text" in plan, plan
    assert "ix_offers_name_trgm" in plan, plan


async def test_an_id_prefix_outranks_a_name_that_merely_contains_it(uow: UnitOfWork) -> None:
    """The ranking the in-memory catalogue imitates, held to the query that really answers.

    The video types `11104`, which is an id. Both arms of the search match here, and the
    order between them is the whole usefulness of the box: an offer named after the number
    must not sit above the offer that *is* the number.
    """
    at = datetime(2026, 3, 1, 12, tzinfo=UTC)
    async with uow.begin() as tx:
        await tx.offers.upsert_catalogue(
            (
                Offer(id=OfferId(11234), name="11104 Special", state="active"),
                Offer(id=OfferId(11104), name="Oxys", state="active"),
                Offer(id=OfferId(11104_9), name="Oxys Plus", state="active"),
            ),
            at=at,
        )
        found = await tx.offers.search("11104", limit=10)

    assert [int(offer.id) for offer in found] == [11104, 111049, 11234]
