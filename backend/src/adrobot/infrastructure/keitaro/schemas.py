"""The Keitaro payloads, as models rather than as dictionaries passed around by hope.

Only `mapping.py` may import this module; `backend/.importlinter` enforces it. The `Kt*`
models are the wire format of somebody else's API, and the day one of them reaches a use
case is the day the tracker's field names become this project's vocabulary.

Two configurations, because reading and writing are not the same risk:

*   **Reads ignore what they do not know.** A field Keitaro adds must not fail a campaign
    the editor is trying to open, and a field it omits must not either — so nearly
    everything carries a default. What a read model does *not* carry is as deliberate as
    what it does: `KtCampaign` has no `token`, so a campaign's Click API token is dropped
    at the boundary and cannot reach a log, a mirror row or an API response, whatever a
    later commit does with the object.
*   **Writes forbid what they do not know.** These are our own payloads, and a typo in a
    field name that the tracker would silently ignore is exactly the failure this project
    cannot afford — it looks like a successful push that changed nothing.

The published schema is wrong in places, and each of those is a model decision here rather
than a surprise later. They are listed in `docs/keitaro-api-notes.md` §2; the three that
shape this file are a filter payload typed `string` on read and `array` on write, an
`action_payload` that is `string | object` on read and declared in no request schema at
all, and a `share` that is — mercifully — an integer on both sides.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Keitaro's own default for a flow that exists: the write schema declares `state` with
# `default: active`, so a row or a flow that arrives without one is one taking traffic.
# Guessing "disabled" here would quietly take an offer out of the division.
ACTIVE: Final = "active"


def _as_values(raw: object) -> object:
    """Return a payload as a list of strings, whichever of the two shapes it arrived in.

    `Filter.payload` is typed `string` on read and `array` on write — one of the schema's
    own contradictions — and `Offer.country` can arrive as `null`. This is the same rule
    the stage-2 probe measured with, deliberately: splitting on commas and newlines is what
    turns a tracker that answers `"AU,NZ"` into one this service can read.
    """
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]
    return []


class KtReadModel(BaseModel):
    """Base of everything read from the tracker: tolerant of extra fields, frozen once built."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class KtWriteModel(BaseModel):
    """Base of everything sent to the tracker."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    def body(self) -> dict[str, Any]:
        """Render this model as the body to send.

        A method rather than a rule to remember at each call site, because all three parts
        of it are load-bearing: `by_alias` is what turns `flow_schema` back into `schema`
        and `from_` into `from`; `exclude_none` is what keeps an unset field out of a body
        that might be a full replacement; and `mode="json"` is what makes the tuples lists.

        `exclude_defaults` deliberately not: `collect_clicks=False` and `filter_or=False`
        are values this service means, and dropping them would leave the tracker's own
        defaults to decide whether a flow records the clicks the statistics screen reads.

        Named `body` and not `payload` because `payload` is a field name on two of the
        models below — a method here would shadow it, and pydantic reports that as a
        warning at class-definition time, which this suite turns into an ImportError.
        """
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class KtGroup(KtReadModel):
    """A group. `type` says which catalogue it groups — campaigns, offers, landings."""

    id: int
    name: str = ""
    type: str | None = None


class KtSource(KtReadModel):
    """A traffic source, without the `postback_url` the tracker returns beside it."""

    id: int
    name: str = ""
    state: str | None = None


class KtDomain(KtReadModel):
    """A domain. `name` is the host a campaign's public link is built on."""

    id: int
    name: str = ""
    state: str | None = None


class KtOffer(KtReadModel):
    """One offer of the catalogue. `preview_path` arrives relative, and stays that way."""

    id: int
    name: str = ""
    state: str = ACTIVE
    group_id: int | None = None
    country: tuple[str, ...] = ()
    affiliate_network: str | None = None
    preview_path: str | None = None

    _countries = field_validator("country", mode="before")(_as_values)


class KtCampaign(KtReadModel):
    """A campaign, with the one field this service refuses to carry left out.

    Keitaro puts the campaign's Click API token inside the campaign object. There is no
    `token` field here and there never will be: `extra="ignore"` drops it at the boundary,
    which is a stronger guarantee than remembering not to log it.
    """

    id: int
    alias: str = ""
    name: str = ""
    state: str = ACTIVE
    group_id: int | None = None
    traffic_source_id: int | None = None
    cookies_ttl: int | None = None
    cost_type: str | None = None
    created_at: str | None = None


class KtFilter(KtReadModel):
    """One condition on a flow, as read. `id` is what lets an update edit it in place."""

    id: int | None = None
    name: str = ""
    mode: str = ""
    payload: tuple[str, ...] = ()

    _payload_values = field_validator("payload", mode="before")(_as_values)


class KtOfferStream(KtReadModel):
    """One offer row of a flow, as read.

    `created_at` stays a string. Keitaro serialises it without an offset, in the tracker's
    own zone, so the only honest place to attach one is `mapping.py`, which knows which
    zone that is — and a timestamp this model could not parse would otherwise fail the read
    of the whole campaign.
    """

    id: int | None = None
    stream_id: int | None = None
    offer_id: int
    share: int = 0
    state: str = ACTIVE
    created_at: str | None = None


class KtLandingStream(KtReadModel):
    """A landing page inside a flow, read so that a push can give it back untouched."""

    id: int | None = None
    landing_id: int
    share: int = 0
    state: str = ACTIVE


class KtStream(KtReadModel):
    """A flow, as read.

    `flow_schema` carries the tracker's `schema` under another name, and that is not taste:
    a pydantic field called `schema` shadows an attribute of `BaseModel` and warns about it
    at class-definition time, which this suite's `filterwarnings = ["error"]` turns into an
    ImportError. The alias keeps the wire spelling where it belongs — on the wire.

    `offer_selection` is not here: it is readable and absent from every request schema, so
    a model that carried it would suggest a push could put it back.
    """

    id: int
    campaign_id: int
    name: str = ""
    type: str = ""
    flow_schema: str = Field(default="", alias="schema")
    action_type: str = ""
    action_payload: str | dict[str, Any] | None = None
    position: int | None = None
    weight: float | None = None
    state: str = ACTIVE
    collect_clicks: bool = False
    filter_or: bool = False
    comments: str | None = None
    filters: tuple[KtFilter, ...] = ()
    landings: tuple[KtLandingStream, ...] = ()
    offers: tuple[KtOfferStream, ...] = ()


class KtReport(KtReadModel):
    """A built report, kept to the one field the statistics screen reads.

    The published schema types `rows` as an array of strings, which it is not — the rows
    are objects, and everything else the endpoint answers with (`total`, `meta`) is dropped
    here rather than modelled from a document already known to be wrong about this shape.
    """

    rows: tuple[dict[str, Any], ...] = ()


class KtGroupCreate(KtWriteModel):
    """`POST /groups`. `type` is always sent: the schema marks it required and defaulted at once."""

    name: str
    type: str = "campaigns"


class KtCampaignCreate(KtWriteModel):
    """`POST /campaigns`.

    `group_id` is a **string**, which is the schema's own asymmetry: a campaign takes its
    group as a string on the write and gives it back as an integer on the read. Typing it
    honestly here is what keeps the conversion in one place instead of at each call site.
    """

    name: str
    alias: str
    group_id: str
    type: str = "position"
    cost_type: str = "CPC"
    cookies_ttl: int = 24
    state: str = ACTIVE
    traffic_source_id: int | None = None
    domain_id: int | None = None


class KtFilterWrite(KtWriteModel):
    """A flow filter, as written: `payload` is an array here and a string on the way back."""

    name: str
    mode: str
    payload: tuple[str, ...] = ()
    id: int | None = None


class KtOfferWrite(KtWriteModel):
    """One offer row of a flow, as written.

    `state` is not optional in this model although the schema makes it so. A removed row
    travels as `{share: 0, state: "disabled"}`, and that pair is the whole mechanism by
    which a removal survives a tracker that merges arrays instead of replacing them.
    """

    offer_id: int
    share: int
    state: str


class KtLandingWrite(KtWriteModel):
    """A landing page of a flow, as written — read from the flow and handed straight back."""

    landing_id: int
    share: int
    state: str = ACTIVE


class KtStreamWrite(KtWriteModel):
    """`POST /streams` and `PUT /streams/{id}`, which are the same body.

    They are the same body because nothing says otherwise: `StreamRequest` requires five
    fields on the create and `StreamRequestPut` requires none on the update, while being a
    bare reference to the same object. One model for both means an update cannot quietly
    carry less than a create.

    `action_payload` is sent although no request schema declares it — a redirect flow is
    nothing but its payload. Whether the create keeps it is the `create` probe's question;
    the answer decides whether part 1 is one call or two, not what this model looks like.
    """

    campaign_id: int
    name: str
    type: str
    flow_schema: str = Field(serialization_alias="schema", validation_alias="schema")
    action_type: str
    position: int | None = None
    weight: float | None = None
    state: str = ACTIVE
    action_payload: str | dict[str, Any] | None = None
    collect_clicks: bool = True
    filter_or: bool = False
    comments: str | None = None
    filters: tuple[KtFilterWrite, ...] = ()
    landings: tuple[KtLandingWrite, ...] = ()
    offers: tuple[KtOfferWrite, ...] = ()


class KtRange(KtWriteModel):
    """The period of a report. `from` is a keyword in Python and a field name in Keitaro."""

    timezone: str
    interval: str | None = None
    from_: str | None = Field(default=None, serialization_alias="from", validation_alias="from")
    to: str | None = None


class KtReportFilter(KtWriteModel):
    """A **report** filter: `{name, operator, expression}`.

    Not to be confused with `KtFilterWrite`, which is a **flow** filter: `{name, mode,
    payload}`. Two schemas a word apart in the published document, and mixing them up
    surfaces only on the statistics screen.
    """

    name: str
    operator: str
    expression: str | int


class KtSort(KtWriteModel):
    """One sort clause, so that the screen does not re-sort what the tracker already can."""

    name: str
    order: str


class KtReportRequest(KtWriteModel):
    """`POST /report/build`, in the dialect the published schema declares.

    Two shipping clients send `grouping` and `metrics` where this sends `dimensions` and
    `measures`. The schema is the tie-breaker until the `report` probe answers; if this
    build speaks the other dialect, the two aliases below are the whole of the change.
    """

    range: KtRange
    dimensions: tuple[str, ...]
    measures: tuple[str, ...]
    filters: tuple[KtReportFilter, ...] = ()
    sort: tuple[KtSort, ...] = ()
