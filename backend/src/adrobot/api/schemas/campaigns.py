"""The campaign endpoints' wire models: what a client may send, and what it gets back.

Three decisions worth the words.

**A field is validated by the domain's own type, in a pydantic validator.** `country` is put
through `CountryCode` and `name` through `CampaignName` while pydantic is still assembling
the model, so a refusal arrives as a 422 naming `body.country` — which is what a form
highlights. Letting the domain error out of the model instead would answer 422 as well, with
a more precise `code` and no idea which field it was about, and the screen this API is for
puts the message under the field.

**The validators keep what the domain normalised.** `CountryCode("mx")` is `"MX"`, and a
name comes back without its invisible characters, so the model holds the cleaned value and
`to_command()` rebuilds the domain type from something that has already passed.

**The cursor is opaque.** It encodes the exact pair the index is on, and a client that
cannot read it is a client that cannot come to depend on the sort order — which would
otherwise make changing the index a breaking change to this API.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Final
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from adrobot.application.dto import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, CreateCampaignCommand
from adrobot.application.ports.persistence import CampaignCursor
from adrobot.domain.campaign import CampaignSetupStatus
from adrobot.domain.errors import DomainError
from adrobot.domain.ids import CampaignId, OfferId
from adrobot.domain.values import CampaignName, CountryCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from adrobot.application.dto import CampaignsPage, CampaignView

_CURSOR_SEPARATOR: Final = "|"


def _country(value: str) -> str:
    """Refuse a geo that is not on the ISO 3166-1 list, and upper-case the one that is."""
    return str(_domain(CountryCode, value))


def _campaign_name(value: str) -> str:
    """Refuse an empty or oversized name, and drop the characters that cannot be seen."""
    return str(_domain(CampaignName, value))


def _domain[T](build: Callable[[str], T], value: str) -> T:
    """Run a domain constructor where pydantic can report which field it refused.

    The re-raise is the point: pydantic catches `ValueError` and turns it into an error with
    a location, and lets anything else through — so a `DomainError` reaching the handler
    would be rendered as a 422 about the request rather than about the field.
    """
    try:
        return build(value)
    except DomainError as refusal:
        message = str(refusal)
        raise ValueError(message) from refusal


def encode_cursor(cursor: CampaignCursor) -> str:
    """Render a keyset position as one token a client hands back unread."""
    raw = f"{cursor.created_at.isoformat()}{_CURSOR_SEPARATOR}{cursor.id}"
    return urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(token: str) -> CampaignCursor:
    """Read a token back, raising `ValueError` on anything this service did not issue."""
    padded = token + "=" * (-len(token) % 4)
    try:
        created_at, _, identifier = urlsafe_b64decode(padded).decode().partition(_CURSOR_SEPARATOR)
        return CampaignCursor(
            created_at=datetime.fromisoformat(created_at), id=CampaignId(UUID(identifier))
        )
    except (ValueError, UnicodeDecodeError) as broken:
        message = "this is not a cursor this service issued"
        raise ValueError(message) from broken


def _cursor(token: str) -> str:
    """Refuse an unreadable cursor while pydantic can still call it `query.after`."""
    decode_cursor(token)
    return token


class CreateCampaignRequest(BaseModel):
    """Part 1's three fields."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, AfterValidator(_campaign_name)] = Field(
        description="What the campaign is called in the tracker."
    )
    country: Annotated[str, AfterValidator(_country)] = Field(
        description="ISO 3166-1 alpha-2. Flow 1 catches this country and sends it away.",
        examples=["MX"],
    )
    offer_id: int = Field(gt=0, description="The offer Flow 2 rotates, as the tracker numbers it.")

    def to_command(self) -> CreateCampaignCommand:
        """Rebuild the validated values as domain types, which cannot fail a second time."""
        return CreateCampaignCommand(
            name=CampaignName(self.name),
            country=CountryCode(self.country),
            offer_id=OfferId(self.offer_id),
        )


class ImportCampaignRequest(BaseModel):
    """The one number that opens a campaign somebody else built."""

    model_config = ConfigDict(extra="forbid")

    keitaro_campaign_id: int = Field(
        gt=0, description="The campaign's id in Keitaro, as its URL shows it.", examples=[93212]
    )


class CampaignsQuery(BaseModel):
    """The campaign list's query string, as one model so that an unknown parameter is refused."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, description="Free text over the name and the alias.")
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    after: Annotated[str, AfterValidator(_cursor)] | None = Field(
        default=None, description="The `next_cursor` of the page before this one."
    )


class CampaignResponse(BaseModel):
    """A campaign as this API answers for one.

    `public_url` is a plain string and not an `HttpUrl`: it is built from a domain name the
    tracker gave us, and a response model that refused to serialise somebody else's data
    would turn a campaign that exists into a 500.
    """

    id: UUID
    keitaro_campaign_id: int
    alias: str
    name: str
    state: str
    setup_status: CampaignSetupStatus
    public_url: str | None = None
    requested_country: str | None = None
    requested_offer_id: int | None = None
    synced_at: datetime | None = None
    created_at: datetime
    setup_failure: str | None = Field(
        default=None,
        description=(
            "Why this campaign's flows are missing, filled in by the call that has just "
            "tried to build them. A campaign read back later says `needs_attention` without "
            "it."
        ),
    )

    @classmethod
    def of(cls, view: CampaignView) -> CampaignResponse:
        """Render one view, which is the only way this model is built."""
        return cls(
            id=view.campaign.id,
            keitaro_campaign_id=view.campaign.keitaro_campaign_id,
            alias=view.campaign.alias,
            name=view.campaign.name,
            state=view.campaign.state,
            setup_status=view.campaign.setup_status,
            public_url=view.public_url,
            requested_country=view.campaign.requested_country,
            requested_offer_id=view.campaign.requested_offer_id,
            synced_at=view.campaign.synced_at,
            created_at=view.campaign.created_at,
            setup_failure=view.setup_failure,
        )


class CampaignsPageResponse(BaseModel):
    """One page and the token that continues it, `null` meaning there is nothing after."""

    campaigns: tuple[CampaignResponse, ...]
    next_cursor: str | None = None

    @classmethod
    def of(cls, page: CampaignsPage) -> CampaignsPageResponse:
        """Render one page, encoding the cursor the repository answered with."""
        return cls(
            campaigns=tuple(CampaignResponse.of(view) for view in page.campaigns),
            next_cursor=None if page.next_cursor is None else encode_cursor(page.next_cursor),
        )
