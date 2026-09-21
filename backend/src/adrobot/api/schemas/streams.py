"""The editor's wire models: one campaign's flows as the screen draws them.

Two decisions worth reading before the fields.

**No endpoint here accepts a share.** A client says `add this offer`, `take that one out`,
`bring it back` — never `these rows hold 34, 33 and 33`. That is the whole reason the
arithmetic exists in one place: an endpoint taking filled-in shares makes the frontend a
second implementation of `redistribute()` in TypeScript, and the day the two disagree the
screen shows 34/33/33 while Keitaro gets 33/33/33. The single number a client may influence
is a pin, and it is validated by the same function that divides the rest.

**The flags are answers, not data.** `can_push`, `block_reason` and `warnings` are decided
on the server for the same reason: a frontend that worked out for itself whether a push was
allowed would be that second implementation wearing a different hat.

`flow_schema` carries `schema` on the wire because that is the tracker's own word for it and
what a reviewer sees in Keitaro. It cannot be spelled that way on the model: a pydantic field
named `schema` shadows an attribute of `BaseModel` and emits a `UserWarning` at
class-definition time, which `filterwarnings = ["error"]` turns into an ImportError pointing
at pydantic's internals rather than at this line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from adrobot.api.schemas.campaigns import CampaignResponse
from adrobot.api.schemas.offers import OfferResponse
from adrobot.api.schemas.problem import OfferShare
from adrobot.domain.draft import DraftOperationKind

if TYPE_CHECKING:
    from adrobot.application.dto import EditorRow, EditorView, StreamEditorView
    from adrobot.domain.diff import DraftDiff
    from adrobot.domain.stream import StreamFilter

MAX_DRAFT_OPERATIONS: Final = 100
"""How many edits one request may stage. A flow with more than a hundred offers is not a
thing anybody runs, and the batch is applied inside one transaction — so this is also the
bound on how long that transaction holds the flow's row."""


# --- what a client may send ---------------------------------------------------------------


class DraftOperationRequest(BaseModel):
    """One edit: what to do, and to which offer."""

    model_config = ConfigDict(extra="forbid")

    kind: DraftOperationKind
    offer_id: int = Field(gt=0, description="The offer, as the tracker numbers it.")


class EditDraftRequest(BaseModel):
    """A batch of edits, applied in order and all or nothing."""

    model_config = ConfigDict(extra="forbid")

    operations: tuple[DraftOperationRequest, ...] = Field(
        min_length=1,
        max_length=MAX_DRAFT_OPERATIONS,
        description=(
            "Applied in the order given. One the flow refuses rolls back the whole batch, "
            "so a request that answers 409 has changed nothing."
        ),
    )


class PinOfferRequest(BaseModel):
    """The one number this API takes from a client, and it is still validated server-side."""

    model_config = ConfigDict(extra="forbid")

    share: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Where to hold the row. Omitted means where it is now, which is the button as "
            "the screen offers it. Pinning recalculates nothing until the next edit."
        ),
    )


class PushDraftRequest(BaseModel):
    """What the push may do about a flow somebody edited in Keitaro meanwhile."""

    model_config = ConfigDict(extra="forbid")

    overwrite: bool = Field(
        default=False,
        description=(
            "Push over changes made in the tracker since this draft was opened. The answer "
            "to a 409 and nothing else: overwriting another person's work is a decision."
        ),
    )


# --- what this API answers with -----------------------------------------------------------


class StreamFilterResponse(BaseModel):
    """One condition on a flow — `country accept ["AU"]` in the reference campaign."""

    name: str
    mode: str
    payload: tuple[str, ...] = ()

    @classmethod
    def of(cls, condition: StreamFilter) -> StreamFilterResponse:
        """Render one filter without its tracker-side id, which is of no use to a screen."""
        return cls(name=condition.name, mode=condition.mode, payload=condition.payload)


class StreamRowResponse(BaseModel):
    """One line of the offer table.

    `offer` is `null` for an offer the local catalogue has never heard of, which the screen
    draws as `#11234 (not in catalogue)` rather than emptying itself. There is no foreign key
    from a flow row to the catalogue and there is not meant to be.
    """

    offer_id: int
    offer: OfferResponse | None = None
    share: int
    pinned_share: int | None = None
    removed: bool = False

    @classmethod
    def of(cls, row: EditorRow, *, tracker: str) -> StreamRowResponse:
        """Render one row, labelling it where the catalogue can."""
        return cls(
            offer_id=row.offer_id,
            offer=None if row.offer is None else OfferResponse.of(row.offer, tracker=tracker),
            share=row.share,
            pinned_share=row.pinned_share,
            removed=row.removed,
        )


class ShareChangeResponse(BaseModel):
    """One offer's share, before and after — the `34 → 33` of the diff summary."""

    offer_id: int
    was: int
    now: int


class DraftDiffResponse(BaseModel):
    """What the draft would change, for a person and for the tracker.

    `desired` is the exact payload a push sends, not a description of one: it is the same
    tuple, rendered by the same function. That is what makes PREVIEW a rehearsal.
    """

    added: tuple[int, ...] = ()
    removed: tuple[int, ...] = ()
    brought_back: tuple[int, ...] = ()
    share_changes: tuple[ShareChangeResponse, ...] = ()
    desired: tuple[OfferShare, ...] = ()

    @classmethod
    def of(cls, diff: DraftDiff) -> DraftDiffResponse:
        """Render one comparison, which is the only way this model is built."""
        return cls(
            added=tuple(int(offer_id) for offer_id in diff.added),
            removed=tuple(int(offer_id) for offer_id in diff.removed),
            brought_back=tuple(int(offer_id) for offer_id in diff.brought_back),
            share_changes=tuple(
                ShareChangeResponse(offer_id=change.offer_id, was=change.was, now=change.now)
                for change in diff.share_changes
            ),
            desired=tuple(
                OfferShare(offer_id=row.offer_id, share=row.share, state=row.state.value)
                for row in diff.desired
            ),
        )


class StreamResponse(BaseModel):
    """One flow: its heading, its rows already ordered, and whether PUSH may be pressed.

    `absent` is a flow Keitaro has stopped returning. It is still drawn, because a flow that
    vanished from the tracker is something to be told about rather than something to hide.
    """

    keitaro_stream_id: int
    name: str
    flow_schema: str = Field(
        serialization_alias="schema",
        description="`landings` is the only one that rotates offers. Flow 1 is a `redirect`.",
    )
    position: int | None = None
    filters: tuple[StreamFilterResponse, ...] = ()
    absent: bool = False
    rows: tuple[StreamRowResponse, ...] = ()
    dirty: bool = False
    diff: DraftDiffResponse | None = None
    can_push: bool = False
    block_reason: str | None = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def of(cls, flow: StreamEditorView, *, tracker: str) -> StreamResponse:
        """Render one flow exactly as the editor assembled it, sorting and deciding nothing."""
        stream = flow.stream
        return cls(
            keitaro_stream_id=int(stream.keitaro_stream_id),
            name=stream.name,
            flow_schema=stream.schema.value,
            position=stream.position,
            filters=tuple(StreamFilterResponse.of(row) for row in stream.filters),
            absent=stream.absent,
            rows=tuple(StreamRowResponse.of(row, tracker=tracker) for row in flow.rows),
            dirty=flow.dirty,
            diff=None if flow.diff is None else DraftDiffResponse.of(flow.diff),
            can_push=flow.can_push,
            block_reason=flow.block_reason,
            warnings=flow.warnings,
        )


class StreamsResponse(BaseModel):
    """The whole editor screen: the campaign in the toolbar, and its flows below it."""

    campaign: CampaignResponse
    streams: tuple[StreamResponse, ...]

    @classmethod
    def of(cls, view: EditorView, *, tracker: str) -> StreamsResponse:
        """Render one campaign's editor screen."""
        return cls(
            campaign=CampaignResponse.of(view.campaign),
            streams=tuple(StreamResponse.of(flow, tracker=tracker) for flow in view.streams),
        )
