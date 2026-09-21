"""The one body every failure of this API arrives in: RFC 9457 `application/problem+json`.

A pydantic model rather than a dict built in the handler, for a reason that is about the
frontend and not about tidiness: stage 9 generates its TypeScript from this service's
OpenAPI, and a body assembled by hand is a body the schema describes from memory. This
model is what the handlers render *and* what the routes declare, so the two cannot drift.

Two members are ours rather than the RFC's.

`code` is the stable slug a client switches on. `type` already identifies the problem, but
it is a URI, and a TypeScript union of URIs is a union of strings with a prefix everybody
has to remember to strip. The two carry the same fact in the two shapes their two readers
want.

`correlation_id` is the one field worth quoting in a bug report: it is on the response
header, in every log line of the request that failed, and here. RFC 9457's own `instance`
is left out because the honest value for it would be the request path, and a path does not
identify an occurrence — this does.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
PROBLEM_TYPE_PREFIX: Final = "urn:adrobot:problem:"
"""A URN and not an https URL: RFC 9457 does not require the type to be dereferenceable, and
a link into a documentation site this project does not host would be a promise the response
cannot keep."""


class InvalidField(BaseModel):
    """One field a request was refused over, and who refused it.

    `location` is dotted from the outside in — `body.country`, `query.limit` — and a
    complaint the *tracker* made about a field it was sent is prefixed `tracker.`, so that
    one list can carry both without a client having to ask which kind it is holding.
    """

    model_config = ConfigDict(frozen=True)

    location: str
    message: str


class OfferShare(BaseModel):
    """One offer row of a flow, in the three fields the tracker itself keeps."""

    model_config = ConfigDict(frozen=True)

    offer_id: int
    share: int
    state: str


class ConflictingState(BaseModel):
    """Two readings of one flow, side by side, in the same shape so they can be diffed.

    Carried on the 409 a push answers when the flow has been edited in Keitaro meanwhile.
    A body that only said "conflict" would leave the screen with nothing to show and the
    person with nothing to decide between overwriting and discarding.
    """

    model_config = ConfigDict(frozen=True)

    tracker_holds: tuple[OfferShare, ...]
    push_would_write: tuple[OfferShare, ...]


class ProblemDetails(BaseModel):
    """A failure, as this API reports one.

    Every member the RFC defines that this service fills in, plus the three that carry what
    a screen does with a failure: the slug, the id to quote, and the campaign the problem is
    about where there is one — which is what lets "already imported" offer to open the
    campaign instead of only refusing the request.
    """

    model_config = ConfigDict(frozen=True)

    type: str
    title: str
    status: int
    detail: str
    code: str
    correlation_id: str | None = None
    campaign_id: UUID | None = None
    errors: tuple[InvalidField, ...] | None = None
    conflict: ConflictingState | None = None


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Declare the failures one endpoint can answer with, so the schema carries them too.

    Without this an operation documents its 200 and nothing else, and the client stage 9
    generates would be typed as though `POST /campaigns` could only succeed. The media type
    is named explicitly because it is not this API's default — and it is what the handlers
    actually send.
    """
    return {
        status: {
            "model": ProblemDetails,
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_MEDIA_TYPE: {}},
        }
        for status in statuses
    }
