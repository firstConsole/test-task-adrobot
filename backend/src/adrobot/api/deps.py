"""The dependencies a route declares, as `Annotated` aliases and nothing else.

A route asks for `UowDep` and is handed one request's unit of work, opened before the
handler runs and closed after it returns — including when it raises, which is what makes
"a use case never has to close anything" true rather than aspirational.

The aliases are the module's whole public surface, and the ruff configuration exempts this
file from the type-checking rules on that basis: an `Annotated[...]` alias is resolved at
runtime by FastAPI, so an import moved into `if TYPE_CHECKING:` here would not fail — it
would silently turn a dependency into a query parameter and answer 422 with nothing wired.

The providers below are private because they are the aliases' implementation. Nothing else
may call them: a handler that called `_ports(request)` itself would be reaching around the
dependency that is supposed to own the lifetime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, cast

from fastapi import Depends, Request

from adrobot.application.ports.persistence import UnitOfWork
from adrobot.application.use_cases.create_campaign import CreateCampaign, RepairCampaign
from adrobot.application.use_cases.edit_draft import DiscardDraft, EditDraft
from adrobot.application.use_cases.editor import GetEditorView, GetStreamView
from adrobot.application.use_cases.list_campaigns import ListCampaigns
from adrobot.application.use_cases.mirror_campaign import ImportCampaign, SyncCampaign
from adrobot.application.use_cases.offer_catalogue import SearchOffers, SyncOfferCatalogue
from adrobot.application.use_cases.pin_offer import ReleaseOfferPin, SetOfferPin
from adrobot.application.use_cases.push_draft import PushDraft
from adrobot.composition import AppPorts
from adrobot.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _held(request: Request, name: str) -> object:
    """Read one thing the lifespan put on the application, or say that it never ran.

    `app.state` and not the mapping a lifespan may yield: that mapping is copied into each
    request scope by the *server*, and the suite drives the application through
    `httpx.ASGITransport`, which is not one. `scope["app"]` is set by Starlette itself, so
    this reads the same object under uvicorn and under a test.
    """
    held = getattr(request.app.state, name, None)
    if held is None:
        message = (
            f"the application has no {name}: it was built without running its lifespan, "
            f"which is what composes the ports"
        )
        raise RuntimeError(message)
    return held


def _ports(request: Request) -> AppPorts:
    return cast("AppPorts", _held(request, "ports"))


def _settings(request: Request) -> Settings:
    return cast("Settings", _held(request, "settings"))


async def _unit_of_work(ports: Annotated[AppPorts, Depends(_ports)]) -> AsyncIterator[UnitOfWork]:
    """Open one unit of work for this request and close it when the response is finished.

    A generator dependency rather than a use case opening its own: the session is a resource
    of the request, and FastAPI is the only thing that knows when a request is over.
    """
    async with ports.unit_of_work() as unit:
        yield unit


def _create_campaign(
    ports: Annotated[AppPorts, Depends(_ports)],
    uow: Annotated[UnitOfWork, Depends(_unit_of_work)],
) -> CreateCampaign:
    """Assemble part 1 for this request: process-wide ports, this request's transaction."""
    return CreateCampaign(
        admin=ports.admin,
        uow=uow,
        references=ports.references,
        aliases=ports.aliases,
        clock=ports.clock,
    )


def _repair_campaign(
    ports: Annotated[AppPorts, Depends(_ports)],
    uow: Annotated[UnitOfWork, Depends(_unit_of_work)],
) -> RepairCampaign:
    return RepairCampaign(admin=ports.admin, uow=uow, clock=ports.clock)


def _import_campaign(
    ports: Annotated[AppPorts, Depends(_ports)],
    uow: Annotated[UnitOfWork, Depends(_unit_of_work)],
) -> ImportCampaign:
    return ImportCampaign(admin=ports.admin, uow=uow, clock=ports.clock)


def _sync_campaign(
    ports: Annotated[AppPorts, Depends(_ports)],
    uow: Annotated[UnitOfWork, Depends(_unit_of_work)],
) -> SyncCampaign:
    return SyncCampaign(admin=ports.admin, uow=uow, clock=ports.clock)


def _list_campaigns(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> ListCampaigns:
    return ListCampaigns(uow=uow)


# The editor. Every one of these but the push holds a unit of work and nothing else: staging
# an edit, pinning a row and cancelling never reach the tracker, which is what makes them
# answer in one short transaction and no network call.


def _editor_view(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> GetEditorView:
    return GetEditorView(uow=uow)


def _stream_view(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> GetStreamView:
    return GetStreamView(uow=uow)


def _edit_draft(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> EditDraft:
    return EditDraft(uow=uow)


def _discard_draft(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> DiscardDraft:
    return DiscardDraft(uow=uow)


def _set_offer_pin(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> SetOfferPin:
    return SetOfferPin(uow=uow)


def _release_offer_pin(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> ReleaseOfferPin:
    return ReleaseOfferPin(uow=uow)


def _push_draft(
    ports: Annotated[AppPorts, Depends(_ports)],
    uow: Annotated[UnitOfWork, Depends(_unit_of_work)],
) -> PushDraft:
    return PushDraft(admin=ports.admin, uow=uow, clock=ports.clock, correlation=ports.correlation)


def _search_offers(uow: Annotated[UnitOfWork, Depends(_unit_of_work)]) -> SearchOffers:
    return SearchOffers(uow=uow)


def _sync_offer_catalogue(
    ports: Annotated[AppPorts, Depends(_ports)],
    uow: Annotated[UnitOfWork, Depends(_unit_of_work)],
) -> SyncOfferCatalogue:
    return SyncOfferCatalogue(admin=ports.admin, uow=uow, clock=ports.clock)


PortsDep = Annotated[AppPorts, Depends(_ports)]
SettingsDep = Annotated[Settings, Depends(_settings)]
# `UnitOfWork` is imported above and not under `TYPE_CHECKING`, which is the whole reason
# this module is exempt from the type-checking rules: FastAPI resolves a handler's
# annotations against the *handler's* module, where a name this file kept to itself does not
# exist — and the failure is a NameError at import time if the alias is a forward reference,
# or a silent demotion to a query parameter if it is not.
UowDep = Annotated[UnitOfWork, Depends(_unit_of_work)]

# One per scenario, so that a handler names what it does and nothing else. Each is built per
# request because each holds that request's unit of work; the ports inside them are the
# process's own and are not rebuilt.
CreateCampaignDep = Annotated[CreateCampaign, Depends(_create_campaign)]
RepairCampaignDep = Annotated[RepairCampaign, Depends(_repair_campaign)]
ImportCampaignDep = Annotated[ImportCampaign, Depends(_import_campaign)]
SyncCampaignDep = Annotated[SyncCampaign, Depends(_sync_campaign)]
ListCampaignsDep = Annotated[ListCampaigns, Depends(_list_campaigns)]

GetEditorViewDep = Annotated[GetEditorView, Depends(_editor_view)]
GetStreamViewDep = Annotated[GetStreamView, Depends(_stream_view)]
EditDraftDep = Annotated[EditDraft, Depends(_edit_draft)]
DiscardDraftDep = Annotated[DiscardDraft, Depends(_discard_draft)]
SetOfferPinDep = Annotated[SetOfferPin, Depends(_set_offer_pin)]
ReleaseOfferPinDep = Annotated[ReleaseOfferPin, Depends(_release_offer_pin)]
PushDraftDep = Annotated[PushDraft, Depends(_push_draft)]
SearchOffersDep = Annotated[SearchOffers, Depends(_search_offers)]
SyncOfferCatalogueDep = Annotated[SyncOfferCatalogue, Depends(_sync_offer_catalogue)]
