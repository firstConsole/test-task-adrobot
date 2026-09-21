"""The offer catalogue's invariants, and the two search indexes nothing else can check.

Alembic compares neither an index's access method nor its operator class, and it reduces
every cast of a column to the bare column name — so a plain btree where a trigram GIN
belongs, and a VARCHAR cast where TEXT belongs, both autogenerate as an empty migration.
"""

from __future__ import annotations

from tests.helpers import checks, ddl, index_ddl, table


def test_the_id_prefix_index_casts_to_text_and_never_to_varchar() -> None:
    # Measured on PostgreSQL 18.6: the TEXT cast gives an Index Scan and the VARCHAR one a
    # Seq Scan, because LIKE adds a second coercion and the planner sees a different
    # expression — `((id)::character varying)::text`.
    rendered = index_ddl("offers", "ix_offers_id_as_text")
    assert "CAST(id AS TEXT)" in rendered
    assert "VARCHAR" not in rendered


def test_the_id_prefix_index_keeps_its_operator_class() -> None:
    # `postgresql_ops` only lands when its key is the expression's own label; keyed on
    # anything else it is dropped silently, and in a non-C collation the index then serves
    # no prefix match at all.
    assert "text_pattern_ops" in index_ddl("offers", "ix_offers_id_as_text")


def test_the_name_index_is_a_trigram_gin() -> None:
    assert "USING gin (name gin_trgm_ops)" in index_ddl("offers", "ix_offers_name_trgm")


def test_nothing_in_the_schema_points_at_the_offer_catalogue() -> None:
    # An offer reaches a flow before it reaches this catalogue, so an unknown one has to
    # render as `#11234 (not in catalogue)` rather than fail a sync.
    pointing = {
        f"{t.name}.{key.parent.name}"
        for t in table("offers").metadata.tables.values()
        for key in t.foreign_keys
        if key.column.table.name == "offers"
    }
    assert pointing == set()
    assert list(table("offers").foreign_keys) == []


def test_the_catalogues_own_values_carry_no_check() -> None:
    # Tolerance made checkable: a dash is a real value in country, and a catalogue we refuse
    # to store is worse than a value we disagree with.
    assert set(checks("offers")) == {
        "ck_offers_absent_since_matches_mirror_state",
        "ck_offers_mirror_state",
    }


def test_the_catalogue_has_one_representation_of_no_countries() -> None:
    assert "country TEXT[] DEFAULT '{}'::text[] NOT NULL" in ddl("offers")


def test_the_catalogue_stores_a_preview_path_and_not_a_url() -> None:
    # The tracker's public base is configuration, not a fact about an offer; storing the
    # absolute link would multiply one env value across every row.
    columns = set(table("offers").c.keys())
    assert "preview_path" in columns
    assert "preview_url" not in columns
