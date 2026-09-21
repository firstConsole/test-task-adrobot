"""FETCH STREAMS FROM KT: what the tracker says now, and what happens to what it no longer says.

The second half is the interesting one. A flow or a row that has disappeared from Keitaro is
kept and marked absent rather than deleted, because that is what the reference tool shows —
a grey row at 0% with BRING BACK live — and because a tombstone is reversible where a delete
is not.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from adrobot.application.errors import CampaignNotFoundError, UpstreamNotFoundError
from adrobot.application.use_cases.mirror_campaign import ImportCampaign, SyncCampaign
from adrobot.domain.ids import CampaignId, KeitaroCampaignId, KeitaroStreamId
from tests.fake_persistence import FakeUnitOfWork, WatchesTheDatabase
from tests.fakes import (
    FIRST_CAMPAIGN_ID,
    FIRST_STREAM_ID,
    REFERENCE_OFFERS,
    FakeClock,
    FakeKeitaroAdmin,
    given_reference_campaign,
)

if TYPE_CHECKING:
    from adrobot.application.ports.persistence import StreamView

HAND_BUILT = KeitaroCampaignId(FIRST_CAMPAIGN_ID)
GEO_FLOW = KeitaroStreamId(FIRST_STREAM_ID)
OFFER_FLOW = KeitaroStreamId(FIRST_STREAM_ID + 1)


@dataclass(frozen=True, slots=True, kw_only=True)
class World:
    """A campaign already imported, and the fakes it was imported from."""

    sync: SyncCampaign
    campaign_id: CampaignId
    admin: FakeKeitaroAdmin
    uow: FakeUnitOfWork
    clock: FakeClock


async def world(admin: FakeKeitaroAdmin | None = None) -> World:
    clock = FakeClock()
    uow = FakeUnitOfWork(clock)
    tracker = admin if admin is not None else FakeKeitaroAdmin()
    given_reference_campaign(tracker)
    imported = await ImportCampaign(admin=tracker, uow=uow, clock=clock)(HAND_BUILT)
    return World(
        sync=SyncCampaign(admin=tracker, uow=uow, clock=clock),
        campaign_id=imported.campaign.id,
        admin=tracker,
        uow=uow,
        clock=clock,
    )


async def mirrored(stage: World) -> dict[str, StreamView]:
    async with stage.uow.begin() as transaction:
        views = await transaction.streams.views_for(stage.campaign_id)
    return {view.stream.name: view for view in views}


async def test_it_picks_up_what_changed_in_the_tracker_and_dates_the_look() -> None:
    stage = await world()
    stage.admin.campaigns[HAND_BUILT] = replace(
        stage.admin.campaigns[HAND_BUILT], name="AU | Oxys v2", state="disabled"
    )
    stage.clock.advance(timedelta(minutes=5))

    view = await stage.sync(stage.campaign_id)

    assert view.campaign.name == "AU | Oxys v2"
    assert view.campaign.state == "disabled"
    assert view.campaign.synced_at == stage.clock.at


async def test_a_flow_added_in_the_tracker_arrives() -> None:
    stage = await world()
    stage.admin.given_stream(
        replace(
            stage.admin.streams[OFFER_FLOW],
            id=KeitaroStreamId(FIRST_STREAM_ID + 7),
            name="Flow 3",
            position=3,
        )
    )

    await stage.sync(stage.campaign_id)

    assert sorted((await mirrored(stage)).keys()) == ["Flow 1", "Flow 2", "Flow 3"]


async def test_a_flow_deleted_in_the_tracker_stays_as_a_tombstone() -> None:
    stage = await world()
    del stage.admin.streams[GEO_FLOW]

    await stage.sync(stage.campaign_id)

    flows = await mirrored(stage)
    # Still on the screen, and no longer claiming to be there: deleting the row instead
    # would take the flow off the editor without anybody being told it had gone.
    assert flows["Flow 1"].stream.absent
    assert not flows["Flow 2"].stream.absent


async def test_an_offer_the_tracker_dropped_stays_as_a_removed_row() -> None:
    stage = await world()
    kept, dropped = REFERENCE_OFFERS
    stage.admin.streams[OFFER_FLOW] = replace(
        stage.admin.streams[OFFER_FLOW],
        offers=tuple(row for row in stage.admin.streams[OFFER_FLOW].offers if row.offer_id == kept),
    )

    await stage.sync(stage.campaign_id)

    rows = {row.offer_id: row for row in (await mirrored(stage))["Flow 2"].mirror_rows}
    assert not rows[kept].removed
    # The row BRING BACK is drawn on, and the reason the mirror tombstones instead of
    # deleting: an offer taken out in Keitaro is still an offer this editor can put back.
    assert rows[dropped].removed


async def test_an_empty_answer_is_believed_and_the_next_one_undoes_it() -> None:
    stage = await world()
    held = dict(stage.admin.streams)
    stage.admin.streams.clear()

    await stage.sync(stage.campaign_id)
    emptied = await mirrored(stage)

    assert [view.stream.absent for view in emptied.values()] == [True, True]

    stage.admin.streams.update(held)
    await stage.sync(stage.campaign_id)

    assert [view.stream.absent for view in (await mirrored(stage)).values()] == [False, False]


async def test_it_refuses_a_campaign_this_service_has_never_heard_of() -> None:
    stage = await world()

    with pytest.raises(CampaignNotFoundError):
        await stage.sync(CampaignId(uuid4()))


async def test_a_campaign_the_tracker_has_lost_leaves_the_mirror_alone() -> None:
    stage = await world()
    del stage.admin.campaigns[HAND_BUILT]

    with pytest.raises(UpstreamNotFoundError):
        await stage.sync(stage.campaign_id)

    # Nothing was written: a 404 from the tracker is not evidence that the flows are gone,
    # and it is the one answer that must not tombstone anything.
    assert sorted((await mirrored(stage)).keys()) == ["Flow 1", "Flow 2"]


async def test_the_database_is_never_held_open_while_the_tracker_is_called() -> None:
    stage = await world()
    watched = FakeUnitOfWork(stage.clock, stage.uow.tables)
    tracker = WatchesTheDatabase(watched)
    given_reference_campaign(tracker)

    await SyncCampaign(admin=tracker, uow=watched, clock=stage.clock)(stage.campaign_id)

    assert not watched.open
