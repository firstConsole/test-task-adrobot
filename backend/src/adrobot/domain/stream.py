"""A Keitaro flow as this service reads one, and the specification that writes one back.

Two types with deliberately identical field names. `Stream` is a flow as the tracker holds
it; `StreamSpec` is a flow as we ask for one. Nothing in the published schema promises that
`PUT /streams/{id}` is partial — `StreamRequestPut` is a bare `$ref` with no required
fields at all — so an update resends the flow whole, and the way to be sure nothing is
dropped on the way out is for the two types to carry the same fields. `respecified` is the
only function allowed to turn one into the other, and `tests/domain/test_tracker_types.py`
holds the two field sets together.

The stakes are concrete: the reference campaign's Flow 2 carries `country accept ["AU"]`
and is exactly the flow the editor pushes to. If a push omits `filters` and this build
replaces rather than merges, pressing PUSH TO KT silently drops the campaign's geo
targeting — a defect nobody would attribute to the button they pressed.

`triggers` is the one exception, and it is the schema's fault: the read model spells the
field `taget` where the write model requires `target`, so a trigger cannot be round-tripped
through the schema as published. Flows this service creates have none.

Which fields are enums and which stay `str` follows one rule: a closed set the tracker
itself defines **on write** becomes an enum, because a value outside it is a flow we could
not write back even if we wanted to, and failing at the read with a clear message beats
failing at the push. Everything else — `state` above all — stays a string, because the
mirror of somebody else's data is tolerant.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from adrobot.domain.diff import DesiredOffer
from adrobot.domain.errors import DuplicateOfferRowError
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId


class StreamSchema(Enum):
    """What a flow does with a click it accepts.

    `landings` is the only one that rotates offers, which is why Flow 1 renders no offer
    table in the editor: an `offers[]` array on a redirect flow is ignored by the tracker.
    """

    LANDINGS = "landings"
    REDIRECT = "redirect"
    ACTION = "action"


class StreamType(Enum):
    """Where a flow sits in its campaign's dispatch: the ordinary case, forced, or default."""

    REGULAR = "regular"
    FORCED = "forced"
    DEFAULT = "default"


class FilterMode(Enum):
    """Whether a filter lets the click through or turns it away."""

    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamFilter:
    """One condition on a flow — `country accept ["AU"]` in the reference campaign.

    `id` travels back on an update on purpose: the write schema says to provide it when
    updating a filter, so resending it edits the filter in place instead of leaving a
    tracker that merges arrays with two copies of the same condition.
    """

    name: str
    mode: FilterMode
    payload: tuple[str, ...] = ()
    id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamLanding:
    """A landing page inside a flow. This service never sets one, and never loses one."""

    landing_id: int
    share: int
    state: str = "active"


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamOffer:
    """One offer row of a flow, as the tracker holds it.

    `share` is a plain `int` rather than a `Share`, and `state` a plain `str` rather than
    an `OfferState`: this is a mirror of somebody else's row, and refusing a number the
    tracker is already sending traffic on would make an existing campaign impossible to
    open. Anything but `active` takes no traffic.

    `created_at` is what orders rows that came from the tracker when the rounding remainder
    is handed out, and `row_id` is the row's own identity in Keitaro — which is how a push
    can tell a row that survived a write from one that was recreated underneath it.
    """

    offer_id: OfferId
    share: int
    state: str
    row_id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamSpec:
    """A flow as this service asks for one, on a create and on an update alike.

    Strict where `Stream` is tolerant: these are our own values, and the duplicate check
    below is the last gate before the tracker's own unique key would answer with a 406.
    """

    campaign_id: KeitaroCampaignId
    name: str
    type: StreamType
    schema: StreamSchema
    action_type: str
    position: int | None = None
    weight: float | None = None
    state: str = "active"
    action_payload: str | None = None
    collect_clicks: bool = True
    filter_or: bool = False
    comments: str | None = None
    filters: tuple[StreamFilter, ...] = ()
    landings: tuple[StreamLanding, ...] = ()
    offers: tuple[DesiredOffer, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a specification naming one offer twice, whatever assembled it."""
        counted = Counter(row.offer_id for row in self.offers)
        duplicated = {offer_id for offer_id, times in counted.items() if times > 1}
        if duplicated:
            raise DuplicateOfferRowError(duplicated)


@dataclass(frozen=True, slots=True, kw_only=True)
class Stream:
    """A flow as the tracker gave it to us."""

    id: KeitaroStreamId
    campaign_id: KeitaroCampaignId
    name: str
    type: StreamType
    schema: StreamSchema
    action_type: str
    position: int | None = None
    weight: float | None = None
    state: str = "active"
    action_payload: str | None = None
    collect_clicks: bool = True
    filter_or: bool = False
    comments: str | None = None
    filters: tuple[StreamFilter, ...] = ()
    landings: tuple[StreamLanding, ...] = ()
    offers: tuple[StreamOffer, ...] = ()

    def respecified(self, offers: tuple[DesiredOffer, ...]) -> StreamSpec:
        """Return the specification that writes this flow back with `offers` in place of its own.

        Every other field is copied across unread. That is the whole point: a push is a
        write of the entire flow, and each field left out here is one the tracker may take
        as a reset.
        """
        return StreamSpec(
            campaign_id=self.campaign_id,
            name=self.name,
            type=self.type,
            schema=self.schema,
            action_type=self.action_type,
            position=self.position,
            weight=self.weight,
            state=self.state,
            action_payload=self.action_payload,
            collect_clicks=self.collect_clicks,
            filter_or=self.filter_or,
            comments=self.comments,
            filters=self.filters,
            landings=self.landings,
            offers=offers,
        )
