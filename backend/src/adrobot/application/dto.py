"""What the use cases are asked for, and what they answer with.

Three rules decide the shapes below, and each of them is a decision rather than a habit.

*   **A command carries validated values and never the strings they arrived as.** By the
    time a scenario sees one, `name` has been through the invisible-character filter and
    `country` through the ISO 3166-1 list — at the edge, where a refusal can still name the
    field it belongs to and answer 422. A use case taking `str` would have to validate, and
    so would every caller that is not the HTTP layer.
*   **A command exists where there is more than one field to get in the wrong order.**
    Creating takes three values, two of which are strings, so it takes a command. Importing
    takes one `KeitaroCampaignId` and synchronising one `CampaignId` — `NewType`s that mypy
    already refuses to swap — so those scenarios take the identifier itself. A wrapper
    around a single typed field is a name, not a guarantee.
*   **An answer re-uses the record the repository already returns.** `CampaignView` carries
    a `MirroredCampaign` instead of re-declaring its eleven fields, because the copy would
    drift from the table on the first migration that adds a column, and because the row is
    already the honest answer to "what do we know about this campaign".

Nothing here is a pydantic model. These cross the application boundary, not the wire: the
request and response models live in `api/schemas/` (6.9), and keeping the two apart is what
lets the API rename a field without a use case hearing about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from adrobot.application.ports.persistence import (
    CampaignCursor,
    MirroredCampaign,
    MirroredStream,
)
from adrobot.domain.campaign import public_link
from adrobot.domain.diff import DraftDiff
from adrobot.domain.ids import OfferId
from adrobot.domain.offer import Offer
from adrobot.domain.values import CampaignName, CountryCode

DEFAULT_PAGE_SIZE: Final = 20
"""One screenful of the campaign list, and the default for every caller that says nothing."""

MAX_PAGE_SIZE: Final = 100
"""The largest page this service will build. Bounded at the edge, where a client-supplied
number can still be answered with a 422 naming the parameter; a use case that clamped
silently would hand back a shorter page than it was asked for and say nothing about it."""


DEFAULT_OFFER_LIMIT: Final = 20
"""One dropful of the offer combobox, and the default for a caller that says nothing."""

MAX_OFFER_LIMIT: Final = 200
"""What SHOW ALL OFFERS gets, and the bound on any search. A tracker's catalogue runs to
thousands; a combobox holding thousands is not a widget anybody uses, and typing two
characters is how the rest of it is reached. Bounded at the edge, where a client-supplied
number can still be answered with a 422 naming the parameter."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateCampaignCommand:
    """The three fields part 1 asks for, as the domain's own types.

    `offer_id` is not checked against the local catalogue first. The catalogue is a mirror
    that can be empty on a fresh install or stale by a minute, so the tracker is the only
    honest judge of whether an offer exists — and it answers with a 406 that becomes an
    `UpstreamRejectedError` naming the field. Refusing here on a stale mirror would block a
    campaign the tracker would have accepted.
    """

    name: CampaignName
    country: CountryCode
    offer_id: OfferId


@dataclass(frozen=True, slots=True, kw_only=True)
class ListCampaignsQuery:
    """One page of the campaign list, continuing after `after`.

    `query` is free text over the name and the alias; `None` is the unfiltered list, which
    is what the screen opens on.
    """

    after: CampaignCursor | None = None
    query: str | None = None
    limit: int = DEFAULT_PAGE_SIZE


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignView:
    """One campaign as every scenario of part 1 hands it back.

    `public_url` is the link a buyer puts in an ad, and this service builds it because the
    tracker will not: `CampaignRequest` takes a `domain_id` on the write and the campaign it
    reads back declares none. What we sent is what we stored, so the link can be rebuilt —
    and for a campaign we did not create, an imported one above all, `public_domain` is
    unknown and this is `None` rather than a guess at somebody else's domain.

    `setup_failure` is the plain-language reason the flows are missing, filled in only by
    the scenario that has just tried to build them. It is deliberately not a column: a
    campaign read back tomorrow still says `needs_attention`, which is the fact that
    matters, while the minute-old reason the tracker gave would be stale comfort.
    """

    campaign: MirroredCampaign
    public_url: str | None = None
    setup_failure: str | None = None

    @classmethod
    def of(cls, campaign: MirroredCampaign, *, setup_failure: str | None = None) -> CampaignView:
        """Wrap one row, building the public link from the domain the row remembers.

        A constructor rather than four use cases each writing the same conditional, which is
        four places for "the campaign has no domain" to be answered differently.
        """
        return cls(
            campaign=campaign,
            public_url=(
                None
                if campaign.public_domain is None
                else public_link(campaign.public_domain, campaign.alias)
            ),
            setup_failure=setup_failure,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignsPage:
    """A page of campaigns and where the next one starts, `None` meaning there is no next.

    Not the repository's `CampaignPage`, although it is that page one step further on: the
    rows have become views, each with the public link built. Two types because the port
    answers with what the table holds and a use case answers with what the screen shows,
    and collapsing them would put link-building behind the repository.
    """

    campaigns: tuple[CampaignView, ...]
    next_cursor: CampaignCursor | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditorRow:
    """One line of a flow's offer table, carrying everything the screen prints on it.

    `offer` is the catalogue's label and is `None` for an offer the local mirror has never
    heard of — which renders as `#11234 (not in catalogue)` rather than emptying the screen.
    There is no foreign key from a flow row to the catalogue and there is not meant to be:
    an offer reaches a flow in Keitaro before it reaches our copy of `GET /offers`.

    `share` is whatever `redistribute()` last wrote, never recomputed on the way out. A pin
    must move no share until the next edit, and a recalculation at render time is exactly
    how that guarantee would be lost.
    """

    offer_id: OfferId
    offer: Offer | None = None
    share: int = 0
    pinned_share: int | None = None
    removed: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamEditorView:
    """One flow as the editor draws it: the rows, and the answers about the buttons above them.

    The three flags are the whole reason this is assembled on the server. The frontend has
    no `redistribute()` of its own and must not grow one, so it cannot decide whether a push
    is allowed either — it formats `can_push`, `block_reason` and `warnings` and nothing
    more.

    `dirty` is "a draft is live", not "the draft would change something". The two part
    company when an offer is added and taken back out again: nothing would be written, so
    `can_push` is false, and CANCEL still has a draft to discard.
    """

    stream: MirroredStream
    rows: tuple[EditorRow, ...]
    dirty: bool = False
    diff: DraftDiff | None = None
    can_push: bool = False
    block_reason: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class EditorView:
    """One campaign's flows, in the one answer the editor screen opens on.

    The campaign travels with them because the toolbar is made of it: the link into Keitaro,
    the public URL, and the name in the heading. Two calls would let the screen render a
    campaign beside somebody else's flows for one frame.
    """

    campaign: CampaignView
    streams: tuple[StreamEditorView, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OfferCatalogueSync:
    """What one pass of the catalogue sync did.

    `synced_at` is `None` when nothing was written, which is the honest answer for a tracker
    that listed no offers at all — see `SyncOfferCatalogue` for why that is not treated as
    an empty catalogue.
    """

    offers: int = 0
    synced_at: datetime | None = None
