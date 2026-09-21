"""The six repositories over SQLAlchemy, each holding one session it does not own.

Three decisions are worth reading before the code.

*   **Nothing here commits, and nothing here catches an `IntegrityError`.** Every write that
    can lose a race carries its own `ON CONFLICT` clause, so the loser reads an empty
    `RETURNING` instead of an exception — measured, a caught integrity error leaves the
    transaction aborted and the loser cannot ask the follow-up question its own error needs.
*   **The campaign scope is inside the statement**, never in a guard before it: `_flow()` for
    the single-flow methods and a VALUES-to-`streams` join for the mirrored rows. A row of
    `stream_offers`, `offer_pins` or `stream_drafts` names no campaign of its own.
*   **Nothing returns an ORM object.** Every method answers with a frozen record, a domain
    type, an id or `None`, built before it returns, so nothing survives the block that could
    still lazy-load.
"""

from __future__ import annotations

from itertools import batched
from typing import TYPE_CHECKING, Any, Final, NoReturn, override
from uuid import uuid4

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Text,
    case,
    cast,
    column,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

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
)
from adrobot.application.push import PushOutcome
from adrobot.domain.draft import LIVE_DRAFT_STATUSES, DraftStatus
from adrobot.domain.ids import CampaignId, DraftId, PushAttemptId
from adrobot.domain.stream import StreamSchema
from adrobot.infrastructure.db.mappers import (
    campaign_values,
    desired_state_json,
    draft_row_values,
    offer_values,
    stream_offer_values,
    stream_values,
    to_draft_rows,
    to_filters,
    to_mirror_rows,
    to_offer,
    to_pins,
)
from adrobot.infrastructure.db.models import (
    DbCampaign,
    DbOffer,
    DbOfferPin,
    DbPushAttempt,
    DbStream,
    DbStreamDraft,
    DbStreamDraftRow,
    DbStreamOffer,
    MirrorState,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Mapping, Sequence
    from datetime import datetime

    from sqlalchemy import ColumnElement, Select, SQLColumnExpression
    from sqlalchemy.dialects.postgresql import Insert
    from sqlalchemy.ext.asyncio import AsyncSession

    from adrobot.application.ports.persistence import CampaignSetup
    from adrobot.domain.campaign import Campaign, CampaignSetupStatus
    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
    from adrobot.domain.offer import Offer
    from adrobot.domain.shares import OfferRow
    from adrobot.domain.stream import Stream, StreamOffer
    from adrobot.domain.values import Share

MAX_BIND_PARAMETERS: Final = 32_767
"""What one statement may carry, which asyncpg reports as `the number of query arguments
cannot exceed 32767`. A 5000-offer catalogue is 45_000 parameters in one INSERT."""

ROWS_PER_STATEMENT: Final = 500
"""Rows in one multi-row write. Well under the ceiling above, because a chunk is also how
many rows one statement locks at once."""

_FRESH: Final[dict[str, Any]] = {"populate_existing": True}
"""Overwrite whatever the session already holds, on every read. The unit of work keeps
`expire_on_commit=False` and a push runs two transactions on one session with an HTTP call
between them; without this the second block is answered from the first block's rows."""

_LIKE_ESCAPE: Final = "\\"

_LIVE_DRAFT: Final = text(
    "status IN ({})".format(", ".join(f"'{status.value}'" for status in LIVE_DRAFT_STATUSES))
)
"""The partial index's own predicate, spelled from the domain's list but rendered as
literals. Measured: `status.in_(LIVE_DRAFT_STATUSES)` sends `status IN ($1, $2)`, which
PostgreSQL infers the arbiter from only while it keeps a custom plan and which fails with
"there is no unique or exclusion constraint matching the ON CONFLICT specification" the
moment it switches to a generic one."""


def _escaped(query: str) -> str:
    """Neutralise what a buyer typed: `%`, `_` and the escape character are data, not pattern.

    The escape character is escaped first, or escaping the two wildcards would produce
    escape characters that then escape each other.
    """
    for character in (_LIKE_ESCAPE, "%", "_"):
        query = query.replace(character, _LIKE_ESCAPE + character)
    return query


def _dropped(*carried: tuple[SQLColumnExpression[Any], Sequence[int]]) -> ColumnElement[bool]:
    """Match every mirrored row whose key the payload did not carry, through an anti-join.

    One array parameter per key column, so the statement text is the same for a payload of
    two rows and of fifty thousand — and `NOT EXISTS (SELECT ... FROM unnest($1))` rather
    than `<> ALL($1)`, which is the whole reason this is a function: measured at 50 000
    offers, the anti-join is a Hash Anti Join at 16 ms where the array comparison is a
    per-row scan of the array at 2166 ms.

    The caller passes the arrays already paired — they are unnested side by side, so sorting
    one alone would pair every flow with another flow's offers.
    """
    kept = (
        func.unnest(*(literal(list(ids), ARRAY(BigInteger)) for _, ids in carried))
        .table_valued(*(column(f"key{n}", BigInteger) for n, _ in enumerate(carried)))
        .render_derived(name="kept")
    )
    return ~(
        select(1)
        .select_from(kept)
        .where(*(kept.c[f"key{n}"] == held for n, (held, _) in enumerate(carried)))
        .exists()
    )


def _in_chunks(
    rows: Sequence[Mapping[str, Any]], columns: int
) -> Iterator[tuple[Mapping[str, Any], ...]]:
    """Cut a payload into statements small enough to carry, in the order the caller sorted it.

    The caller sorts by conflict key first: a multi-row upsert locks rows as ON CONFLICT
    reaches them, so two syncs whose payloads are ordered differently deadlock.
    """
    size = max(1, min(ROWS_PER_STATEMENT, MAX_BIND_PARAMETERS // columns))
    return batched(rows, size, strict=False)


def _synced(values_: Mapping[str, Any], *, keys: Collection[str]) -> tuple[str, ...]:
    """Name the columns a sync owns, read off the mapper's own keys minus the conflict key."""
    return tuple(name for name in values_ if name not in keys)


def _overwrite(statement: Insert, names: Sequence[str], *, at: datetime) -> dict[str, Any]:
    """Render what ON CONFLICT writes, `updated_at` by hand where the table has one.

    `on_conflict_do_update` does not fire a column's `onupdate` — measured, the compiled
    clause is exactly what `set_` says and nothing more.
    """
    written: dict[str, Any] = {name: statement.excluded[name] for name in names}
    if "updated_at" in statement.table.columns:
        written["updated_at"] = at
    return written


def _changed(statement: Insert, names: Sequence[str]) -> ColumnElement[bool]:
    """Guard the DO UPDATE so that a row the tracker returned unchanged is not rewritten.

    Keeps `updated_at` meaning "when this row last changed" rather than "when we last
    looked", which is `synced_at`'s job, and spares a no-op sync its dead tuples.
    """
    return tuple_(*(statement.table.c[name] for name in names)).is_distinct_from(
        tuple_(*(statement.excluded[name] for name in names))
    )


def _tombstoned(at: datetime) -> dict[str, Any]:
    """Render the two columns a tombstone is: they move together or the CHECK refuses the row."""
    return {"mirror_state": MirrorState.ABSENT, "absent_since": at}


def _flow(*, campaign_id: CampaignId, stream_id: KeitaroStreamId) -> Select[tuple[KeitaroStreamId]]:
    """Select this campaign's flow: the scope every statement naming a flow shares.

    Written once so that no caller can spell the predicate with the campaign left out, and
    as a SELECT rather than a boolean so that an INSERT can draw its row from it.
    """
    return select(DbStream.keitaro_stream_id).where(
        DbStream.keitaro_stream_id == stream_id, DbStream.campaign_id == campaign_id
    )


def _live_drafts_of(campaign_id: CampaignId) -> Select[tuple[DbStreamDraft]]:
    """Select every live draft of one campaign in one statement, the campaign being the join.

    There is no draft relationship on `DbStream` to eager-load, and one query per flow is
    what `views_for` exists to avoid. `uq_stream_drafts_live_draft_per_stream` is a partial
    index on exactly this status list, so it serves the lookup as well as the uniqueness.
    """
    return (
        select(DbStreamDraft)
        .join(DbStream, DbStream.keitaro_stream_id == DbStreamDraft.stream_id)
        .where(
            DbStream.campaign_id == campaign_id,
            DbStreamDraft.status.in_(LIVE_DRAFT_STATUSES),
        )
        .options(selectinload(DbStreamDraft.rows))
        .execution_options(**_FRESH)
    )


def _mirrored_campaign(row: DbCampaign) -> MirroredCampaign:
    return MirroredCampaign(
        id=row.id,
        keitaro_campaign_id=row.keitaro_campaign_id,
        alias=row.alias,
        name=row.name,
        state=row.state,
        setup_status=row.setup_status,
        public_domain=row.public_domain,
        requested_country=row.requested_country,
        requested_offer_id=row.requested_offer_id,
        synced_at=row.synced_at,
        created_at=row.created_at,
    )


def _mirrored_stream(row: DbStream) -> MirroredStream:
    return MirroredStream(
        keitaro_stream_id=row.keitaro_stream_id,
        campaign_id=row.campaign_id,
        name=row.name,
        # The mirror stores the tracker's tolerant text; the record is strict, and this is
        # the one narrowing mappers.py cannot do for us.
        schema=StreamSchema(row.schema),
        position=row.position,
        filters=to_filters(row.filters),
        absent=row.mirror_state == MirrorState.ABSENT,
    )


def _live_draft(row: DbStreamDraft, pins: Mapping[OfferId, int]) -> LiveDraft:
    return LiveDraft(
        id=row.id,
        status=row.status,
        base_snapshot_hash=row.base_snapshot_hash,
        rows=to_draft_rows(row.rows, pins),
    )


def _view(row: DbStream, draft: DbStreamDraft | None) -> StreamView:
    """Assemble one flow from collections the caller has already loaded.

    The pins are read once and joined into both the mirror rows and the draft's, which is
    what makes a pinned row read the same on either side of the screen.
    """
    pins = to_pins(row.pins)
    return StreamView(
        stream=_mirrored_stream(row),
        mirror_rows=to_mirror_rows(row.offers, pins),
        draft=None if draft is None else _live_draft(draft, pins),
    )


class SqlCampaignRepository(CampaignRepository):
    """The campaigns table, the only one of the six whose rows carry our own identity."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def add(self, campaign: Campaign, *, setup: CampaignSetup) -> MirroredCampaign:
        """Insert and read back in one statement, refusing a tracker campaign already here.

        `ON CONFLICT DO NOTHING` and not a caught `IntegrityError`: the second aborts the
        transaction, so the caller that wanted to answer the race by reading the existing
        row could not.
        """
        created = (
            await self._session.scalars(
                insert(DbCampaign)
                .values(
                    id=CampaignId(uuid4()),
                    **campaign_values(campaign),
                    setup_status=setup.status,
                    public_domain=setup.public_domain,
                    requested_country=setup.requested_country,
                    requested_offer_id=setup.requested_offer_id,
                )
                .on_conflict_do_nothing(index_elements=[DbCampaign.keitaro_campaign_id])
                .returning(DbCampaign)
            )
        ).one_or_none()
        if created is None:
            raise CampaignAlreadyImportedError(campaign.id)
        return _mirrored_campaign(created)

    @override
    async def get(self, campaign_id: CampaignId) -> MirroredCampaign:
        found = await self._session.get(DbCampaign, campaign_id, populate_existing=True)
        if found is None:
            raise CampaignNotFoundError(campaign_id)
        return _mirrored_campaign(found)

    @override
    async def by_keitaro_id(
        self, keitaro_campaign_id: KeitaroCampaignId
    ) -> MirroredCampaign | None:
        found = (
            await self._session.scalars(
                select(DbCampaign)
                .where(DbCampaign.keitaro_campaign_id == keitaro_campaign_id)
                .execution_options(**_FRESH)
            )
        ).one_or_none()
        return None if found is None else _mirrored_campaign(found)

    @override
    async def page(
        self, *, after: CampaignCursor | None, query: str | None, limit: int
    ) -> CampaignPage:
        """One row-value comparison, read one row long, cut back to `limit`.

        `(created_at, id) < (:at, :id)` is an Index Cond on `ix_campaigns_created_at_id`;
        the same predicate spelled `created_at < :at OR (...)` is a filter that re-reads
        every row of every page before it.
        """
        statement = (
            select(DbCampaign)
            .order_by(DbCampaign.created_at.desc(), DbCampaign.id.desc())
            .limit(limit + 1)
            .execution_options(**_FRESH)
        )
        if after is not None:
            statement = statement.where(
                tuple_(DbCampaign.created_at, DbCampaign.id) < (after.created_at, after.id)
            )
        if query:
            pattern = f"%{_escaped(query)}%"
            statement = statement.where(
                or_(
                    DbCampaign.name.ilike(pattern, escape=_LIKE_ESCAPE),
                    DbCampaign.alias.ilike(pattern, escape=_LIKE_ESCAPE),
                )
            )
        found = (await self._session.scalars(statement)).all()
        page = tuple(_mirrored_campaign(row) for row in found[:limit])
        return CampaignPage(
            campaigns=page,
            # The cursor is the last row RETURNED and never the extra one read: handing back
            # the row nobody has seen would skip it.
            next_cursor=CampaignCursor(created_at=page[-1].created_at, id=page[-1].id)
            if page and len(found) > limit
            else None,
        )

    @override
    async def note_fetched(
        self, campaign_id: CampaignId, campaign: Campaign, *, at: datetime
    ) -> None:
        """Write the four columns the tracker owns and date the fetch.

        `updated_at` moves only when one of those four actually changed — models.py fixes
        that meaning for this column, and `synced_at` is the one that answers "when did we
        last look". The CASE compares against the stored row, which a SET clause still sees.
        """
        owned = campaign_values(campaign)
        stored = tuple_(*(DbCampaign.__table__.c[name] for name in owned))
        fetched = tuple_(*(literal(value) for value in owned.values()))
        noted = await self._session.scalars(
            update(DbCampaign)
            .where(DbCampaign.id == campaign_id)
            .values(
                **owned,
                synced_at=at,
                # Naming it here is also what overrides the column's onupdate.
                updated_at=case(
                    (stored.is_distinct_from(fetched), at), else_=DbCampaign.updated_at
                ),
            )
            .returning(DbCampaign.id)
        )
        if noted.one_or_none() is None:
            raise CampaignNotFoundError(campaign_id)

    @override
    async def set_setup_status(self, campaign_id: CampaignId, status: CampaignSetupStatus) -> None:
        """Move the one column a fetch never touches; `updated_at` follows by its onupdate."""
        recorded = await self._session.scalars(
            update(DbCampaign)
            .where(DbCampaign.id == campaign_id)
            .values(setup_status=status)
            .returning(DbCampaign.id)
        )
        if recorded.one_or_none() is None:
            raise CampaignNotFoundError(campaign_id)


class SqlStreamRepository(StreamRepository):
    """The mirror of one campaign's flows and their rows, and the editor's read of both."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def views_for(self, campaign_id: CampaignId) -> tuple[StreamView, ...]:
        """Four statements, five when any flow has a live draft — and never one per flow.

        Measured: 4 for two flows and for 500, 5 once a draft exists. `selectinload` chunks
        parent keys at 500, so 600 flows cost two more; a Keitaro campaign has two.
        """
        found = (
            await self._session.scalars(
                select(DbStream)
                .where(DbStream.campaign_id == campaign_id)
                .order_by(DbStream.position.asc(), DbStream.keitaro_stream_id.asc())
                .options(selectinload(DbStream.offers), selectinload(DbStream.pins))
                .execution_options(**_FRESH)
            )
        ).all()
        live = await self._live_drafts(_live_drafts_of(campaign_id))
        return tuple(_view(row, live.get(row.keitaro_stream_id)) for row in found)

    @override
    async def lock(self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId) -> StreamView:
        """FOR UPDATE on the flow row, which the foreign keys extend to inserting its children.

        Measured: while this is held, an INSERT into `stream_drafts`, `stream_offers` or
        `offer_pins` naming this flow blocks, because the key it needs is FOR KEY SHARE on
        this row. `FOR NO KEY UPDATE` does not block them, which is why this is the plain
        one; `selectinload` and never `joinedload`, which compiles to FOR UPDATE over an
        outer join and PostgreSQL refuses that at runtime.
        """
        locked = (
            await self._session.scalars(
                select(DbStream)
                .where(
                    DbStream.keitaro_stream_id == stream_id,
                    DbStream.campaign_id == campaign_id,
                )
                .with_for_update()
                .options(selectinload(DbStream.offers), selectinload(DbStream.pins))
                .execution_options(**_FRESH)
            )
        ).one_or_none()
        if locked is None:
            raise StreamNotFoundError(stream_id)
        live = await self._live_drafts(
            _live_drafts_of(campaign_id).where(DbStreamDraft.stream_id == stream_id)
        )
        return _view(locked, live.get(stream_id))

    async def _live_drafts(
        self, statement: Select[tuple[DbStreamDraft]]
    ) -> dict[KeitaroStreamId, DbStreamDraft]:
        return {draft.stream_id: draft for draft in await self._session.scalars(statement)}

    @override
    async def upsert_campaign_streams(
        self, *, campaign_id: CampaignId, streams: tuple[Stream, ...], at: datetime
    ) -> None:
        """Leave this campaign's mirror holding exactly `streams`, their offer rows included.

        Four statements for any realistic campaign: the flows, their rows, and one sweep
        each. An empty `streams` tombstones the whole campaign mirror, which is the
        contract — whether an empty answer from the tracker was believable is a judgement
        only the use case can make.
        """
        rendered = sorted(
            (stream_values(stream, campaign_id=campaign_id) for stream in streams),
            key=lambda row: row["keitaro_stream_id"],
        )
        for chunk in _in_chunks(rendered, len(rendered[0]) if rendered else 1):
            statement = insert(DbStream).values(list(chunk))
            names = _synced(chunk[0], keys=("keitaro_stream_id",))
            await self._session.execute(
                statement.on_conflict_do_update(
                    index_elements=[DbStream.keitaro_stream_id],
                    set_=_overwrite(statement, names, at=at),
                    where=_changed(statement, names),
                )
            )
        await self._upsert_offer_rows(
            [
                stream_offer_values(offer, stream_id=stream.id)
                for stream in streams
                for offer in stream.offers
            ],
            campaign_id=campaign_id,
        )
        await self._session.execute(
            update(DbStream)
            .where(
                DbStream.campaign_id == campaign_id,
                # Already a tombstone: `absent_since` is when it went, not when we looked.
                DbStream.mirror_state == MirrorState.PRESENT,
                _dropped((DbStream.keitaro_stream_id, [stream.id for stream in streams])),
            )
            .values(**_tombstoned(at), updated_at=at)
        )
        await self._tombstone_dropped_rows(campaign_id=campaign_id, streams=streams, at=at)

    @override
    async def upsert_stream_offers(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offers: tuple[StreamOffer, ...],
        at: datetime,
    ) -> None:
        """Rewrite one flow's rows, tombstoning that flow's rows alone and no other flow's."""
        written = await self._upsert_offer_rows(
            [stream_offer_values(offer, stream_id=stream_id) for offer in offers],
            campaign_id=campaign_id,
        )
        if not written and not await self._session.scalar(
            _flow(campaign_id=campaign_id, stream_id=stream_id).exists().select()
        ):
            # Nothing written is either an empty payload or another campaign's flow, and
            # only this read tells them apart. It runs in neither ordinary case.
            raise StreamNotFoundError(stream_id)
        await self._session.execute(
            update(DbStreamOffer)
            .where(
                DbStreamOffer.stream_id == stream_id,
                _flow(campaign_id=campaign_id, stream_id=stream_id).exists(),
                DbStreamOffer.mirror_state == MirrorState.PRESENT,
                _dropped((DbStreamOffer.offer_id, [offer.offer_id for offer in offers])),
            )
            .values(**_tombstoned(at))
        )

    async def _upsert_offer_rows(
        self, rows: Sequence[Mapping[str, Any]], *, campaign_id: CampaignId
    ) -> bool:
        """Write mirrored rows for flows of this campaign, and answer whether any landed.

        The payload is a VALUES list joined to `streams`, so the campaign scope is in the
        same predicate as the write: a row naming another campaign's flow does not join, and
        the empty RETURNING is what says so. A `where=` on the DO UPDATE could not do this —
        it guards the update branch and lets the insert branch through.
        """
        if not rows:
            return False
        table = DbStreamOffer.__table__
        names = tuple(rows[0])
        written = False
        ordered = sorted(rows, key=lambda row: (row["stream_id"], row["offer_id"]))
        for chunk in _in_chunks(ordered, len(names)):
            payload = values(
                *(column(name, table.c[name].type) for name in names), name="payload"
            ).data([tuple(row[name] for name in names) for row in chunk])
            scoped = select(
                # Cast every column: one that is NULL in every row of a VALUES list renders
                # as a bare NULL, which PostgreSQL reads as text and then refuses to insert.
                *(cast(payload.c[name], table.c[name].type) for name in names)
            ).select_from(
                payload.join(
                    DbStream,
                    (DbStream.keitaro_stream_id == payload.c.stream_id)
                    & (DbStream.campaign_id == campaign_id),
                )
            )
            statement = insert(DbStreamOffer).from_select(list(names), scoped)
            landed = await self._session.scalars(
                statement.on_conflict_do_update(
                    index_elements=[DbStreamOffer.stream_id, DbStreamOffer.offer_id],
                    set_={
                        name: statement.excluded[name]
                        for name in _synced(chunk[0], keys=("stream_id", "offer_id"))
                    },
                ).returning(DbStreamOffer.offer_id)
            )
            written = landed.first() is not None or written
        return written

    async def _tombstone_dropped_rows(
        self, *, campaign_id: CampaignId, streams: tuple[Stream, ...], at: datetime
    ) -> None:
        """Tombstone every mirrored row of this campaign the payload did not carry.

        Two arrays anti-joined rather than a pair list: the statement text does not change
        with the payload, and the rows of a flow that vanished are swept by the same
        statement as a row dropped from a flow that stayed.
        """
        pairs = sorted(
            (int(stream.id), int(offer.offer_id)) for stream in streams for offer in stream.offers
        )
        await self._session.execute(
            update(DbStreamOffer)
            .where(
                DbStreamOffer.stream_id.in_(
                    select(DbStream.keitaro_stream_id).where(DbStream.campaign_id == campaign_id)
                ),
                DbStreamOffer.mirror_state == MirrorState.PRESENT,
                _dropped(
                    (DbStreamOffer.stream_id, [stream_id for stream_id, _ in pairs]),
                    (DbStreamOffer.offer_id, [offer_id for _, offer_id in pairs]),
                ),
            )
            .values(**_tombstoned(at))
        )


class SqlPinRepository(PinRepository):
    """The pinned shares, held in the mirror so a pin survives both a push and a cancel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def hold(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offer_id: OfferId,
        share: Share,
    ) -> None:
        """Insert the pin or move it, in one statement that reads the flow to prove the scope.

        This is the one write a URL reaches with no `lock()` before it, so the campaign is a
        predicate of the statement rather than of a caller.
        """
        source = _flow(campaign_id=campaign_id, stream_id=stream_id).add_columns(
            literal(offer_id, DbOfferPin.offer_id.type),
            literal(int(share), DbOfferPin.locked_share.type),
        )
        statement = insert(DbOfferPin).from_select(
            ["stream_id", "offer_id", "locked_share"], source
        )
        held = await self._session.scalars(
            statement.on_conflict_do_update(
                index_elements=[DbOfferPin.stream_id, DbOfferPin.offer_id],
                # No `where` on the conflict: re-pinning at the share already held must
                # still return a row, because the returned row is what says the flow exists.
                set_={
                    "locked_share": statement.excluded.locked_share,
                    "updated_at": func.now(),
                },
            ).returning(DbOfferPin.offer_id)
        )
        if held.one_or_none() is None:
            raise StreamNotFoundError(stream_id)

    @override
    async def release(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId, offer_id: OfferId
    ) -> None:
        """Delete the pin if there is one, and say nothing when there is not."""
        await self._session.execute(
            delete(DbOfferPin).where(
                DbOfferPin.stream_id == stream_id,
                DbOfferPin.offer_id == offer_id,
                _flow(campaign_id=campaign_id, stream_id=stream_id).exists(),
            )
        )


class SqlDraftRepository(DraftRepository):
    """One flow's staged edits: at most one live draft, closed softly and never deleted."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def open_for(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        rows: tuple[OfferRow, ...],
        base_snapshot_hash: str,
    ) -> LiveDraft:
        """Open the flow's one live draft, or lose the race to whoever opened it first.

        The partial unique index is the arbiter, so the loser reads an empty RETURNING
        rather than an exception and the transaction is still usable for the question that
        tells a taken flow from a foreign one.
        """
        # Minted here and not left to the column default: `from_select()` renders no
        # Python-side default, so an omitted id is a NOT NULL violation.
        draft_id = DraftId(uuid4())
        source = _flow(campaign_id=campaign_id, stream_id=stream_id).add_columns(
            literal(draft_id, DbStreamDraft.id.type),
            literal(base_snapshot_hash, DbStreamDraft.base_snapshot_hash.type),
        )
        opened = (
            (
                await self._session.execute(
                    insert(DbStreamDraft)
                    .from_select(["stream_id", "id", "base_snapshot_hash"], source)
                    .on_conflict_do_nothing(
                        index_elements=[DbStreamDraft.stream_id], index_where=_LIVE_DRAFT
                    )
                    .returning(DbStreamDraft.id, DbStreamDraft.status)
                )
            )
            .tuples()
            .one_or_none()
        )
        if opened is None:
            await self._refuse(campaign_id=campaign_id, stream_id=stream_id)
        await self._write_rows(draft_id, rows)
        return LiveDraft(
            id=opened[0],
            # Read back rather than spelled: `status` is a server default, and this is the
            # one place that would have to repeat it.
            status=opened[1],
            base_snapshot_hash=base_snapshot_hash,
            rows=rows,
        )

    @override
    async def replace_rows(self, draft_id: DraftId, rows: tuple[OfferRow, ...]) -> None:
        """Delete the draft's rows and write the new set, never updating one in place.

        UNIQUE (draft_id, seq) and UNIQUE (draft_id, activated_at) are checked per row, so a
        rewrite that moved rows one at a time collides with itself the moment two of them
        swap ordinals — and a BRING BACK is exactly that swap.
        """
        await self._session.execute(
            delete(DbStreamDraftRow).where(DbStreamDraftRow.draft_id == draft_id)
        )
        await self._write_rows(draft_id, rows)

    @override
    async def change_status(
        self, draft_id: DraftId, *, was: DraftStatus, becomes: DraftStatus
    ) -> None:
        """Move the draft only if it is still at `was`, and name what it is if it is not.

        One statement when it wins. The read that names the status on the losing side takes
        the row, so the status the caller renders is still true when it renders it.
        """
        moved = await self._session.scalars(
            update(DbStreamDraft)
            .where(DbStreamDraft.id == draft_id, DbStreamDraft.status == was)
            .values(status=becomes)
            .returning(DbStreamDraft.id)
        )
        if moved.one_or_none() is not None:
            return
        # `one()` and not `one_or_none()`: nothing deletes a draft and no URL carries a
        # draft id, so a missing row is this service's own bug and deserves its 500.
        found = (
            await self._session.scalars(
                select(DbStreamDraft.status).where(DbStreamDraft.id == draft_id).with_for_update()
            )
        ).one()
        raise DraftStatusChangedError(was, found)

    async def _refuse(self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId) -> NoReturn:
        """Say which of the two an empty RETURNING was — askable only because nothing raised."""
        known = await self._session.scalar(
            _flow(campaign_id=campaign_id, stream_id=stream_id).exists().select()
        )
        if not known:
            raise StreamNotFoundError(stream_id)
        raise DraftAlreadyOpenError(stream_id)

    async def _write_rows(self, draft_id: DraftId, rows: tuple[OfferRow, ...]) -> None:
        """Write a draft's rows in one statement, or none at all when there are none."""
        if not rows:
            # `insert().values([])` is not valid SQL, and a draft with every row removed is
            # a state the editor reaches.
            return
        await self._session.execute(
            insert(DbStreamDraftRow).values(
                [draft_row_values(row, draft_id=draft_id) for row in rows]
            )
        )


class SqlPushAttemptRepository(PushAttemptRepository):
    """The audit of one press of PUSH TO KT: opened before the tracker is called, closed after."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def start(
        self, *, draft_id: DraftId, desired: tuple[DesiredOffer, ...], correlation_id: str
    ) -> PushAttemptId:
        """Record the desired state; `outcome` is left out so the server default writes it."""
        attempt_id = PushAttemptId(uuid4())
        await self._session.execute(
            insert(DbPushAttempt).values(
                id=attempt_id,
                draft_id=draft_id,
                desired_state=desired_state_json(desired),
                correlation_id=correlation_id,
            )
        )
        return attempt_id

    @override
    async def settle(self, attempt_id: PushAttemptId, outcome: PushOutcome) -> None:
        """Close the attempt if it is still in flight, so a late phase 3 cannot rewrite history."""
        settled = await self._session.scalars(
            update(DbPushAttempt)
            .where(DbPushAttempt.id == attempt_id, DbPushAttempt.outcome == PushOutcome.IN_FLIGHT)
            .values(outcome=outcome)
            .returning(DbPushAttempt.id)
        )
        if settled.one_or_none() is None:
            raise PushAttemptSettledError(attempt_id)

    @override
    async def latest_for(self, draft_id: DraftId) -> PushAttempt | None:
        """Read the newest attempt of one draft, `id` making the order total.

        `now()` is one instant for a whole transaction, so two attempts written in one block
        share `created_at` to the microsecond — measured. `id` then decides, arbitrarily but
        repeatably; phase 1 writes one attempt per transaction, so the tie is not a case this
        has to get right, only one it must not answer differently twice.
        """
        found = (
            await self._session.scalars(
                select(DbPushAttempt)
                .where(DbPushAttempt.draft_id == draft_id)
                .order_by(DbPushAttempt.created_at.desc(), DbPushAttempt.id.desc())
                .limit(1)
                .execution_options(**_FRESH)
            )
        ).one_or_none()
        if found is None:
            return None
        return PushAttempt(
            id=found.id,
            outcome=found.outcome,
            # There is no `pushing_since` column: this row, still in flight, is what dates a
            # wedged push.
            started_at=found.created_at,
            correlation_id=found.correlation_id,
        )


class SqlOfferCatalogueRepository(OfferCatalogueRepository):
    """The local mirror of `GET /offers`, which exists because that endpoint takes no parameters."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def search(self, query: str | None, *, limit: int) -> tuple[Offer, ...]:
        """One statement over both indexes: `cast(id, Text)` seeks, `name ILIKE` gins.

        The id arm ranks first, so typing 11104 cannot bury offer 11104 under an offer merely
        named `11104 Special`; within that arm `id ASC` puts the exact match on top, because
        any longer prefix match is at least ten times the number.
        """
        statement = (
            select(DbOffer)
            .where(DbOffer.mirror_state == MirrorState.PRESENT)
            .limit(limit)
            .execution_options(**_FRESH)
        )
        typed = "" if query is None else query.strip()
        if typed:
            # `Text` and never `String`: the second renders VARCHAR, LIKE then adds a
            # coercion, and `ix_offers_id_as_text` stops matching.
            prefix = cast(DbOffer.id, Text).like(f"{_escaped(typed)}%", escape=_LIKE_ESCAPE)
            statement = statement.where(
                or_(prefix, DbOffer.name.ilike(f"%{_escaped(typed)}%", escape=_LIKE_ESCAPE))
            ).order_by(prefix.desc(), DbOffer.id.asc())
        else:
            statement = statement.order_by(DbOffer.id.asc())
        return tuple(to_offer(row) for row in await self._session.scalars(statement))

    @override
    async def by_ids(self, offer_ids: Collection[OfferId]) -> Mapping[OfferId, Offer]:
        """One statement, answering short: an id the catalogue has never seen is simply absent.

        No `mirror_state` filter, deliberately unlike `search`: a row can stay in a flow after
        the tracker drops the offer, and the label has to survive.
        """
        if not offer_ids:
            return {}
        rows = await self._session.scalars(
            select(DbOffer).where(DbOffer.id.in_(set(offer_ids))).execution_options(**_FRESH)
        )
        return {row.id: to_offer(row) for row in rows}

    @override
    async def upsert_catalogue(self, offers: tuple[Offer, ...], *, at: datetime) -> None:
        """Write every offer the tracker returned and tombstone the rest.

        An empty `offers` tombstones the whole catalogue: that is the contract, and the guard
        against a tracker answering emptily belongs to the use case, which is the only place
        that knows whether the answer was believable.
        """
        rendered = sorted((offer_values(offer) for offer in offers), key=lambda row: row["id"])
        for chunk in _in_chunks(rendered, len(rendered[0]) if rendered else 1):
            statement = insert(DbOffer).values(list(chunk))
            names = _synced(chunk[0], keys=("id",))
            await self._session.execute(
                statement.on_conflict_do_update(
                    index_elements=[DbOffer.id],
                    set_=_overwrite(statement, names, at=at),
                    where=_changed(statement, names),
                )
            )
        await self._session.execute(
            update(DbOffer)
            .where(
                DbOffer.mirror_state == MirrorState.PRESENT,
                _dropped((DbOffer.id, [offer.id for offer in offers])),
            )
            .values(**_tombstoned(at), updated_at=at)
        )


def repositories(session: AsyncSession) -> Transaction:
    """Bundle one session's six repositories, which is the only shape `Transaction` has.

    Here and not in the unit of work: this is the one module that names all six concrete
    classes, and a port gaining a second implementation changes this line alone.
    """
    return Transaction(
        campaigns=SqlCampaignRepository(session),
        streams=SqlStreamRepository(session),
        pins=SqlPinRepository(session),
        drafts=SqlDraftRepository(session),
        pushes=SqlPushAttemptRepository(session),
        offers=SqlOfferCatalogueRepository(session),
    )
