"""The border between our tables and the domain, exercised without a database.

Every fixture here is a transient ORM instance, which is why each one spells `mirror_state`
out: a server default is applied by PostgreSQL on INSERT, so an object never flushed reads
back `None` where mypy promises a `MirrorState`.

The database half of the round trip — these dicts actually inserting and reading back — is
5.9, with the `db`-marked session fixture.
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from adrobot.domain.campaign import Campaign
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.ids import CampaignId, DraftId, KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.offer import Offer
from adrobot.domain.shares import redistribute
from adrobot.domain.stream import Stream, StreamFilter, StreamOffer, StreamSchema, StreamType
from adrobot.domain.values import OfferState
from adrobot.infrastructure.db import mappers, models
from tests.domain.test_shares import AFTER_PINNING_3717_AND_BRINGING_BACK_3749
from tests.helpers import shares, table

TRACKER_ZONE = UTC


def mirror_row(  # noqa: PLR0913  # one keyword per column that matters here, by design
    offer_id: int,
    *,
    created_at: datetime | None = None,
    row_id: int | None = None,
    share: int = 0,
    state: str = "active",
    mirror_state: models.MirrorState = models.MirrorState.PRESENT,
) -> models.DbStreamOffer:
    return models.DbStreamOffer(
        stream_id=KeitaroStreamId(564221),
        offer_id=OfferId(offer_id),
        share=share,
        state=state,
        keitaro_row_id=row_id,
        keitaro_created_at=created_at,
        mirror_state=mirror_state,
        absent_since=datetime(2026, 1, 1, tzinfo=UTC)
        if mirror_state is models.MirrorState.ABSENT
        else None,
    )


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 3, 1, hour, minute, tzinfo=TRACKER_ZONE)


# The video's t=0, handed to the mapper in deliberately scrambled order. 3749 and 3717 share
# a stamp to the second and are told apart only by their row id; 11111 is the most recent and
# carries the SMALLEST row id, so a mapper ranking on the row id alone pairs the 34 with 3717.
VIDEO_INITIAL_FETCH = (
    mirror_row(11111, created_at=at(11, 30), row_id=7, share=34),
    mirror_row(3717, created_at=at(9), row_id=51, share=33),
    mirror_row(3749, created_at=at(9), row_id=49, share=33),
)


def ordinals(rows: tuple[object, ...]) -> dict[int, tuple[int, int]]:
    return {int(row.offer_id): (row.seq, row.activated_at) for row in rows}  # type: ignore[attr-defined]


def test_the_mirror_is_ranked_by_the_trackers_own_order_not_by_the_order_it_arrived_in() -> None:
    rows = mappers.to_mirror_rows(VIDEO_INITIAL_FETCH, {})
    assert ordinals(rows) == {3749: (1, 1), 3717: (2, 2), 11111: (3, 3)}
    # Carried, not recomputed: these are the tracker's own numbers.
    assert shares(rows) == {3749: 33, 3717: 33, 11111: 34}
    # And they survive the division unchanged, which is the video's t=0.
    assert shares(redistribute(rows)) == {3749: 33, 3717: 33, 11111: 34}


def test_two_rows_sharing_a_stamp_still_get_distinct_ordinals() -> None:
    # row_number() and not rank(): a shared ordinal is refused by UNIQUE (draft_id,
    # activated_at) the moment these rows seed a draft.
    rows = mappers.to_mirror_rows(VIDEO_INITIAL_FETCH, {})
    stamps = [row.activated_at for row in rows]
    assert sorted(stamps) == [1, 2, 3]


def test_a_row_the_tracker_gave_no_stamp_for_ranks_oldest_and_never_takes_the_remainder() -> None:
    rows = mappers.to_mirror_rows(
        (
            mirror_row(11111, created_at=at(11, 30), row_id=7),
            mirror_row(9001),
            mirror_row(3749, created_at=at(9), row_id=49),
        ),
        {},
    )
    assert ordinals(rows) == {9001: (1, 1), 3749: (2, 2), 11111: (3, 3)}
    assert shares(redistribute(rows)) == {9001: 33, 3749: 33, 11111: 34}


def test_the_mirror_is_not_normalised() -> None:
    # A clean stream in the reference tool sums to 50. Any mapper calling redistribute()
    # fails here.
    rows = mappers.to_mirror_rows(
        (
            mirror_row(3717, created_at=at(9), share=25),
            mirror_row(11112, created_at=at(10), share=25),
        ),
        {},
    )
    assert shares(rows) == {3717: 25, 11112: 25}


@pytest.mark.parametrize(
    ("state", "mirror_state", "expected"),
    [
        ("disabled", models.MirrorState.PRESENT, True),
        ("active", models.MirrorState.ABSENT, True),
        ("paused", models.MirrorState.PRESENT, True),
        ("active", models.MirrorState.PRESENT, False),
    ],
)
def test_both_columns_that_can_silence_a_row_fold_into_removed(
    state: str,
    mirror_state: models.MirrorState,
    expected: bool,  # noqa: FBT001  # a parametrised expectation, not a flag
) -> None:
    (row,) = mappers.to_mirror_rows((mirror_row(3717, state=state, mirror_state=mirror_state),), {})
    assert row.removed is expected


def test_a_row_disabled_by_hand_in_keitaro_reads_as_taking_nothing() -> None:
    """The tolerance belongs to the column, not to the kernel row read out of it.

    `stream_offers.share` still holds whatever Keitaro holds, disabled row included — that
    is what makes the mirror a mirror. But `OfferRow` is what the arithmetic and the screen
    see, and there `removed` implies a share of 0: it is the invariant `redistribute`
    maintains on every row it returns, and a mapper handing back `removed=True, share=25`
    hands back a row the arithmetic itself could not have produced.

    It is also load-bearing twice over. The editor draws a clean flow straight from these
    rows with no recalculation in between, so a tombstoned row would otherwise sit under the
    word (removed) showing the percentage it held before the push that dropped it. And
    `DraftDiff` would report a share change on a row that is disabled on both sides.
    """
    (row,) = mappers.to_mirror_rows((mirror_row(3717, state="disabled", share=25),), {})
    assert (row.removed, row.share) == (True, 0)


def test_an_active_row_keeps_whatever_the_tracker_holds_however_little_it_sums_to() -> None:
    rows = mappers.to_mirror_rows((mirror_row(3749, share=25), mirror_row(3717, share=25)), {})
    assert shares(rows) == {3749: 25, 3717: 25}, "50% is a real clean state, never normalised"


def test_a_pin_is_joined_on_and_moves_no_share() -> None:
    rows = mappers.to_mirror_rows(VIDEO_INITIAL_FETCH, {OfferId(3717): 25})
    pinned = {int(row.offer_id): row.pinned_share for row in rows}
    assert pinned == {3749: None, 3717: 25, 11111: None}
    assert shares(rows) == {3749: 33, 3717: 33, 11111: 34}


def test_a_pin_at_zero_is_still_a_pin() -> None:
    pins = mappers.to_pins(
        (
            models.DbOfferPin(
                stream_id=KeitaroStreamId(564221), offer_id=OfferId(3717), locked_share=0
            ),
        )
    )
    assert pins == {3717: 0}
    (row,) = mappers.to_mirror_rows((mirror_row(3717, created_at=at(9)),), pins)
    assert row.pinned_share is not None


def test_a_pin_for_an_offer_the_flow_no_longer_holds_conjures_no_row() -> None:
    # The row may come back through BRING BACK, and the pin has to survive until it does.
    rows = mappers.to_mirror_rows((mirror_row(3717, created_at=at(9)),), {OfferId(99999): 40})
    assert [int(row.offer_id) for row in rows] == [3717]


def test_a_draft_row_round_trips_whole_except_for_its_pin() -> None:
    # The ** is half the test: a key that is not a column raises TypeError right here.
    original = replace(
        next(iter(mappers.to_mirror_rows((mirror_row(3717, created_at=at(9), share=33),), {}))),
        seq=2,
        activated_at=5,
    )
    draft_id = DraftId(UUID(int=1))
    stored = models.DbStreamDraftRow(**mappers.draft_row_values(original, draft_id=draft_id))
    assert mappers.to_draft_rows((stored,), {}) == (replace(original, pinned_share=None),)
    assert mappers.to_draft_rows((stored,), {OfferId(3717): 25}) == (
        replace(original, pinned_share=25),
    )


def test_the_last_state_from_the_video_survives_the_draft_round_trip() -> None:
    state = AFTER_PINNING_3717_AND_BRINGING_BACK_3749
    draft_id = DraftId(UUID(int=2))
    stored = tuple(
        models.DbStreamDraftRow(**mappers.draft_row_values(row, draft_id=draft_id)) for row in state
    )
    read_back = mappers.to_draft_rows(stored, {OfferId(3717): 25})
    assert read_back == state
    # 3749 is the oldest row and the most recently activated, so it takes the remainder.
    assert shares(redistribute(read_back)) == {3749: 38, 3717: 25, 11111: 0, 11112: 37}


def test_a_catalogue_offer_round_trips_whole() -> None:
    for offer in (
        Offer(id=OfferId(11112), name="", state="active"),
        Offer(
            # The dash is a value Keitaro really sends, and a validated CountryCode would
            # refuse the catalogue rather than mirror it.
            id=OfferId(11104),
            name="Oxys 100% [BEAUTY_CL] — 'два' \\ _%",
            state="disabled",
            country=("pl", "es", "-"),
            group_id=7,
            affiliate_network="Everad",
            preview_path="/preview/11104",
        ),
    ):
        assert mappers.to_offer(models.DbOffer(**mappers.offer_values(offer))) == offer


def test_a_flows_filters_round_trip_through_the_shape_the_column_holds() -> None:
    for filters in (
        (),
        (StreamFilter(name="country", mode="accept", payload=("AU",), id=4172),),
        (StreamFilter(name="country", mode="reject", payload=(), id=None),),
        # A mode this build has never heard of is carried, not refused: the mirror is tolerant.
        (StreamFilter(name="ua", mode="whatever", payload=("a", "b")),),
    ):
        assert mappers.to_filters(mappers.filters_json(filters)) == filters


def test_the_audit_column_is_a_json_array_of_the_trackers_own_three_keys() -> None:
    desired = (
        DesiredOffer(offer_id=OfferId(3749), share=50, state=OfferState.ACTIVE),
        DesiredOffer(offer_id=OfferId(3717), share=0, state=OfferState.DISABLED),
    )
    rendered = mappers.desired_state_json(desired)
    # It has to survive JSONB at all: json raises TypeError on an Enum member.
    assert json.loads(json.dumps(rendered)) == rendered
    assert [set(row) for row in rendered] == [{"offer_id", "share", "state"}] * 2
    assert [row["state"] for row in rendered] == ["active", "disabled"]


def test_a_mirror_row_is_always_written_present_and_never_tombstoned() -> None:
    # Not a default but the fact: the only object that reaches these is one the tracker just
    # returned. The pair travels together, which is what the CHECK on it demands.
    stream = Stream(
        id=KeitaroStreamId(564221),
        campaign_id=KeitaroCampaignId(93212),
        name="Flow 2",
        type=StreamType.REGULAR,
        schema=StreamSchema.LANDINGS,
        action_type="http",
        position=2,
    )
    written = (
        mappers.offer_values(Offer(id=OfferId(1), name="n", state="active")),
        mappers.stream_offer_values(
            StreamOffer(offer_id=OfferId(1), share=50, state="active"),
            stream_id=KeitaroStreamId(564221),
        ),
        mappers.stream_values(stream, campaign_id=CampaignId(UUID(int=3))),
    )
    for values in written:
        assert values["mirror_state"] is models.MirrorState.PRESENT
        assert values["absent_since"] is None


def test_a_sync_writes_only_what_the_tracker_is_authoritative_for() -> None:
    # The test that fails the day somebody adds setup_status "for completeness" and makes
    # every FETCH reset a repaired campaign to ready.
    campaign = Campaign(id=KeitaroCampaignId(93212), alias="Hand-Typed", name="n", state="active")
    assert set(mappers.campaign_values(campaign)) == {
        "keitaro_campaign_id",
        "alias",
        "name",
        "state",
    }


def test_every_values_mapper_names_every_column_of_its_table_and_no_others() -> None:
    written_by_the_database = {"created_at", "updated_at"}
    offer = Offer(id=OfferId(1), name="n", state="active")
    assert (
        set(mappers.offer_values(offer)) == set(table("offers").c.keys()) - written_by_the_database
    )
    assert set(
        mappers.stream_offer_values(
            StreamOffer(offer_id=OfferId(1), share=1, state="active"),
            stream_id=KeitaroStreamId(1),
        )
    ) == set(table("stream_offers").c.keys())


def test_no_mapper_can_reach_for_a_relationship_or_emit_sql() -> None:
    # Purity as a property of the file rather than of a convention somebody remembers.
    source = Path(mappers.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_names = {"Session", "select", "insert", "update", "session"}
    forbidden_attributes = {"offers", "pins", "streams", "rows"}
    for node in ast.walk(tree):
        assert not isinstance(node, ast.AsyncFunctionDef | ast.Await), ast.dump(node)
        if isinstance(node, ast.Name):
            assert node.id not in forbidden_names, node.id
        if isinstance(node, ast.Attribute):
            assert node.attr not in forbidden_attributes, node.attr
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("sqlalchemy"), node.module
