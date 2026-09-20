"""The values this wrapper refuses to take on trust, validated at construction.

Each subclasses its primitive rather than boxing it: a `Share` has to survive `divmod` and
a `CountryCode` has to serialise without a custom encoder.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, final

from adrobot.domain.errors import (
    InvalidCampaignAliasError,
    InvalidCampaignNameError,
    InvalidCountryCodeError,
    InvalidShareError,
)
from adrobot.domain.geo import COUNTRY_CODES

TOTAL_SHARE: Final = 100
"""What one stream's shares add up to after a recalculation, and never a rule about stored
state: an untouched stream read from Keitaro can sum to anything."""

MAX_CAMPAIGN_NAME: Final = 255
"""Our bound, not the tracker's — its own limit is what the `name-limit` probe asks."""

_ALIAS = re.compile(r"\A[a-z0-9][a-z0-9-]{2,63}\Z")

# Cf is the category of both the Trojan Source bidi overrides and the zero-width spaces.
_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})


@final
class Share(int):
    """A whole percentage in [0, 100]."""

    __slots__ = ()

    def __new__(cls, value: int) -> Share:
        """Reject anything outside [0, 100]: this API has no fractional shares."""
        if not 0 <= value <= TOTAL_SHARE:
            raise InvalidShareError(value)
        return super().__new__(cls, value)


@final
class CountryCode(str):
    """An ISO 3166-1 alpha-2 code, upper-cased and checked against the published list."""

    __slots__ = ()

    def __new__(cls, value: str) -> CountryCode:
        """Fold with NFKC first — that is what turns a pasted full-width code into ASCII."""
        candidate = unicodedata.normalize("NFKC", value).strip()
        if not candidate.isascii():
            raise InvalidCountryCodeError(value)
        code = candidate.upper()
        if code not in COUNTRY_CODES:
            raise InvalidCountryCodeError(value)
        return super().__new__(cls, code)


@final
class CampaignName(str):
    """A campaign name with the invisible characters removed."""

    __slots__ = ()

    def __new__(cls, value: str) -> CampaignName:
        """Drop the control, format and surrogate characters, then bound the length."""
        cleaned = "".join(
            character
            for character in unicodedata.normalize("NFKC", value)
            if unicodedata.category(character) not in _FORBIDDEN_CATEGORIES
        ).strip()
        if not 0 < len(cleaned) <= MAX_CAMPAIGN_NAME:
            raise InvalidCampaignNameError(value)
        return super().__new__(cls, cleaned)


@final
class CampaignAlias(str):
    """The path segment of a campaign's public link, which this service generates."""

    __slots__ = ()

    def __new__(cls, value: str) -> CampaignAlias:
        """Accept only what stays safe and stable in a URL path segment."""
        if not _ALIAS.fullmatch(value):
            raise InvalidCampaignAliasError(value)
        return super().__new__(cls, value)
