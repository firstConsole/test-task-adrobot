"""The offer catalogue: searching the local copy, and refreshing it from the tracker.

There is a local copy at all because `GET /offers` takes **no query parameters** — no
search, no paging, no filter. The editor's combobox searches by id prefix and by name while
somebody types, so the only way to answer that is to hold the catalogue here and index it.
That is the whole reason this module exists, and it is worth a line in the README.

The search deliberately takes no `stream_id`, although PLAN-BACKEND §4 sketches one. It
would be there to leave out the offers a flow already carries, and two things make it not
worth a change to the repository: the client already holds that flow's rows, and adding one
twice is refused by name — `this stream already carries offer 11112` — rather than silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.dto import DEFAULT_OFFER_LIMIT, OfferCatalogueSync

if TYPE_CHECKING:
    from adrobot.application.ports.keitaro import KeitaroAdminPort
    from adrobot.application.ports.persistence import UnitOfWork
    from adrobot.application.ports.system import Clock
    from adrobot.domain.offer import Offer


class SearchOffers:
    """The combobox behind ADD, and SHOW ALL OFFERS, which is the same query with no text."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(
        self, *, query: str | None = None, limit: int = DEFAULT_OFFER_LIMIT
    ) -> tuple[Offer, ...]:
        """Match the local catalogue, `None` or empty text being SHOW ALL OFFERS.

        `limit` arrives already bounded: the API refuses an unusable one where it can still
        name the parameter it refused.
        """
        async with self._uow.begin() as transaction:
            return await transaction.offers.search(query, limit=limit)


class SyncOfferCatalogue:
    """Read the tracker's whole offer list and leave the local copy holding exactly it."""

    def __init__(self, *, admin: KeitaroAdminPort, uow: UnitOfWork, clock: Clock) -> None:
        self._admin = admin
        self._uow = uow
        self._clock = clock

    async def __call__(self) -> OfferCatalogueSync:
        """Refresh the catalogue, and leave it alone if the tracker listed nothing.

        The read is outside the transaction, as every read of the tracker is: this is one
        unpaginated response against a ten-second timeout, and a connection held for the
        length of it is a connection the rest of the service is not using.

        **An empty answer is not believed.** A tracker with no offers and a tracker that
        failed to list them look identical from here, and `upsert_catalogue(())` tombstones
        the whole catalogue — so believing it would empty the editor's combobox on a bad
        minute upstream, and the next good minute would fill it again with nothing to say
        that anything had happened. The cost of not believing it is a genuinely emptied
        tracker whose last offers stay listed here until one is created again, which is the
        cheaper mistake by a wide margin.
        """
        offers = await self._admin.list_offers()
        if not offers:
            return OfferCatalogueSync()
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            # The whole catalogue every time, because that is the only shape the tracker
            # will answer in: a partial sweep would tombstone everything it left out.
            await transaction.offers.upsert_catalogue(offers, at=at)
        return OfferCatalogueSync(offers=len(offers), synced_at=at)
