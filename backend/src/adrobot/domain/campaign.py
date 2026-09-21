"""A Keitaro campaign as this service reads one, and the blueprint part 1 builds one from.

Two fields are missing from `Campaign` and both absences are decisions.

`token` is the campaign's Click API token, which Keitaro puts inside the campaign object
itself. A field that exists is a field that reaches a log line, a mirror row and an API
response, and nothing here needs it — so it stops at the wire model and is never carried.

`domain_id` is missing because the tracker does not give it back: `CampaignRequest` takes
one on the write and `Campaign` declares none on the read. The public link of a campaign is
therefore built from the domain we sent, not from one we can ask for afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from adrobot.domain.diff import DesiredOffer
from adrobot.domain.ids import KeitaroCampaignId, OfferId
from adrobot.domain.shares import OfferRow, redistribute
from adrobot.domain.stream import (
    FilterMode,
    StreamFilter,
    StreamSchema,
    StreamSpec,
    StreamType,
)
from adrobot.domain.values import CampaignAlias, CampaignName, CountryCode, OfferState

GEO_REDIRECT_URL: Final = "https://google.com"
"""Where Flow 1 sends the traffic it catches. The task names this address, so it is a
constant of the specification and not a default somebody may one day want to configure."""

GEO_FILTER: Final = "country"
"""The tracker's own name for the condition Flow 1 filters on."""

HTTP_ACTION: Final = "http"
"""The action type both flows are created with. Flow 2 rotates offers and does nothing with
an action at all, but `action_type` is sent for it too: the published schema marks no field
of a flow as required, which is the schema declining to say, and a create refused for a
missing field would be found on a reviewer's first campaign."""

FIRST_FLOW: Final = "Flow 1"
SECOND_FLOW: Final = "Flow 2"
"""The names from the reference campaign, spelled exactly as the video shows them: this is
the first thing anyone compares, and matching it costs nothing."""


class CampaignRotation(Enum):
    """How a campaign picks between its flows.

    `position` is what part 1 builds: Flow 1 catches the geo and Flow 2 takes the rest, in
    that order. `weight` turns the same two flows into a split test, which this service has
    no screen for.
    """

    POSITION = "position"
    WEIGHT = "weight"


class CampaignSetupStatus(Enum):
    """How far part 1 got with this campaign.

    The tracker cannot answer it: the campaign exists there either way, and only a flow
    that was never created tells the two apart.
    """

    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"


class CostType(Enum):
    """The three cost models the write schema accepts, out of the eight it reads back."""

    CPC = "CPC"
    CPUC = "CPUC"
    CPM = "CPM"


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignBlueprint:
    """Everything `POST /campaigns` is told, with our own values already validated.

    `name` and `alias` arrive as the validated types rather than as strings: the alias
    becomes the path segment of a public link and the name is user input that has been
    through the invisible-character filter. The three reference ids are plain integers —
    they come from the tracker's own catalogues a moment earlier, and validating somebody
    else's identifier would only invent a way to fail.
    """

    name: CampaignName
    alias: CampaignAlias
    group_id: int
    traffic_source_id: int | None = None
    domain_id: int | None = None
    rotation: CampaignRotation = CampaignRotation.POSITION
    cost_type: CostType = CostType.CPC
    cookies_ttl: int = 24


@dataclass(frozen=True, slots=True, kw_only=True)
class Campaign:
    """A campaign as the tracker gave it to us.

    `alias` is a plain `str` and not a `CampaignAlias`: ours are generated from a safe
    alphabet, but the editor has to open campaigns made by hand — campaign 93212 from the
    video among them — and refusing to read one because somebody typed a capital letter
    into its alias would be this service failing at the one thing part 2 asks of it.
    """

    id: KeitaroCampaignId
    alias: str
    name: str
    state: str
    group_id: int | None = None
    traffic_source_id: int | None = None
    cookies_ttl: int | None = None
    cost_type: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Group:
    """A campaign group. Part 1 puts what it creates in one, making it if there is none."""

    id: int
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TrafficSource:
    """A traffic source, without the postback URL the tracker returns beside it."""

    id: int
    name: str
    state: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackerDomain:
    """A domain a campaign's public link is built on.

    Prefixed because `domain` is already the name of this ring; the tracker's word for it
    is the unqualified one.
    """

    id: int
    name: str
    state: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceData:
    """The three catalogues part 1 picks from, read in one go and cached for a few minutes.

    Together rather than three separate calls because they are wanted together, on the one
    screen, at the one moment — and because a campaign built from a group that resolved and
    a domain that did not is the partial failure §5 has to compensate for.
    """

    campaign_groups: tuple[Group, ...] = ()
    traffic_sources: tuple[TrafficSource, ...] = ()
    domains: tuple[TrackerDomain, ...] = ()


def public_link(domain: str, alias: str) -> str:
    """Build a campaign's public link from the domain it was created on and its alias.

    Assembled here because the tracker will not assemble it: `Campaign` carries no
    `domain_id`, so the only moment this is knowable is the create, and the only place it is
    stored is our own row. `https` and not the domain's own `is_ssl`, which this service
    does not read: every Keitaro domain this wrapper can reach is one its own settings
    already insist be https, and an http link printed into a browser would be the wrong
    half of the guess.
    """
    return f"https://{domain}/{alias}"


def flows_for(
    campaign_id: KeitaroCampaignId, *, country: CountryCode, offer_id: OfferId
) -> tuple[StreamSpec, StreamSpec]:
    """Describe the two flows part 1 builds, in the order they are dispatched in.

    Flow 1 catches the campaign's own country and sends it away; Flow 2 takes everything
    else and rotates the offers, which is the flow part 2 then edits. The campaign's
    rotation is `position`, so the order below is the behaviour and not a presentation
    detail — Flow 1 is tried first because its position says 1.

    The single offer's share goes through `redistribute()` rather than being written as
    100. It is one row and the answer is not in doubt; what is in doubt is the next person
    to touch this function, and the rule they must not break is that no share in this
    service is arrived at anywhere but in `shares.py`.
    """
    seeded = redistribute((OfferRow(offer_id=offer_id, seq=0, activated_at=0),))
    return (
        StreamSpec(
            campaign_id=campaign_id,
            name=FIRST_FLOW,
            type=StreamType.REGULAR,
            schema=StreamSchema.REDIRECT,
            action_type=HTTP_ACTION,
            position=1,
            action_payload=GEO_REDIRECT_URL,
            filters=(StreamFilter(name=GEO_FILTER, mode=FilterMode.ACCEPT, payload=(country,)),),
        ),
        StreamSpec(
            campaign_id=campaign_id,
            name=SECOND_FLOW,
            type=StreamType.REGULAR,
            schema=StreamSchema.LANDINGS,
            action_type=HTTP_ACTION,
            position=2,
            offers=tuple(
                DesiredOffer(offer_id=row.offer_id, share=row.share, state=OfferState.ACTIVE)
                for row in seeded
            ),
        ),
    )
