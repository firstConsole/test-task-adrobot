"""Searching the local offer catalogue, and refreshing it from the tracker.

The catalogue exists because `GET /offers` takes no parameters at all, so every assertion
here is about the search that endpoint cannot do — and about the one thing the refresh must
never do, which is believe an empty answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from adrobot.application.errors import UpstreamUnavailableError
from adrobot.application.use_cases.editor import GetEditorView
from adrobot.application.use_cases.offer_catalogue import SearchOffers, SyncOfferCatalogue
from adrobot.domain.ids import OfferId
from adrobot.domain.offer import Offer
from tests.fake_persistence import FakeUnitOfWork, WatchesTheDatabase
from tests.fakes import REFERENCE_OFFERS, FakeKeitaroAdmin
from tests.helpers import given_mirrored_campaign

if TYPE_CHECKING:
    from tests.wiring import FakeWorld

OLDEST, NEWEST = REFERENCE_OFFERS

CATALOGUE = (
    Offer(id=OfferId(11104), name="Oxys", state="active", country=("pl", "es", "-")),
    Offer(id=OfferId(11112), name="Miaflow", state="active"),
    Offer(id=OfferId(11234), name="11104 Special", state="active"),
    Offer(id=OfferId(3749), name="Keto Light", state="active"),
)


def syncing(world: FakeWorld) -> SyncOfferCatalogue:
    return SyncOfferCatalogue(admin=world.admin, uow=world.uow, clock=world.clock)


async def given_a_catalogue(world: FakeWorld, offers: tuple[Offer, ...] = CATALOGUE) -> None:
    world.admin.offers = list(offers)
    await syncing(world)()


def listed(offers: tuple[Offer, ...]) -> list[int]:
    return [int(offer.id) for offer in offers]


# --- the search --------------------------------------------------------------------------


async def test_an_id_prefix_outranks_a_name_that_merely_contains_it(world: FakeWorld) -> None:
    """The video types `11104`, which is an id. An offer named after it must not bury it."""
    await given_a_catalogue(world)

    found = await SearchOffers(uow=world.uow)(query="11104")

    assert listed(found) == [11104, 11234]


async def test_a_search_by_name_ignores_case(world: FakeWorld) -> None:
    await given_a_catalogue(world)

    assert listed(await SearchOffers(uow=world.uow)(query="miaflow")) == [11112]


async def test_no_text_is_show_all_offers(world: FakeWorld) -> None:
    await given_a_catalogue(world)

    assert listed(await SearchOffers(uow=world.uow)()) == [3749, 11104, 11112, 11234]


async def test_the_answer_is_bounded_by_the_limit_it_was_asked_for(world: FakeWorld) -> None:
    await given_a_catalogue(world)

    assert len(await SearchOffers(uow=world.uow)(limit=2)) == 2


async def test_an_offer_the_tracker_dropped_is_not_offered_for_adding(world: FakeWorld) -> None:
    await given_a_catalogue(world)

    await given_a_catalogue(world, CATALOGUE[:2])

    assert listed(await SearchOffers(uow=world.uow)()) == [11104, 11112]


async def test_searching_an_empty_catalogue_finds_nothing_rather_than_failing(
    world: FakeWorld,
) -> None:
    assert await SearchOffers(uow=world.uow)(query="oxys") == ()


# --- the refresh -------------------------------------------------------------------------


async def test_a_refresh_reports_what_it_wrote(world: FakeWorld) -> None:
    world.admin.offers = list(CATALOGUE)

    written = await syncing(world)()

    assert written.offers == len(CATALOGUE)
    assert written.synced_at == world.clock.at


async def test_a_tracker_that_lists_nothing_leaves_the_catalogue_alone(
    world: FakeWorld,
) -> None:
    """The one behaviour worth the whole use case: an empty answer is not believed.

    A tracker with no offers and a tracker that failed to list them are the same answer from
    here, and writing it through would empty the editor's combobox on a bad minute upstream.
    """
    await given_a_catalogue(world)
    world.admin.offers = []

    written = await syncing(world)()

    assert written == type(written)(), "nothing written, and the report says so"
    assert listed(await SearchOffers(uow=world.uow)()) == [3749, 11104, 11112, 11234]


async def test_the_database_is_never_held_open_while_the_tracker_is_listing(
    world: FakeWorld,
) -> None:
    watched = FakeUnitOfWork(world.clock, world.uow.tables)
    tracker = WatchesTheDatabase(watched)
    tracker.offers = list(CATALOGUE)

    await SyncOfferCatalogue(admin=tracker, uow=watched, clock=world.clock)()

    assert not watched.open
    assert watched.blocks == 1, "one transaction, opened after the listing came back"


async def test_a_refreshed_catalogue_labels_the_rows_of_a_flow(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    world.admin.offers = [
        Offer(id=OLDEST, name="Oxys", state="active"),
        Offer(id=NEWEST, name="Miaflow", state="active"),
    ]
    await syncing(world)()

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert [None if row.offer is None else row.offer.name for row in flow.rows] == [
        "Oxys",
        "Miaflow",
    ]


async def test_a_tracker_this_service_cannot_reach_says_so_rather_than_emptying_anything(
    world: FakeWorld,
) -> None:
    await given_a_catalogue(world)
    failing = FakeKeitaroAdmin()
    failing.fail_on("list_offers", UpstreamUnavailableError("read timed out"))

    with pytest.raises(UpstreamUnavailableError):
        await SyncOfferCatalogue(admin=failing, uow=world.uow, clock=world.clock)()

    assert listed(await SearchOffers(uow=world.uow)()) == [3749, 11104, 11112, 11234]
