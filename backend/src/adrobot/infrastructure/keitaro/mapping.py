"""The border between Keitaro's payloads and this project's own types, and the only crossing.

`backend/.importlinter` makes this the only module allowed to import `schemas.py`, so
everything the tracker says is turned into a domain object here or not at all. What that
buys is a single place to answer three questions the rest of the code should never ask:

*   **Which shape did it really arrive in.** A filter's payload is a string on read and an
    array on write; an offer's countries can be null; a timestamp has no offset.
*   **What to do when it does not parse.** A 200 whose body cannot be read is not a
    success, and the failure belongs to the tracker rather than to us — `UpstreamProtocolError`
    and not a traceback out of pydantic.
*   **What never to repeat.** A `ValidationError`'s own message prints `input_value=` for
    every failing field, and the input here is a tracker response: a campaign object
    carries a Click API token. `_why` below rebuilds the message from the field locations
    and the messages alone, which is the same precaution `settings.py` takes at boot, one
    ring further out.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from adrobot.application.errors import UpstreamProtocolError
from adrobot.domain.campaign import Campaign, Group, ReferenceData, TrackerDomain, TrafficSource
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.offer import Offer
from adrobot.domain.stream import (
    Stream,
    StreamFilter,
    StreamLanding,
    StreamOffer,
    StreamSchema,
    StreamType,
)
from adrobot.infrastructure.keitaro.schemas import (
    KtCampaign,
    KtCampaignCreate,
    KtDomain,
    KtFilterWrite,
    KtGroup,
    KtGroupCreate,
    KtLandingWrite,
    KtOffer,
    KtOfferWrite,
    KtReadModel,
    KtSource,
    KtStream,
    KtStreamWrite,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import tzinfo

    from adrobot.domain.campaign import CampaignBlueprint
    from adrobot.domain.stream import StreamSpec


def to_campaign(raw: object, *, zone: tzinfo) -> Campaign:
    """Read one campaign. Its Click API token is dropped by the wire model, not here."""
    wire = _validated(KtCampaign, raw)
    return Campaign(
        id=KeitaroCampaignId(wire.id),
        alias=wire.alias,
        name=wire.name,
        state=wire.state,
        group_id=wire.group_id,
        traffic_source_id=wire.traffic_source_id,
        cookies_ttl=wire.cookies_ttl,
        cost_type=wire.cost_type,
        created_at=_moment(wire.created_at, zone),
    )


def to_group(raw: object) -> Group:
    """Read one campaign group, as `POST /groups` answers with."""
    wire = _validated(KtGroup, raw)
    return Group(id=wire.id, name=wire.name)


def to_reference_data(*, groups: object, sources: object, domains: object) -> ReferenceData:
    """Read the three catalogues part 1 picks a campaign's group, source and domain from."""
    return ReferenceData(
        campaign_groups=tuple(to_group(row) for row in _rows(groups, "campaign groups")),
        traffic_sources=tuple(
            TrafficSource(id=wire.id, name=wire.name, state=wire.state)
            for wire in _each(KtSource, _rows(sources, "traffic sources"))
        ),
        domains=tuple(
            TrackerDomain(id=wire.id, name=wire.name, state=wire.state)
            for wire in _each(KtDomain, _rows(domains, "domains"))
        ),
    )


def to_offers(raw: object) -> tuple[Offer, ...]:
    """Read the offer catalogue, which this API only ever answers with in full."""
    return tuple(
        Offer(
            id=OfferId(wire.id),
            name=wire.name,
            state=wire.state,
            country=wire.country,
            group_id=wire.group_id,
            affiliate_network=wire.affiliate_network,
            preview_path=wire.preview_path,
        )
        for wire in _each(KtOffer, _rows(raw, "offers"))
    )


def to_stream(raw: object, *, zone: tzinfo) -> Stream:
    """Read one flow, with its filters, landings and offers."""
    return _stream(_validated(KtStream, raw), zone=zone)


def to_streams(raw: object, *, zone: tzinfo) -> tuple[Stream, ...]:
    """Read every flow of a campaign, in the order the tracker listed them."""
    return tuple(_stream(wire, zone=zone) for wire in _each(KtStream, _rows(raw, "flows")))


def campaign_body(blueprint: CampaignBlueprint) -> dict[str, Any]:
    """Render `POST /campaigns`, with the group as the string the write schema wants."""
    return KtCampaignCreate(
        name=str(blueprint.name),
        alias=str(blueprint.alias),
        group_id=str(blueprint.group_id),
        type=blueprint.rotation.value,
        cost_type=blueprint.cost_type.value,
        cookies_ttl=blueprint.cookies_ttl,
        traffic_source_id=blueprint.traffic_source_id,
        domain_id=blueprint.domain_id,
    ).body()


def group_body(name: str) -> dict[str, Any]:
    """Render `POST /groups` for the campaign group part 1 creates when there is none."""
    return KtGroupCreate(name=name).body()


def stream_body(spec: StreamSpec) -> dict[str, Any]:
    """Render `POST /streams` and `PUT /streams/{id}`, which are the same body.

    Every field of the specification travels, including the ones this service never sets
    itself. On an update that is the whole point: nothing promises a `PUT` is partial, so a
    field left out of the body is a field that may come back as the tracker's default.
    """
    return KtStreamWrite(
        campaign_id=int(spec.campaign_id),
        name=spec.name,
        type=spec.type.value,
        flow_schema=spec.schema.value,
        action_type=spec.action_type,
        position=spec.position,
        weight=spec.weight,
        state=spec.state,
        action_payload=_payload(spec.action_payload),
        collect_clicks=spec.collect_clicks,
        filter_or=spec.filter_or,
        comments=spec.comments,
        filters=tuple(
            KtFilterWrite(name=row.name, mode=row.mode, payload=row.payload, id=row.id)
            for row in spec.filters
        ),
        landings=tuple(
            KtLandingWrite(landing_id=row.landing_id, share=row.share, state=row.state)
            for row in spec.landings
        ),
        offers=tuple(
            KtOfferWrite(offer_id=int(row.offer_id), share=row.share, state=row.state.value)
            for row in spec.offers
        ),
    ).body()


def _payload(value: str | Mapping[str, object] | None) -> str | dict[str, Any] | None:
    """Hand a flow's action payload back in whichever of its two shapes it came in."""
    return dict(value) if isinstance(value, Mapping) else value


def _stream(wire: KtStream, *, zone: tzinfo) -> Stream:
    return Stream(
        id=KeitaroStreamId(wire.id),
        campaign_id=KeitaroCampaignId(wire.campaign_id),
        name=wire.name,
        type=_member(StreamType, wire.type, "flow type", wire.id),
        schema=_member(StreamSchema, wire.flow_schema, "flow schema", wire.id),
        action_type=wire.action_type,
        position=wire.position,
        weight=wire.weight,
        state=wire.state,
        action_payload=wire.action_payload,
        collect_clicks=wire.collect_clicks,
        filter_or=wire.filter_or,
        comments=wire.comments,
        filters=tuple(
            StreamFilter(name=row.name, mode=row.mode, payload=row.payload, id=row.id)
            for row in wire.filters
        ),
        landings=tuple(
            StreamLanding(landing_id=row.landing_id, share=row.share, state=row.state)
            for row in wire.landings
        ),
        offers=tuple(
            StreamOffer(
                offer_id=OfferId(row.offer_id),
                share=row.share,
                state=row.state,
                row_id=row.id,
                created_at=_moment(row.created_at, zone),
            )
            for row in wire.offers
        ),
    )


def _member[E: StreamType | StreamSchema](
    enumeration: type[E], value: str, what: str, stream_id: int
) -> E:
    """Read a closed set the tracker itself defines, and refuse a value outside it.

    Refusing is the point. These two decide what a flow *is* — whether it rotates offers at
    all — so a value this build has never heard of is a flow the editor would draw wrongly
    and push back worse. A filter's mode, which nothing branches on, is read as a plain
    string instead.
    """
    try:
        return enumeration(value)
    except ValueError as exc:
        unknown = (
            f"flow {stream_id} has a {what} this service does not know: {value!r}. "
            f"Known: {', '.join(member.value for member in enumeration)}"
        )
        raise UpstreamProtocolError(unknown) from exc


def _moment(raw: str | None, zone: tzinfo) -> datetime | None:
    """Read one of the tracker's timestamps, which arrive without an offset.

    The zone is the tracker's own, from `ADROBOT_KEITARO_TIMEZONE`. Reading these as UTC is
    what moves the boundary of "clicks today" by a few hours, and here it would also
    reorder the rows the rounding remainder is handed out by.

    An unreadable stamp becomes `None` rather than an error: it costs the tie-break its
    ordering for that row, where refusing would cost the campaign its editor.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=zone)


def _validated[M: KtReadModel](model: type[M], raw: object) -> M:
    """Build one wire model, turning a body this service cannot read into the tracker's fault."""
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        unreadable = f"a {model.__name__} could not be read: {_why(exc)}"
        raise UpstreamProtocolError(unreadable) from exc


def _each[M: KtReadModel](model: type[M], rows: Iterable[object]) -> tuple[M, ...]:
    return tuple(_validated(model, row) for row in rows)


def _rows(raw: object, what: str) -> list[object]:
    """Insist that a listing really is a list.

    Every list endpoint of this API answers with a bare JSON array — no envelope, no paging
    metadata. A build that wrapped one would otherwise surface as an empty screen.
    """
    if not isinstance(raw, list):
        envelope = f"the list of {what} came back as {type(raw).__name__}, not as an array"
        raise UpstreamProtocolError(envelope)
    return raw


def _why(exc: ValidationError) -> str:
    """Say which fields failed and how, and never what was in them.

    `str(ValidationError)` renders `input_value=` for every error it reports, and the input
    is whatever the tracker just sent — which, for a campaign, includes its Click API
    token. Only the location and the message are taken.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
        for error in exc.errors(include_url=False)
    )
