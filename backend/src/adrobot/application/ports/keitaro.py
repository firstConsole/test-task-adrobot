"""What this service asks of Keitaro, as two interfaces rather than one.

The split is the one place interface segregation pays for itself here. `/report/build` is
the most fragile endpoint the tracker has — the published schema types its rows as strings,
and two shipping clients disagree with it about what the request body is even called — and
a use case that only edits a flow must not be able to depend on it. Its failure darkens a
statistics column; it does not darken the editor.

Every method is `async` and every one of them is a network call. Nothing here returns a
wire model: the adapter converts in `infrastructure/keitaro/mapping.py`, which is the only
module allowed to see the wire models at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from adrobot.domain.campaign import Campaign, CampaignBlueprint, Group, ReferenceData
    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
    from adrobot.domain.offer import Offer, OfferStats
    from adrobot.domain.stream import Stream, StreamSpec


class KeitaroAdminPort(ABC):
    """The Admin API, as far as creating a campaign and editing a flow need it."""

    @abstractmethod
    async def list_reference_data(self) -> ReferenceData:
        """Read the campaign groups, traffic sources and domains in one go."""

    @abstractmethod
    async def create_campaign_group(self, name: str) -> Group:
        """Create a campaign group, for the first run against a tracker that has none."""

    @abstractmethod
    async def create_campaign(self, blueprint: CampaignBlueprint) -> Campaign:
        """Create a campaign and return it as the tracker echoed it back.

        Never retried, whatever the failure. A create whose outcome is unknown is two
        campaigns if it is repeated, and the compensation for one that half-succeeded is
        visible and human, not a retry loop.
        """

    @abstractmethod
    async def get_campaign(self, campaign_id: KeitaroCampaignId) -> Campaign:
        """Read one campaign. This is what importing an existing campaign starts from."""

    @abstractmethod
    async def create_stream(self, spec: StreamSpec) -> Stream:
        """Create one flow of a campaign."""

    @abstractmethod
    async def get_stream(self, stream_id: KeitaroStreamId) -> Stream:
        """Read one flow on its own, which is what a push looks at before it overwrites it.

        Separate from `list_campaign_streams` because the caller has a flow id and no reason
        to read its siblings, and because the answer decides whether a push goes ahead at
        all: the conflict check fingerprints this and compares it with the state the draft
        was opened on.
        """

    @abstractmethod
    async def update_stream(self, stream_id: KeitaroStreamId, spec: StreamSpec) -> Stream:
        """Write a flow back whole, `spec` being the state it should be left in."""

    @abstractmethod
    async def list_campaign_streams(self, campaign_id: KeitaroCampaignId) -> tuple[Stream, ...]:
        """Read every flow of a campaign, offers and filters included."""

    @abstractmethod
    async def replace_stream_offers(
        self, stream_id: KeitaroStreamId, desired: tuple[DesiredOffer, ...]
    ) -> Stream:
        """Leave the flow holding exactly `desired`, and return it as it then reads.

        The contract is deliberately strong, because the alternative is a screen that says
        a push succeeded when it did not:

        *   `desired` is the whole intended state and not a delta. A removed offer travels
            in it explicitly, at share 0 and disabled, which is correct whether this build
            replaces the array or merges into it.
        *   Everything else about the flow survives. The implementation reads the flow
            first for that reason — nothing promises a `PUT` is partial, so the fields it
            leaves out may be reset, and the campaign's own geo filter is one of them.
        *   The return value is the flow read back **after** the write, not the tracker's
            echo of the write itself. An implementation that finds the two disagreeing
            raises rather than returning; the caller mirrors what this returns.
        """

    @abstractmethod
    async def list_offers(self) -> tuple[Offer, ...]:
        """Read the whole offer catalogue, which is the only way this API offers to read it."""


class KeitaroReportsPort(ABC):
    """The report builder, kept apart so that losing it costs a column and nothing more."""

    @abstractmethod
    async def clicks_by_stream(
        self, campaign_id: KeitaroCampaignId, day: date
    ) -> Mapping[KeitaroStreamId, int]:
        """Return the clicks each flow of one campaign took on one day, in the tracker's zone.

        The day is the caller's, and the zone is the tracker's: Keitaro serialises its
        timestamps without an offset, so a report asked for in the wrong zone answers with
        somebody else's boundary between yesterday and today.
        """

    @abstractmethod
    async def clicks_by_offer(
        self, campaign_id: KeitaroCampaignId, day: date
    ) -> Mapping[OfferId, OfferStats]:
        """Return the same day grouped by offer — the editor's Stats column, in one call."""
