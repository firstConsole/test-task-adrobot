"""An offer from the tracker's catalogue, and what a report says about one.

`GET /offers` takes no parameters at all, so there is no server-side search to build the
editor's autocomplete on and the catalogue is mirrored locally instead. This type is what
gets mirrored: the fields the reference tool's own label is made of, and no others.
"""

from __future__ import annotations

from dataclasses import dataclass

from adrobot.domain.ids import OfferId


@dataclass(frozen=True, slots=True, kw_only=True)
class Offer:
    """One offer, as the editor labels it: `11112 Oxys [BEAUTY-CL-BE_0155] [pl es -]`.

    `country` is a tuple of plain strings and not of `CountryCode`s. The reference tool
    shows `[pl es -]` for a real offer — a dash is one of the values Keitaro keeps in that
    array, and a validated type would refuse the catalogue rather than mirror it.

    `preview_path` arrives relative (`/preview/11111`). It stays relative here and is
    joined onto the tracker's public base where a link is rendered, because this ring knows
    nothing about which tracker it is talking to.
    """

    id: OfferId
    name: str
    state: str
    country: tuple[str, ...] = ()
    group_id: int | None = None
    affiliate_network: str | None = None
    preview_path: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OfferStats:
    """What one offer did in one day, as far as the report builder will say.

    Two numbers, and both of them whole. Every measure a report asks for is one more name
    a particular build of the tracker might not know, and one unknown name is answered by
    rejecting the whole report — so the Stats column would go dark to show a figure nobody
    asked for. Money is absent for a second reason: revenue would need a decimal and a
    currency to mean anything, and the column it would feed is empty in every frame of the
    reference tool.
    """

    clicks: int = 0
    conversions: int = 0
