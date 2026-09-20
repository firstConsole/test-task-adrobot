"""The border between this service's own tables and its domain types, and the only crossing.

Every function here is pure and takes what it maps: one row, or a sequence of rows the caller
has already loaded. None of them names a relationship attribute, so none can trip
`lazy="raise_on_sql"` — a loader that forgot its `selectinload` fails at the query that forgot
it rather than three rings away. Nothing here computes a share: `to_mirror_rows` numbers the
ordinals the rounding remainder is handed out by, and `domain/shares.py` does the division.

The grammar is `keitaro/mapping.py`'s: `to_*` reads into the domain, and where that module's
`*_body` renders what you hand an HTTP client, `*_values` renders what you hand
`insert().values()`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from adrobot.domain.draft import to_kernel_rows
from adrobot.domain.offer import Offer
from adrobot.domain.shares import OfferRow
from adrobot.domain.stream import StreamFilter
from adrobot.domain.values import OfferState
from adrobot.infrastructure.db.models import MirrorState

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from adrobot.domain.campaign import Campaign
    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.ids import CampaignId, DraftId, KeitaroStreamId, OfferId
    from adrobot.domain.stream import Stream, StreamOffer
    from adrobot.infrastructure.db.models import (
        DbOffer,
        DbOfferPin,
        DbStreamDraftRow,
        DbStreamOffer,
    )

_OLDEST: Final = datetime.min.replace(tzinfo=UTC)
"""Where a row the tracker gave no `created_at` for ranks: oldest, as the mirror's NULLS FIRST
does. Aware, so it can never meet an aware stamp and raise mid-comparison."""


def to_mirror_rows(
    offers: Sequence[DbStreamOffer], pins: Mapping[OfferId, int]
) -> tuple[OfferRow, ...]:
    """Turn one flow's mirrored rows into kernel rows, numbering the ordinals the remainder follows.

    Ascending, so the most recent `created_at` holds the largest `activated_at` — and
    `enumerate` is `row_number()` and not `rank()`, which is what UNIQUE (draft_id,
    activated_at) demands of the draft these rows seed. Shares are carried untouched: a clean
    flow summing to 50 is real.
    """
    return to_kernel_rows(
        (
            OfferRow(
                offer_id=row.offer_id,
                # A row read from the tracker was last activated when it was created. The two
                # ordinals part company on the first add or BRING BACK, never before.
                seq=ordinal,
                activated_at=ordinal,
                share=row.share,
                removed=_is_removed(row),
            )
            # start=1 twice over: ck_stream_draft_rows_ordinals_are_positive refuses a 0, and
            # _next_seq() in domain/draft.py counts from max(..., default=0) + 1.
            for ordinal, row in enumerate(sorted(offers, key=_tracker_order), start=1)
        ),
        pins,
    )


def to_draft_rows(
    rows: Sequence[DbStreamDraftRow], pins: Mapping[OfferId, int]
) -> tuple[OfferRow, ...]:
    """Read a draft's rows back as the last recalculation left them, with the pins rejoined.

    The share is carried and never recomputed, and the ordinals are never renumbered: a pin
    must move no share until the next edit, and renumbering would re-elect the row that takes
    the remainder.
    """
    return to_kernel_rows(
        (
            OfferRow(
                offer_id=row.offer_id,
                seq=row.seq,
                activated_at=row.activated_at,
                share=row.share,
                removed=row.removed,
            )
            for row in sorted(rows, key=lambda row: row.seq)
        ),
        pins,
    )


def to_pins(pins: Sequence[DbOfferPin]) -> dict[OfferId, int]:
    """Collapse one flow's pin rows into the mapping `to_kernel_rows` joins by.

    Nothing is filtered: row presence is the pin, and a pin at 0 is a pin — any truthiness
    test here would quietly release it.
    """
    return {pin.offer_id: pin.locked_share for pin in pins}


def to_offer(row: DbOffer) -> Offer:
    """Read one catalogue row as the offer the editor's combobox labels a line with.

    `tuple(...)` is the only conversion: the ARRAY column hands back a list, and `Offer` is
    frozen and compared by value.
    """
    return Offer(
        id=row.id,
        name=row.name,
        state=row.state,
        country=tuple(row.country),
        group_id=row.group_id,
        affiliate_network=row.affiliate_network,
        preview_path=row.preview_path,
    )


def offer_values(offer: Offer) -> dict[str, Any]:
    """Render one catalogue row for the offers upsert.

    The tombstone pair travels because the only `Offer` that reaches here is one `GET /offers`
    just returned: writing it back resurrects a tombstoned row, and the CHECK on the pair is
    what refuses a half-reset.
    """
    return {
        "id": offer.id,
        "name": offer.name,
        "state": offer.state,
        "country": list(offer.country),
        "group_id": offer.group_id,
        "affiliate_network": offer.affiliate_network,
        "preview_path": offer.preview_path,
        "mirror_state": MirrorState.PRESENT,
        "absent_since": None,
    }


def stream_offer_values(offer: StreamOffer, *, stream_id: KeitaroStreamId) -> dict[str, Any]:
    """Render one mirrored offer row of the flow the tracker just gave back.

    `stream_id` is a keyword rather than a field of `StreamOffer`: the tracker nests offers
    inside a flow, so the owner is context — and mypy is then what keeps it apart from an
    `OfferId`.
    """
    return {
        "stream_id": stream_id,
        "offer_id": offer.offer_id,
        "share": offer.share,
        "state": offer.state,
        "keitaro_row_id": offer.row_id,
        # None when keitaro/mapping.py could not read the stamp. Stored as None and never
        # guessed at: a NULL ranks oldest, so that row can never take the remainder.
        "keitaro_created_at": offer.created_at,
        "mirror_state": MirrorState.PRESENT,
        "absent_since": None,
    }


def stream_values(stream: Stream, *, campaign_id: CampaignId) -> dict[str, Any]:
    """Render the reduced flow row, which is everything the mirror keeps of a flow.

    Ten of the tracker's fields are dropped on purpose: the push re-reads the flow before
    writing it back, so the mirror never needs them.
    """
    return {
        "keitaro_stream_id": stream.id,
        # stream.campaign_id is the TRACKER's campaign id; this column is our UUID. Same word,
        # two worlds, and a required keyword is what stops a caller reaching for the wrong one.
        "campaign_id": campaign_id,
        "name": stream.name,
        "schema": stream.schema.value,
        "position": stream.position,
        "filters": filters_json(stream.filters),
        "mirror_state": MirrorState.PRESENT,
        "absent_since": None,
    }


def campaign_values(campaign: Campaign) -> dict[str, Any]:
    """Render the four columns a read from the tracker is authoritative for, and no others.

    A fifth key here is how `setup_status` would be reset to `ready` on the next fetch,
    undoing the one signal that a half-created campaign needs repairing.
    """
    return {
        "keitaro_campaign_id": campaign.id,
        "alias": campaign.alias,
        "name": campaign.name,
        "state": campaign.state,
    }


def draft_row_values(row: OfferRow, *, draft_id: DraftId) -> dict[str, Any]:
    """Render one draft row exactly as `redistribute()` left it.

    `pinned_share` is dropped on the floor: the pin lives in `offer_pins` so that it survives
    a push and a cancel, and a second home for it here is one that could fall out of step.
    """
    return {
        "draft_id": draft_id,
        "offer_id": row.offer_id,
        "seq": row.seq,
        "activated_at": row.activated_at,
        "share": row.share,
        "removed": row.removed,
    }


def filters_json(filters: Sequence[StreamFilter]) -> list[dict[str, Any]]:
    """Write a flow's filters in the domain's own four keys, never the wire's.

    `id` travels: a filter resent without one leaves a tracker that merges arrays holding two
    copies of the same country condition.
    """
    return [
        {"id": row.id, "name": row.name, "mode": row.mode, "payload": list(row.payload)}
        for row in filters
    ]


def to_filters(payload: Sequence[Mapping[str, Any]]) -> tuple[StreamFilter, ...]:
    """Read a flow's filters back out of the JSONB `filters_json` wrote.

    Subscripted and not `.get()`: every key here was written by the function above, so a
    missing one is our own bug — and a `KeyError` names the key and never the value.
    """
    return tuple(
        StreamFilter(
            name=row["name"],
            mode=row["mode"],
            # JSONB hands back a list. Without tuple() the frozen dataclass holds one and the
            # sync's "stored != fetched" is true forever, so every fetch rewrites the row.
            payload=tuple(row["payload"]),
            id=row["id"],
        )
        for row in payload
    )


def desired_state_json(desired: Sequence[DesiredOffer]) -> list[dict[str, Any]]:
    """Render the audit column of one push: what phase 2 is about to ask the tracker to hold.

    The three keys are the tracker's own, so a postmortem can diff this column against Keitaro
    with no translation table in between.
    """
    # state.value is not optional: json serialisation raises TypeError on an Enum, and this
    # column is written while a push is being recorded — the worst moment to find out.
    return [
        {"offer_id": row.offer_id, "share": row.share, "state": row.state.value} for row in desired
    ]


def _tracker_order(row: DbStreamOffer) -> tuple[datetime, int, OfferId]:
    """Rank one mirrored row the way `DbStream.offers` does, without being handed that order.

    Spelled here as well as in SQL because push phase 1 takes the flow row FOR UPDATE and
    reads these rows with a select of its own: an ORDER BY forgotten there would move the
    rounding remainder to a different offer while every share still summed to 100.
    """
    created = row.keitaro_created_at
    return (_OLDEST if created is None else created, row.keitaro_row_id or 0, row.offer_id)


def _is_removed(row: DbStreamOffer) -> bool:
    """Fold the two columns that can silence a row into the one distinction a screen draws.

    `absent` is a row the tracker stopped returning; a non-active state is one a push left
    behind disabled. Both render grey, at 0%, with BRING BACK live.
    """
    # `!= active` and not `== disabled`: `state` is the tracker's tolerant text, and anything
    # but `active` takes no traffic in Keitaro. `.value` is mandatory — OfferState is a plain
    # Enum, so comparing the string against the member is silently always unequal.
    return row.mirror_state == MirrorState.ABSENT or row.state != OfferState.ACTIVE.value
