"""The clock and the alias generator: two lines of standard library, one of which has a trap.

The trap is the alias. PLAN-00 §11 arrived at a fixed length over a filtered
`token_urlsafe(8)` because the filtered one is shorter than six characters about a third of
the time — and the first campaign whose alias is too short is a 500 on the demo's first
button, from a line nobody would look at twice.
"""

from __future__ import annotations

from datetime import UTC, datetime

from adrobot.domain.values import CampaignAlias
from adrobot.infrastructure.system import (
    ALIAS_ALPHABET,
    ALIAS_LENGTH,
    SecretsAliasFactory,
    SystemClock,
)

DRAWS = 500


def test_the_clock_answers_in_utc_and_says_so() -> None:
    now = SystemClock().now()

    # Aware, because every timestamp column of this service is `timestamptz`: a naive value
    # is stored as though the process ran in UTC, which it does until it does not.
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(datetime.now(UTC))


def test_every_alias_is_one_the_domain_would_accept() -> None:
    aliases = [SecretsAliasFactory().new() for _ in range(DRAWS)]

    assert all(isinstance(alias, CampaignAlias) for alias in aliases)
    assert {len(alias) for alias in aliases} == {ALIAS_LENGTH}
    assert set("".join(aliases)) <= set(ALIAS_ALPHABET)


def test_the_alphabet_leaves_out_the_characters_that_are_read_back_as_each_other() -> None:
    assert not set("lo01") & set(ALIAS_ALPHABET)


def test_two_aliases_in_a_row_are_not_the_same_alias() -> None:
    # 40 bits a draw: a collision inside one run is not a flake worth tolerating, it is
    # evidence that something is seeding this.
    factory = SecretsAliasFactory()

    assert len({factory.new() for _ in range(DRAWS)}) == DRAWS
