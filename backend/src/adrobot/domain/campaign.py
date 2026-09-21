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

from adrobot.domain.ids import KeitaroCampaignId
from adrobot.domain.values import CampaignAlias, CampaignName


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
