"""What this service could not do, as exceptions, for everything that is not a domain rule.

`domain/errors.py` holds the rules — a share outside [0, 100], an offer twice in one flow.
This holds the operational failures: the tracker did not answer, refused our key, rejected
what we sent. The two families stay apart because they are answered differently. A domain
error is the caller's fault and is fixed by sending something else; an upstream error is
nobody's fault at the call site and is often fixed by waiting.

There is deliberately no shared `AdRobotError` root yet, although PLAN-BACKEND §8 sketches
one. A common base earns its keep when something renders both families — the problem+json
handlers of 6.7 — and designing that base three stages before its only consumer would be
guessing at the slug and status each error carries. Two handlers cost a line.

The names say `Upstream` and not `Keitaro`: this ring does not know which tracker it is
wrapping, and the vendor belongs in the message, where a person reads it.

The six at the foot are the persistence port's, and they are direct children of
`ApplicationError` rather than of a `NotFoundError` and a `ConflictError`: what they have in
common is the HTTP status 6.7 will render them as, which is a fact about the API and not
about the failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from adrobot.domain.diff import DesiredOffer
    from adrobot.domain.draft import DraftStatus
    from adrobot.domain.ids import CampaignId


class ApplicationError(Exception):
    """A use case could not be carried out. Never raised for a broken domain rule."""


class UpstreamError(ApplicationError):
    """The tracker was asked something and the answer cannot be used.

    `status` is what it answered, or `None` when it did not answer at all — which is a
    distinction worth keeping: 6.7 renders the first as the tracker's verdict and the
    second as this service's inability to reach it.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        fields: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Field name to the complaints about it, straight from the tracker. Empty for every
        # failure but a rejected payload, so a renderer can ask without checking the type.
        self.fields: Mapping[str, tuple[str, ...]] = dict(fields or {})


class UpstreamUnavailableError(UpstreamError):
    """The tracker did not answer, or answered that it was having trouble."""

    def __init__(self, summary: str, *, status: int | None = None) -> None:
        super().__init__(f"the tracker is not answering: {summary}", status=status)


class UpstreamDeniedError(UpstreamError):
    """The tracker refused this service's key, or the key's edition cannot use the API.

    Operationally the most useful of these: everything else is a bad minute, and this one
    will not clear up on its own.
    """

    def __init__(self, summary: str, *, status: int) -> None:
        super().__init__(
            f"the tracker refused this service's admin key, set in "
            f"ADROBOT_KEITARO_API_KEY: {summary}",
            status=status,
        )


class UpstreamNotFoundError(UpstreamError):
    """The tracker has no such campaign, flow or offer."""

    def __init__(self, summary: str, *, status: int) -> None:
        super().__init__(f"the tracker has no such thing: {summary}", status=status)


class UpstreamRejectedError(UpstreamError):
    """The tracker rejected what this service sent, and often said which field."""

    def __init__(
        self, summary: str, *, status: int, fields: Mapping[str, tuple[str, ...]] | None = None
    ) -> None:
        super().__init__(
            f"the tracker rejected this request: {summary}", status=status, fields=fields
        )


class UpstreamProtocolError(UpstreamError):
    """The tracker answered, in a shape or with a state this service cannot act on.

    Raised where believing the answer would be worse than failing: a redirect that would
    carry the admin key elsewhere, a flow whose `schema` is not one of the three, a write
    that reads back as something other than what was written.
    """

    def __init__(self, summary: str, *, status: int | None = None) -> None:
        super().__init__(
            f"the tracker answered in a way this service cannot use: {summary}", status=status
        )


class CampaignNotFoundError(ApplicationError):
    """No campaign carries that id. The id came from a URL, so this is a 404."""

    def __init__(self, campaign_id: object) -> None:
        super().__init__(f"no campaign {campaign_id}")


class CampaignAlreadyImportedError(ApplicationError):
    """That tracker campaign is already open here.

    A second local copy would give one flow two editors.

    `campaign_id` is the copy that already exists, and it is optional because the two places
    this is raised from know different things: the import scenario looked the row up and can
    name it, while the repository's own `ON CONFLICT DO NOTHING` learns only that somebody
    won the race. Where it is known it reaches the problem body, so the screen can offer the
    campaign instead of only refusing the request.
    """

    def __init__(
        self, keitaro_campaign_id: object, *, campaign_id: CampaignId | None = None
    ) -> None:
        super().__init__(f"campaign {keitaro_campaign_id} in the tracker is already imported")
        self.campaign_id = campaign_id


class StreamNotFoundError(ApplicationError):
    """This campaign has no such flow.

    Also the answer for another campaign's flow: the lookup is scoped, so it cannot tell the
    two apart — and should not.
    """

    def __init__(self, stream_id: object) -> None:
        super().__init__(f"this campaign has no flow {stream_id}")


class DraftAlreadyOpenError(ApplicationError):
    """A flow already has a live draft. Losing this race means re-reading and editing that one."""

    def __init__(self, stream_id: object) -> None:
        super().__init__(f"flow {stream_id} already has a draft being edited or pushed")


class DraftBeingPushedError(ApplicationError):
    """The flow's draft is in flight to the tracker, so it is nobody's to edit or cancel.

    A push is two short transactions with an HTTP call between them, and the draft is
    `pushing` for the whole of it. An edit landing in that window would be written into
    rows the second half is about to close, and a cancel would throw away the very state
    the tracker is being told to hold.
    """

    def __init__(self, stream_id: object) -> None:
        super().__init__(
            f"flow {stream_id} is being pushed to the tracker: wait for that to finish"
        )


class StreamDoesNotRotateOffersError(ApplicationError):
    """The flow dispatches clicks some other way, so it has no offer rotation to edit.

    Only a `landings` flow rotates offers. Flow 1 of every campaign this service builds is a
    `redirect`, and an `offers[]` array on one is ignored by Keitaro — so an editor that
    accepted the edit would show a share that no click will ever follow, and a push would
    rewrite a flow whose whole content is the redirect it is about to drop.
    """

    def __init__(self, stream_id: object, schema: object) -> None:
        super().__init__(
            f"flow {stream_id} is a {schema} flow: it rotates no offers, and Keitaro would "
            f"ignore any this service sent"
        )


class DraftStatusChangedError(ApplicationError):
    """The draft was not in the status the caller expected, so somebody else moved it first."""

    def __init__(self, expected: DraftStatus, found: DraftStatus) -> None:
        # `.value` on both: this message reaches a problem+json body, and `DraftStatus.PUSHING`
        # is Python's word for it rather than the API's.
        super().__init__(
            f"the draft is {found.value} and not {expected.value}: another request moved it"
        )


class DraftConflictError(ApplicationError):
    """The flow in Keitaro is not the flow this draft was opened on.

    Somebody edited it in the tracker meanwhile, so pushing would silently overwrite their
    work. The two states travel with the refusal — what Keitaro holds now, and what this
    push was about to write — because "there is a conflict" is not something anybody can
    act on and "these two rows differ" is.

    There is no rebase. Replaying the journal over somebody else's flow would produce a
    third state neither person asked for; the two honest answers are to overwrite
    deliberately or to throw the draft away, and both are buttons.
    """

    def __init__(
        self,
        stream_id: object,
        *,
        held: tuple[DesiredOffer, ...],
        wanted: tuple[DesiredOffer, ...],
    ) -> None:
        super().__init__(
            f"flow {stream_id} has been edited in Keitaro since this draft was opened: "
            f"pushing it now would overwrite those changes"
        )
        self.held = held
        self.wanted = wanted


class NothingToPushError(ApplicationError):
    """The flow already reads the way the draft wants it, or has no draft at all.

    One error for both because they are one answer: there is nothing here to write. The
    button is dark in either case, so reaching this means a client pressed it anyway.
    """

    def __init__(self, stream_id: object) -> None:
        super().__init__(
            f"flow {stream_id} has nothing to push: it already reads the way this draft wants it"
        )


class PushBlockedError(ApplicationError):
    """The draft would write a state this service will not ask the tracker to hold.

    Carries the sentence the editor already had on screen as `block_reason`, so the refusal
    and the dark button say the same thing in the same words rather than two services'
    worth of phrasing about one situation.
    """


class PushAttemptSettledError(ApplicationError):
    """This attempt is already closed — a phase 3 returning after somebody took the push over."""

    def __init__(self, attempt_id: object) -> None:
        super().__init__(f"push attempt {attempt_id} is already closed")


class CampaignNotRepairableError(ApplicationError):
    """The campaign's setup is unfinished and this service has no record of what it was.

    Only a campaign created here carries the country and the offer that part 1 was asked
    for, and only those two describe the flows that are missing. Rebuilding them from
    somebody else's half-built campaign would mean guessing what its author meant.
    """

    def __init__(self, campaign_id: object) -> None:
        super().__init__(
            f"campaign {campaign_id} was not created here, so there is nothing to finish: "
            f"its flows can be edited, but not built"
        )
