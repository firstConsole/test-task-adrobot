"""Every failure of this service, rendered as one body, in one place.

Routers contain no `try/except` and raise no `HTTPException` — a rule AGENTS.md states and
this module is the reason it is affordable. A use case raises what went wrong in its own
vocabulary; the table below is the only place that decides what that is worth in HTTP.

**The table is here and not on the errors.** A `status` attribute on
`CampaignNotFoundError` would make `domain/` and `application/` know they are behind an API,
which they are not: the CLI raises the same errors, and what they have in common is the
failure, not the status code.

**The detail of a 5xx is withheld in production.** A 4xx describes the caller's own request
and is theirs to read; a 5xx describes this service's insides — which environment variable
holds the refused key, what the tracker answered — and outside dev it is replaced by the
correlation id, which is the thing worth quoting anyway.

**Nothing here logs a body or a header.** The one line a handler writes carries the slug,
the status and the detail this service composed, never the upstream payload that produced
it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from uuid import UUID

import structlog
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from adrobot.api.schemas.problem import (
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TYPE_PREFIX,
    InvalidField,
    ProblemDetails,
)
from adrobot.application.errors import (
    ApplicationError,
    CampaignAlreadyImportedError,
    CampaignNotFoundError,
    CampaignNotRepairableError,
    DraftAlreadyOpenError,
    DraftBeingPushedError,
    DraftStatusChangedError,
    NothingToPushError,
    PushAttemptSettledError,
    PushBlockedError,
    StreamDoesNotRotateOffersError,
    StreamNotFoundError,
    UpstreamDeniedError,
    UpstreamError,
    UpstreamNotFoundError,
    UpstreamProtocolError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from adrobot.domain.errors import (
    DomainError,
    DuplicateOfferRowError,
    InvalidCampaignAliasError,
    InvalidCampaignNameError,
    InvalidCountryCodeError,
    InvalidShareError,
    OfferAlreadyInStreamError,
    OfferAlreadyRemovedError,
    OfferNotInStreamError,
    OfferNotRemovedError,
    PinnedSharesExceedTotalError,
)
from adrobot.logging import correlation_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI, Request
    from starlette.responses import Response

logger = structlog.stdlib.get_logger(__name__)

_PROBLEM_EVENT: Final = "http.problem"
_SERVER_ERROR: Final = 500

WITHHELD: Final = (
    "This service could not carry the request out. Quote the correlation id below to "
    "whoever runs it."
)
"""What a 5xx says in production. Deliberately not "internal server error": the reader is a
media buyer whose campaign did not get created, and the one useful thing they can do is
carry the id to somebody with a log."""


class NotAuthenticatedError(Exception):
    """No usable credential arrived with a request that needs one.

    Declared here rather than beside the dependency that raises it, so that everything this
    API can answer with is in one table. It is not an `ApplicationError`: no use case ran,
    and nothing about this failure would mean anything to the CLI.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class Problem:
    """What one kind of failure is worth in HTTP, and what a client calls it."""

    status: int
    code: str
    title: str
    headers: Mapping[str, str] | None = None
    """What the status line needs to be honest. A 401 without `WWW-Authenticate` is a 401
    that does not say how to authenticate, which RFC 9110 requires of one."""

    @property
    def type(self) -> str:
        """The problem type URI, which is the slug and nothing a reader has to visit."""
        return f"{PROBLEM_TYPE_PREFIX}{self.code}"


PROBLEMS: Final[Mapping[type[Exception], Problem]] = {
    # --- the caller did not say who they are --------------------------------------------
    NotAuthenticatedError: Problem(
        status=401,
        code="not-authenticated",
        title="This endpoint needs the shared token",
        # `Bearer` and never `Basic`: a browser answers `Basic` with its own credential
        # dialog, which this service has no way to satisfy.
        headers={"WWW-Authenticate": "Bearer"},
    ),
    # --- the caller asked for something that is not there -------------------------------
    CampaignNotFoundError: Problem(status=404, code="campaign-not-found", title="No such campaign"),
    StreamNotFoundError: Problem(status=404, code="flow-not-found", title="No such flow"),
    UpstreamNotFoundError: Problem(
        status=404, code="tracker-has-no-such-thing", title="The tracker has no such thing"
    ),
    # --- the caller asked for something the current state refuses -----------------------
    CampaignAlreadyImportedError: Problem(
        status=409, code="campaign-already-imported", title="This campaign is already open here"
    ),
    CampaignNotRepairableError: Problem(
        status=409, code="campaign-not-repairable", title="There is nothing here to finish"
    ),
    DraftAlreadyOpenError: Problem(
        status=409, code="draft-already-open", title="This flow is already being edited"
    ),
    DraftBeingPushedError: Problem(
        status=409, code="draft-being-pushed", title="This flow is being pushed right now"
    ),
    StreamDoesNotRotateOffersError: Problem(
        status=409, code="flow-rotates-no-offers", title="This flow has no offers to rotate"
    ),
    DraftStatusChangedError: Problem(
        status=409, code="draft-status-changed", title="Somebody moved this draft first"
    ),
    PushAttemptSettledError: Problem(
        status=409, code="push-attempt-settled", title="This push is already finished"
    ),
    NothingToPushError: Problem(
        status=409, code="nothing-to-push", title="There is nothing here to push"
    ),
    PushBlockedError: Problem(
        status=409, code="push-blocked", title="This flow cannot be pushed as it stands"
    ),
    OfferAlreadyInStreamError: Problem(
        status=409, code="offer-already-in-flow", title="That offer is already in this flow"
    ),
    OfferAlreadyRemovedError: Problem(
        status=409, code="offer-already-removed", title="That offer is already out"
    ),
    OfferNotRemovedError: Problem(
        status=409, code="offer-not-removed", title="That offer was never taken out"
    ),
    # --- the caller sent something this service will not build on -----------------------
    InvalidShareError: Problem(status=422, code="invalid-share", title="A share is out of range"),
    InvalidCountryCodeError: Problem(
        status=422, code="invalid-country", title="That is not a country code"
    ),
    InvalidCampaignNameError: Problem(
        status=422, code="invalid-campaign-name", title="That campaign name cannot be used"
    ),
    InvalidCampaignAliasError: Problem(
        status=422, code="invalid-campaign-alias", title="That alias cannot be used in a link"
    ),
    OfferNotInStreamError: Problem(
        status=422, code="offer-not-in-flow", title="That offer is not in this flow"
    ),
    DuplicateOfferRowError: Problem(
        status=422, code="duplicate-offer", title="One offer cannot be in a flow twice"
    ),
    PinnedSharesExceedTotalError: Problem(
        status=422,
        code="pinned-shares-exceed-total",
        title="The pinned shares leave nothing to divide",
    ),
    UpstreamRejectedError: Problem(
        status=422, code="tracker-rejected-request", title="The tracker refused this"
    ),
    DomainError: Problem(
        status=422, code="invalid-request", title="This request cannot be carried out"
    ),
    # --- nobody's fault at the call site ------------------------------------------------
    UpstreamUnavailableError: Problem(
        status=502, code="tracker-unavailable", title="The tracker is not answering"
    ),
    UpstreamDeniedError: Problem(
        status=502, code="tracker-refused-our-key", title="The tracker refused this service"
    ),
    UpstreamProtocolError: Problem(
        status=502, code="tracker-answered-unusably", title="The tracker answered unusably"
    ),
    UpstreamError: Problem(
        status=502, code="tracker-failed", title="The tracker could not be used"
    ),
    ApplicationError: Problem(status=500, code="internal-error", title="This service failed"),
}
"""Every error either ring raises, and what each is worth in HTTP.

`tests/api/test_errors.py` walks both exception hierarchies and fails on a class that is not
a key here, so the two fallbacks — `DomainError` and `ApplicationError` — catch a *subclass*
somebody adds at runtime rather than a family nobody mapped.

`UpstreamRejectedError` is a 422 and not a 502 on purpose. The tracker refuses a payload for
one common reason — an offer, a geo or a name the caller chose — and answering 502 would
send somebody to check a service that is working perfectly."""

INTERNAL: Final = Problem(status=500, code="internal-error", title="This service failed")
"""For an exception no ring raised deliberately: a bug, rendered rather than leaked."""


def install_error_handlers(app: FastAPI, *, expose_internals: bool) -> None:
    """Register the four handlers that stand between a failure and the client.

    `expose_internals` is `settings.env != "prod"`, resolved by the caller for the same
    reason the docs policy is: this module renders errors, it does not read configuration.

    The three signatures take the request and ignore it — Starlette hands it to every
    handler, and none of these needs it: the correlation id comes off the context, which is
    also where a handler raised outside a request would look.
    """

    async def handled(_: Request, exc: Exception) -> Response:
        return _rendered(exc, problem=_problem_for(exc), expose_internals=expose_internals)

    async def invalid(_: Request, exc: Exception) -> Response:
        return _invalid_request(exc)

    async def http_error(_: Request, exc: Exception) -> Response:
        return _http_error(exc)

    app.add_exception_handler(NotAuthenticatedError, handled)
    app.add_exception_handler(DomainError, handled)
    app.add_exception_handler(ApplicationError, handled)
    app.add_exception_handler(RequestValidationError, invalid)
    # Starlette's own, for the 404 of an unknown path and the 405 of a wrong method: without
    # it those two are the only responses of this API shaped like something else.
    app.add_exception_handler(HTTPException, http_error)
    # Everything that was not meant to happen. Starlette re-raises after this answers, so
    # the traceback still reaches the access log and the test that asserts on it.
    app.add_exception_handler(Exception, handled)


def _problem_for(exc: Exception) -> Problem:
    """Find the rendering of this error, or of the nearest ancestor that has one."""
    for ancestor in type(exc).__mro__:
        found = PROBLEMS.get(ancestor)
        if found is not None:
            return found
    return INTERNAL


def _rendered(exc: Exception, *, problem: Problem, expose_internals: bool) -> JSONResponse:
    """Build the body for one failure, withholding the detail of a 5xx in production."""
    internal = problem.status >= _SERVER_ERROR
    detail = str(exc) if expose_internals or not internal else WITHHELD
    if internal:
        # The one place the reason for a 5xx is recorded when the response will not carry
        # it, and an unhandled bug reaches here too — so the traceback goes on the line.
        # `exc_info=exc` rather than `.exception()`, which reads `sys.exc_info()`: this is a
        # handler Starlette calls, not an `except` block, and the exception it was handed is
        # a better answer than whatever happens to be current.
        logger.error(_PROBLEM_EVENT, code=problem.code, status=problem.status, exc_info=exc)
    return _response(
        problem=problem,
        detail=detail,
        campaign_id=getattr(exc, "campaign_id", None),
        errors=_tracker_fields(exc),
        headers=problem.headers,
    )


def _invalid_request(exc: Exception) -> JSONResponse:
    """Render a request the schema refused, without echoing what was sent back at its sender.

    FastAPI's own handler puts the offending value in `input` and, for a refusal raised by a
    validator, the exception object in `ctx`. Both are the request coming back — which on a
    field carrying a credential is the credential in the response body — so this renders the
    two things a client can act on and drops the rest.
    """
    raised = exc.errors() if isinstance(exc, RequestValidationError) else ()
    problem = PROBLEMS[DomainError]
    return _response(
        problem=problem,
        detail="This request does not fit the shape this endpoint takes.",
        errors=tuple(
            InvalidField(
                location=".".join(str(part) for part in error.get("loc", ())),
                message=str(error.get("msg", "")),
            )
            for error in raised
        ),
    )


def _http_error(exc: Exception) -> JSONResponse:
    """Render Starlette's own refusals — an unknown path, a wrong method — in the one shape."""
    status = exc.status_code if isinstance(exc, HTTPException) else _SERVER_ERROR
    detail = exc.detail if isinstance(exc, HTTPException) else WITHHELD
    phrase = HTTPStatus(status).phrase
    return _response(
        problem=Problem(status=status, code=phrase.lower().replace(" ", "-"), title=phrase),
        detail=str(detail),
        headers=getattr(exc, "headers", None),
    )


def _response(
    *,
    problem: Problem,
    detail: str,
    campaign_id: object = None,
    errors: Sequence[InvalidField] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Serialise one problem, leaving out every member that has nothing to say."""
    body = ProblemDetails(
        type=problem.type,
        title=problem.title,
        status=problem.status,
        detail=detail,
        code=problem.code,
        correlation_id=correlation_id(),
        campaign_id=campaign_id if isinstance(campaign_id, UUID) else None,
        errors=tuple(errors) if errors else None,
    )
    return JSONResponse(
        status_code=problem.status,
        content=body.model_dump(mode="json", exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=dict(headers) if headers else None,
    )


def _tracker_fields(exc: Exception) -> tuple[InvalidField, ...]:
    """Turn the tracker's own field complaints into the list a 422 already carries.

    Prefixed `tracker.` rather than given a member of their own: a screen that highlights a
    field does not care which side of the wire refused it, and one list is one thing for it
    to read.
    """
    fields = getattr(exc, "fields", None)
    if not isinstance(fields, Mapping):
        return ()
    return tuple(
        InvalidField(location=f"tracker.{name}", message=str(complaint))
        for name, complaints in fields.items()
        for complaint in complaints
    )
