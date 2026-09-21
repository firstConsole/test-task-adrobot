"""The Admin API port, over HTTP.

Thin on purpose. Every method is a path, a body built by `mapping.py` and a body read by
it; the transport decides how many times to ask and `errors.py` decides what the answer
means. The one method with real behaviour is `replace_stream_offers`, and all of it is
there because nothing in the published schema says what `PUT /streams/{id}` does with the
fields a body leaves out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

from adrobot.application.errors import UpstreamProtocolError
from adrobot.application.ports.keitaro import KeitaroAdminPort
from adrobot.domain.values import OfferState
from adrobot.infrastructure.keitaro.mapping import (
    campaign_body,
    group_body,
    stream_body,
    to_campaign,
    to_group,
    to_offers,
    to_reference_data,
    to_stream,
    to_streams,
)

if TYPE_CHECKING:
    from datetime import tzinfo

    from adrobot.domain.campaign import Campaign, CampaignBlueprint, Group, ReferenceData
    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId
    from adrobot.domain.offer import Offer
    from adrobot.domain.stream import Stream, StreamOffer, StreamSpec
    from adrobot.infrastructure.keitaro.transport import KeitaroTransport

# `GET /groups` declares `type` as required and gives it a default in the same breath,
# which is how a generator writes "optional". It is always sent.
CAMPAIGN_GROUPS: Final = {"type": "campaigns"}


class HttpKeitaroAdmin(KeitaroAdminPort):
    """The real tracker, behind the port that `tests/fakes.py` implements a second time."""

    def __init__(self, transport: KeitaroTransport, *, zone: tzinfo) -> None:
        self._transport = transport
        # The tracker's own time zone, needed at every read that carries a timestamp:
        # Keitaro serialises them without an offset (PLAN-00 §5.14).
        self._zone = zone

    @override
    async def list_reference_data(self) -> ReferenceData:
        """Read the three catalogues, one after another.

        Sequentially, although they are independent and an `asyncio.TaskGroup` would read
        them at once. A TaskGroup raises an `ExceptionGroup`, and the one thing this
        adapter promises its callers is *which* exception comes out — a refused admin key
        wrapped in a group is a 401 that the error handlers of 6.7 would render as an
        unhandled failure. Three reads on a path that already ends in a create, cached for
        five minutes at 6.2, is not where this service spends its time.
        """
        groups = await self._transport.get("/groups", params=CAMPAIGN_GROUPS)
        sources = await self._transport.get("/traffic_sources")
        domains = await self._transport.get("/domains")
        return to_reference_data(
            groups=groups.json(), sources=sources.json(), domains=domains.json()
        )

    @override
    async def create_campaign_group(self, name: str) -> Group:
        """Create the campaign group part 1 puts its campaigns in."""
        created = await self._transport.post("/groups", json=group_body(name))
        return to_group(created.json())

    @override
    async def create_campaign(self, blueprint: CampaignBlueprint) -> Campaign:
        """Create a campaign, in the one call this API never repeats."""
        created = await self._transport.post("/campaigns", json=campaign_body(blueprint))
        return to_campaign(created.json(), zone=self._zone)

    @override
    async def get_campaign(self, campaign_id: KeitaroCampaignId) -> Campaign:
        """Read one campaign, which is where importing an existing one begins."""
        found = await self._transport.get(f"/campaigns/{campaign_id}")
        return to_campaign(found.json(), zone=self._zone)

    @override
    async def create_stream(self, spec: StreamSpec) -> Stream:
        """Create one flow of a campaign."""
        created = await self._transport.post("/streams", json=stream_body(spec))
        return to_stream(created.json(), zone=self._zone)

    @override
    async def get_stream(self, stream_id: KeitaroStreamId) -> Stream:
        """Read one flow. The same request `replace_stream_offers` makes twice of its own."""
        return await self._read_stream(stream_id)

    @override
    async def update_stream(self, stream_id: KeitaroStreamId, spec: StreamSpec) -> Stream:
        """Write a flow back whole, `spec` being the state it should be left in."""
        written = await self._transport.put(f"/streams/{stream_id}", json=stream_body(spec))
        return to_stream(written.json(), zone=self._zone)

    @override
    async def list_campaign_streams(self, campaign_id: KeitaroCampaignId) -> tuple[Stream, ...]:
        """Read every flow of a campaign, offers nested inside each one."""
        listed = await self._transport.get(f"/campaigns/{campaign_id}/streams")
        return to_streams(listed.json(), zone=self._zone)

    @override
    async def replace_stream_offers(
        self, stream_id: KeitaroStreamId, desired: tuple[DesiredOffer, ...]
    ) -> Stream:
        """Leave the flow holding exactly `desired`, and return it as it then reads.

        Three calls, and each one earns its round trip:

        1.  **Read the flow.** A `PUT` body that named only the offers would, on a build
            whose update replaces rather than merges, take the campaign's geo filter with
            it. The other fields travel because this read put them in reach.
        2.  **Write it back whole**, with the desired offers substituted. A removed offer
            is in that list explicitly, at 0% and disabled, which is the right shape under
            either semantics.
        3.  **Read it again**, and compare. Not the tracker's echo of the write — a build
            that merges arrays would echo what it was sent while holding something else.

        A disagreement is raised, never returned. The single quality criterion this project
        is judged on is that the numbers are right, and a push that reports success while
        the tracker holds different shares is the worst way to fail it.
        """
        current = await self._read_stream(stream_id)
        await self._transport.put(
            f"/streams/{stream_id}", json=stream_body(current.respecified(desired))
        )
        written = await self._read_stream(stream_id)
        disagreements = _disagreements(written, desired)
        if disagreements:
            refused = f"flow {stream_id} did not take the push: {'; '.join(disagreements)}"
            raise UpstreamProtocolError(refused)
        return written

    @override
    async def list_offers(self) -> tuple[Offer, ...]:
        """Read the whole offer catalogue, in the only way this API offers.

        `GET /offers` takes no parameters at all — no search, no paging — so this is one
        unpaginated response against the transport's ten-second read timeout, and it is
        why the catalogue is mirrored locally and searched there.
        """
        listed = await self._transport.get("/offers")
        return to_offers(listed.json())

    async def _read_stream(self, stream_id: KeitaroStreamId) -> Stream:
        found = await self._transport.get(f"/streams/{stream_id}")
        return to_stream(found.json(), zone=self._zone)


def _disagreements(written: Stream, desired: tuple[DesiredOffer, ...]) -> tuple[str, ...]:
    """Say, in a person's words, how the flow differs from what the push asked for."""
    held = {row.offer_id: row for row in written.offers}
    complaints: list[str] = []
    for row in desired:
        actual = held.pop(row.offer_id, None)
        if row.state is OfferState.DISABLED and row.share == 0:
            # Both answers are right for a removed row, and which one arrives is the
            # `put-semantics` question: a tracker that replaces the array drops the row
            # altogether, one that merges keeps it, switched off. Either way it takes no
            # traffic, which is the whole of what was asked.
            if actual is not None and _is_taking_traffic(actual):
                complaints.append(
                    f"offer {actual.offer_id}: asked for it to be switched off, the "
                    f"tracker holds {actual.share}% {actual.state}"
                )
            continue
        if actual is None:
            complaints.append(
                f"offer {row.offer_id}: asked for {row.share}% {row.state.value}, the "
                f"tracker holds no row at all"
            )
        elif (actual.share, actual.state) != (row.share, row.state.value):
            complaints.append(
                f"offer {row.offer_id}: asked for {row.share}% {row.state.value}, the "
                f"tracker holds {actual.share}% {actual.state}"
            )
    complaints.extend(
        f"offer {leftover.offer_id}: the tracker holds a row at {leftover.share}% "
        f"{leftover.state} that this push did not ask for"
        for leftover in held.values()
    )
    return tuple(complaints)


def _is_taking_traffic(row: StreamOffer) -> bool:
    """Whether a row the push switched off is still in the rotation."""
    return row.share != 0 or row.state == OfferState.ACTIVE.value
