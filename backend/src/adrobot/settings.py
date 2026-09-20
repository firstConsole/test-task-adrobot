"""Typed configuration: one flat `ADROBOT_` namespace, read and validated once at start-up.

`Settings` is built exactly once, in `main.create_asgi_app`, and handed down explicitly.
There is deliberately no module-level instance and no cached `get_settings()`: a cached
getter is a global that any module can reach for behind the composition root's back, and
every test would have to remember to clear it.

There is no `env_file` either. The monorepo keeps `.env` at its root while every backend
tool runs with `cwd=backend/`, so no single relative path is right from both; compose and
the Makefile export the file as real environment variables instead. The side effect is
worth having: a stray `backend/.env` cannot silently change what the test suite sees.

**The field set below is the whole of PLAN-BACKEND §9, declared at once even though four
of these variables are first read at stage 4 or later.** That is not eagerness, it is what
makes `_unknown_prefixed_variables` honest: the guard tells an operator that a prefixed
name no field claims is a typo, and it can only say that while this class and
`.env.example` (1.8) hold the same list. The rule for every later stage is therefore one
rule: *a field and its `.env.example` line move in the same commit.*
`ADROBOT_KEITARO_CA_BUNDLE` (§3.1) is absent from §9's list and so from both; 4.3 adds it
to both or to neither.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    UrlConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from adrobot.logging import LogLevel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import ValidationInfo

ENV_PREFIX: Final = "ADROBOT_"

Environment = Literal["dev", "prod"]
"""What the process is. Two values, because all three things that branch on it —
the log renderer, the OpenAPI exposure and (at 6.7) the error detail — are binary,
and a third value would silently take the `dev` branch of each."""


class UnknownSettingError(Exception):
    """A variable carries the project prefix but matches no field on `Settings`.

    **Not a `ValueError`, and that is the load-bearing part.** pydantic catches
    `ValueError` out of a validator and re-reports it as a `ValidationError` carrying
    `input_value=` — which, for a `mode="before"` model validator on `BaseSettings`, is
    the entire raw settings dict, with `ADROBOT_KEITARO_API_KEY` in clear because nothing
    has turned it into a `SecretStr` yet. Measured: a misspelled variable printed
    `input_value={'key': 'THE-REAL-ADMIN-KEY-VALUE'}` into the start-up traceback an
    operator then pastes into a chat. Anything that is not a `ValueError` or an
    `AssertionError` propagates out of pydantic untouched, so this one carries its own
    message and nothing else.

    A class of its own rather than a long message at the `raise` site, which ruff TRY003
    rejects; the `Error` suffix is N818, which PLAN-BACKEND's own sketches get wrong.
    """

    def __init__(self, names: Iterable[str]) -> None:
        listed = ", ".join(sorted(names))
        message = (
            f"unknown {ENV_PREFIX}* environment variables: {listed}. Every name this "
            f"service reads is in .env.example; this is a typo or a stale export."
        )
        super().__init__(message)


def _unknown_prefixed_variables(environ: Mapping[str, str], known: Iterable[str]) -> frozenset[str]:
    """Return the `ADROBOT_*` names in `environ` that no field would ever consume.

    PLAN-00 §5.1 and PLAN-01 §4 both promise that `extra="forbid"` makes a misspelled
    variable fail at start-up. Measured against pydantic-settings 2.15, it does not:
    `extra="forbid"` rejects an unknown key that arrived through an `.env` *file*, while
    `EnvSettingsSource` looks up one variable per declared field and never enumerates the
    process environment. Compose, the Makefile and CI all export real variables, so on the
    only path this project uses, `ADROBOT_KEITAROO_API_KEY=...` was silently discarded and
    the operator was told `keitaro_api_key: Field required` — which names the one variable
    they spelled correctly. The invariant has to be written out.
    """
    claimed = {f"{ENV_PREFIX}{name}".casefold() for name in known}
    return frozenset(
        name
        for name in environ
        if name.casefold().startswith(ENV_PREFIX.casefold()) and name.casefold() not in claimed
    )


def _reject_unusable_tracker_url(url: HttpUrl, info: ValidationInfo) -> HttpUrl:
    """Refuse a tracker URL that would misdirect the Admin API key or silently lose part of itself.

    PLAN-BACKEND §9 asks for the scheme and the host to be checked at start-up. The asset
    is the key: every Admin API request carries it in an `Api-Key` header, so whatever
    host stands in this variable is the host that receives it.

    What a start-up check cannot do is stop DNS rebinding — the name is resolved per
    request, much later, and resolving it here would put a network call in the boot path.
    Containment for that is `follow_redirects=False` in the transport (§3.1). What this
    catches is the configuration mistake, and it names the variable carrying it.
    """
    variable = f"{ENV_PREFIX}{(info.field_name or '').upper()}"
    if url.scheme != "https":
        message = (
            f"{variable} must be https, not {url.scheme!r}: the tracker key travels in a "
            f"request header, and the preview links built from this URL open in a browser."
        )
        raise ValueError(message)
    if url.username or url.password:
        message = (
            f"{variable} must not carry credentials. Keitaro authenticates with the "
            f"Api-Key header, and a password in a URL reaches logs and error bodies."
        )
        raise ValueError(message)
    if url.query or url.fragment:
        message = (
            f"{variable} must be a bare base URL, with no query and no fragment: httpx "
            f"joins a path onto it and drops both without saying so."
        )
        raise ValueError(message)
    return url


def _known_time_zone(name: str) -> str:
    """Resolve the name through `zoneinfo` now, so a typo surfaces at boot and not in stats.

    Keitaro serialises timestamps without an offset, in the tracker's own zone (§5.14). An
    unresolvable name would otherwise first be noticed by a stage-8 report query, as a
    "clicks today" boundary in the wrong place. Because `validate_default` is on, this
    also runs against the `"UTC"` default, which is what catches a runtime image built
    without a tz database.

    `ZoneInfoNotFoundError` subclasses `KeyError`, not `ValueError`, so pydantic would let
    it out raw; it is re-raised as the `ValueError` a field validator is allowed to throw.
    """
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        message = (
            f"{ENV_PREFIX}KEITARO_TIMEZONE={name!r} is not an IANA time zone on this "
            f"machine. If the name looks right, the image has no tz database."
        )
        raise ValueError(message) from exc
    return name


# pydantic's own PostgresDsn accepts nine drivers, psycopg2 among them. The engine is
# built with create_async_engine and alembic runs through connection.run_sync, so a
# synchronous driver is a start-up error rather than a MissingGreenlet on the first query.
AsyncPostgresDsn = Annotated[
    PostgresDsn, UrlConstraints(allowed_schemes=["postgresql+asyncpg"], host_required=True)
]
TrackerUrl = Annotated[HttpUrl, AfterValidator(_reject_unusable_tracker_url)]
TimeZoneName = Annotated[str, AfterValidator(_known_time_zone)]


class Settings(BaseSettings):
    """Every `ADROBOT_*` variable the service reads. `.env.example` (1.8) is this field set."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        extra="forbid",
        # Configuration is read once and never changes. Frozen turns a late write into an
        # error instead of a value half the process has already read past.
        frozen=True,
        # Defaults go through the validators too, which is what resolves the "UTC" default
        # against this machine's tz database.
        validate_default=True,
        # `.env.example` ships the two secrets with empty values. Treating an empty value
        # as unset turns `cp .env.example .env && docker compose up` into "Field required"
        # naming each variable, instead of a 401 from the tracker an hour later.
        env_ignore_empty=True,
    )

    # No default. `dev` would fail open — a forgotten variable in production would get
    # human-readable logs and an exposed /docs — and `prod` would fail confusingly in dev.
    env: Environment
    log_level: LogLevel = "INFO"

    database_url: AsyncPostgresDsn
    access_token: SecretStr

    keitaro_base_url: TrackerUrl
    keitaro_api_key: SecretStr
    keitaro_public_base_url: TrackerUrl
    keitaro_timezone: TimeZoneName = "UTC"

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_variables(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Fail on a misspelled variable, before "field required" can point at the wrong one.

        `mode="before"`: the typo is *why* the field is missing, so an after-validator
        would never run. The check reads the process environment rather than `data`
        because that is the channel the mistake arrives on — which also means a test
        building `Settings` from explicit keyword arguments is still protected, and
        that `data` is never read and so can never be echoed back.
        """
        unknown = _unknown_prefixed_variables(os.environ, cls.model_fields)
        if unknown:
            raise UnknownSettingError(unknown)
        return data
