from __future__ import annotations

import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest
from pydantic import BaseModel, ValidationError

from adrobot.settings import (
    ENV_PREFIX,
    MissingSettingError,
    Settings,
    UnknownSettingError,
    _missing_required_variables,
)
from tests.helpers import VALID_ENVIRONMENT

if TYPE_CHECKING:
    from collections.abc import Callable

# Where `adrobot` is on disk, so a traceback walk can tell our frames from pydantic's.
# Spelled the way tests/test_secret_containment.py spells it, for the same reason.
SOURCE_ROOT: Final = Path(__file__).resolve().parents[1] / "src" / "adrobot"

pytestmark = pytest.mark.usefixtures("valid_environment")

# The contract 1.8 writes .env.example from, and the contract the unknown-variable guard
# leans on: a prefixed name outside this set is a typo only while the two lists agree.
# Asserted in both directions on purpose — a field added "just in case" (cors_origins is
# the one PLAN-BACKEND §9 all but invites) fails here with the reason written above it.
EXPECTED_FIELDS = frozenset(
    {
        "env",
        "log_level",
        "database_url",
        "access_token",
        "keitaro_base_url",
        "keitaro_api_key",
        "keitaro_public_base_url",
        "keitaro_timezone",
    }
)

# The second construction path, derived from the first so the two cannot drift: what
# `Settings()` finds in the environment, handed in by keyword instead. The values stay
# strings, which is what the environment would have delivered anyway.
VALID_KEYWORDS: Final[dict[str, Any]] = {
    name.removeprefix(ENV_PREFIX).lower(): value for name, value in VALID_ENVIRONMENT.items()
}


def test_the_declared_fields_are_the_expected_set() -> None:
    assert frozenset(Settings.model_fields) == EXPECTED_FIELDS


def test_env_example_declares_exactly_the_model_fields() -> None:
    """Read the shipped template and hold it to the model, in both directions.

    Until 1.8 this contract was a sentence in three files. It is the premise the
    unknown-variable guard rests on — a prefixed name is a typo only while the template
    and the model list the same names — so it is worth a test that fails when a field is
    added without its line, or a line survives a field being removed.
    """
    template = Path(__file__).parents[2] / ".env.example"
    declared = {f"{ENV_PREFIX}{name}".upper() for name in Settings.model_fields}
    written = {
        line.split("=", 1)[0]
        for line in template.read_text(encoding="utf-8").splitlines()
        if line.startswith(ENV_PREFIX)
    }
    assert written == declared


def test_every_required_field_is_in_the_canonical_test_environment() -> None:
    required = {
        f"{ENV_PREFIX}{name}".upper()
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }

    assert required == set(VALID_ENVIRONMENT)


def test_the_environment_is_parsed_into_typed_values() -> None:
    settings = Settings()

    assert settings.env == "dev"
    assert settings.log_level == "INFO"
    assert settings.keitaro_timezone == "UTC"
    assert str(settings.keitaro_base_url) == "https://tracker.invalid/admin_api/v1"
    assert settings.database_url.scheme == "postgresql+asyncpg"


def test_settings_are_frozen(settings: Settings) -> None:
    # `# type: ignore` and not a cast: the pydantic plugin makes this a *static* error
    # under frozen=True as well, so the ignore is the second half of the lock — drop
    # frozen and this test fails twice, once here and once on warn_unused_ignores.
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        ("ADROBOT_ENV", "staging", "Input should be 'dev' or 'prod'"),
        ("ADROBOT_LOG_LEVEL", "INFOO", "Input should be 'DEBUG'"),
        ("ADROBOT_DATABASE_URL", "postgresql://a:b@db/adrobot", "URL scheme should be"),
        ("ADROBOT_KEITARO_BASE_URL", "http://tracker.invalid", "must be https"),
        ("ADROBOT_KEITARO_BASE_URL", "https://u:p@tracker.invalid", "must not carry credentials"),
        ("ADROBOT_KEITARO_BASE_URL", "https://tracker.invalid/?x=1", "no query and no fragment"),
        ("ADROBOT_KEITARO_TIMEZONE", "Mars/Olympus", "is not an IANA time zone"),
    ],
)
def test_a_bad_value_is_refused_at_startup_with_a_message_about_it(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, expected: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError, match=expected):
        Settings()


def test_the_ssrf_rules_do_not_reject_an_ordinary_tracker_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other direction of the rules above: a check that only ever says no is a check
    # nobody can configure around.
    monkeypatch.setenv("ADROBOT_KEITARO_BASE_URL", "https://tracker.example.com:8443/admin_api/v1")

    assert Settings().keitaro_base_url.port == 8443


def test_a_misspelled_variable_fails_at_startup_and_names_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PLAN-00 §5.1's promise, which `extra="forbid"` alone does not keep for process
    # environment variables — the only channel compose and CI use.
    monkeypatch.setenv("ADROBOT_KEITAROO_API_KEY", "some-value")

    with pytest.raises(UnknownSettingError, match="ADROBOT_KEITAROO_API_KEY"):
        Settings()


def test_no_configured_value_is_echoed_by_the_unknown_variable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lock on UnknownSettingError not being a ValueError. pydantic re-reports a
    # ValueError from a before-validator with `input_value=` set to the whole raw settings
    # dict, which at that point still holds the admin key as a plain string — and the
    # operator pastes that traceback into a chat. Both secrets are checked, and the typo
    # is itself a misspelling of a secret's name.
    monkeypatch.setenv("ADROBOT_KEITARO_APIKEY", "a-second-copy-of-the-key")

    with pytest.raises(UnknownSettingError) as caught:
        Settings()

    reported = str(caught.value)

    assert VALID_ENVIRONMENT["ADROBOT_KEITARO_API_KEY"] not in reported
    assert VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"] not in reported
    assert "a-second-copy-of-the-key" not in reported


def test_all_misspelled_variables_are_reported_together(monkeypatch: pytest.MonkeyPatch) -> None:
    # So that an operator fixes the whole compose file in one pass instead of one
    # container restart per typo.
    monkeypatch.setenv("ADROBOT_TYPO_ONE", "1")
    monkeypatch.setenv("ADROBOT_TYPO_TWO", "2")

    with pytest.raises(UnknownSettingError) as caught:
        Settings()

    assert "ADROBOT_TYPO_ONE" in str(caught.value)
    assert "ADROBOT_TYPO_TWO" in str(caught.value)


def test_a_half_filled_environment_is_refused_without_echoing_the_key_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first-run state `cp .env.example .env && docker compose up` produces: the two
    # secrets filled in, the rest still blank. pydantic reported that as one "Field
    # required" per absent field, each carrying `input_value=` — the merged settings dict,
    # truncated from the middle, with `keitaro_api_key` still a plain `str` because
    # nothing had wrapped it in a SecretStr yet. The truncation keeps the tail, so the end
    # of the admin key went into the start-up traceback once per absent field.
    for name in VALID_ENVIRONMENT:
        if name not in {"ADROBOT_ENV", "ADROBOT_KEITARO_API_KEY"}:
            monkeypatch.delenv(name)
    secret = VALID_ENVIRONMENT["ADROBOT_KEITARO_API_KEY"]

    with pytest.raises(MissingSettingError) as caught:
        Settings()

    message = str(caught.value)
    assert {name for name in VALID_ENVIRONMENT if name in message} == {
        "ADROBOT_DATABASE_URL",
        "ADROBOT_ACCESS_TOKEN",
        "ADROBOT_KEITARO_BASE_URL",
        "ADROBOT_KEITARO_PUBLIC_BASE_URL",
    }
    # Fragments, not the whole value: `secret not in printed` passes against the unfixed
    # file, because what leaked was the tail. Eight characters of an API key are already
    # enough to recognise one in a paste.
    printed = message + "".join(traceback.format_exception(caught.value))
    assert not any(secret[at : at + 8] in printed for at in range(len(secret) - 7))


def test_the_refusal_carries_no_second_surface_to_render_it_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `str(exc)` is not the only renderer of a ValidationError: `.errors()` and `.json()`
    # hand back `input_value` whole and untruncated, so an error short enough to print
    # cleanly still served the merged dict to anything structured — 6.7's problem+json
    # body, a structured log of a start-up failure. A fix that only shortened the printed
    # line would leave that open, so what is pinned here is that the exception has no
    # state beyond its message, and that nothing upstream is chained behind it.
    for name in VALID_ENVIRONMENT:
        if name != "ADROBOT_KEITARO_API_KEY":
            monkeypatch.delenv(name)
    secret = VALID_ENVIRONMENT["ADROBOT_KEITARO_API_KEY"]

    with pytest.raises(MissingSettingError) as caught:
        Settings()

    assert caught.value.args == (str(caught.value),)
    assert caught.value.__cause__ is None
    chained = "".join(traceback.format_exception(caught.value))
    assert not any(secret[at : at + 8] in chained for at in range(len(secret) - 7))


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (lambda m: m.delenv("ADROBOT_DATABASE_URL"), MissingSettingError),
        (lambda m: m.setenv("ADROBOT_KEITARO_APIKEY", "x"), UnknownSettingError),
    ],
    ids=["absent", "misspelled"],
)
def test_neither_refusal_leaves_a_configured_value_in_a_frame_of_ours(
    monkeypatch: pytest.MonkeyPatch,
    break_it: Callable[[pytest.MonkeyPatch], None],
    expected: type[Exception],
) -> None:
    # The half of the promise a message cannot keep. `--showlocals` is in this project's
    # own addopts, and a renderer that captures frame locals prints a frame's parameters
    # whether or not the body reads them — so the validator's `data`, the merged dict with
    # the admin key still a plain `str`, was printed twice per failure from a frame whose
    # error text was clean. Hence the `del` on the refusing path; this is what makes it
    # load-bearing rather than decorative. Only our own frames are asserted on:
    # pydantic's `BaseModel.__init__` holds the same dict and is not ours to unbind — it
    # sets `__tracebackhide__`, which is why pytest does not print it.
    break_it(monkeypatch)
    secrets = [
        VALID_ENVIRONMENT[name] for name in ("ADROBOT_KEITARO_API_KEY", "ADROBOT_ACCESS_TOKEN")
    ]

    with pytest.raises(expected) as caught:
        Settings()

    ours = [
        (frame.f_code.co_name, name, repr(value))
        for frame, _ in traceback.walk_tb(caught.value.__traceback__)
        if Path(frame.f_code.co_filename).is_relative_to(SOURCE_ROOT)
        for name, value in frame.f_locals.items()
    ]
    assert ours, "no frame of ours was walked: the traceback shape changed"
    assert not [
        (function, name)
        for function, name, printed in ours
        for secret in secrets
        if any(secret[at : at + 8] in printed for at in range(len(secret) - 7))
    ]


def test_an_empty_environment_names_every_required_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One error listing six names, not six errors listing one each: an operator fixes the
    # compose file in a single pass, for the reason the typo guard batches its names too.
    for name in VALID_ENVIRONMENT:
        monkeypatch.delenv(name)

    with pytest.raises(MissingSettingError) as caught:
        Settings()

    message = str(caught.value)
    assert {name for name in VALID_ENVIRONMENT if name in message} == set(VALID_ENVIRONMENT)
    # The two fields that carry defaults are not demanded, and .env.example is where the
    # operator is sent for the names that are.
    assert f"{ENV_PREFIX}LOG_LEVEL" not in message
    assert f"{ENV_PREFIX}KEITARO_TIMEZONE" not in message
    assert ".env.example" in message


def test_the_demanded_set_is_read_off_the_fields_rather_than_written_out() -> None:
    # A hard-coded list would keep demanding a variable the day its field gains a default,
    # and stop demanding one the day a default goes away. `is_required()` moves with the
    # field; these two attributes are one field, before and after.
    class Moving(BaseModel):
        gained_a_default: str = "x"
        still_required: str

    assert _missing_required_variables([], Moving.model_fields) == {f"{ENV_PREFIX}STILL_REQUIRED"}
    assert _missing_required_variables(["still_required"], Moving.model_fields) == frozenset()
    # And against the real model, cross-checked against the list helpers.py writes out by
    # hand — the one place in this suite that is allowed to be a literal.
    assert _missing_required_variables([], Settings.model_fields) == frozenset(VALID_ENVIRONMENT)


def test_the_missing_variable_error_is_not_a_validation_error() -> None:
    # The lock on MissingSettingError not being a ValueError, for the reason
    # UnknownSettingError carries: pydantic catches a ValueError out of a before-validator
    # and re-reports it with `input_value=` set to the merged settings dict — which is
    # exactly the disclosure this error closes, coming back through the door it shut.
    assert not issubclass(MissingSettingError, ValueError)
    assert not issubclass(MissingSettingError, AssertionError)


def test_a_typo_is_reported_before_the_variable_it_made_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Why both checks sit in one validator in a fixed order. The key is missing *because*
    # of the typo, and "ADROBOT_KEITARO_API_KEY has no value" would send an operator to
    # add a line their compose file already has, spelled wrongly. An unrelated variable is
    # missing as well, so this is not merely the causal pair.
    monkeypatch.delenv("ADROBOT_KEITARO_API_KEY")
    monkeypatch.delenv("ADROBOT_DATABASE_URL")
    monkeypatch.setenv("ADROBOT_KEITAROO_API_KEY", "a-copy-of-the-key")

    with pytest.raises(UnknownSettingError, match="ADROBOT_KEITAROO_API_KEY") as caught:
        Settings()

    assert "a-copy-of-the-key" not in str(caught.value)


def test_keyword_construction_still_builds_a_complete_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The path production never takes and the suite's own fixtures do not either, which is
    # why it needs stating: a completeness check reading os.environ instead of `data`
    # would refuse this with every field supplied.
    for name in VALID_ENVIRONMENT:
        monkeypatch.delenv(name)

    settings = Settings(**VALID_KEYWORDS)

    assert settings.env == "dev"
    assert settings.keitaro_timezone == "UTC"


def test_keyword_construction_is_checked_for_completeness_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other direction: a check reading only os.environ would let this through, and the
    # error would arrive as pydantic's, with the input dict attached.
    for name in VALID_ENVIRONMENT:
        monkeypatch.delenv(name)
    partial = {name: value for name, value in VALID_KEYWORDS.items() if name != "keitaro_api_key"}

    with pytest.raises(MissingSettingError, match="ADROBOT_KEITARO_API_KEY"):
        Settings(**partial)


def test_the_unknown_variable_guard_still_fires_on_the_keyword_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The property the file had before this commit, on the path the new check made
    # interesting: every field is supplied, so nothing is missing, and a stale export
    # still has to stop the process.
    for name in VALID_ENVIRONMENT:
        monkeypatch.delenv(name)
    monkeypatch.setenv("ADROBOT_KEITARO_TIMEZOME", "UTC")

    with pytest.raises(UnknownSettingError, match="ADROBOT_KEITARO_TIMEZOME"):
        Settings(**VALID_KEYWORDS)


def test_an_unprefixed_variable_is_none_of_our_business(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both halves: some other tool's KEITARO_API_KEY must not be read as ours, and must
    # not be reported as a typo of ours either. It is now reported as an absence, which is
    # what it is, and the message names the variable this service does read.
    monkeypatch.delenv("ADROBOT_KEITARO_API_KEY")
    monkeypatch.setenv("KEITARO_API_KEY", "belongs-to-something-else")

    with pytest.raises(MissingSettingError, match="ADROBOT_KEITARO_API_KEY") as caught:
        Settings()

    assert "belongs-to-something-else" not in str(caught.value)


def test_an_empty_value_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `.env.example` ships the two secrets empty, so `cp .env.example .env && up` has to
    # fail naming the variable rather than 401 against the tracker an hour later.
    # `env_ignore_empty` drops the name before the validator sees it, so the half-filled
    # .env and the never-exported variable are one failure with one message — asserted
    # rather than assumed, because that equality is what makes one message enough.
    monkeypatch.setenv("ADROBOT_KEITARO_API_KEY", "")

    with pytest.raises(MissingSettingError) as empty:
        Settings()

    monkeypatch.delenv("ADROBOT_KEITARO_API_KEY")

    with pytest.raises(MissingSettingError) as absent:
        Settings()

    assert "ADROBOT_KEITARO_API_KEY" in str(empty.value)
    assert str(empty.value) == str(absent.value)


@pytest.mark.parametrize("field", ["access_token", "keitaro_api_key"])
def test_a_secret_is_masked_everywhere_it_could_be_printed(settings: Settings, field: str) -> None:
    # repr is what --showlocals prints for every frame of a failing test; model_dump_json
    # is what an accidental `return settings` from a handler would serialise.
    secret = VALID_ENVIRONMENT[f"ADROBOT_{field.upper()}"]

    assert secret not in repr(settings)
    assert secret not in str(getattr(settings, field))
    assert secret not in settings.model_dump_json()
