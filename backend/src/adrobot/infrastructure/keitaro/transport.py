"""The one client this process talks to the tracker with, and the only module that holds its key.

What is decided here, and why each of them is not a default:

*   **`follow_redirects=False`**, for two independent reasons. httpx strips `Authorization`
    when a redirect crosses hosts and carries every other header — including this API's
    `Api-Key` — to wherever the redirect points, so a tracker that has been moved, or a
    DNS answer that has been tampered with, is enough to hand the admin key to a stranger.
    And a 302 on a `POST` turns it into a `GET`, which would silently do nothing while
    answering 200. A redirect is a fact to report, not one to chase.
*   **Verification is never turned off, and there is no setting that could turn it off.**
    PLAN-BACKEND §3.1 proposed an `ADROBOT_KEITARO_CA_BUNDLE` for a private certificate
    authority; this does not add one. httpx already reads `SSL_CERT_FILE` and
    `SSL_CERT_DIR` when it builds the default context, so the capability exists without a
    name of ours — and `verify=<path>` is deprecated in httpx 0.28 anyway. A variable that
    duplicates the platform's own is one more name an operator can misspell under
    `extra="forbid"` and one more branch nothing tests.
*   **A semaphore of five.** The editor makes one call per keystroke-adjacent action, and
    the connection limits below bound sockets rather than requests in flight. This is
    back-pressure on somebody else's production tracker, which is the polite half of being
    a wrapper.
*   **Retries are for reads and for writes that describe a state.** `post` — the creating
    verb — is never retried, on any failure: the outcome of a create that timed out is
    unknown, and a second attempt is how two campaigns get made. `put` is retried because
    every body this service sends with it is the whole intended state of a flow, so
    repeating one cannot compound. `query` is the report builder: a `POST` by method and a
    read by meaning.
*   **Only a timeout is retried, never a connection error.** A refused connection or a name
    that does not resolve is a configuration fact, not a transient one, and asking three
    times is three chances for the key to reach whatever is answering on that address.

The key is unwrapped here and nowhere else — `tests/test_secret_containment.py` is the
check, not this sentence — and it is unwrapped straight into the header it belongs in,
never bound to a name, because pytest runs with `--showlocals`.

Measured, and the reason nothing in this module logs a request: `httpx.Headers.__repr__`
obfuscates `authorization`, `proxy-authorization` and `cookie`, and prints everything else
in full. `Api-Key` is everything else.
"""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

import httpx
import structlog

from adrobot.infrastructure.keitaro.errors import raise_for_keitaro, unreachable_tracker
from adrobot.logging import redact

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from adrobot.settings import Settings

logger = structlog.stdlib.get_logger(__name__)

# Three seconds to establish a connection, ten to be answered. The read timeout is the one
# that matters: the offer catalogue comes back in a single unpaginated response, and the
# stage-2 probe measures it against exactly this number.
KEITARO_TIMEOUT: Final = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=5.0)
KEITARO_LIMITS: Final = httpx.Limits(max_connections=20, max_keepalive_connections=10)

CONCURRENCY: Final = 5
MAX_ATTEMPTS: Final = 3
BACKOFF_SECONDS: Final = 0.25
BACKOFF_CAP_SECONDS: Final = 5.0

# 429 and 408 by name; everything from 500 up by range, so that a status this project has
# never seen is treated as the tracker having a bad minute rather than as a verdict.
_RETRY_STATUSES: Final = frozenset({HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS})
_SERVER_ERROR: Final = 500

_REQUEST_EVENT: Final = "keitaro.request"
_BODY_CLIP: Final = 600


def build_keitaro_client(settings: Settings) -> httpx.AsyncClient:
    """Build the tracker client, with the admin key on it."""
    return httpx.AsyncClient(
        base_url=str(settings.keitaro_base_url),
        # Unwrapped into the header and never into a variable. A leading slash on a path
        # does not reset the `/admin_api/v1` in this base URL: httpx merges the two, where
        # `urljoin` would discard the prefix.
        headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
        timeout=KEITARO_TIMEOUT,
        limits=KEITARO_LIMITS,
        follow_redirects=False,
    )


class KeitaroTransport:
    """Every HTTP call to the tracker goes through one of these four methods.

    All four answer the same way: a successful response, or an exception from
    `application/errors.py`. Which exception is `errors.py`'s judgement and not this
    module's — nothing here reads a status except to decide whether to ask again — and
    turning a body into a domain object is `mapping.py`'s.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        concurrency: int = CONCURRENCY,
        max_attempts: int = MAX_ATTEMPTS,
        backoff: float = BACKOFF_SECONDS,
    ) -> None:
        self._client = client
        self._gate = asyncio.Semaphore(concurrency)
        self._max_attempts = max_attempts
        self._backoff = backoff

    async def get(
        self, path: str, *, params: Mapping[str, str | int] | None = None
    ) -> httpx.Response:
        """Read something. Retried: a read that did not happen costs nothing to ask again."""
        return await self._send("GET", path, params=params, retry=True)

    async def query(self, path: str, *, json: Mapping[str, Any]) -> httpx.Response:
        """Ask a question whose body is too large for a query string — `/report/build`.

        A separate verb from `post` so that the rule about creates can stay simple enough
        to be true. This one creates nothing and is retried like the read it is.
        """
        return await self._send("POST", path, json=json, retry=True)

    async def post(self, path: str, *, json: Mapping[str, Any]) -> httpx.Response:
        """Create something. **Never retried**, whatever went wrong.

        A create whose outcome is unknown stays unknown. The compensation for one that
        half-succeeded is a visible state and a button a person presses, not a loop.
        """
        return await self._send("POST", path, json=json, retry=False)

    async def put(self, path: str, *, json: Mapping[str, Any]) -> httpx.Response:
        """Replace something. Retried, because every body sent this way is a whole state."""
        return await self._send("PUT", path, json=json, retry=True)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Mapping[str, Any] | None = None,
        retry: bool,
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            final = attempt >= self._max_attempts
            started = time.perf_counter()
            try:
                # The gate is held for the call and released before the backoff: a request
                # waiting to be repeated must not occupy one of the five slots while it
                # sleeps, or a tracker having a bad minute would stall every other caller.
                async with self._gate:
                    response = await self._client.request(method, path, params=params, json=json)
            except httpx.TimeoutException as exc:
                self._log_failure(method, path, exc, attempt=attempt, started=started)
                if final or not retry:
                    raise unreachable_tracker(method, path, exc) from exc
                await asyncio.sleep(_retry_delay(attempt, None, self._backoff))
                continue
            except httpx.HTTPError as exc:
                # A refused connection, a name that does not resolve, a protocol error.
                # Not retried, and translated here rather than left to escape as httpx's
                # own type: a use case that had to import httpx would have the adapter's
                # choice of client baked into it.
                self._log_failure(method, path, exc, attempt=attempt, started=started)
                raise unreachable_tracker(method, path, exc) from exc

            self._log(method, path, response, attempt=attempt, started=started)
            if not retry or final or not _is_retryable(response.status_code):
                raise_for_keitaro(response)
                return response
            await asyncio.sleep(
                _retry_delay(attempt, response.headers.get("retry-after"), self._backoff)
            )

    def _log(
        self, method: str, path: str, response: httpx.Response, *, attempt: int, started: float
    ) -> None:
        fields: dict[str, object] = {
            "method": method,
            # The path as this service asked for it, with no query string and no base URL:
            # a `redirect` finding aside, everything identifying is in those two.
            "path": path,
            "status": response.status_code,
            # Timed here rather than read off `response.elapsed`, which httpx stamps
            # when the response stream closes and leaves unset on one that was never
            # streamed. This also measures what the caller actually waited for.
            "duration_ms": _since(started),
            "attempt": attempt,
        }
        if response.is_success:
            # No body, ever. A campaign object carries the Click API token, and a 2xx body
            # is the one place it is guaranteed to be.
            logger.info(_REQUEST_EVENT, **fields)
            return
        if response.is_redirect:
            # Worth its own field: with redirects off, a 3xx means the tracker is somewhere
            # else, and the next thing to know is whether it is somewhere else on purpose.
            fields["location"] = response.headers.get("location")
        body = _failure_body(response)
        if body is not None:
            # Omitted rather than logged empty: a 502 from a proxy often has no body at
            # all, and `body: ""` on every one of them is a field that stops being read.
            fields["body"] = body
        logger.warning(_REQUEST_EVENT, **fields)

    def _log_failure(
        self, method: str, path: str, exc: httpx.HTTPError, *, attempt: int, started: float
    ) -> None:
        logger.warning(
            _REQUEST_EVENT,
            method=method,
            path=path,
            status=None,
            duration_ms=_since(started),
            attempt=attempt,
            # The class name and not the message: httpx puts the full URL in the latter.
            error=type(exc).__name__,
        )


@asynccontextmanager
async def keitaro_transport(settings: Settings) -> AsyncIterator[KeitaroTransport]:
    """Open the client for the life of a caller's block, and close it afterwards.

    The resource's lifetime belongs to the module that owns the resource, rather than to an
    application lifespan that would have to remember it. Stage 6.6 composes this into
    `build_ports`, and the application factory never learns that a socket exists.
    """
    async with build_keitaro_client(settings) as client:
        yield KeitaroTransport(client)


def _since(started: float) -> float:
    """Milliseconds since `started`, rounded the way the access log rounds them."""
    return round((time.perf_counter() - started) * 1000, 3)


def _is_retryable(status: int) -> bool:
    """Whether a status is the tracker having a bad minute rather than an answer."""
    return status in _RETRY_STATUSES or status >= _SERVER_ERROR


def _retry_delay(attempt: int, retry_after: str | None, base: float) -> float:
    """Return how long to wait before attempt `attempt + 1`.

    Exponential from `base`, capped, and jittered across the whole interval rather than
    around it — several callers backing off in step is how one slow minute becomes a
    thundering herd on the minute after. A `Retry-After` the tracker sent wins outright,
    within the same cap: it knows when it will be ready and this does not.
    """
    if retry_after is not None:
        try:
            return min(BACKOFF_CAP_SECONDS, max(0.0, float(retry_after)))
        except ValueError:
            # Also a legal Retry-After — an HTTP date. Parsing one to gain a second of
            # accuracy on a backoff is not worth the timezone question it opens.
            pass
    ceiling = min(BACKOFF_CAP_SECONDS, base * 2 ** (attempt - 1))
    return random.uniform(0, ceiling)  # noqa: S311 — a backoff, not a secret


def _failure_body(response: httpx.Response) -> object | None:
    """Return a non-2xx body in a shape that is safe to log.

    Keitaro answers a rejected create with `{"error": ...}` and a validation failure with a
    field-to-messages object, both worth having in the log. Either can also carry a nested
    `token`, which is what `redact` is for; a body that is not JSON at all — a proxy's HTML
    error page — is clipped instead.
    """
    try:
        parsed = response.json()
    except ValueError:
        return response.text[:_BODY_CLIP] or None
    return redact(parsed)
