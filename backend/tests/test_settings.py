from __future__ import annotations

import pytest
from pydantic import ValidationError

from adrobot.settings import ENV_PREFIX, Settings, UnknownSettingError
from tests.helpers import VALID_ENVIRONMENT

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


def test_the_declared_fields_are_the_env_example_contract() -> None:
    assert frozenset(Settings.model_fields) == EXPECTED_FIELDS


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


def test_an_unprefixed_variable_is_none_of_our_business(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both halves: some other tool's KEITARO_API_KEY must not be read as ours, and must
    # not be reported as a typo of ours either.
    monkeypatch.delenv("ADROBOT_KEITARO_API_KEY")
    monkeypatch.setenv("KEITARO_API_KEY", "belongs-to-something-else")

    with pytest.raises(ValidationError, match="keitaro_api_key") as caught:
        Settings()

    assert "belongs-to-something-else" not in str(caught.value)


def test_an_empty_value_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `.env.example` ships the two secrets empty, so `cp .env.example .env && up` has to
    # fail naming the variable rather than 401 against the tracker an hour later.
    monkeypatch.setenv("ADROBOT_KEITARO_API_KEY", "")

    with pytest.raises(ValidationError, match="Field required"):
        Settings()


@pytest.mark.parametrize("field", ["access_token", "keitaro_api_key"])
def test_a_secret_is_masked_everywhere_it_could_be_printed(settings: Settings, field: str) -> None:
    # repr is what --showlocals prints for every frame of a failing test; model_dump_json
    # is what an accidental `return settings` from a handler would serialise.
    secret = VALID_ENVIRONMENT[f"ADROBOT_{field.upper()}"]

    assert secret not in repr(settings)
    assert secret not in str(getattr(settings, field))
    assert secret not in settings.model_dump_json()
