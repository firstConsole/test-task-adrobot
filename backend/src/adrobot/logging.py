"""Structured logging: one stream, one correlation id, one redactor.

Three decisions live here because they are expensive to reverse later:

*   **One stream.** Our records and uvicorn's are rendered by structlog and written by a
    single stdlib handler. A container that emits JSON on half its lines and coloured
    text on the other half cannot be ingested by anything.
*   **The correlation id is a `ContextVar`, not a parameter.** An ASGI server runs each
    request in its own task and a task copies the context when it is spawned, so the id
    reaches every record made while serving that request and reaches no other request.
    `correlation_id()` is the accessor 6.7 needs for the problem+json body.
*   **Redaction is by key name and by nothing else.** See `redact`.

`configure_logging` takes primitives rather than `Settings`: alembic's `env.py` (1.9) and
the CLI want the same stream without dragging pydantic-settings in.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sys
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final, Literal

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO

    from structlog.typing import EventDict, Processor, WrappedLogger

__all__ = [
    "LogLevel",
    "LogRenderer",
    "configure_logging",
    "correlation_id",
    "correlation_id_scope",
    "new_correlation_id",
    "redact",
    "sanitize_correlation_id",
]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
"""A stdlib level name. Spelled as names so `.env.example` stays readable."""

LogRenderer = Literal["json", "console"]
"""How a record is written. `console` is for a developer's terminal only."""

_CORRELATION_ID_KEY: Final = "correlation_id"

_correlation_id: Final[ContextVar[str | None]] = ContextVar(
    f"adrobot.{_CORRELATION_ID_KEY}", default=None
)

# The id is echoed into a response header, into every log record and (at 6.7) into the
# problem+json body, so an inbound one is quoted back at whoever sent it. Anything outside
# this alphabet is replaced rather than escaped: records are newline delimited and JSON
# quoted, and a value carrying a newline or a quote forges a record. The lower bound
# rejects the one-character ids that make a log unsearchable; the upper bound keeps a
# megabyte header out of every line.
_CORRELATION_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")

_REDACTED: Final = "[redacted]"
_TOO_DEEP: Final = "[too deep]"

# How far `redact` walks before it stops. A Keitaro error body is three or four levels
# deep; the bound is what keeps a cycle or a pathological payload from being the thing
# that takes the process down.
_MAX_REDACT_DEPTH: Final = 8

# Matched against the key with `-` and `_` removed, case-folded, as a substring. `token`
# is on the list because Keitaro returns a campaign's Click API token inside the campaign
# object (PLAN-BACKEND §10) and a non-2xx body is logged.
_SECRET_KEY_MARKERS: Final = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        # `GET /settings`, which the time-zone resolver reads, answers with the tracker's
        # own licence key beside the zone. Its 2xx body is never logged; this is what keeps
        # a build answering 500 with the same object from putting it in the stream.
        "license",
        "password",
        "secret",
        "token",
    }
)

# uvicorn's own dictConfig runs before our factory is imported and leaves these three with
# their own handlers and `propagate = False`. Left alone they emit plain text next to our
# JSON.
_UVICORN_LOGGERS: Final = ("uvicorn", "uvicorn.access", "uvicorn.error")

# Turned down here rather than with a `--no-access-log` flag a Dockerfile could forget:
#
# *   `uvicorn.access` — our own access line says the same thing and carries the
#     correlation id.
# *   `httpx` — logs `HTTP Request: GET <full url> "HTTP/1.1 200 OK"` at INFO, and the
#     full URL means every query value. From 4.3 that logger is our Keitaro client, which
#     makes it the one place in the process that would put a tracker path and its query
#     into the stream without going through §10's own event.
_QUIETENED_LOGGERS: Final = ("uvicorn.access", "httpx")

# uvicorn attaches `extra={"color_message": ...}` to several of its records: the same
# sentence again, with ANSI escapes in it. In a JSON stream that is a second copy of the
# message carrying terminal control codes.
_NOISY_FOREIGN_KEYS: Final = ("color_message",)


def new_correlation_id() -> str:
    """Return a fresh correlation id."""
    return uuid.uuid4().hex


def sanitize_correlation_id(raw: str | None) -> str:
    """Return `raw` if it is a usable correlation id, otherwise a fresh one.

    The argument is whatever arrived in a request header, which is to say
    attacker-controlled. Nothing is escaped or truncated: a value that does not match is
    discarded whole, because a half-accepted id is worse than a generated one — it looks
    like the caller's id and is not.
    """
    if raw is not None and _CORRELATION_ID_PATTERN.match(raw):
        return raw
    return new_correlation_id()


def correlation_id() -> str | None:
    """Return the correlation id of the request being served, if there is one.

    `None` outside a request — a CLI command, a migration — and the caller renders that as
    an absent field rather than as the string "None".
    """
    return _correlation_id.get()


@contextlib.contextmanager
def correlation_id_scope(value: str) -> Iterator[str]:
    """Bind `value` as the correlation id for the duration of the block.

    The token is reset on the way out so that a context outliving the block — a CLI
    process, a synchronous test — does not keep the id. Inside an ASGI server the reset is
    belt and braces: each request already runs in its own task, hence its own context.
    """
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalised = key.replace("-", "").replace("_", "").casefold()
    return any(marker in normalised for marker in _SECRET_KEY_MARKERS)


def _redact(value: object, budget: int) -> object:
    if budget <= 0:
        return _TOO_DEEP
    if isinstance(value, Mapping):
        return {
            key: _REDACTED if _is_secret_key(key) else _redact(item, budget - 1)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        # Rebuilt as a tuple and not as a list: `exc_info` reaches the processor chain as
        # a three-tuple and `format_exc_info` dispatches on its type.
        return tuple(_redact(item, budget - 1) for item in value)
    if isinstance(value, list):
        return [_redact(item, budget - 1) for item in value]
    return value


def redact(payload: object) -> object:
    """Return `payload` with the value of every secret-looking key replaced.

    Redaction is **by key name**, recursively, and by nothing else. That is worth being
    exact about, because a redactor which claims more than it does is worse than none:

    *   Not by value. The one high-value secret in this process is the Keitaro admin key,
        and matching it by value would mean holding its plaintext here — where PLAN-01 §4
        puts it in exactly one module, `infrastructure/keitaro/transport.py`. A redactor
        that has to unwrap a secret in order to hide it has moved the leak, not closed it.
    *   Not by type. `SecretStr` already masks itself in `str` and `repr`, which is what
        both renderers call; there is nothing here to add.

    What is left is the case the type system cannot see: a plain `str` a caller put under
    a telling key, and specifically a Keitaro error body with a campaign's Click API
    `token` nested inside it (PLAN-BACKEND §10). Stage 4 calls this function on that body
    rather than growing a second redactor.

    The cost is false positives — a key named `tokens_used` is redacted too. That is the
    right way round, and it is the kind of surprise that belongs in AGENTS.md.
    """
    return _redact(payload, _MAX_REDACT_DEPTH)


def _add_correlation_id(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Stamp the current correlation id on the record, if the caller has not."""
    current = _correlation_id.get()
    if current is not None:
        event_dict.setdefault(_CORRELATION_ID_KEY, current)
    return event_dict


def _redact_record(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Apply `redact` to the whole record, its own top-level keys included."""
    return {
        key: _REDACTED if _is_secret_key(key) else _redact(value, _MAX_REDACT_DEPTH)
        for key, value in event_dict.items()
    }


def _drop_noisy_foreign_keys(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Drop the `extra` fields of third parties that only duplicate `event`."""
    for key in _NOISY_FOREIGN_KEYS:
        event_dict.pop(key, None)
    return event_dict


def _json_fallback(value: object) -> str:
    """Render what `json` cannot: `repr`, because that is what masks a `SecretStr`."""
    return repr(value)


def _shared_processors() -> list[Processor]:
    """Return the processors applied to our records and to stdlib records alike."""
    return [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        _redact_record,
    ]


def _render(renderer: LogRenderer, stream: TextIO) -> list[Processor]:
    """Return the tail of the chain: exception handling plus the renderer itself.

    The two modes differ in more than their last element. `ConsoleRenderer` formats
    `exc_info` itself and must not have `format_exc_info` in front of it; `JSONRenderer`
    needs the traceback turned into a string first.
    """
    if renderer == "json":
        return [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(default=_json_fallback, sort_keys=True),
        ]
    return [
        structlog.dev.ConsoleRenderer(
            colors=stream.isatty(),
            # Pinned rather than left to structlog.dev's own choice, which is `rich` when
            # `rich` can be imported. `rich` is in this project only as a transitive dev
            # dependency of mypy, so the default would give one traceback format in a
            # developer's venv and another in the runtime image, and would change the day
            # mypy stopped shipping it. It also defaults to show_locals=True, which puts
            # every local of every frame into the stream.
            exception_formatter=structlog.dev.plain_traceback,
        )
    ]


def configure_logging(
    *, level: LogLevel, renderer: LogRenderer, stream: TextIO | None = None
) -> None:
    """Point every logger in the process at one handler and one renderer.

    A process-wide side effect, and an idempotent one: calling it again replaces the
    handler rather than adding a second. It belongs in the composition root of the
    *process* (`main.create_asgi_app`) and never in `create_app`, which a test builds many
    times and which must not reconfigure the world each time.

    Args:
        level: The threshold on the root logger.
        renderer: `json` in production, `console` in a developer's terminal.
        stream: Where records go. Defaults to stdout, which is where a container runtime
            looks, and is overridden by the test suite to read the real chain's output.
    """
    target = sys.stdout if stream is None else stream
    shared = _shared_processors()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Not cached on purpose. A cached bound logger freezes the processor chain it was
        # first used with, so a module-level logger plus a reconfiguring test suite gives
        # a test that passes alone and fails in company. The price is a dict lookup.
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(target)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Records that never went through structlog — uvicorn, SQLAlchemy at stage 5,
            # alembic at 1.9 — enter the chain here and come out in the same shape.
            # `ExtraAdder` runs first and `shared` ends with the redactor, so a third
            # party's `extra={"api_key": ...}` is redacted like anything of ours.
            foreign_pre_chain=[
                structlog.stdlib.ExtraAdder(),
                _drop_noisy_foreign_keys,
                *shared,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *_render(renderer, target),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    for name in _QUIETENED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
