"""This service's own tables, in dictionaries, so that a scenario can be tested without one.

`tests/fakes.py` is the tracker; this is the database. They are separate files because they
are separate worlds — one models somebody else's API and the other models our schema — and
because a scenario usually wants both and never wants to read either.

What is modelled is what a use case can tell apart:

*   **The block is the transaction.** Leaving it keeps the writes, raising out of it loses
    them, and `blocks` counts how many were opened — which is how a test states that a push
    entered the database twice with the tracker call in between rather than once around it.
*   **`open` says whether one is held right now.** A fake tracker that asserts it is `False`
    is what turns "no transaction is ever open across an HTTP call" from a rule in AGENTS.md
    into a failing test.
*   **A campaign's `created_at` is the transaction's moment, not the row's.** PostgreSQL's
    `now()` is identical for every row written in one transaction, which is the whole reason
    the keyset cursor carries an id as well; a fake handing out distinct timestamps would
    make the pagination look safer than it is.
*   **Tombstones, not deletes.** A flow or a row the tracker stopped returning is marked
    absent and stays readable, which is the behaviour the editor's grey BRING BACK rows
    depend on.

What is not modelled is SQL: `lock` takes nothing, because one process and one event loop
have nothing to take it from. The locking itself is held to PostgreSQL in
`tests/infrastructure/test_db_editor.py`, which is where it can be.

The four repositories that raise are the ones the editor uses, and stage 7 fills them in
when it has a scenario to hold them to.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn, override
from uuid import uuid4

from adrobot.application.errors import (
    CampaignAlreadyImportedError,
    CampaignNotFoundError,
    StreamNotFoundError,
)
from adrobot.application.ports.persistence import (
    CampaignCursor,
    CampaignPage,
    CampaignRepository,
    DraftRepository,
    LiveDraft,
    MirroredCampaign,
    MirroredStream,
    OfferCatalogueRepository,
    PinRepository,
    PushAttempt,
    PushAttemptRepository,
    StreamRepository,
    StreamView,
    Transaction,
    UnitOfWork,
)
from adrobot.domain.draft import to_kernel_rows
from adrobot.domain.ids import (
    CampaignId,
    KeitaroCampaignId,
    KeitaroStreamId,
    OfferId,
    PushAttemptId,
)
from adrobot.domain.shares import OfferRow
from adrobot.domain.stream import StreamOffer
from adrobot.domain.values import OfferState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping
    from contextlib import AbstractAsyncContextManager

    from adrobot.application.ports.persistence import CampaignSetup
    from adrobot.application.ports.system import Clock
    from adrobot.application.push import PushOutcome
    from adrobot.domain.campaign import Campaign, CampaignSetupStatus
    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.draft import DraftStatus
    from adrobot.domain.ids import DraftId
    from adrobot.domain.offer import Offer
    from adrobot.domain.stream import Stream
    from adrobot.domain.values import Share

_OLDEST = datetime.min.replace(tzinfo=UTC)


def _not_yet(what: str) -> NoReturn:
    message = f"the in-memory {what} is written at stage 7, with the scenario that needs it"
    raise NotImplementedError(message)


@dataclass(frozen=True, slots=True, kw_only=True)
class MirroredRow:
    """One mirrored offer row: what the tracker said, and whether it has since stopped saying it."""

    offer: StreamOffer
    absent: bool = False


@dataclass
class Tables:
    """Every table these fakes hold, in one object, so that a block can snapshot the lot."""

    campaigns: dict[CampaignId, MirroredCampaign] = field(default_factory=dict)
    streams: dict[KeitaroStreamId, MirroredStream] = field(default_factory=dict)
    rows: dict[KeitaroStreamId, dict[OfferId, MirroredRow]] = field(default_factory=dict)

    def copy(self) -> Tables:
        """Take the snapshot a rollback restores. Every value is frozen, so this is shallow."""
        return Tables(
            campaigns=dict(self.campaigns),
            streams=dict(self.streams),
            rows={stream: dict(held) for stream, held in self.rows.items()},
        )


class FakeCampaigns(CampaignRepository):
    """The campaigns table, including the one column a fetch is forbidden to touch."""

    def __init__(self, tables: Tables, at: datetime) -> None:
        self._tables = tables
        self._at = at

    @override
    async def add(self, campaign: Campaign, *, setup: CampaignSetup) -> MirroredCampaign:
        if self._by_tracker_id(campaign.id) is not None:
            raise CampaignAlreadyImportedError(campaign.id)
        row = MirroredCampaign(
            id=CampaignId(uuid4()),
            keitaro_campaign_id=campaign.id,
            alias=campaign.alias,
            name=campaign.name,
            state=campaign.state,
            setup_status=setup.status,
            public_domain=setup.public_domain,
            requested_country=setup.requested_country,
            requested_offer_id=setup.requested_offer_id,
            synced_at=None,
            created_at=self._at,
        )
        self._tables.campaigns[row.id] = row
        return row

    @override
    async def get(self, campaign_id: CampaignId) -> MirroredCampaign:
        found = self._tables.campaigns.get(campaign_id)
        if found is None:
            raise CampaignNotFoundError(campaign_id)
        return found

    @override
    async def by_keitaro_id(
        self, keitaro_campaign_id: KeitaroCampaignId
    ) -> MirroredCampaign | None:
        return self._by_tracker_id(keitaro_campaign_id)

    @override
    async def page(
        self, *, after: CampaignCursor | None, query: str | None, limit: int
    ) -> CampaignPage:
        found = sorted(
            self._tables.campaigns.values(),
            key=lambda row: (row.created_at, row.id),
            reverse=True,
        )
        if query:
            wanted = query.casefold()
            found = [
                row
                for row in found
                if wanted in row.name.casefold() or wanted in row.alias.casefold()
            ]
        if after is not None:
            found = [
                row for row in found if (row.created_at, row.id) < (after.created_at, after.id)
            ]
        page = tuple(found[:limit])
        return CampaignPage(
            campaigns=page,
            next_cursor=CampaignCursor(created_at=page[-1].created_at, id=page[-1].id)
            if page and len(found) > limit
            else None,
        )

    @override
    async def note_fetched(
        self, campaign_id: CampaignId, campaign: Campaign, *, at: datetime
    ) -> None:
        stored = await self.get(campaign_id)
        # The four columns the tracker owns, and never `setup_status` or the requested
        # values: a fetch that reset those would undo the record of a half-built campaign.
        self._tables.campaigns[campaign_id] = replace(
            stored,
            keitaro_campaign_id=campaign.id,
            alias=campaign.alias,
            name=campaign.name,
            state=campaign.state,
            synced_at=at,
        )

    @override
    async def set_setup_status(self, campaign_id: CampaignId, status: CampaignSetupStatus) -> None:
        stored = await self.get(campaign_id)
        self._tables.campaigns[campaign_id] = replace(stored, setup_status=status)

    def _by_tracker_id(self, keitaro_campaign_id: KeitaroCampaignId) -> MirroredCampaign | None:
        return next(
            (
                row
                for row in self._tables.campaigns.values()
                if row.keitaro_campaign_id == keitaro_campaign_id
            ),
            None,
        )


class FakeStreams(StreamRepository):
    """One campaign's flows and their rows, tombstoned rather than deleted."""

    def __init__(self, tables: Tables) -> None:
        self._tables = tables

    @override
    async def views_for(self, campaign_id: CampaignId) -> tuple[StreamView, ...]:
        flows = sorted(
            (row for row in self._tables.streams.values() if row.campaign_id == campaign_id),
            # `position` ascending with the unset ones last, which is what ASC does to NULLs
            # in PostgreSQL and what `sorted` refuses to do to `None` without being told.
            key=lambda row: (row.position is None, row.position or 0, row.keitaro_stream_id),
        )
        return tuple(self._view(flow) for flow in flows)

    @override
    async def lock(self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId) -> StreamView:
        flow = self._tables.streams.get(stream_id)
        if flow is None or flow.campaign_id != campaign_id:
            raise StreamNotFoundError(stream_id)
        return self._view(flow)

    @override
    async def upsert_campaign_streams(
        self, *, campaign_id: CampaignId, streams: tuple[Stream, ...], at: datetime
    ) -> None:
        for stream in streams:
            self._tables.streams[stream.id] = MirroredStream(
                keitaro_stream_id=stream.id,
                campaign_id=campaign_id,
                name=stream.name,
                schema=stream.schema,
                position=stream.position,
                filters=stream.filters,
                absent=False,
            )
            self._write_rows(stream.id, stream.offers)
        written = {stream.id for stream in streams}
        for stream_id, flow in self._tables.streams.items():
            if flow.campaign_id == campaign_id and stream_id not in written:
                self._tables.streams[stream_id] = replace(flow, absent=True)

    @override
    async def upsert_stream_offers(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offers: tuple[StreamOffer, ...],
        at: datetime,
    ) -> None:
        await self.lock(campaign_id=campaign_id, stream_id=stream_id)
        self._write_rows(stream_id, offers)

    def _write_rows(self, stream_id: KeitaroStreamId, offers: tuple[StreamOffer, ...]) -> None:
        held = self._tables.rows.setdefault(stream_id, {})
        for offer in offers:
            held[offer.offer_id] = MirroredRow(offer=offer)
        written = {offer.offer_id for offer in offers}
        for offer_id, row in held.items():
            if offer_id not in written:
                held[offer_id] = replace(row, absent=True)

    def _view(self, flow: MirroredStream) -> StreamView:
        return StreamView(
            stream=flow,
            mirror_rows=_kernel_rows(self._tables.rows.get(flow.keitaro_stream_id, {})),
            draft=None,
        )


def _kernel_rows(held: Mapping[OfferId, MirroredRow]) -> tuple[OfferRow, ...]:
    """Number the rows the way the tie-break reads them: oldest first, ordinals from one.

    The same order the SQL mapper spells, for the same reason — the last ordinal is the row
    that takes the rounding remainder, so a fake that ordered these differently would agree
    with the real repository on every share but the one the video is about.
    """
    ordered = sorted(
        held.values(),
        key=lambda row: (
            row.offer.created_at or _OLDEST,
            row.offer.row_id or 0,
            row.offer.offer_id,
        ),
    )
    return to_kernel_rows(
        (
            OfferRow(
                offer_id=row.offer.offer_id,
                seq=ordinal,
                activated_at=ordinal,
                share=row.offer.share,
                removed=row.absent or row.offer.state != OfferState.ACTIVE.value,
            )
            for ordinal, row in enumerate(ordered, start=1)
        ),
        {},
    )


class FakePins(PinRepository):
    """Stage 7."""

    @override
    async def hold(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offer_id: OfferId,
        share: Share,
    ) -> None:
        _not_yet("pins")

    @override
    async def release(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId, offer_id: OfferId
    ) -> None:
        _not_yet("pins")


class FakeDrafts(DraftRepository):
    """Stage 7."""

    @override
    async def open_for(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        rows: tuple[OfferRow, ...],
        base_snapshot_hash: str,
    ) -> LiveDraft:
        _not_yet("drafts")

    @override
    async def replace_rows(self, draft_id: DraftId, rows: tuple[OfferRow, ...]) -> None:
        _not_yet("drafts")

    @override
    async def change_status(
        self, draft_id: DraftId, *, was: DraftStatus, becomes: DraftStatus
    ) -> None:
        _not_yet("drafts")


class FakePushAttempts(PushAttemptRepository):
    """Stage 7."""

    @override
    async def start(
        self, *, draft_id: DraftId, desired: tuple[DesiredOffer, ...], correlation_id: str
    ) -> PushAttemptId:
        _not_yet("push attempts")

    @override
    async def settle(self, attempt_id: PushAttemptId, outcome: PushOutcome) -> None:
        _not_yet("push attempts")

    @override
    async def latest_for(self, draft_id: DraftId) -> PushAttempt | None:
        _not_yet("push attempts")


class FakeOfferCatalogue(OfferCatalogueRepository):
    """Stage 7."""

    @override
    async def search(self, query: str | None, *, limit: int) -> tuple[Offer, ...]:
        _not_yet("offer catalogue")

    @override
    async def by_ids(self, offer_ids: Collection[OfferId]) -> Mapping[OfferId, Offer]:
        _not_yet("offer catalogue")

    @override
    async def upsert_catalogue(self, offers: tuple[Offer, ...], *, at: datetime) -> None:
        _not_yet("offer catalogue")


class FakeUnitOfWork(UnitOfWork):
    """The same block the SQL unit of work hands out, over dictionaries."""

    def __init__(self, clock: Clock, tables: Tables | None = None) -> None:
        self.tables = tables if tables is not None else Tables()
        self.clock = clock
        # How many blocks have been opened, and whether one is open right now. Both are
        # assertions a scenario makes about itself: two short transactions around a tracker
        # call, and never one transaction around it.
        self.blocks = 0
        self.open = False

    @override
    def begin(self) -> AbstractAsyncContextManager[Transaction]:
        return self._block()

    @asynccontextmanager
    async def _block(self) -> AsyncIterator[Transaction]:
        if self.open:
            message = "a transaction is already open: these fakes do not nest"
            raise AssertionError(message)
        self.blocks += 1
        self.open = True
        snapshot = self.tables.copy()
        try:
            yield Transaction(
                campaigns=FakeCampaigns(self.tables, self.clock.now()),
                streams=FakeStreams(self.tables),
                pins=FakePins(),
                drafts=FakeDrafts(),
                pushes=FakePushAttempts(),
                offers=FakeOfferCatalogue(),
            )
        except BaseException:
            self.tables = snapshot
            raise
        finally:
            self.open = False
