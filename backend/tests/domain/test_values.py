from __future__ import annotations

import pytest

from adrobot.domain.errors import (
    InvalidCampaignAliasError,
    InvalidCampaignNameError,
    InvalidCountryCodeError,
    InvalidShareError,
)
from adrobot.domain.geo import COUNTRY_CODES, COUNTRY_NAMES
from adrobot.domain.values import (
    MAX_CAMPAIGN_NAME,
    TOTAL_SHARE,
    CampaignAlias,
    CampaignName,
    CountryCode,
    Share,
)


@pytest.mark.parametrize("value", [0, 1, 33, 100])
def test_share_accepts_the_whole_range(value: int) -> None:
    assert Share(value) == value


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_share_rejects_anything_outside_it(value: int) -> None:
    with pytest.raises(InvalidShareError):
        Share(value)


def test_share_is_an_int_so_the_kernel_can_do_arithmetic_on_it() -> None:
    assert divmod(TOTAL_SHARE - Share(25), 3) == (25, 0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MX", "MX"),
        ("mx", "MX"),
        ("  mx  ", "MX"),
        # Full-width letters: what a copy-paste out of a spreadsheet produces.
        ("\uff2d\uff38", "MX"),
    ],
)
def test_country_code_normalises(value: str, expected: str) -> None:
    assert CountryCode(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "M",
        "MEX",
        # The two codes a ^[A-Z]{2}$ check would let through.
        "XX",
        "ZZ",
        # The Cyrillic letters that look exactly like CA and are not it.
        "\u0421\u0410",
    ],
)
def test_country_code_rejects(value: str) -> None:
    with pytest.raises(InvalidCountryCodeError):
        CountryCode(value)


def test_campaign_name_drops_the_invisible_characters() -> None:
    # U+202E is the Trojan Source right-to-left override; U+200B is a zero-width space.
    assert CampaignName("Black\u202eFriday\u200b MX") == "BlackFriday MX"


def test_campaign_name_keeps_the_letters_of_a_language_that_is_not_english() -> None:
    cyrillic = "\u0427\u0451\u0440\u043d\u0430\u044f mx"
    assert CampaignName(cyrillic) == cyrillic


@pytest.mark.parametrize("value", ["", "   ", "\u200b\u202e", "x" * (MAX_CAMPAIGN_NAME + 1)])
def test_campaign_name_rejects(value: str) -> None:
    with pytest.raises(InvalidCampaignNameError):
        CampaignName(value)


@pytest.mark.parametrize("value", ["adr", "black-friday-mx-7f3a", "a" * 64])
def test_campaign_alias_accepts(value: str) -> None:
    assert CampaignAlias(value) == value


@pytest.mark.parametrize("value", ["", "ab", "-black", "Black", "black_friday", "a" * 65])
def test_campaign_alias_rejects(value: str) -> None:
    with pytest.raises(InvalidCampaignAliasError):
        CampaignAlias(value)


def test_geo_carries_every_officially_assigned_code() -> None:
    assert len(COUNTRY_CODES) == 249
    assert {"MX", "RU", "US", "BR", "IN"} <= COUNTRY_CODES


def test_geo_codes_are_two_upper_case_ascii_letters_with_a_name_each() -> None:
    assert all(len(code) == 2 and code.isascii() and code.isupper() for code in COUNTRY_CODES)
    assert all(COUNTRY_NAMES[code].strip() for code in COUNTRY_CODES)


def test_geo_table_cannot_be_mutated_by_a_caller() -> None:
    with pytest.raises(TypeError):
        COUNTRY_NAMES["XX"] = "Nowhere"  # type: ignore[index]  # the point of the proxy
