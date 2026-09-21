"""The three ports that are a line or two of the standard library each.

They are behind ports for what they do to a test, not for what they do here: a scenario
that cannot say what time it is has to sleep, one that cannot predict an alias has to read
it out of the answer it is checking, and one that cannot predict a correlation id cannot
assert that the push wrote the right audit row.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Final, override

from adrobot.application.ports.system import AliasFactory, Clock, CorrelationIds
from adrobot.domain.values import CampaignAlias
from adrobot.logging import correlation_id, new_correlation_id

ALIAS_ALPHABET: Final = "abcdefghijkmnpqrstuvwxyz23456789"
"""Lower case and digits, less `l`, `o`, `0` and `1`. An alias is read off a screen and typed
into a chat, and those four are the pairs that get read back as each other."""

ALIAS_LENGTH: Final = 8
"""Fixed, and that is the point: filtering something like `token_urlsafe(8)` down to this
alphabet leaves fewer than six characters about a third of the time, which is a 500 on the
very first button of the demo. Eight draws from 32 characters is 40 bits — enough that the
tracker's own uniqueness check is a formality rather than a retry loop this service would
have to grow."""


class SystemClock(Clock):
    """The wall clock of this machine, in UTC."""

    @override
    def now(self) -> datetime:
        return datetime.now(UTC)


class SecretsAliasFactory(AliasFactory):
    """Aliases drawn from `secrets`, which is the right default for a value that reaches a URL.

    Not because the alias is a credential — a campaign link is meant to be handed out — but
    because it is the whole of what makes one campaign's link different from another's, and
    a generator somebody could step through would let a competitor walk a tracker's
    campaigns by asking for the next one.
    """

    @override
    def new(self) -> CampaignAlias:
        return CampaignAlias("".join(secrets.choice(ALIAS_ALPHABET) for _ in range(ALIAS_LENGTH)))


class ContextCorrelationIds(CorrelationIds):
    """The id the HTTP middleware bound for this request, or a fresh one outside a request."""

    @override
    def current(self) -> str:
        bound = correlation_id()
        # A CLI push and a scheduled sync are work worth correlating, and neither has a
        # request that bound anything. Minting one here keeps the audit row joinable to
        # whatever that process logged rather than leaving it NULL.
        return bound if bound is not None else new_correlation_id()
