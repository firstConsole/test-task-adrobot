"""The two families of application failure, and that they stay apart.

`errors.py` says the tracker's failures and our own are answered differently — 6.7 registers
one handler per family — so a persistence error that inherited `UpstreamError` would render
as the tracker's verdict on a request the tracker never saw.
"""

from __future__ import annotations

import pytest

from adrobot.application.errors import (
    ApplicationError,
    CampaignAlreadyImportedError,
    CampaignNotFoundError,
    DraftAlreadyOpenError,
    DraftStatusChangedError,
    PushAttemptSettledError,
    StreamNotFoundError,
    UpstreamError,
)
from adrobot.domain.draft import DraftStatus

# Each error beside the subjects its message has to name, because these reach a problem+json
# body and a message with no subject in it is a support ticket that begins "which one?".
OURS = (
    (CampaignNotFoundError("c7f1a3"), ("c7f1a3",)),
    (CampaignAlreadyImportedError(93212), ("93212",)),
    (StreamNotFoundError(564221), ("564221",)),
    (DraftAlreadyOpenError(564221), ("564221",)),
    (DraftStatusChangedError(DraftStatus.OPEN, DraftStatus.PUSHING), ("open", "pushing")),
    (PushAttemptSettledError("a41c"), ("a41c",)),
)


@pytest.mark.parametrize(("error", "subjects"), OURS, ids=lambda pair: type(pair).__name__)
def test_a_persistence_failure_is_never_reported_as_the_trackers(
    error: ApplicationError, subjects: tuple[str, ...]
) -> None:
    assert isinstance(error, ApplicationError)
    assert not isinstance(error, UpstreamError)
    assert subjects  # the pair is read by the sibling test; asserted here so neither drifts


@pytest.mark.parametrize(("error", "subjects"), OURS, ids=lambda pair: type(pair).__name__)
def test_every_message_names_what_it_is_about(
    error: ApplicationError, subjects: tuple[str, ...]
) -> None:
    message = str(error)
    assert message == message.strip()
    for subject in subjects:
        assert subject in message, message


def test_a_lost_status_race_says_both_states_in_the_api_s_own_words() -> None:
    # Which one it found is the whole information, and `DraftStatus.PUSHING` is Python's
    # word for it rather than the one the client switches on.
    message = str(DraftStatusChangedError(DraftStatus.OPEN, DraftStatus.PUSHING))
    assert "DraftStatus" not in message
    assert "the draft is pushing and not open" in message
