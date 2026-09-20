"""Request middleware: the correlation id and the access log.

Both are **pure ASGI** classes rather than `BaseHTTPMiddleware` subclasses, for two
reasons measured against starlette 1.6 rather than recalled:

*   `BaseHTTPMiddleware.call_next` runs the rest of the application in a new anyio task,
    and a task copies the context when it is spawned. A `ContextVar` set in `dispatch`
    before `call_next` *is* visible to the endpoint; one set by the endpoint is **not**
    visible to `dispatch` afterwards. FastAPI moves its own `AsyncExitStackMiddleware`
    inside every user middleware for exactly this reason. The correlation id would
    survive it today, but a middleware whose correctness depends on which side of a
    context copy a value was written is a trap for whoever edits it next.
*   It costs a task group and two memory object streams per request to build `Request`
    and `Response` objects. This file works in `scope` and in the ASGI `message`, which
    it already has.

The third thing PLAN-BACKEND §1 puts in this module — the request body size limit — is
not written here. starlette 1.6 ships `RequestBodyLimitMiddleware`, already pure ASGI,
which refuses on `Content-Length` before reading a byte and mid-stream when there is
none. It is wired in `api/app.py`; a hand-rolled copy of a well-tested class the
framework already carries is exactly the slop this project is about.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

import structlog
from starlette.datastructures import Headers

from adrobot.logging import correlation_id_scope, sanitize_correlation_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["CORRELATION_ID_HEADER", "AccessLogMiddleware", "CorrelationIdMiddleware"]

# `X-Request-ID`, not `X-Correlation-ID`: nginx generates `$request_id` natively, so 9.8 is
# one `proxy_set_header X-Request-ID $request_id;` line and the proxy's access log already
# joins ours on that value. It is also the name the frontend reads off a failed response.
CORRELATION_ID_HEADER: Final = "x-request-id"

_ACCESS_EVENT: Final = "http.request"
_SERVER_ERROR: Final = 500
_CLIENT_ERROR: Final = 400

# Named `logger` and not `_log`: ruff's flake8-logging-format rules find a logger by the
# binding's name, so this spelling is what makes G004 and friends police the calls below.
logger = structlog.stdlib.get_logger(__name__)


class CorrelationIdMiddleware:
    """Give every HTTP request an id, put it in the context, echo it back.

    Outermost of the three, so that the access line carries the id and so does a 413 from
    the body limit.
    """

    def __init__(self, app: ASGIApp, *, header: str = CORRELATION_ID_HEADER) -> None:
        self.app = app
        self.header = header
        self._raw_header = header.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the application with a correlation id bound to the current context."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = sanitize_correlation_id(Headers(scope=scope).get(self.header))

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Any value a handler set is dropped rather than appended to: two
                # `X-Request-ID` headers is a response nothing downstream can read.
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != self._raw_header
                ]
                headers.append((self._raw_header, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        with correlation_id_scope(request_id):
            await self.app(scope, receive, send_with_header)


class AccessLogMiddleware:
    """Log one record per HTTP request.

    On the line: method, path, status, duration, the client address, and — through the
    context rather than through an argument — the correlation id.

    Deliberately not on it: any part of a request or response **body**, any request
    **header** (`Authorization` and `Cookie` both carry §9's shared token) and the query
    string in any form. Stage 8 is the first to take query parameters and is where a
    decision to log their *names* belongs; until then the rule is the simple one, and a
    rule that starts narrow is the one that still holds when `?token=` shows up.
    """

    def __init__(self, app: ASGIApp, *, quiet_paths: Iterable[str] = ()) -> None:
        self.app = app
        # The health paths, in practice. A compose healthcheck and a kubelet probe hit
        # `/healthz` every few seconds, and at INFO that would be the only thing anyone
        # ever saw. They are logged at DEBUG while they answer 2xx and at the ordinary
        # level as soon as they do not — a failing readiness probe is the line you want.
        self.quiet_paths = frozenset(quiet_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Time the request, take its status from the ASGI messages, log exactly once."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status = 0

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_status)
        # `Exception` and not `BaseException`: a cancelled request — the client hung up,
        # or the server is draining — gets no line, because it produced no response
        # either. A deliberate hole; filling it would put a record on every shutdown.
        except Exception:
            # `status or _SERVER_ERROR`: if the response had already started, the client
            # really did get that status and then a broken body, and claiming a 500 it
            # never saw would make the line disagree with its own traceback. Otherwise
            # `ServerErrorMiddleware`, which sits outside us, sends the 500.
            # Written inline rather than through a helper because `.exception()` reads
            # `sys.exc_info()`, and ruff's LOG004 is right to want the call inside the
            # handler where a reader can see that.
            logger.exception(_ACCESS_EVENT, **self._fields(scope, status or _SERVER_ERROR, started))
            # Re-raised untouched: this middleware observes, it does not handle. Rendering
            # the failure as problem+json is 6.7's job.
            raise
        self._emit(scope, status=status, started=started)

    def _fields(self, scope: Scope, status: int, started: float) -> dict[str, object]:
        client = scope.get("client")
        return {
            "method": scope["method"],
            "path": scope["path"],
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            # From the socket, never from `X-Forwarded-For`: behind nginx that header is
            # whatever the caller wrote, and believing it without a trusted-proxy list is
            # a lie. uvicorn's `--proxy-headers` is where that gets fixed, at 1.7.
            "client_ip": None if client is None else client[0],
        }

    def _emit(self, scope: Scope, *, status: int, started: float) -> None:
        fields = self._fields(scope, status, started)
        if status >= _SERVER_ERROR:
            logger.error(_ACCESS_EVENT, **fields)
        elif status >= _CLIENT_ERROR:
            logger.warning(_ACCESS_EVENT, **fields)
        elif scope["path"] in self.quiet_paths:
            logger.debug(_ACCESS_EVENT, **fields)
        else:
            logger.info(_ACCESS_EVENT, **fields)
