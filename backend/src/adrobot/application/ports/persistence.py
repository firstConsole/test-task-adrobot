"""This service's own tables, as one unit of work and six repositories.

Three decisions are spelled in the shapes below rather than in a rule somebody remembers.

*   **The transaction is the block.** `begin()` is the only way to reach a repository, and
    the object it yields lives only for the body of the `async with`. Leaving commits,
    raising rolls back; there is no `commit()` to forget and no `rollback()` to misplace. A
    push therefore enters it twice, with the tracker call between the two blocks, where a
    reader and a diff can see it.
*   **A flow is never addressed alone.** Every method taking a `KeitaroStreamId` takes the
    `CampaignId` beside it, and both go into the same SQL predicate — there is no unscoped
    variant to call, so a URL naming another campaign's flow finds nothing.
*   **Nothing here is an ORM object.** Every return is a frozen record, a domain type, an id
    or `None`, so nothing survives the block that could still lazy-load.

The records are declared here and not in `domain/`: they are the shape of our tables, not of
the tracker's world, and `domain/` already has `Stream` and `Campaign` for that. `id` always
names this service's identifier; a tracker identifier keeps the `keitaro_` prefix its column
has.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from adrobot.application.push import PushOutcome
from adrobot.domain.campaign import CampaignSetupStatus
from adrobot.domain.draft import DraftStatus
from adrobot.domain.ids import (
    CampaignId,
    DraftId,
    KeitaroCampaignId,
    KeitaroStreamId,
    OfferId,
    PushAttemptId,
)
from adrobot.domain.shares import OfferRow
from adrobot.domain.stream import StreamFilter, StreamSchema
from adrobot.domain.values import CountryCode

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from contextlib import AbstractAsyncContextManager

    from adrobot.domain.campaign import Campaign
    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.offer import Offer
    from adrobot.domain.stream import Stream, StreamOffer
    from adrobot.domain.values import Share


@dataclass(frozen=True, slots=True, kw_only=True)
class MirroredCampaign:
    """One `campaigns` row: what the tracker said, and the five facts it cannot answer."""

    id: CampaignId
    keitaro_campaign_id: KeitaroCampaignId
    alias: str
    name: str
    state: str
    setup_status: CampaignSetupStatus
    public_domain: str | None
    requested_country: str | None
    requested_offer_id: OfferId | None
    synced_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignSetup:
    """The half of a campaigns row `campaign_values` deliberately refuses to render."""

    status: CampaignSetupStatus
    public_domain: str | None = None
    requested_country: CountryCode | None = None
    requested_offer_id: OfferId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignCursor:
    """A keyset position, made of exactly the pair `ix_campaigns_created_at_id` is on.

    Named rather than left a bare tuple, so that an offset is unspellable here: `now()` is
    identical for every row written in one transaction, so `created_at` alone loses rows.
    """

    created_at: datetime
    id: CampaignId


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignPage:
    """One page, and the honest answer to whether there is another.

    "Is there another" is a fact of the query — it read one row more than it returns — and
    not of the rows, so the trick lives behind the port instead of in every caller.
    """

    campaigns: tuple[MirroredCampaign, ...]
    next_cursor: CampaignCursor | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MirroredStream:
    """One `streams` row — the reduced flow, and never a flow we could write back.

    `campaign_id` is OUR uuid where `Stream.campaign_id` is the tracker's: same word, two
    worlds, and mypy is what keeps them apart.
    """

    keitaro_stream_id: KeitaroStreamId
    campaign_id: CampaignId
    name: str
    schema: StreamSchema
    position: int | None
    filters: tuple[StreamFilter, ...]
    absent: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveDraft:
    """A draft still open or pushing, and never a closed one.

    It carries the identity `StreamDraft` refuses: that aggregate's docstring says identity
    belongs to the repository, and this is the repository saying it.
    """

    id: DraftId
    status: DraftStatus
    base_snapshot_hash: str
    rows: tuple[OfferRow, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamView:
    """One flow as the editor holds it: the mirror, its pins, and the live draft if there is one.

    The three together because that is what the screen is — handed them apart, a caller can
    render two and believe it has a flow. The pins are already joined into the rows.
    """

    stream: MirroredStream
    mirror_rows: tuple[OfferRow, ...]
    draft: LiveDraft | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PushAttempt:
    """The read side of the audit: how old an unfinished push is, and which request began it."""

    id: PushAttemptId
    outcome: PushOutcome
    started_at: datetime
    correlation_id: str


class CampaignRepository(ABC):
    """The campaigns table: the four columns the tracker owns, and the five only we know."""

    @abstractmethod
    async def add(self, campaign: Campaign, *, setup: CampaignSetup) -> MirroredCampaign:
        """Open a campaign locally and return the row as it now reads, our own id included.

        Two arguments because a later fetch overwrites the first and must never touch the
        second. Raises `CampaignAlreadyImportedError` when that tracker id is already here.
        """

    @abstractmethod
    async def get(self, campaign_id: CampaignId) -> MirroredCampaign:
        """Read one campaign by the id its URL carries, or raise `CampaignNotFoundError`.

        Raises rather than answering `None`: absence here is a 404, and every caller would
        otherwise repeat the same guard.
        """

    @abstractmethod
    async def by_keitaro_id(
        self, keitaro_campaign_id: KeitaroCampaignId
    ) -> MirroredCampaign | None:
        """Find the row already mirrored for a tracker campaign, or `None`.

        The one read that answers `None`: whether campaign 93212 is already imported is the
        question being asked, not a failure.
        """

    @abstractmethod
    async def page(
        self, *, after: CampaignCursor | None, query: str | None, limit: int
    ) -> CampaignPage:
        """Read one page of campaigns, newest first, continuing after `after`.

        Keyset and never an offset: a page read while a campaign is being created must
        neither repeat a row nor skip one.
        """

    @abstractmethod
    async def note_fetched(
        self, campaign_id: CampaignId, campaign: Campaign, *, at: datetime
    ) -> None:
        """Write back the four columns the tracker owns and move `synced_at` to `at`.

        `setup_status` and the three `requested_*` columns survive a fetch untouched, which
        is exactly what `campaign_values` refuses to render.
        """

    @abstractmethod
    async def set_setup_status(self, campaign_id: CampaignId, status: CampaignSetupStatus) -> None:
        """Record how far part 1 got, this being the only thing that may write that column."""


class StreamRepository(ABC):
    """The mirror of one campaign's flows and their rows, and the editor's read of both."""

    @abstractmethod
    async def views_for(self, campaign_id: CampaignId) -> tuple[StreamView, ...]:
        """Read every flow of one campaign as the editor draws it, in `position` order.

        A constant number of statements whatever the flow count, and tombstoned flows and
        rows come too: a flow deleted in Keitaro must be visible, not missing.
        """

    @abstractmethod
    async def lock(self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId) -> StreamView:
        """Take this flow for the rest of the transaction and read it whole.

        No other transaction writes this flow, its rows, its pins or its draft while the
        block is open, and there is no unlocked single-flow read to reach for instead.
        Raises `StreamNotFoundError`, which is also what another campaign's flow gets.
        """

    @abstractmethod
    async def upsert_campaign_streams(
        self, *, campaign_id: CampaignId, streams: tuple[Stream, ...], at: datetime
    ) -> None:
        """Leave this campaign's mirror holding exactly `streams`, their offer rows included.

        A flow or a row the tracker stopped returning is tombstoned at `at` and never
        deleted. `streams` is the whole flow list: a subset tombstones the rest.
        """

    @abstractmethod
    async def upsert_stream_offers(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offers: tuple[StreamOffer, ...],
        at: datetime,
    ) -> None:
        """Leave one flow's mirrored rows holding exactly `offers`, tombstoning the rest at `at`.

        Deliberately not the method above with one flow, which would tombstone every other
        flow of the campaign. It moves no `synced_at`: a push re-read one flow, and the
        campaign's others are exactly as stale as they were.
        """


class PinRepository(ABC):
    """The pinned shares, held in the mirror so that a pin survives both a push and a cancel."""

    @abstractmethod
    async def hold(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offer_id: OfferId,
        share: Share,
    ) -> None:
        """Pin one row at `share`, inserting the pin or moving it, and write nothing else.

        No draft is dirtied and no share is recomputed — which looks like a bug and is what
        the reference tool does. `Share` and not `int`: the one number a client influences.
        """

    @abstractmethod
    async def release(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId, offer_id: OfferId
    ) -> None:
        """Delete the pin row, presence being the pin — which is why a pin at 0 is still a pin.

        Silent when there is none: the button is idempotent, and a second press is not an
        error worth a status code.
        """


class DraftRepository(ABC):
    """One flow's staged edits: at most one live draft, closed softly and never deleted.

    Write-only. A draft is read back on the `StreamView` its flow arrives on, so no caller
    can hold a draft without the mirror it must be diffed against.
    """

    @abstractmethod
    async def open_for(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        rows: tuple[OfferRow, ...],
        base_snapshot_hash: str,
    ) -> LiveDraft:
        """Open the one live draft a flow may have, seeded with `rows` exactly as handed in.

        `base_snapshot_hash` is written here and nowhere else: recomputing it on an edit
        would re-baseline the conflict check into a no-op. Raises `DraftAlreadyOpenError`.
        """

    @abstractmethod
    async def replace_rows(self, draft_id: DraftId, rows: tuple[OfferRow, ...]) -> None:
        """Store the draft's rows exactly as `redistribute()` left them, ordinals included.

        Never renumbered: renumbering re-elects the row that takes the rounding remainder.
        `pinned_share` is dropped, the pin living in `offer_pins`.
        """

    @abstractmethod
    async def change_status(
        self, draft_id: DraftId, *, was: DraftStatus, becomes: DraftStatus
    ) -> None:
        """Move a draft between its four statuses, refusing one that is no longer at `was`.

        Raises `DraftStatusChangedError` naming the status found, so two pushes cannot both
        believe they won. Both states are keyword-only: they share a type, and a swapped
        pair would compile.
        """


class PushAttemptRepository(ABC):
    """The audit of one press of PUSH TO KT: opened before the tracker is called, closed after."""

    @abstractmethod
    async def start(
        self, *, draft_id: DraftId, desired: tuple[DesiredOffer, ...], correlation_id: str
    ) -> PushAttemptId:
        """Record, at `in_flight`, what phase 2 is about to ask the tracker to hold.

        Written in phase 1 so that a process dying mid-write still leaves a dated row of
        what it meant to do.
        """

    @abstractmethod
    async def settle(self, attempt_id: PushAttemptId, outcome: PushOutcome) -> None:
        """Close one attempt, refusing one that is no longer in flight.

        Raises `PushAttemptSettledError`, so a phase 3 returning after somebody took the
        push over fails loudly instead of rewriting history.
        """

    @abstractmethod
    async def latest_for(self, draft_id: DraftId) -> PushAttempt | None:
        """Read the newest attempt of one draft, for the push that finds one already pushing.

        There is no `pushing_since` column: this row, still in flight, is what dates a
        wedged push. The deadline stays in the use case, so this port needs no clock.
        """


class OfferCatalogueRepository(ABC):
    """The local mirror of `GET /offers`, which exists because that endpoint takes no parameters."""

    @abstractmethod
    async def search(self, query: str | None, *, limit: int) -> tuple[Offer, ...]:
        """Match the catalogue by id prefix and by name, `None` being SHOW ALL OFFERS.

        Tombstoned offers are left out: the tracker no longer has them to add. Ranked before
        it is cut, so `limit` bounds the answer rather than a clause the caller cannot see.
        """

    @abstractmethod
    async def by_ids(self, offer_ids: Collection[OfferId]) -> Mapping[OfferId, Offer]:
        """Label a screenful of flow rows in one query, tombstoned offers included.

        Allowed to answer short: an offer can reach a flow before it reaches our catalogue,
        and a missing one renders as `#11234 (not in catalogue)` rather than failing.
        """

    @abstractmethod
    async def upsert_catalogue(self, offers: tuple[Offer, ...], *, at: datetime) -> None:
        """Leave the catalogue holding exactly `offers`, tombstoning at `at` what was dropped.

        Always the whole catalogue, because `GET /offers` returns it whole or not at all: a
        partial sweep would tombstone the rest.
        """


@dataclass(frozen=True, slots=True, kw_only=True)
class Transaction:
    """The repositories of one open transaction, and the only place any of them exists.

    A frozen record and not an ABC: nothing about the bundle varies per implementation, and
    the one method somebody would add to an abstract one is the `commit()` that must not be
    here.
    """

    campaigns: CampaignRepository
    streams: StreamRepository
    pins: PinRepository
    drafts: DraftRepository
    pushes: PushAttemptRepository
    offers: OfferCatalogueRepository


class UnitOfWork(ABC):
    """One database transaction per block, and the only way to reach a repository at all."""

    @abstractmethod
    def begin(self) -> AbstractAsyncContextManager[Transaction]:
        """Open one short transaction: leaving the block commits, raising out of it rolls back.

        The one method in this module that is not awaitable, because the alternative reads
        `async with await uow.begin()`. A push enters it twice, once on each side of the
        tracker call.
        """
