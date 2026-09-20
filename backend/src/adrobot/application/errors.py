"""What this service could not do, as exceptions, for everything that is not a domain rule.

`domain/errors.py` holds the rules — a share outside [0, 100], an offer twice in one flow.
This holds the operational failures: the tracker did not answer, refused our key, rejected
what we sent. The two families stay apart because they are answered differently. A domain
error is the caller's fault and is fixed by sending something else; an upstream error is
nobody's fault at the call site and is often fixed by waiting.

There is deliberately no shared `AdRobotError` root yet, although PLAN-BACKEND §8 sketches
one. A common base earns its keep when something renders both families — the problem+json
handlers of 6.7 — and designing that base three stages before its only consumer would be
guessing at the slug and status each error carries. Two handlers cost a line.

The names say `Upstream` and not `Keitaro`: this ring does not know which tracker it is
wrapping, and the vendor belongs in the message, where a person reads it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class ApplicationError(Exception):
    """A use case could not be carried out. Never raised for a broken domain rule."""


class UpstreamError(ApplicationError):
    """The tracker was asked something and the answer cannot be used.

    `status` is what it answered, or `None` when it did not answer at all — which is a
    distinction worth keeping: 6.7 renders the first as the tracker's verdict and the
    second as this service's inability to reach it.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        fields: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Field name to the complaints about it, straight from the tracker. Empty for every
        # failure but a rejected payload, so a renderer can ask without checking the type.
        self.fields: Mapping[str, tuple[str, ...]] = dict(fields or {})


class UpstreamUnavailableError(UpstreamError):
    """The tracker did not answer, or answered that it was having trouble."""

    def __init__(self, summary: str, *, status: int | None = None) -> None:
        super().__init__(f"the tracker is not answering: {summary}", status=status)


class UpstreamDeniedError(UpstreamError):
    """The tracker refused this service's key, or the key's edition cannot use the API.

    Operationally the most useful of these: everything else is a bad minute, and this one
    will not clear up on its own.
    """

    def __init__(self, summary: str, *, status: int) -> None:
        super().__init__(
            f"the tracker refused this service's admin key, set in "
            f"ADROBOT_KEITARO_API_KEY: {summary}",
            status=status,
        )


class UpstreamNotFoundError(UpstreamError):
    """The tracker has no such campaign, flow or offer."""

    def __init__(self, summary: str, *, status: int) -> None:
        super().__init__(f"the tracker has no such thing: {summary}", status=status)


class UpstreamRejectedError(UpstreamError):
    """The tracker rejected what this service sent, and often said which field."""

    def __init__(
        self, summary: str, *, status: int, fields: Mapping[str, tuple[str, ...]] | None = None
    ) -> None:
        super().__init__(
            f"the tracker rejected this request: {summary}", status=status, fields=fields
        )


class UpstreamProtocolError(UpstreamError):
    """The tracker answered, in a shape or with a state this service cannot act on.

    Raised where believing the answer would be worse than failing: a redirect that would
    carry the admin key elsewhere, a flow whose `schema` is not one of the three, a write
    that reads back as something other than what was written.
    """

    def __init__(self, summary: str, *, status: int | None = None) -> None:
        super().__init__(
            f"the tracker answered in a way this service cannot use: {summary}", status=status
        )
