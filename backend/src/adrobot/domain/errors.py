"""The rules this package enforces, as exceptions.

One base class, so that the problem+json handlers of 6.7 can map the whole family without
enumerating it. Every message quotes the offending value through `clip`, which bounds its
length: these are raised on user input and end up in a log and in an HTTP body.
"""

from __future__ import annotations

from typing import Final

_CLIP_LENGTH: Final = 32


def clip(value: object) -> str:
    """Render a rejected value for a message, bounded in length."""
    text = repr(value)
    return text if len(text) <= _CLIP_LENGTH else f"{text[:_CLIP_LENGTH]}…"


class DomainError(Exception):
    """A domain rule was broken. Never raised for an infrastructure failure."""


class InvalidShareError(DomainError):
    """A percentage outside [0, 100]. Rounding one into range would invent data."""

    def __init__(self, value: object) -> None:
        super().__init__(f"share must be a whole percentage between 0 and 100, got {clip(value)}")


class InvalidCountryCodeError(DomainError):
    """A geo that is not on the published list — including the XX a regexp would pass."""

    def __init__(self, value: object) -> None:
        super().__init__(f"not an ISO 3166-1 alpha-2 country code: {clip(value)}")


class InvalidCampaignNameError(DomainError):
    """A name that is empty, or too long, once the invisible characters are gone."""

    def __init__(self, value: object) -> None:
        super().__init__(f"campaign name is empty or too long once cleaned: {clip(value)}")


class InvalidCampaignAliasError(DomainError):
    """An alias that would not be safe or stable as a URL path segment."""

    def __init__(self, value: object) -> None:
        super().__init__(
            f"campaign alias must be lowercase letters, digits and dashes: {clip(value)}"
        )
