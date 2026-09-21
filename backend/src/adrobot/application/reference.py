"""Which group, traffic source and domain a new campaign is built on.

Part 1 asks for three things — a name, a country and an offer — and Keitaro needs three
more before it will create anything. The task's own wording is that they are "also in the
API", so they are read from it rather than pinned in the environment: a tracker that gains
a domain needs no redeploy, and a deployment cannot name an identifier the tracker never
had.

Three decisions are worth the paragraph each.

**The reference data is cached for five minutes, and the cache is the reason this is an
object.** Creating a campaign is three writes preceded by three reads, and those reads
change about once a month. The instance is built once per process (6.6) and shared by every
request, so a burst of creates pays for one round of reads.

**The group is created if there is none, and never a second time.** Two requests arriving
together on a tracker with no campaign groups would each read "none" and each create one,
leaving two groups named the same. The lock below is what makes that a single create, and
it is held across the tracker calls deliberately: it guards a write, not a dictionary.

**A missing source or domain is not a failure.** `traffic_source_id` and `domain_id` are
optional on the create, so a tracker that has neither still gets its campaign — one without
attribution, and one whose public link this service cannot build, which is visible on the
screen rather than fatal at the moment the button is pressed. A missing *group* is the
opposite, because `group_id` is not optional; that is why it is the one this creates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Protocol

from adrobot.domain.campaign import TrackerDomain

if TYPE_CHECKING:
    from datetime import datetime

    from adrobot.application.ports.keitaro import KeitaroAdminPort
    from adrobot.application.ports.system import Clock
    from adrobot.domain.campaign import Group

CAMPAIGN_GROUP_NAME: Final = "AD Robot"
"""The group this service creates when a tracker has none, and prefers when it has many."""

REFERENCE_TTL: Final = timedelta(minutes=5)
"""How long a read of the three catalogues is believed. Long enough that a burst of creates
reads once, short enough that a domain added in the tracker becomes usable without a
restart — and this is the whole of the staleness, because nothing else here is cached."""

ACTIVE: Final = "active"
"""The one state the tracker calls usable. Compare against it, and treat an absent state as
usable too: `Domain.state` is documented `active | deleted`, `Source.state` is an
undocumented string, and a build that answers without the field at all would otherwise
leave every source and every domain looking deleted."""


class _Listed(Protocol):
    """A row of a tracker catalogue, as far as choosing between them needs to see one."""

    @property
    def state(self) -> str | None:
        """Whether the tracker still has this row, in its own vocabulary."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignReferences:
    """What `POST /campaigns` needs beyond the three fields a person filled in.

    The domain travels whole rather than as an id, because both halves are wanted: the id
    goes into the create, and the name becomes the host of the public link — which the
    tracker will not give back afterwards, so this is the one moment it can be recorded.
    """

    group_id: int
    traffic_source_id: int | None = None
    domain: TrackerDomain | None = None


class ReferenceResolver:
    """The three catalogues, read at most once every `ttl` and shared by every request."""

    def __init__(
        self,
        admin: KeitaroAdminPort,
        clock: Clock,
        *,
        ttl: timedelta = REFERENCE_TTL,
        group_name: str = CAMPAIGN_GROUP_NAME,
    ) -> None:
        self._admin = admin
        self._clock = clock
        self._ttl = ttl
        self._group_name = group_name
        self._held: tuple[CampaignReferences, datetime] | None = None
        # Built here although there is no running loop yet, which asyncio has allowed since
        # 3.10: a lock no longer binds itself to a loop until it is first awaited.
        self._lock = asyncio.Lock()

    async def resolve(self) -> CampaignReferences:
        """Answer with the group, source and domain to build the next campaign on.

        Reads the tracker only when nothing fresh is held. A caller that finds the cache
        cold while another call is already filling it waits for that one instead of asking
        again, so the group is created once however many creates arrive together.
        """
        fresh = self._fresh()
        if fresh is not None:
            return fresh
        async with self._lock:
            # Asked again inside the lock: whoever held it may have just filled the cache,
            # and without this re-read that whole queue would go on to read the tracker.
            fresh = self._fresh()
            if fresh is not None:
                return fresh
            resolved = await self._read()
            self._held = (resolved, self._clock.now())
            return resolved

    def forget(self) -> None:
        """Drop what is held, so that the next resolve reads the tracker again.

        For the operator who has just added the domain this service told them was missing:
        without it the tracker is right and this process is wrong for another five minutes.
        """
        self._held = None

    def _fresh(self) -> CampaignReferences | None:
        if self._held is None:
            return None
        resolved, read_at = self._held
        if self._clock.now() - read_at >= self._ttl:
            return None
        return resolved

    async def _read(self) -> CampaignReferences:
        reference = await self._admin.list_reference_data()
        group = _preferred_group(reference.campaign_groups, self._group_name)
        if group is None:
            group = await self._admin.create_campaign_group(self._group_name)
        source = _first_usable(reference.traffic_sources)
        return CampaignReferences(
            group_id=group.id,
            traffic_source_id=None if source is None else source.id,
            domain=_first_usable(reference.domains),
        )


def _preferred_group(groups: tuple[Group, ...], name: str) -> Group | None:
    """Pick this service's own group if the tracker has one, and otherwise the first.

    Preferring our own name is what makes the second run of this service put its campaigns
    where the first one did. Without it a tracker that has grown a group since — an
    archive, somebody else's test — would quietly start collecting our campaigns, and a
    reviewer comparing the tracker with the screen would find them under a heading nobody
    chose.

    A `Group` has no state: `GET /groups` answers with id, name, position and type, so
    there is nothing here to filter the way a source or a domain is filtered.
    """
    ours = next(
        (group for group in groups if group.name.strip().casefold() == name.casefold()), None
    )
    if ours is not None:
        return ours
    return groups[0] if groups else None


def _first_usable[Row: _Listed](rows: tuple[Row, ...]) -> Row | None:
    """Take the first row the tracker has not deleted, keeping the order it listed them in.

    The tracker's order is somebody's choice — it is what their own dropdown shows first —
    and re-sorting it here would mean this service picking a domain a person did not expect
    on a screen where they cannot see why.
    """
    return next((row for row in rows if row.state is None or row.state == ACTIVE), None)
