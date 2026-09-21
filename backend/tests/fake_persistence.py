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
*   **The pin is joined in on the way out, never stored on a draft row.** It lives in its
    own table, as it does in PostgreSQL, because it has to survive both a push and a cancel
    — and a second home for it on the draft is one that could fall out of step.

What is not modelled is SQL: `lock` takes nothing, because one process and one event loop
have nothing to take it from. The locking itself is held to PostgreSQL in
`tests/infrastructure/test_db_editor.py`, which is where it can be. The uniqueness of a
live draft is modelled, because it is a rule a scenario meets rather than a race.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override
from uuid import uuid4

from adrobot.application.errors import (
    CampaignAlreadyImportedError,
    CampaignNotFoundError,
    DraftAlreadyOpenError,
    DraftStatusChangedError,
    PushAttemptSettledError,
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
from adrobot.application.push import PushOutcome
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.draft import LIVE_DRAFT_STATUSES, DraftStatus, to_kernel_rows
from adrobot.domain.ids import (
    CampaignId,
    DraftId,
    KeitaroCampaignId,
    KeitaroStreamId,
    OfferId,
    PushAttemptId,
)
from adrobot.domain.offer import Offer
from adrobot.domain.shares import OfferRow
from adrobot.domain.stream import StreamOffer
from adrobot.domain.values import OfferState
from tests.fakes import FakeKeitaroAdmin, FakeKeitaroReports

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Iterable, Mapping
    from contextlib import AbstractAsyncContextManager
    from datetime import date

    from adrobot.application.ports.persistence import CampaignSetup
    from adrobot.application.ports.system import Clock
    from adrobot.domain.campaign import Campaign, CampaignSetupStatus
    from adrobot.domain.offer import OfferStats
    from adrobot.domain.stream import Stream
    from adrobot.domain.values import Share

_OLDEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class MirroredRow:
    """One mirrored offer row: what the tracker said, and whether it has since stopped saying it."""

    offer: StreamOffer
    absent: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class StagedDraft:
    """One `stream_drafts` row with its `stream_draft_rows` inside it.

    The rows carry no `pinned_share`, exactly as the column list does not: `draft_row_values`
    drops it on the way in and `to_draft_rows` rejoins it on the way out.
    """

    id: DraftId
    stream_id: KeitaroStreamId
    status: DraftStatus
    base_snapshot_hash: str
    rows: tuple[OfferRow, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordedPush:
    """One `push_attempts` row.

    `seq` stands in for the uuid the real table breaks a tie on: `now()` is one instant for a
    whole transaction, so two attempts written in one block share `started_at` exactly, and
    `latest_for` still has to answer the same way twice.
    """

    id: PushAttemptId
    draft_id: DraftId
    outcome: PushOutcome
    desired: tuple[DesiredOffer, ...]
    correlation_id: str
    started_at: datetime
    seq: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CataloguedOffer:
    """One `offers` row, and whether the tracker has stopped listing it."""

    offer: Offer
    absent: bool = False


@dataclass
class Tables:
    """Every table these fakes hold, in one object, so that a block can snapshot the lot."""

    campaigns: dict[CampaignId, MirroredCampaign] = field(default_factory=dict)
    streams: dict[KeitaroStreamId, MirroredStream] = field(default_factory=dict)
    rows: dict[KeitaroStreamId, dict[OfferId, MirroredRow]] = field(default_factory=dict)
    pins: dict[KeitaroStreamId, dict[OfferId, int]] = field(default_factory=dict)
    drafts: dict[DraftId, StagedDraft] = field(default_factory=dict)
    pushes: dict[PushAttemptId, RecordedPush] = field(default_factory=dict)
    catalogue: dict[OfferId, CataloguedOffer] = field(default_factory=dict)

    def copy(self) -> Tables:
        """Take the snapshot a rollback restores. Every value is frozen, so this is shallow."""
        return Tables(
            campaigns=dict(self.campaigns),
            streams=dict(self.streams),
            rows={stream: dict(held) for stream, held in self.rows.items()},
            pins={stream: dict(held) for stream, held in self.pins.items()},
            drafts=dict(self.drafts),
            pushes=dict(self.pushes),
            catalogue=dict(self.catalogue),
        )


def _flow_of(
    tables: Tables, *, campaign_id: CampaignId, stream_id: KeitaroStreamId
) -> MirroredStream | None:
    """Find this campaign's flow, `None` covering both "no such flow" and "not yours".

    The one scope predicate these fakes have, spelled once for the same reason `_flow()` is
    spelled once in the real repositories: a row of `stream_offers`, `offer_pins` or
    `stream_drafts` names no campaign of its own, so leaving the campaign out is a lookup
    that answers about somebody else's flow.
    """
    flow = tables.streams.get(stream_id)
    return flow if flow is not None and flow.campaign_id == campaign_id else None


def _live_draft_of(tables: Tables, stream_id: KeitaroStreamId) -> StagedDraft | None:
    """The one draft a flow may have open or pushing, which the partial unique index enforces."""
    return next(
        (
            draft
            for draft in tables.drafts.values()
            if draft.stream_id == stream_id and draft.status in LIVE_DRAFT_STATUSES
        ),
        None,
    )


def _stored(rows: Iterable[OfferRow]) -> tuple[OfferRow, ...]:
    """Drop what the draft tables have no column for, which is the pin and only the pin."""
    return tuple(replace(row, pinned_share=None) for row in rows)


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
        flow = _flow_of(self._tables, campaign_id=campaign_id, stream_id=stream_id)
        if flow is None:
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
        """Assemble one flow, reading its pins once and joining them into both sides.

        Both sides out of the same mapping, which is what makes a pinned row read the same
        in the mirror and in the draft.
        """
        stream_id = flow.keitaro_stream_id
        pins = self._tables.pins.get(stream_id, {})
        staged = _live_draft_of(self._tables, stream_id)
        return StreamView(
            stream=flow,
            mirror_rows=_kernel_rows(self._tables.rows.get(stream_id, {}), pins),
            draft=None if staged is None else _live_draft(staged, pins),
        )


def _kernel_rows(
    held: Mapping[OfferId, MirroredRow], pins: Mapping[OfferId, int]
) -> tuple[OfferRow, ...]:
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
                # 0 for a removed row, as the SQL mapper does and for the reason given
                # there: `share` is what the row receives, and a row out of the rotation
                # receives nothing.
                share=0 if _is_silenced(row) else row.offer.share,
                removed=_is_silenced(row),
            )
            for ordinal, row in enumerate(ordered, start=1)
        ),
        pins,
    )


def _is_silenced(row: MirroredRow) -> bool:
    """Fold the two columns that can silence a row into the one distinction a screen draws.

    Absent is a row the tracker stopped returning; a non-active state is one a push left
    behind disabled. Both render grey, at 0%, with BRING BACK live.
    """
    return row.absent or row.offer.state != OfferState.ACTIVE.value


def _live_draft(staged: StagedDraft, pins: Mapping[OfferId, int]) -> LiveDraft:
    """Read a draft back as the last recalculation left it, with the pins rejoined.

    The shares are carried and the ordinals are never renumbered: renumbering would re-elect
    the row that takes the rounding remainder.
    """
    return LiveDraft(
        id=staged.id,
        status=staged.status,
        base_snapshot_hash=staged.base_snapshot_hash,
        rows=to_kernel_rows(sorted(staged.rows, key=lambda row: row.seq), pins),
    )


class FakePins(PinRepository):
    """The pinned shares, held in the mirror so a pin survives both a push and a cancel."""

    def __init__(self, tables: Tables) -> None:
        self._tables = tables

    @override
    async def hold(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offer_id: OfferId,
        share: Share,
    ) -> None:
        if _flow_of(self._tables, campaign_id=campaign_id, stream_id=stream_id) is None:
            raise StreamNotFoundError(stream_id)
        # `int()`: a `Share` would keep its subclass through the dictionary and read back as
        # a validated type the column cannot hand out, which is a difference a test would
        # eventually assert on.
        self._tables.pins.setdefault(stream_id, {})[offer_id] = int(share)

    @override
    async def release(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId, offer_id: OfferId
    ) -> None:
        """Silent on a flow that has no such pin, and on one that is not this campaign's.

        The second half is the scope, not forgiveness: the real DELETE carries the campaign
        in its own WHERE, so another campaign's pin is a row the statement never reaches.
        """
        if _flow_of(self._tables, campaign_id=campaign_id, stream_id=stream_id) is None:
            return
        self._tables.pins.get(stream_id, {}).pop(offer_id, None)


class FakeDrafts(DraftRepository):
    """One flow's staged edits: at most one live draft, closed softly and never deleted."""

    def __init__(self, tables: Tables) -> None:
        self._tables = tables

    @override
    async def open_for(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        rows: tuple[OfferRow, ...],
        base_snapshot_hash: str,
    ) -> LiveDraft:
        """Refuse an unknown flow before a taken one, which is the order the real insert answers in.

        There the INSERT ... SELECT writes nothing in either case and the follow-up question
        asks about the flow first; here the two checks are plain, and they are in that order
        so that a URL naming another campaign's flow is a 404 rather than a 409 telling the
        caller that somebody else's flow is busy.
        """
        if _flow_of(self._tables, campaign_id=campaign_id, stream_id=stream_id) is None:
            raise StreamNotFoundError(stream_id)
        if _live_draft_of(self._tables, stream_id) is not None:
            raise DraftAlreadyOpenError(stream_id)
        staged = StagedDraft(
            id=DraftId(uuid4()),
            stream_id=stream_id,
            status=DraftStatus.OPEN,
            base_snapshot_hash=base_snapshot_hash,
            rows=_stored(rows),
        )
        self._tables.drafts[staged.id] = staged
        return LiveDraft(
            id=staged.id, status=staged.status, base_snapshot_hash=base_snapshot_hash, rows=rows
        )

    @override
    async def replace_rows(self, draft_id: DraftId, rows: tuple[OfferRow, ...]) -> None:
        self._tables.drafts[draft_id] = replace(self._held(draft_id), rows=_stored(rows))

    @override
    async def change_status(
        self, draft_id: DraftId, *, was: DraftStatus, becomes: DraftStatus
    ) -> None:
        staged = self._held(draft_id)
        if staged.status is not was:
            raise DraftStatusChangedError(was, staged.status)
        self._tables.drafts[draft_id] = replace(staged, status=becomes)

    def _held(self, draft_id: DraftId) -> StagedDraft:
        """A `KeyError` on a draft nobody opened, matching the real repository's own `one()`.

        Nothing deletes a draft and no URL carries a draft id, so a missing one is this
        service's own bug and deserves the 500 rather than a refusal a caller could act on.
        """
        return self._tables.drafts[draft_id]


class FakePushAttempts(PushAttemptRepository):
    """The audit of one press of PUSH TO KT: opened before the tracker is called, closed after."""

    def __init__(self, tables: Tables, at: datetime) -> None:
        self._tables = tables
        self._at = at

    @override
    async def start(
        self, *, draft_id: DraftId, desired: tuple[DesiredOffer, ...], correlation_id: str
    ) -> PushAttemptId:
        attempt = RecordedPush(
            id=PushAttemptId(uuid4()),
            draft_id=draft_id,
            outcome=PushOutcome.IN_FLIGHT,
            desired=desired,
            correlation_id=correlation_id,
            started_at=self._at,
            seq=len(self._tables.pushes) + 1,
        )
        self._tables.pushes[attempt.id] = attempt
        return attempt.id

    @override
    async def settle(self, attempt_id: PushAttemptId, outcome: PushOutcome) -> None:
        recorded = self._tables.pushes[attempt_id]
        if recorded.outcome is not PushOutcome.IN_FLIGHT:
            raise PushAttemptSettledError(attempt_id)
        self._tables.pushes[attempt_id] = replace(recorded, outcome=outcome)

    @override
    async def latest_for(self, draft_id: DraftId) -> PushAttempt | None:
        mine = [row for row in self._tables.pushes.values() if row.draft_id == draft_id]
        if not mine:
            return None
        newest = max(mine, key=lambda row: (row.started_at, row.seq))
        return PushAttempt(
            id=newest.id,
            outcome=newest.outcome,
            started_at=newest.started_at,
            correlation_id=newest.correlation_id,
        )


class FakeOfferCatalogue(OfferCatalogueRepository):
    """The local mirror of `GET /offers`, which exists because that endpoint takes no parameters."""

    def __init__(self, tables: Tables) -> None:
        self._tables = tables

    @override
    async def search(self, query: str | None, *, limit: int) -> tuple[Offer, ...]:
        """The id arm ahead of the name arm, which is what `ORDER BY prefix DESC, id ASC` does.

        Typing 11104 must not bury offer 11104 under an offer merely named `11104 Special`.
        The SQL escapes `%` and `_` before they reach LIKE, so both sides match the literal
        text a buyer typed and `startswith` is the same predicate.
        """
        present = [row.offer for row in self._tables.catalogue.values() if not row.absent]
        typed = "" if query is None else query.strip()
        if not typed:
            return tuple(sorted(present, key=lambda offer: offer.id)[:limit])
        folded = typed.casefold()
        matched = [
            offer
            for offer in present
            if str(offer.id).startswith(typed) or folded in offer.name.casefold()
        ]
        matched.sort(key=lambda offer: (not str(offer.id).startswith(typed), offer.id))
        return tuple(matched[:limit])

    @override
    async def by_ids(self, offer_ids: Collection[OfferId]) -> Mapping[OfferId, Offer]:
        """Tombstoned offers included, deliberately unlike `search`: a row can stay in a flow
        after the tracker drops the offer, and the label has to survive."""
        return {
            offer_id: self._tables.catalogue[offer_id].offer
            for offer_id in set(offer_ids)
            if offer_id in self._tables.catalogue
        }

    @override
    async def upsert_catalogue(self, offers: tuple[Offer, ...], *, at: datetime) -> None:
        for offer in offers:
            self._tables.catalogue[offer.id] = CataloguedOffer(offer=offer)
        written = {offer.id for offer in offers}
        for offer_id, held in self._tables.catalogue.items():
            if offer_id not in written and not held.absent:
                self._tables.catalogue[offer_id] = replace(held, absent=True)


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
        # Read once for the whole block, as `now()` is: two rows written either side of a
        # tracker call that failed must not be datable to different moments.
        at = self.clock.now()
        try:
            yield Transaction(
                campaigns=FakeCampaigns(self.tables, at),
                streams=FakeStreams(self.tables),
                pins=FakePins(self.tables),
                drafts=FakeDrafts(self.tables),
                pushes=FakePushAttempts(self.tables, at),
                offers=FakeOfferCatalogue(self.tables),
            )
        except BaseException:
            self.tables = snapshot
            raise
        finally:
            self.open = False


class WatchesTheDatabase(FakeKeitaroAdmin):
    """A tracker that fails the test if a transaction is open when it is called.

    Here rather than beside the other tracker fakes because the rule is about this file: a
    connection held for the length of a network call empties the pool the moment the tracker
    is slow, and nothing about the code that does it looks wrong. Every port method goes
    through `_called`, so one override covers the ones written later too.
    """

    def __init__(self, uow: FakeUnitOfWork) -> None:
        super().__init__()
        self._uow = uow

    @override
    def _called(self, method: str) -> None:
        assert not self._uow.open, f"{method} was called with a database transaction open"
        super()._called(method)


class ReportsThatWatchTheDatabase(FakeKeitaroReports):
    """The same rule for the report builder, which is where it costs the most.

    A statistics screen is the one thing on this API that is asked for again every few
    seconds, and `/report/build` is the slowest endpoint the tracker has. A connection held
    across those two calls is therefore not a rare pathology — it is the pool emptying under
    exactly the load the column was built for.
    """

    def __init__(
        self,
        uow: FakeUnitOfWork,
        *,
        clicks: Mapping[int, int] = {},
        stats: Mapping[int, OfferStats] = {},
    ) -> None:
        super().__init__(clicks=clicks, stats=stats)
        self._uow = uow

    @override
    def _called(
        self, method: str, campaign_id: KeitaroCampaignId, day: date, timezone: str
    ) -> None:
        assert not self._uow.open, f"{method} was called with a database transaction open"
        super()._called(method, campaign_id, day, timezone)
