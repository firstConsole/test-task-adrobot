"""The editor's read path, against a real PostgreSQL.

These are the assertions the rest of stage 5 was measured for but could not make: the share
arithmetic carried through the database rather than through a function, the statement count of
the aggregate read, and the two behaviours the reference tool is recognised by — a removed row
that stays on screen, and a pin that survives both a push and a cancel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text

from adrobot.application.errors import StreamNotFoundError
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.domain.campaign import Campaign, CampaignSetupStatus
from adrobot.domain.ids import CampaignId, KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.shares import redistribute
from adrobot.domain.stream import (
    Stream,
    StreamFilter,
    StreamOffer,
    StreamSchema,
    StreamType,
)
from adrobot.domain.values import Share
from tests.helpers import shares

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncConnection

    from adrobot.application.ports.persistence import (
        MirroredCampaign,
        Transaction,
        UnitOfWork,
    )
    from tests.helpers import Statements

NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)
CAMPAIGN = KeitaroCampaignId(93212)
FLOW = KeitaroStreamId(564221)

# The three offers of the video's t=0, with the tracker's own timestamps. 3749 and 3717 share
# a stamp to the second and are told apart only by their row id; 11111 is the most recently
# created and carries the SMALLEST row id, so a mirror ordered by row id alone pairs the 34
# with the wrong offer — the same values, the wrong offers, which is why every assertion here
# is a dict.
VIDEO_ROWS = (
    StreamOffer(
        offer_id=OfferId(11111),
        share=34,
        state="active",
        row_id=7,
        created_at=datetime(2024, 3, 1, 11, 30, tzinfo=UTC),
    ),
    StreamOffer(
        offer_id=OfferId(3717),
        share=33,
        state="active",
        row_id=51,
        created_at=datetime(2024, 3, 1, 9, tzinfo=UTC),
    ),
    StreamOffer(
        offer_id=OfferId(3749),
        share=33,
        state="active",
        row_id=49,
        created_at=datetime(2024, 3, 1, 9, tzinfo=UTC),
    ),
)


def flow(stream_id: int, position: int, offers: tuple[StreamOffer, ...]) -> Stream:
    return Stream(
        id=KeitaroStreamId(stream_id),
        campaign_id=CAMPAIGN,
        name=f"Flow {position}",
        type=StreamType.REGULAR,
        schema=StreamSchema.LANDINGS,
        action_type="http",
        position=position,
        filters=(StreamFilter(name="country", mode="accept", payload=("AU",), id=4172),),
        offers=offers,
    )


async def given_a_campaign(uow: UnitOfWork, streams: tuple[Stream, ...]) -> MirroredCampaign:
    """Write one campaign and its flows through the repositories the service itself uses."""
    async with uow.begin() as tx:
        mirrored = await tx.campaigns.add(
            Campaign(id=CAMPAIGN, alias="a1b2c3d4", name="Demo", state="active"),
            setup=CampaignSetup(status=CampaignSetupStatus.READY, public_domain="kt.example"),
        )
        await tx.streams.upsert_campaign_streams(campaign_id=mirrored.id, streams=streams, at=NOW)
    return mirrored


async def test_the_videos_three_offers_keep_their_pairing_through_the_database(
    uow: UnitOfWork,
) -> None:
    # The one test that exercises the stack rather than the function: the ordinals the
    # remainder follows are derived from the tracker's timestamps as PostgreSQL stored them.
    mirrored = await given_a_campaign(uow, (flow(FLOW, 1, VIDEO_ROWS),))
    async with uow.begin() as tx:
        (view,) = await tx.streams.views_for(mirrored.id)

    assert shares(view.mirror_rows) == {3749: 33, 3717: 33, 11111: 34}
    assert shares(redistribute(view.mirror_rows)) == {3749: 33, 3717: 33, 11111: 34}


async def test_the_editor_read_costs_the_same_four_statements_whatever_the_flow_count(
    uow: UnitOfWork, statements: Statements
) -> None:
    # Four: the flows, their offer rows, their pins, the live drafts. Never one per flow —
    # which is what lazy="raise_on_sql" makes a failure rather than a slow endpoint.
    mirrored = await given_a_campaign(
        uow, tuple(flow(FLOW + index, index + 1, VIDEO_ROWS) for index in range(12))
    )
    statements.sent.clear()
    async with uow.begin() as tx:
        views = await tx.streams.views_for(mirrored.id)

    assert len(views) == 12
    assert statements.queries == ("SELECT", "SELECT", "SELECT", "SELECT"), statements


async def test_a_row_the_tracker_stopped_returning_is_still_drawn(uow: UnitOfWork) -> None:
    # The most characteristic behaviour of the reference tool and the easiest to lose: a
    # removed row stays on screen, greyed, at 0%, with BRING BACK live. This test fails the
    # day the mirror write becomes a replace.
    mirrored = await given_a_campaign(uow, (flow(FLOW, 1, VIDEO_ROWS),))
    async with uow.begin() as tx:
        await tx.streams.upsert_stream_offers(
            campaign_id=mirrored.id, stream_id=FLOW, offers=VIDEO_ROWS[:2], at=NOW
        )
        (view,) = await tx.streams.views_for(mirrored.id)

    drawn = {int(row.offer_id): row.removed for row in view.mirror_rows}
    assert drawn == {3749: True, 3717: False, 11111: False}
    assert shares(redistribute(view.mirror_rows)) == {3749: 0, 3717: 50, 11111: 50}


async def test_a_pin_moves_no_share_and_survives_the_next_mirror_write(
    uow: UnitOfWork,
) -> None:
    # A pin looks like a bug: it changes nothing until the next edit. It also lives outside
    # the draft, so a push and a cancel both leave it standing.
    mirrored = await given_a_campaign(uow, (flow(FLOW, 1, VIDEO_ROWS),))
    async with uow.begin() as tx:
        await tx.pins.hold(
            campaign_id=mirrored.id, stream_id=FLOW, offer_id=OfferId(3717), share=Share(25)
        )
        (before,) = await tx.streams.views_for(mirrored.id)
    assert shares(before.mirror_rows) == {3749: 33, 3717: 33, 11111: 34}

    async with uow.begin() as tx:
        await tx.streams.upsert_stream_offers(
            campaign_id=mirrored.id, stream_id=FLOW, offers=VIDEO_ROWS, at=NOW
        )
        (after,) = await tx.streams.views_for(mirrored.id)

    held = {int(row.offer_id): row.pinned_share for row in after.mirror_rows}
    assert held == {3749: None, 3717: 25, 11111: None}
    # And only now, at the next recalculation, does it decide anything.
    assert shares(redistribute(after.mirror_rows)) == {3749: 37, 3717: 25, 11111: 38}


async def test_no_method_taking_a_flow_id_will_answer_for_another_campaign(
    uow: UnitOfWork, db: AsyncConnection
) -> None:
    # `{stream_id}` in a URL is the tracker's id and is global, so the campaign in the path is
    # the whole of the authorisation. The scope is inside each statement rather than in a
    # guard before it, which is why this sweeps the methods instead of trusting one check.
    await given_a_campaign(uow, (flow(FLOW, 1, VIDEO_ROWS),))
    stranger = CampaignId(UUID(int=0xFEED))

    refused: dict[str, str] = {}
    scoped: tuple[tuple[str, Callable[[Transaction], Awaitable[object]]], ...] = (
        ("streams.lock", lambda tx: tx.streams.lock(campaign_id=stranger, stream_id=FLOW)),
        (
            "streams.upsert_stream_offers",
            lambda tx: tx.streams.upsert_stream_offers(
                campaign_id=stranger, stream_id=FLOW, offers=(), at=NOW
            ),
        ),
        (
            "pins.hold",
            lambda tx: tx.pins.hold(
                campaign_id=stranger, stream_id=FLOW, offer_id=OfferId(3717), share=Share(25)
            ),
        ),
        (
            "drafts.open_for",
            lambda tx: tx.drafts.open_for(
                campaign_id=stranger, stream_id=FLOW, rows=(), base_snapshot_hash="a" * 64
            ),
        ),
    )
    for name, call in scoped:
        async with uow.begin() as tx:
            with pytest.raises(StreamNotFoundError):
                await call(tx)
            refused[name] = "raised"

    # `pins.release` is the one that does not raise: presence is the pin, so releasing one
    # that is not there is the same button pressed twice. It must still write nothing.
    async with uow.begin() as tx:
        await tx.pins.release(campaign_id=stranger, stream_id=FLOW, offer_id=OfferId(3717))

    assert len(refused) == 4
    written = await db.execute(
        text("SELECT (SELECT count(*) FROM offer_pins), (SELECT count(*) FROM stream_drafts)")
    )
    assert written.one() == (0, 0)
