"""The second implementation of the ports, in memory.

A port with one implementation is a layer of indirection; a port with two is a boundary.
This is the second, and writing it is what proves the first one's signatures are about what
the application needs rather than about what httpx returns.

It is also the tracker that the scenarios of stages 6 and 7 run against, so it models the
behaviour those scenarios turn on, and no more:

*   **Either semantics of a write.** Whether `PUT /streams/{id}` replaces the offers array
    or merges into it is the open question of `docs/keitaro-api-notes.md` §4, and the push
    is meant to be correct in both worlds. `FakeKeitaroAdmin(merges=True)` is the other
    world, so that claim can be a parametrised test rather than a sentence.
*   **Timestamps that survive a write.** The rounding remainder goes to the most recently
    activated row, and for rows that came from the tracker that order is
    `stream_offer.created_at`. A fake that restamped every row on every push would hide the
    one behaviour the arithmetic depends on.
*   **Refusal on demand.** `fail_on` is how the compensation path of 6.3 — a campaign
    created and a flow that never was — gets tested without a tracker having a bad day.

What it does not model is transport: no retries, no statuses, no redirects. Those are the
adapter's own, and `tests/infrastructure/` exercises them against respx.

`FakeClock` is here for the same reason as the rest and not because it is about Keitaro: a
scenario that has to say "five minutes and one second later" — the reference cache of 6.2 —
would otherwise say it by sleeping.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import TYPE_CHECKING, override

from adrobot.application.errors import UpstreamNotFoundError
from adrobot.application.ports.keitaro import KeitaroAdminPort, KeitaroReportsPort
from adrobot.application.ports.system import AliasFactory, Clock, CorrelationIds
from adrobot.domain.campaign import (
    Campaign,
    CampaignBlueprint,
    Group,
    ReferenceData,
    TrackerDomain,
    TrafficSource,
)
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.stream import (
    Stream,
    StreamFilter,
    StreamOffer,
    StreamSchema,
    StreamType,
)
from adrobot.domain.values import CampaignAlias, OfferState

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.offer import Offer, OfferStats
    from adrobot.domain.stream import StreamSpec

# The identifiers from the video, so that a failing assertion reads like the campaign a
# reviewer can open in the tracker.
FIRST_CAMPAIGN_ID = 93212
FIRST_STREAM_ID = 564221
FIRST_MOMENT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
REFERENCE_OFFERS = (OfferId(11112), OfferId(11234))


def _issuing_after(ids: count[int], given: int) -> count[int]:
    """Move an id counter past one that was handed in, so a create cannot reissue it.

    The video's identifiers are the ones a test seeds with, and they are also where these
    counters start: without this, a scenario that seeds flow 564221 and then creates one
    would get 564221 back and silently overwrite the flow it was editing.
    """
    return count(max(next(ids), given + 1))


DEFAULT_REFERENCE = ReferenceData(
    campaign_groups=(Group(id=7, name="AD Robot"),),
    traffic_sources=(TrafficSource(id=2, name="Facebook", state="active"),),
    domains=(TrackerDomain(id=4, name="track.example", state="active"),),
)


class FakeKeitaroAdmin(KeitaroAdminPort):
    """A tracker that remembers what it was told, in one process and one dictionary."""

    def __init__(
        self,
        *,
        merges: bool = False,
        reference: ReferenceData = DEFAULT_REFERENCE,
        offers: tuple[Offer, ...] = (),
    ) -> None:
        # True for a tracker whose update merges the offers array: a row sent as disabled
        # stays, switched off. False for one that replaces it: the row is simply gone.
        self.merges = merges
        self.reference = reference
        self.offers = list(offers)
        self.campaigns: dict[KeitaroCampaignId, Campaign] = {}
        # What it was ASKED to create, which is the only record of the fields the tracker
        # accepts on a write and declines to give back — `domain_id` above all.
        self.blueprints: list[CampaignBlueprint] = []
        self.streams: dict[KeitaroStreamId, Stream] = {}
        # What was asked of it, in order, for a scenario that cares how many times.
        self.calls: list[str] = []
        # Method name to the failure it should raise instead of answering.
        self.failures: dict[str, Exception] = {}
        self._campaign_ids = count(FIRST_CAMPAIGN_ID)
        self._stream_ids = count(FIRST_STREAM_ID)
        self._row_ids = count(1)
        self._moments = count()

    def fail_on(self, method: str, error: Exception) -> None:
        """Make one method raise until a test says otherwise."""
        self.failures[method] = error

    def given_campaign(self, campaign: Campaign) -> Campaign:
        """Put a campaign into the tracker as if somebody had built it by hand."""
        self.campaigns[campaign.id] = campaign
        self._campaign_ids = _issuing_after(self._campaign_ids, campaign.id)
        return campaign

    def given_stream(self, stream: Stream) -> Stream:
        """Put a flow into the tracker as if somebody had built it by hand.

        The editor has to open campaigns this service never created — 93212 from the video
        among them — so a scenario needs a way to say "this was already there".
        """
        self.streams[stream.id] = stream
        self._stream_ids = _issuing_after(self._stream_ids, stream.id)
        return stream

    @override
    async def list_reference_data(self) -> ReferenceData:
        self._called("list_reference_data")
        return self.reference

    @override
    async def create_campaign_group(self, name: str) -> Group:
        self._called("create_campaign_group")
        group = Group(id=len(self.reference.campaign_groups) + 1, name=name)
        self.reference = replace(
            self.reference, campaign_groups=(*self.reference.campaign_groups, group)
        )
        return group

    @override
    async def create_campaign(self, blueprint: CampaignBlueprint) -> Campaign:
        self._called("create_campaign")
        self.blueprints.append(blueprint)
        campaign = Campaign(
            id=KeitaroCampaignId(next(self._campaign_ids)),
            alias=str(blueprint.alias),
            name=str(blueprint.name),
            state="active",
            group_id=blueprint.group_id,
            traffic_source_id=blueprint.traffic_source_id,
            cookies_ttl=blueprint.cookies_ttl,
            cost_type=blueprint.cost_type.value,
            created_at=self._moment(),
        )
        self.campaigns[campaign.id] = campaign
        return campaign

    @override
    async def get_campaign(self, campaign_id: KeitaroCampaignId) -> Campaign:
        self._called("get_campaign")
        found = self.campaigns.get(campaign_id)
        if found is None:
            absent = f"campaign {campaign_id}"
            raise UpstreamNotFoundError(absent, status=404)
        return found

    @override
    async def create_stream(self, spec: StreamSpec) -> Stream:
        self._called("create_stream")
        stream = Stream(
            id=KeitaroStreamId(next(self._stream_ids)),
            campaign_id=spec.campaign_id,
            name=spec.name,
            type=spec.type,
            schema=spec.schema,
            action_type=spec.action_type,
            position=spec.position,
            weight=spec.weight,
            state=spec.state,
            action_payload=spec.action_payload,
            collect_clicks=spec.collect_clicks,
            filter_or=spec.filter_or,
            comments=spec.comments,
            filters=spec.filters,
            landings=spec.landings,
            offers=tuple(self._fresh_row(row) for row in spec.offers),
        )
        self.streams[stream.id] = stream
        return stream

    @override
    async def get_stream(self, stream_id: KeitaroStreamId) -> Stream:
        self._called("get_stream")
        return self._stream(stream_id)

    @override
    async def update_stream(self, stream_id: KeitaroStreamId, spec: StreamSpec) -> Stream:
        self._called("update_stream")
        current = self._stream(stream_id)
        written = replace(
            current,
            name=spec.name,
            type=spec.type,
            schema=spec.schema,
            action_type=spec.action_type,
            position=spec.position,
            weight=spec.weight,
            state=spec.state,
            action_payload=spec.action_payload,
            collect_clicks=spec.collect_clicks,
            filter_or=spec.filter_or,
            comments=spec.comments,
            filters=spec.filters,
            landings=spec.landings,
            offers=self._written_rows(current, spec.offers),
        )
        self.streams[stream_id] = written
        return written

    @override
    async def list_campaign_streams(self, campaign_id: KeitaroCampaignId) -> tuple[Stream, ...]:
        self._called("list_campaign_streams")
        return tuple(
            sorted(
                (row for row in self.streams.values() if row.campaign_id == campaign_id),
                key=lambda row: (row.position or 0, row.id),
            )
        )

    @override
    async def replace_stream_offers(
        self, stream_id: KeitaroStreamId, desired: tuple[DesiredOffer, ...]
    ) -> Stream:
        self._called("replace_stream_offers")
        current = self._stream(stream_id)
        written = replace(current, offers=self._written_rows(current, desired))
        self.streams[stream_id] = written
        return written

    @override
    async def list_offers(self) -> tuple[Offer, ...]:
        self._called("list_offers")
        return tuple(self.offers)

    def _written_rows(
        self, current: Stream, desired: tuple[DesiredOffer, ...]
    ) -> tuple[StreamOffer, ...]:
        held = {row.offer_id: row for row in current.offers}
        written: list[StreamOffer] = []
        for row in desired:
            if row.state is OfferState.DISABLED and row.share == 0 and not self.merges:
                continue
            existing = held.get(row.offer_id)
            if existing is None:
                written.append(self._fresh_row(row))
            else:
                # Its own id and its own timestamp, both kept: the tie-break orders the
                # tracker's rows by exactly this, so restamping them here would make every
                # push look like the whole flow had just been added.
                written.append(replace(existing, share=row.share, state=row.state.value))
        return tuple(written)

    def _fresh_row(self, row: DesiredOffer) -> StreamOffer:
        return StreamOffer(
            offer_id=row.offer_id,
            share=row.share,
            state=row.state.value,
            row_id=next(self._row_ids),
            created_at=self._moment(),
        )

    def _stream(self, stream_id: KeitaroStreamId) -> Stream:
        found = self.streams.get(stream_id)
        if found is None:
            absent = f"flow {stream_id}"
            raise UpstreamNotFoundError(absent, status=404)
        return found

    def _moment(self) -> datetime:
        return FIRST_MOMENT + timedelta(minutes=next(self._moments))

    def _called(self, method: str) -> None:
        self.calls.append(method)
        failure = self.failures.get(method)
        if failure is not None:
            raise failure


class FakeKeitaroReports(KeitaroReportsPort):
    """Statistics that were put there by the test, or a failure that was."""

    def __init__(
        self,
        *,
        clicks: Mapping[int, int] = {},
        stats: Mapping[int, OfferStats] = {},
    ) -> None:
        self.clicks = {KeitaroStreamId(key): value for key, value in clicks.items()}
        self.stats = {OfferId(key): value for key, value in stats.items()}
        self.calls: list[str] = []
        # The tracker's report builder is the part that falls over first, and a screen that
        # loses its Stats column while the editor keeps working is what 8.2 has to do.
        self.failure: Exception | None = None

    @override
    async def clicks_by_stream(
        self, campaign_id: KeitaroCampaignId, day: date
    ) -> Mapping[KeitaroStreamId, int]:
        self._called("clicks_by_stream")
        return dict(self.clicks)

    @override
    async def clicks_by_offer(
        self, campaign_id: KeitaroCampaignId, day: date
    ) -> Mapping[OfferId, OfferStats]:
        self._called("clicks_by_offer")
        return dict(self.stats)

    def _called(self, method: str) -> None:
        self.calls.append(method)
        if self.failure is not None:
            raise self.failure


class FakeClock(Clock):
    """A clock that moves only when a test moves it."""

    def __init__(self, at: datetime = FIRST_MOMENT) -> None:
        self.at = at

    @override
    def now(self) -> datetime:
        return self.at

    def advance(self, by: timedelta) -> None:
        """Move the clock forward, which is the whole reason this exists."""
        self.at += by


class FakeAliasFactory(AliasFactory):
    """Aliases a test can predict, numbered in the order they were handed out."""

    def __init__(self, stem: str = "kt-alias") -> None:
        self.stem = stem
        self.issued: list[CampaignAlias] = []

    @override
    def new(self) -> CampaignAlias:
        alias = CampaignAlias(f"{self.stem}-{len(self.issued) + 1}")
        self.issued.append(alias)
        return alias


class FakeCorrelationIds(CorrelationIds):
    """Correlation ids a test can predict, numbered in the order they were handed out.

    Predictable on purpose: `push_attempts.correlation_id` is the join between one press of
    PUSH TO KT and the log lines it produced, and a test that could not name the id could
    only assert that *some* string was written.
    """

    def __init__(self, stem: str = "cid") -> None:
        self.stem = stem
        self.issued: list[str] = []

    @override
    def current(self) -> str:
        issued = f"{self.stem}-{len(self.issued) + 1}"
        self.issued.append(issued)
        return issued


def given_reference_campaign(admin: FakeKeitaroAdmin) -> Campaign:
    """Seed the tracker with campaign 93212 and its two flows, as the video shows them.

    The one campaign a reviewer is most likely to open, and the fixture part 2 is written
    against. Flow 2's two offers hold 25% each: the shares of a flow nobody has recalculated
    do not have to sum to 100, and every scenario that touches this campaign has to survive
    that.
    """
    campaign = admin.given_campaign(
        Campaign(
            id=KeitaroCampaignId(FIRST_CAMPAIGN_ID),
            alias="Gd7Hk2",
            name="AU | Oxys",
            state="active",
        )
    )
    admin.given_stream(
        Stream(
            id=KeitaroStreamId(FIRST_STREAM_ID),
            campaign_id=campaign.id,
            name="Flow 1",
            type=StreamType.REGULAR,
            schema=StreamSchema.REDIRECT,
            action_type="http",
            position=1,
            action_payload="https://google.com",
            filters=(StreamFilter(id=9, name="country", mode="accept", payload=("AU",)),),
        )
    )
    admin.given_stream(
        Stream(
            id=KeitaroStreamId(FIRST_STREAM_ID + 1),
            campaign_id=campaign.id,
            name="Flow 2",
            type=StreamType.REGULAR,
            schema=StreamSchema.LANDINGS,
            action_type="http",
            position=2,
            offers=(
                StreamOffer(
                    offer_id=REFERENCE_OFFERS[0],
                    share=25,
                    state="active",
                    row_id=1,
                    created_at=FIRST_MOMENT,
                ),
                StreamOffer(
                    offer_id=REFERENCE_OFFERS[1],
                    share=25,
                    state="active",
                    row_id=2,
                    created_at=FIRST_MOMENT + timedelta(days=1),
                ),
            ),
        )
    )
    return campaign
