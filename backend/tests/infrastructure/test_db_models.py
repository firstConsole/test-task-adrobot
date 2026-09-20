"""The mapping's invariants, asserted against the metadata rather than against a reviewer.

Every test here reads the metadata or the compiled DDL, so none of them needs PostgreSQL.
The three draft tables have their own module beside this one.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

from adrobot.infrastructure.db import models
from adrobot.infrastructure.db.base import Base

# SQLAlchemy does not annotate its dialect constructors, and this is the whole suite's
# one PostgreSQL dialect.
POSTGRES = postgresql.dialect()  # type: ignore[no-untyped-call]


def ddl(table_name: str) -> str:
    return str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=POSTGRES))


def checks(table_name: str) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_every_relationship_refuses_to_lazy_load() -> None:
    configure_mappers()
    lazy = {
        f"{mapper.class_.__name__}.{name}": relation.lazy
        for mapper in Base.registry.mappers
        for name, relation in mapper.relationships.items()
    }
    assert lazy, "the walk found no relationships, so it would pass on an empty mapping too"
    assert set(lazy.values()) == {"raise_on_sql"}, lazy


def test_every_foreign_key_refuses_to_delete_the_row_it_points_at() -> None:
    # The mirror tombstones rather than deletes, and an audit row's draft outlives it —
    # which is why push_attempts.draft_id is RESTRICT and not PLAN §7's SET NULL.
    actions = {
        f"{key.parent.table.name}.{key.parent.name}": key.ondelete
        for table in Base.metadata.tables.values()
        for key in table.foreign_keys
    }
    assert actions
    assert set(actions.values()) == {"RESTRICT"}, actions


def test_a_stream_offer_does_not_point_at_the_offer_catalogue() -> None:
    # An offer can reach a flow before it reaches our mirror of the catalogue.
    targets = {
        key.parent.name: key.column.table.name
        for key in Base.metadata.tables["stream_offers"].foreign_keys
    }
    assert targets == {"stream_id": "streams"}


def test_the_tracker_assigns_its_own_ids_and_postgres_does_not() -> None:
    # A BIGSERIAL here would attach a sequence to a column only Keitaro writes, and an
    # insert that omitted the id would invent one.
    for name in Base.metadata.tables:
        rendered = ddl(name).upper()
        assert "SERIAL" not in rendered, name
        # An identity column would pass the letter of the line above and break its intent.
        assert "IDENTITY" not in rendered, name


def test_no_constraint_has_an_opinion_about_a_share_read_from_the_tracker() -> None:
    # A clean stream in the reference tool sums to 50, and a row a push left behind sits at
    # 0: both have to be storable, which is also why no constraint asserts the sum is 100.
    assert [text for text in checks("stream_offers").values() if "share" in text] == []


def test_a_pinned_share_is_the_one_number_a_client_names_and_so_is_checked() -> None:
    assert checks("offer_pins") == {
        "ck_offer_pins_locked_share_is_a_percentage": "locked_share BETWEEN 0 AND 100"
    }


def test_a_tombstone_carries_the_moment_it_became_one() -> None:
    wanted = "(mirror_state = 'absent') = (absent_since IS NOT NULL)"
    for name in ("streams", "stream_offers", "offers"):
        assert wanted in checks(name).values(), name


def test_our_own_enumerations_store_the_value_and_not_the_member_name() -> None:
    # Read off the DDL and not off the constraint: SQLAlchemy builds this one as an
    # expression, so str() renders the values as bind parameters. The default would store
    # 'PRESENT' while every server_default and query says 'present'.
    assert "mirror_state IN ('present', 'absent')" in ddl("streams")
    assert "setup_status IN ('ready', 'needs_attention')" in ddl("campaigns")
    assert "status IN ('open', 'pushing', 'pushed', 'discarded')" in ddl("stream_drafts")
    assert models.MirrorState.PRESENT.value == "present"
