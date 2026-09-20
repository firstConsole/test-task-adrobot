"""The draft and audit tables' invariants, read off the mapping. None of these needs PostgreSQL."""

from __future__ import annotations

import re

from sqlalchemy import Index, UniqueConstraint

from adrobot.application.push import PushOutcome
from adrobot.domain.draft import LIVE_DRAFT_STATUSES
from tests.helpers import checks, ddl, table


def live_draft_index() -> Index:
    return next(
        index
        for index in table("stream_drafts").indexes
        if index.name == "uq_stream_drafts_live_draft_per_stream"
    )


def test_at_most_one_draft_per_stream_is_live_at_a_time() -> None:
    # A second draft opened while the first is being pushed would be seeded from the
    # pre-push mirror, and pushing it would silently revert the push in flight.
    index = live_draft_index()
    assert index.unique
    assert [column.name for column in index.columns] == ["stream_id"]


def test_the_live_draft_index_names_exactly_the_live_statuses() -> None:
    # The predicate repeats the enumeration as SQL literals, so nothing but this holds the
    # two together.
    predicate = str(live_draft_index().dialect_options["postgresql"]["where"])
    assert set(re.findall(r"'([a-z_]+)'", predicate)) == {
        status.value for status in LIVE_DRAFT_STATUSES
    }


def test_a_draft_row_carries_our_own_number_and_so_is_checked() -> None:
    # The other end of the asymmetry that test_no_constraint_has_an_opinion_about_a_share_
    # read_from_the_tracker asserts for stream_offers.
    assert (
        checks("stream_draft_rows")["ck_stream_draft_rows_share_is_a_percentage"]
        == "share BETWEEN 0 AND 100"
    )


def test_a_draft_rows_ordinals_are_unique_within_the_draft() -> None:
    # Exactly one row can be the most recently activated: a tie would hand the rounding
    # remainder to seq, which is a different rule from the one the video established.
    unique = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in table("stream_draft_rows").constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert sorted(unique.values()) == [["draft_id", "activated_at"], ["draft_id", "seq"]]


def test_a_draft_row_does_not_point_at_the_offer_catalogue() -> None:
    targets = {
        key.parent.name: key.column.table.name for key in table("stream_draft_rows").foreign_keys
    }
    assert targets == {"draft_id": "stream_drafts"}


def test_a_push_attempt_cannot_be_orphaned_from_the_draft_it_pushed() -> None:
    # The blanket foreign-key test would still pass if this column were made nullable.
    column = table("push_attempts").c.draft_id
    assert column.nullable is False
    assert {key.ondelete for key in column.foreign_keys} == {"RESTRICT"}


def test_an_attempt_opens_before_the_tracker_is_called_and_not_after() -> None:
    # The default is the state a row is inserted in, which is what dates a wedged push.
    assert "outcome VARCHAR(14) DEFAULT 'in_flight' NOT NULL" in ddl("push_attempts")
    assert PushOutcome.IN_FLIGHT.value == "in_flight"


def test_the_push_outcome_column_stores_every_value_the_use_case_can_produce() -> None:
    rendered = ddl("push_attempts")
    for outcome in PushOutcome:
        assert f"'{outcome.value}'" in rendered, outcome
