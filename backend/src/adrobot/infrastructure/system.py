"""The two ports that are one line of the standard library each.

They are behind ports for what they do to a test, not for what they do here: a scenario
that cannot say what time it is has to sleep, and one that cannot predict an alias has to
read it out of the answer it is checking.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Final, override

from adrobot.application.ports.system import AliasFactory, Clock
from adrobot.domain.values import CampaignAlias

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
