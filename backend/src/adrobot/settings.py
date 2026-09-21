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
    from pydantic.fields import FieldInfo

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


class MissingSettingError(Exception):
    """A required field has no value, said without printing the values that do have one.

    Not a `ValueError`, for the reason `UnknownSettingError` gives above — there that is
    a precaution, here it is the whole point. pydantic's own report for an absent field
    reads `Field required [type=missing, input_value={...}]`, and on `BaseSettings` that
    input value is the merged settings dict, holding `keitaro_api_key` as a plain `str`
    because wrapping it in a `SecretStr` is the validation this error pre-empts. The repr
    is truncated from the middle, so what reached the start-up traceback was the *tail* of
    the admin key, once per absent field — which is also why the textual scan in
    tests/test_secret_containment.py, grepping for the whole value, could not see it.

    The message is composed from `Settings`'s own field names and from nothing else, so no
    configured value is in scope where it is built.
    """

    def __init__(self, names: Iterable[str]) -> None:
        listed = ", ".join(sorted(names))
        message = (
            f"required {ENV_PREFIX}* environment variables have no value: {listed}. "
            f"Every name this service reads is in .env.example; copy it to .env and give "
            f"each of these a value. An empty value counts as unset."
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


def _missing_required_variables(
    supplied: Iterable[str], fields: Mapping[str, FieldInfo]
) -> frozenset[str]:
    """Return the variable names of the required fields nothing has supplied a value for.

    Read off `FieldInfo.is_required()` rather than written out: `log_level` and
    `keitaro_timezone` carry defaults and must not be demanded, and the day a field gains
    a default — or loses one — the demand moves with the field instead of waiting for
    somebody to remember a second list. It is the derivation the suite's own
    `test_every_required_field_is_in_the_canonical_test_environment` already uses, so the
    two now agree by construction.

    `supplied` is a view of names, never the mapping they were taken from: that mapping
    still holds the admin key as a plain string at this point, and a name is the whole of
    what this question needs.
    """
    present = {name.casefold() for name in supplied}
    return frozenset(
        f"{ENV_PREFIX}{name}".upper()
        for name, field in fields.items()
        if field.is_required() and name.casefold() not in present
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
    unresolvable name would otherwise first be noticed by a report query, as a "clicks
    today" boundary in the wrong place. Because `validate_default` is on, this also runs
    against the `"UTC"` default, which is what catches a runtime image built without a tz
    database.

    This is the fallback and not the last word: `application/time_zone.py` asks the tracker
    for its own zone on the first statistics screen and prefers what it answers. It is
    still worth setting, because `GET /settings` is in no part of the published schema and
    a build without it leaves this value standing for the life of the process.

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
        # An empty value is not a value. `.env.example` ships every required name with
        # a usable placeholder, because the four-command start would otherwise die on a
        # fresh clone — but the moment anyone blanks one, this is what turns
        # `docker compose up` into a refusal naming that variable, instead of a 401 from
        # the tracker an hour later.
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
    def _reject_an_unusable_environment(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Fail on a misspelled variable, then on an absent one, before pydantic reports either.

        `mode="before"` for both: the typo is *why* the field is missing, so an
        after-validator would never run, and pydantic's report for an absent field is
        itself the disclosure `MissingSettingError` exists to pre-empt.

        One validator and not two, because the order is the behaviour and two could not
        promise it — measured on pydantic 2.13, `mode="before"` model validators run in
        *reverse* declaration order, so a split would make the error an operator sees a
        fact about where the methods sit in the file. The typo has to win: it is usually
        why the variable is absent, and naming the absence first would send an operator to
        add a line their compose file already has, misspelled — which is verbatim the
        wrong-variable report the unknown-name guard exists to end, arriving through the
        new check.

        The two read different things because the two mistakes arrive on different
        channels. A prefixed name no field claims can only come from the process
        environment and never appears in `data`, which is also why a `Settings` built from
        keyword arguments is guarded. Absence is visible only in `data` — measured, a dict
        keyed by field name, identically shaped whether the values came from the
        environment, from keyword arguments or from both, and already emptied of what
        `env_ignore_empty` discards. It is read as keys and handed on as keys, and the
        refusing path unbinds it, so neither the message nor the frame beneath it can
        carry a value.
        """
        unknown = _unknown_prefixed_variables(os.environ, cls.model_fields)
        missing = _missing_required_variables(data.keys(), cls.model_fields)
        if not (unknown or missing):
            return data
        # A traceback renderer that captures frame locals — pytest's own `--showlocals`,
        # which this project turns on — prints a frame's parameters whether or not the body
        # reads them, and `data` still holds the admin key as a plain `str`. Keeping it out
        # of the message is half a promise while the frame under the message still carries
        # it, so the refusing path lets go of it first. Measured: without this line the
        # merged dict is printed twice per failure.
        del data
        if unknown:
            raise UnknownSettingError(unknown)
        raise MissingSettingError(missing)
