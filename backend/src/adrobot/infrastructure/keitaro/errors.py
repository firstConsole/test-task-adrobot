"""What a status and a body from the tracker mean, in the application's own vocabulary.

Two rules shape this module.

**It reads the error body by hand, with no wire model.** That is not an oversight and not
only the import contract talking: the error path is precisely where a server misbehaves.
A 502 comes from a proxy as HTML, a rate limiter answers with nothing at all, and a
validation failure arrives in a shape the published schema never describes — Keitaro's own
406 is an object of field names to lists of complaints, which no named schema in the
document declares. A strict model here would turn "the tracker refused us, and here is why"
into "the tracker refused us, and the reason failed to parse".

**It never widens a failure into a success.** Every branch below ends in a raise. A status
this module has not thought about lands in the last one, which says so.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from adrobot.application.errors import (
    UpstreamDeniedError,
    UpstreamNotFoundError,
    UpstreamProtocolError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)

if TYPE_CHECKING:
    import httpx

_DENIED: Final = frozenset(
    {HTTPStatus.UNAUTHORIZED, HTTPStatus.PAYMENT_REQUIRED, HTTPStatus.FORBIDDEN}
)
# 400 and 406 are both "your payload", and the difference is which one this build of the
# tracker happens to use: the published schema declares 406 for a failed validation on
# every create, where most APIs would answer 400. 422 is here for the same reason.
_REJECTED: Final = frozenset(
    {
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_ACCEPTABLE,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    }
)
_SERVER_ERROR: Final = 500
_SUMMARY_CLIP: Final = 300


def raise_for_keitaro(response: httpx.Response) -> None:
    """Do nothing if the tracker answered with a success, and raise otherwise."""
    if response.is_success:
        return

    status = response.status_code
    summary, fields = _explain(response)

    if response.is_redirect:
        # Redirects are off, so a 3xx is the tracker telling us it is somewhere else. It is
        # a fact to report and not a place to go: every request carries the admin key in a
        # header httpx would hand to the new host.
        location = response.headers.get("location", "somewhere unnamed")
        redirected = f"it redirected to {location}, which was not followed"
        raise UpstreamProtocolError(redirected, status=status)
    if status in _DENIED:
        raise UpstreamDeniedError(summary, status=status)
    if status == HTTPStatus.NOT_FOUND:
        raise UpstreamNotFoundError(summary, status=status)
    if status in _REJECTED:
        raise UpstreamRejectedError(summary, status=status, fields=fields)
    if status >= _SERVER_ERROR:
        raise UpstreamUnavailableError(summary, status=status)
    unexpected = f"it answered {status}: {summary}"
    raise UpstreamProtocolError(unexpected, status=status)


def unreachable_tracker(method: str, path: str, exc: Exception) -> UpstreamUnavailableError:
    """Name a failure that never became an answer — a timeout, a refused connection, a bad name.

    Returned rather than raised, so the call site keeps its `raise ... from exc` and the
    original exception stays in the chain. The message carries the class name and not
    `str(exc)`: httpx puts the full request URL in the latter, and the base URL is the one
    piece of the tracker's identity this service does not scatter through its logs.
    """
    return UpstreamUnavailableError(f"{method} {path} failed with {type(exc).__name__}")


def _explain(response: httpx.Response) -> tuple[str, dict[str, tuple[str, ...]]]:
    """Return what the tracker said, and the per-field complaints if it made any.

    Three shapes are understood, and everything else is clipped text:

    *   `{"error": "..."}` — the documented failure body of nearly every endpoint;
    *   `{"field": ["...", "..."], ...}` — the undocumented shape of a 406, which is the
        one that matters: it is how the tracker says *which* part of a campaign it did not
        like, and part 1's form wants to put that beside the field;
    *   an empty body, from a proxy or a rate limiter.
    """
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return (_clip(text) if text else f"it answered {response.status_code} with no body", {})

    if isinstance(body, dict):
        stated = body.get("error")
        if isinstance(stated, str) and stated:
            return _clip(stated), {}
        fields = _field_complaints(body)
        if fields:
            listed = "; ".join(
                f"{name}: {', '.join(messages)}" for name, messages in sorted(fields.items())
            )
            return _clip(listed), fields
    return _clip(str(body)), {}


def _field_complaints(body: dict[str, object]) -> dict[str, tuple[str, ...]]:
    """Read the `{field: [messages]}` shape, taking only the entries that really are that."""
    complaints = {}
    for name, value in body.items():
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            complaints[name] = tuple(str(item) for item in value)
    return complaints


def _clip(text: str) -> str:
    """Bound what the tracker said: this ends up in a log line and in an HTTP body."""
    return text if len(text) <= _SUMMARY_CLIP else f"{text[:_SUMMARY_CLIP]}…"
