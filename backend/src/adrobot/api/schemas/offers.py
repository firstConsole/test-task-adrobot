"""The offer catalogue on the wire: what the combobox labels a line with.

One decision worth the words. **`preview_url` is assembled here and nowhere else.** Keitaro
hands back `preview_path` relative — `/preview/11111` — and the domain keeps it that way on
purpose: which tracker this service is wrapping is configuration, and a ring that knows
nothing about HTTP cannot be the one to decide a host. This is the layer that has the
configured base, so this is the layer that joins the two.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from adrobot.application.dto import OfferCatalogueSync
    from adrobot.domain.offer import Offer


def preview_url(base: str, path: str | None) -> str | None:
    """Join the tracker's public base onto a relative preview path.

    `urljoin` rather than concatenation: the path arrives with its leading slash and the
    base may or may not carry a trailing one, and the two spellings that produces are
    `//preview/11111` and a working link.
    """
    return None if path is None else urljoin(base, path)


class OfferResponse(BaseModel):
    """One catalogue offer, in the fields the reference tool's own label is made of.

    `11112 Oxys [BEAUTY-CL-BE_0155] [pl es -]` — the id, the name, the network and the
    countries, which is every field here and no others.

    `country` is a tuple of plain strings and `preview_url` a plain `str`. Both are somebody
    else's data: a dash is one of the values Keitaro really keeps in that array, and a
    response model that refused to serialise what the tracker sent would turn an offer that
    exists into a 500.
    """

    id: int
    name: str
    state: str
    country: tuple[str, ...] = ()
    affiliate_network: str | None = None
    preview_url: str | None = None

    @classmethod
    def of(cls, offer: Offer, *, tracker: str) -> OfferResponse:
        """Render one offer, building the preview link against the tracker's public base."""
        return cls(
            id=offer.id,
            name=offer.name,
            state=offer.state,
            country=offer.country,
            affiliate_network=offer.affiliate_network,
            preview_url=preview_url(tracker, offer.preview_path),
        )


class OffersResponse(BaseModel):
    """A page of the combobox. No cursor: the catalogue is searched, not walked."""

    offers: tuple[OfferResponse, ...]


class OfferCatalogueSyncResponse(BaseModel):
    """What one press of the refresh did.

    `synced_at` is `null` when the tracker listed no offers at all: the local copy was left
    exactly as it was, because an empty answer and a failed listing are the same answer from
    here and emptying a working catalogue is the worse of the two mistakes.
    """

    model_config = ConfigDict(frozen=True)

    offers: int = Field(description="How many offers the tracker listed.")
    synced_at: datetime | None = None

    @classmethod
    def of(cls, written: OfferCatalogueSync) -> OfferCatalogueSyncResponse:
        """Render one pass of the refresh, which is the only way this model is built."""
        return cls(offers=written.offers, synced_at=written.synced_at)
