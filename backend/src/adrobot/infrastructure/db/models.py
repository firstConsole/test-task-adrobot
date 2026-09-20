"""Every table this service owns: the mirror of Keitaro, the draft, the audit, the catalogue.

Two rules shape every table here. The mirror of somebody else's data is tolerant — their
values arrive as text and integers with no CHECK, because a campaign we cannot open is
worse than a number we disagree with. What is ours is strict.

The mirror is a read model, not the source of a push payload: `replace_stream_offers`
re-reads the flow from the tracker before writing it, so the fields of a flow that no
screen shows are deliberately absent here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    cast,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adrobot.application.push import PushOutcome
from adrobot.domain.campaign import CampaignSetupStatus
from adrobot.domain.draft import DraftStatus
from adrobot.domain.ids import (
    CampaignId,
    DraftId,
    KeitaroCampaignId,
    KeitaroStreamId,
    OfferId,
)
from adrobot.infrastructure.db.base import Base, TimestampsMixin


class MirrorState(StrEnum):
    """Whether the tracker still returns this row.

    Never leaves the ORM: the mapper folds it into `OfferRow.removed`, which is the only
    distinction a screen draws.
    """

    PRESENT = "present"
    ABSENT = "absent"


def _as_varchar_with_check[E: Enum](enumeration: type[E], name: str, length: int) -> SqlEnum:
    """Persist one of our own enumerations as VARCHAR plus a named CHECK.

    Not a native PostgreSQL enum: `mirror_state` is used by three tables, alembic creates a
    shared type implicitly and never drops it, and `ALTER TYPE` cannot run in the same
    transaction as the migration that needs it.
    """
    return SqlEnum(
        enumeration,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
        validate_strings=True,
        # Without this SQLAlchemy stores the member NAME, so the column would hold
        # 'NEEDS_ATTENTION' while every server_default and query says 'needs_attention'.
        values_callable=lambda members: [member.value for member in members],
    )


MIRROR_STATE = _as_varchar_with_check(MirrorState, "mirror_state", 8)
SETUP_STATUS = _as_varchar_with_check(CampaignSetupStatus, "setup_status", 16)
DRAFT_STATUS = _as_varchar_with_check(DraftStatus, "draft_status", 10)
PUSH_OUTCOME = _as_varchar_with_check(PushOutcome, "push_outcome", 14)


class DbCampaign(Base, TimestampsMixin):
    """A campaign this service has opened, plus the local facts the tracker cannot give back."""

    __tablename__ = "campaigns"

    # Generated in Python, so a campaign and its flows are built before the one flush.
    id: Mapped[CampaignId] = mapped_column(primary_key=True, default=uuid4)
    keitaro_campaign_id: Mapped[KeitaroCampaignId] = mapped_column(unique=True)
    alias: Mapped[str]
    name: Mapped[str]
    state: Mapped[str]
    setup_status: Mapped[CampaignSetupStatus] = mapped_column(
        SETUP_STATUS, server_default=text("'ready'")
    )
    # `Campaign` has no domain_id on the read side, so a public link this service does not
    # store can never be rebuilt. Null for an imported campaign, which has none of ours.
    public_domain: Mapped[str | None]
    # Part 1's two inputs, kept only so that repairing a half-created campaign can rebuild
    # Flow 1's country filter and Flow 2's single row.
    requested_country: Mapped[str | None]
    requested_offer_id: Mapped[OfferId | None]
    # The editor's staleness figure. Distinct from `updated_at`, which moves only when a
    # fetch found a difference.
    synced_at: Mapped[datetime | None]

    streams: Mapped[list[DbStream]] = relationship(
        lazy="raise_on_sql",
        order_by=lambda: (DbStream.position.asc(), DbStream.keitaro_stream_id.asc()),
    )

    __table_args__ = (
        # The keyset page: WHERE (created_at, id) < (:c, :i) ORDER BY created_at DESC, id DESC.
        Index(None, "created_at", "id"),
    )


class DbStream(Base, TimestampsMixin):
    """One flow, reduced to what the editor draws a group from, plus the tombstone pair."""

    __tablename__ = "streams"

    # Without autoincrement=False the DDL is BIGSERIAL: Postgres would attach a sequence to
    # a column only the tracker assigns, and an insert omitting it would invent an id.
    keitaro_stream_id: Mapped[KeitaroStreamId] = mapped_column(
        primary_key=True, autoincrement=False
    )
    campaign_id: Mapped[CampaignId] = mapped_column(ForeignKey("campaigns.id", ondelete="RESTRICT"))
    name: Mapped[str]
    # Whether the group renders an offer table at all: only `landings` rotates offers.
    schema: Mapped[str]
    position: Mapped[int | None]
    # The header's geo line, in the domain's own {id, name, mode, payload} shape rather than
    # the wire's — raw tracker JSON here would carry the wire format past mapping.py.
    filters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    mirror_state: Mapped[MirrorState] = mapped_column(
        MIRROR_STATE, server_default=text("'present'")
    )
    absent_since: Mapped[datetime | None]

    offers: Mapped[list[DbStreamOffer]] = relationship(
        lazy="raise_on_sql",
        # The tie-break order, made total: an unparsable stamp ranks oldest and so can never
        # take the rounding remainder.
        order_by=lambda: (
            DbStreamOffer.keitaro_created_at.asc().nulls_first(),
            DbStreamOffer.keitaro_row_id.asc().nulls_first(),
            DbStreamOffer.offer_id.asc(),
        ),
    )
    pins: Mapped[list[DbOfferPin]] = relationship(
        lazy="raise_on_sql",
        # The lambda is not redundant: DbOfferPin is defined below this class, so the
        # reference has to be deferred.
        order_by=lambda: DbOfferPin.offer_id.asc(),  # noqa: PLW0108
    )

    # No `campaign` relationship: its one caller would be the push, which takes the flow row
    # FOR UPDATE — and a joinedload under with_for_update() compiles to FOR UPDATE over a
    # LEFT OUTER JOIN, which PostgreSQL refuses at runtime and never at compile time.

    __table_args__ = (
        CheckConstraint(
            "(mirror_state = 'absent') = (absent_since IS NOT NULL)",
            name="absent_since_matches_mirror_state",
        ),
        # The aggregate's selectinload, and the index PostgreSQL does not create for a
        # referencing column. Not unique: a tombstone keeps the position it had.
        Index(None, "campaign_id", "position"),
    )


class DbStreamOffer(Base):
    """One offer row of one flow, as the tracker holds it.

    No `TimestampsMixin`, deliberately: a column named `created_at` beside
    `keitaro_created_at` is the one mistake that silently misroutes the rounding remainder.
    """

    __tablename__ = "stream_offers"

    stream_id: Mapped[KeitaroStreamId] = mapped_column(
        ForeignKey("streams.keitaro_stream_id", ondelete="RESTRICT"), primary_key=True
    )
    # No ForeignKey to offers.id: an offer can reach a flow before it reaches our catalogue,
    # and an unknown one renders as `#11234 (not in catalogue)` rather than failing the sync.
    offer_id: Mapped[OfferId] = mapped_column(primary_key=True)
    # Theirs, so no CHECK in either direction: a clean stream summing to 50 is real.
    share: Mapped[int]
    # Not the same fact as `mirror_state`: this is what a push left behind as disabled.
    state: Mapped[str]
    # How a push tells a row that survived the write from one recreated underneath it.
    # Explicit BigInteger, since a bare `int` maps to INTEGER.
    keitaro_row_id: Mapped[int | None] = mapped_column(BigInteger)
    # The tie-break column the share rule names, in the tracker's zone as mapping.py read it.
    keitaro_created_at: Mapped[datetime | None]
    mirror_state: Mapped[MirrorState] = mapped_column(
        MIRROR_STATE, server_default=text("'present'")
    )
    absent_since: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "(mirror_state = 'absent') = (absent_since IS NOT NULL)",
            name="absent_since_matches_mirror_state",
        ),
    )


class DbOfferPin(Base, TimestampsMixin):
    """One pinned share.

    Kept in the mirror and outside the draft, so that it survives both a push and a cancel.
    """

    __tablename__ = "offer_pins"

    stream_id: Mapped[KeitaroStreamId] = mapped_column(
        ForeignKey("streams.keitaro_stream_id", ondelete="RESTRICT"), primary_key=True
    )
    # Row presence is the pin, so unpinning is a DELETE and no boolean can fall out of step.
    offer_id: Mapped[OfferId] = mapped_column(primary_key=True)
    locked_share: Mapped[int]

    __table_args__ = (
        # Ours, and the only number a client can influence — hence the range check that
        # stream_offers.share is denied.
        CheckConstraint("locked_share BETWEEN 0 AND 100", name="locked_share_is_a_percentage"),
    )


class DbOffer(Base, TimestampsMixin):
    """The local mirror of the tracker's offer catalogue, and the only thing a search reads.

    It exists because `GET /offers` takes no query parameters at all, so there is no
    server-side search to build the editor's combobox on. One column per field of the domain
    `Offer` and no more: PLAN-BACKEND §7 also asks for a `raw jsonb`, which is dropped
    because the admin port hands this layer a domain `Offer` — there is no wire payload here
    to put in it.
    """

    __tablename__ = "offers"

    id: Mapped[OfferId] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str]
    state: Mapped[str]
    # NOT NULL defaulted to the empty array: the domain's `tuple[str, ...] = ()` has one
    # representation of "no countries", and a dash is one of the values Keitaro really sends.
    country: Mapped[list[str]] = mapped_column(server_default=text("'{}'::text[]"))
    group_id: Mapped[int | None] = mapped_column(BigInteger)
    affiliate_network: Mapped[str | None]
    # Stays relative, as the domain insists: the absolute link is joined onto the tracker's
    # public base where it is rendered, and that base is configuration rather than a fact.
    preview_path: Mapped[str | None]
    mirror_state: Mapped[MirrorState] = mapped_column(
        MIRROR_STATE, server_default=text("'present'")
    )
    absent_since: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "(mirror_state = 'absent') = (absent_since IS NOT NULL)",
            name="absent_since_matches_mirror_state",
        ),
        # Serves the `name ILIKE '%...%'` arm of the search. A btree cannot, in any collation
        # and with any operator class. Needs pg_trgm, which migration 0004 creates first.
        Index(
            "ix_offers_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


# Serves the id-prefix arm — the video's «11104». Two measured traps, both silent:
#   * cast to `Text` and never to `String`. `String` renders VARCHAR, and LIKE then adds a
#     second coercion, so the planner sees `((id)::character varying)::text` and scans.
#   * `postgresql_ops` only lands when its key is the expression's own label. Keyed on
#     anything else the operator class is dropped with no error, and without
#     `text_pattern_ops` a non-C collation makes the index unusable for a prefix match.
# Module level rather than in `__table_args__` because the expression names the mapped
# column; declaring it here still attaches it to the table.
ID_AS_TEXT = Index(
    "ix_offers_id_as_text",
    cast(DbOffer.id, Text).label("id_as_text"),
    postgresql_ops={"id_as_text": "text_pattern_ops"},
)


class DbStreamDraft(Base, TimestampsMixin):
    """One flow's staged edits, and what the tracker looked like when they started."""

    __tablename__ = "stream_drafts"

    id: Mapped[DraftId] = mapped_column(primary_key=True, default=uuid4)
    stream_id: Mapped[KeitaroStreamId] = mapped_column(
        ForeignKey("streams.keitaro_stream_id", ondelete="RESTRICT")
    )
    status: Mapped[DraftStatus] = mapped_column(DRAFT_STATUS, server_default=text("'open'"))
    # Written once, at the insert that opens the draft, over the rows the tracker still
    # returns. Recomputing it on an edit would re-baseline the conflict check into a no-op.
    base_snapshot_hash: Mapped[str]

    rows: Mapped[list[DbStreamDraftRow]] = relationship(
        lazy="raise_on_sql",
        # The lambda is not redundant: DbStreamDraftRow is defined below this class.
        order_by=lambda: DbStreamDraftRow.seq.asc(),  # noqa: PLW0108
    )

    __table_args__ = (
        # The full truth of the column, not an approximation of it: a `.digest()` where a
        # `.hexdigest()` belongs would otherwise answer 409 to every push, forever.
        CheckConstraint(
            "base_snapshot_hash ~ '^[0-9a-f]{64}$'", name="base_snapshot_hash_is_a_sha256_digest"
        ),
        # Named by hand: the convention's `ix_` template can say neither "unique" nor
        # "partial", and a partial unique in PostgreSQL has to be an Index.
        Index(
            "uq_stream_drafts_live_draft_per_stream",
            "stream_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'pushing')"),
        ),
    )


class DbStreamDraftRow(Base):
    """The rows of one draft as the last recalculation left them."""

    __tablename__ = "stream_draft_rows"

    draft_id: Mapped[DraftId] = mapped_column(
        ForeignKey("stream_drafts.id", ondelete="RESTRICT"), primary_key=True
    )
    # No ForeignKey to offers.id, for the reason stream_offers gives; the composite key is
    # also what makes DuplicateOfferRowError unrepresentable.
    offer_id: Mapped[OfferId] = mapped_column(primary_key=True)
    seq: Mapped[int]
    # An ordinal and not a clock, as OfferRow's docstring insists. INTEGER, never DateTime.
    activated_at: Mapped[int]
    # redistribute()'s output, stored rather than recomputed on read: a pin must move no
    # share until the next edit, which a recalculation at render time would break.
    share: Mapped[int]
    removed: Mapped[bool] = mapped_column(server_default=text("false"))

    __table_args__ = (
        UniqueConstraint("draft_id", "seq"),
        # Exactly one row can be the most recently activated. This is what obliges the
        # seeding mapper to rank with row_number() rather than rank(): a tie would hand the
        # rounding remainder to seq, which is a different rule from the one chosen.
        UniqueConstraint("draft_id", "activated_at"),
        CheckConstraint("seq > 0 AND activated_at > 0", name="ordinals_are_positive"),
        # Ours, so checked — the opposite of stream_offers.share, which is the tracker's.
        CheckConstraint("share BETWEEN 0 AND 100", name="share_is_a_percentage"),
    )


class DbPushAttempt(Base, TimestampsMixin):
    """One press of PUSH TO KT, opened before the tracker is called and closed after it.

    Written in phase 1 rather than phase 3 so that a process dying mid-write still leaves a
    dated record: that row is how a later request tells a wedged push from a live one.
    """

    __tablename__ = "push_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # NOT NULL where PLAN-BACKEND §7 asked for ON DELETE SET NULL: a draft is closed softly
    # and never deleted, so the nullable column would only describe a state nothing creates.
    draft_id: Mapped[DraftId] = mapped_column(ForeignKey("stream_drafts.id", ondelete="RESTRICT"))
    outcome: Mapped[PushOutcome] = mapped_column(PUSH_OUTCOME, server_default=text("'in_flight'"))
    desired_state: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # The join to the structured log, which already holds the method, status and duration.
    # A caller outside a request — the CLI — mints one with new_correlation_id().
    correlation_id: Mapped[str]

    __table_args__ = (
        CheckConstraint("jsonb_typeof(desired_state) = 'array'", name="desired_state_is_an_array"),
        # The newest attempt of one draft, for the wedged-push branch and for a postmortem.
        Index(None, "draft_id", "created_at"),
    )
