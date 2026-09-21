"""That campaign 93212 — the one from the video, built by hand — opens in this editor.

The reference campaign's two offers hold 25% each, and they stay at 25% all the way into
the mirror.
That is the invariant most likely to be "fixed" by somebody being helpful: a clean flow read
from Keitaro is not normalised, because normalising it would invent traffic nobody asked
for and show a screen the tracker disagrees with.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from adrobot.application.errors import CampaignAlreadyImportedError, UpstreamNotFoundError
from adrobot.application.use_cases.mirror_campaign import ImportCampaign
from adrobot.domain.campaign import CampaignSetupStatus
from adrobot.domain.ids import KeitaroCampaignId
from tests.fake_persistence import FakeUnitOfWork, WatchesTheDatabase
from tests.fakes import (
    FIRST_CAMPAIGN_ID,
    REFERENCE_OFFERS,
    FakeClock,
    FakeKeitaroAdmin,
    given_reference_campaign,
)

HAND_BUILT = KeitaroCampaignId(FIRST_CAMPAIGN_ID)
FIRST_OFFER, SECOND_OFFER = REFERENCE_OFFERS


@dataclass(frozen=True, slots=True, kw_only=True)
class World:
    """One wiring of the import, with every fake it was built from still reachable."""

    imports: ImportCampaign
    admin: FakeKeitaroAdmin
    uow: FakeUnitOfWork
    clock: FakeClock


def world(admin: FakeKeitaroAdmin | None = None, *, seeded: bool = True) -> World:
    clock = FakeClock()
    uow = FakeUnitOfWork(clock)
    tracker = admin if admin is not None else FakeKeitaroAdmin()
    if seeded:
        given_reference_campaign(tracker)
    return World(
        imports=ImportCampaign(admin=tracker, uow=uow, clock=clock),
        admin=tracker,
        uow=uow,
        clock=clock,
    )


async def test_it_adopts_the_campaign_and_dates_the_look() -> None:
    stage = world()

    view = await stage.imports(HAND_BUILT)

    assert view.campaign.keitaro_campaign_id == HAND_BUILT
    assert view.campaign.name == "AU | Oxys"
    assert view.campaign.alias == "Gd7Hk2"
    assert view.campaign.setup_status is CampaignSetupStatus.READY
    assert view.campaign.synced_at == stage.clock.at


async def test_it_records_nothing_it_would_be_guessing_at() -> None:
    stage = world()

    view = await stage.imports(HAND_BUILT)

    # The domain a campaign is served on cannot be read back, so there is no public link —
    # and the two requested values are empty because nobody here requested anything.
    assert view.campaign.public_domain is None
    assert view.public_url is None
    assert view.campaign.requested_country is None
    assert view.campaign.requested_offer_id is None


async def test_the_flows_arrive_with_the_shares_the_tracker_actually_holds() -> None:
    stage = world()

    view = await stage.imports(HAND_BUILT)

    async with stage.uow.begin() as transaction:
        mirrored = await transaction.streams.views_for(view.campaign.id)
    assert [held.stream.name for held in mirrored] == ["Flow 1", "Flow 2"]
    assert mirrored[0].stream.filters[0].payload == ("AU",)
    # 25 and 25. Not 50 and 50, and not 34/33/33 of anything: the state read from Keitaro is
    # mirrored, never repaired.
    assert {row.offer_id: row.share for row in mirrored[1].mirror_rows} == {
        FIRST_OFFER: 25,
        SECOND_OFFER: 25,
    }


async def test_importing_the_same_campaign_twice_names_the_copy_that_exists() -> None:
    stage = world()
    imported = await stage.imports(HAND_BUILT)

    stage.admin.calls.clear()
    with pytest.raises(CampaignAlreadyImportedError) as refusal:
        await stage.imports(HAND_BUILT)

    assert refusal.value.campaign_id == imported.campaign.id
    # Refused from the row that is already here: the tracker is not asked a second time.
    assert stage.admin.calls == []


async def test_a_campaign_the_tracker_does_not_have_writes_nothing() -> None:
    stage = world(seeded=False)

    with pytest.raises(UpstreamNotFoundError):
        await stage.imports(KeitaroCampaignId(404))

    assert stage.uow.tables.campaigns == {}


async def test_the_database_is_never_held_open_while_the_tracker_is_called() -> None:
    watched = FakeUnitOfWork(FakeClock())
    stage = world(WatchesTheDatabase(watched))

    await ImportCampaign(admin=stage.admin, uow=watched, clock=stage.clock)(HAND_BUILT)

    assert not watched.open
