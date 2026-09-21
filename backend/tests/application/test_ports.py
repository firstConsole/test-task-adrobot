"""That every port is abstract in the way a second implementation will rely on.

A port whose method lost its `@abstractmethod` is the quiet kind of defect: the fake in
`tests/fakes.py` inherits a body that returns `None`, every scenario test against it passes,
and the shape is only wrong against the real tracker. The same goes for a method declared
`def` instead of `async def` — the caller awaits it and gets a `TypeError` in production
and nowhere else. Both are checked here by walking the classes rather than by listing
their methods, so a method added at stage 6 or 8 is covered on the day it is written.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest

from adrobot.application.ports.keitaro import KeitaroAdminPort, KeitaroReportsPort
from adrobot.application.ports.persistence import (
    CampaignRepository,
    DraftRepository,
    OfferCatalogueRepository,
    PinRepository,
    PushAttemptRepository,
    StreamRepository,
    Transaction,
    UnitOfWork,
)
from adrobot.application.ports.system import Clock

if TYPE_CHECKING:
    from abc import ABCMeta

# Every method is a call that goes somewhere — the network or the database — so every one
# of them is awaited.
AWAITABLE_PORTS = (
    KeitaroAdminPort,
    KeitaroReportsPort,
    CampaignRepository,
    StreamRepository,
    PinRepository,
    DraftRepository,
    PushAttemptRepository,
    OfferCatalogueRepository,
)

# `Clock` is a port and is held to everything but the rule above: it reads this machine's
# own clock, so an `async def now()` would only make `await` the price of asking the time.
# `UnitOfWork.begin` is synchronous for a different reason, which its own test states.
PORTS = (*AWAITABLE_PORTS, UnitOfWork, Clock)


def _declared(port: ABCMeta) -> dict[str, object]:
    return {
        name: member
        for name, member in vars(port).items()
        if not name.startswith("_") and callable(member)
    }


@pytest.mark.parametrize("port", PORTS)
def test_a_port_cannot_be_instantiated(port: ABCMeta) -> None:
    with pytest.raises(TypeError, match="abstract"):
        port()


@pytest.mark.parametrize("port", PORTS)
def test_the_port_declares_something_at_all(port: ABCMeta) -> None:
    # Without this the two tests below pass on an empty class, which is exactly what a
    # rename of the module would leave behind.
    assert _declared(port)


@pytest.mark.parametrize("port", PORTS)
def test_every_method_of_a_port_is_abstract(port: ABCMeta) -> None:
    concrete = set(_declared(port)) - port.__abstractmethods__

    assert not concrete, (
        f"{sorted(concrete)} would be inherited by every implementation, fakes included, "
        f"as a method that returns None"
    )


@pytest.mark.parametrize("port", AWAITABLE_PORTS)
def test_every_method_of_a_port_is_awaitable(port: ABCMeta) -> None:
    synchronous = [
        name for name, member in _declared(port).items() if not inspect.iscoroutinefunction(member)
    ]

    assert not synchronous, f"{synchronous} would be awaited by the caller and is not async"


def test_the_unit_of_work_hands_out_a_block_rather_than_a_coroutine() -> None:
    # `begin()` is the one synchronous method in either port module: it returns the context
    # manager, so a caller writes `async with uow.begin() as tx` and not
    # `async with await uow.begin()`.
    assert not inspect.iscoroutinefunction(UnitOfWork.begin)
    assert set(_declared(UnitOfWork)) == {"begin"}


def test_the_unit_of_work_offers_no_way_to_commit_or_roll_back_by_hand() -> None:
    # Leaving the block commits and raising rolls back. A commit() here is one a use case
    # could forget, and a rollback() is one it could call with the tracker half-written.
    assert not {"commit", "rollback", "flush"} & set(vars(UnitOfWork))


def test_a_transaction_is_the_only_place_a_repository_lives() -> None:
    # Not an ABC: nothing about the bundle varies per implementation, and the one method
    # somebody would add to an abstract one is the commit that must not be there.
    assert set(Transaction.__dataclass_fields__) == {
        "campaigns",
        "streams",
        "pins",
        "drafts",
        "pushes",
        "offers",
    }
    assert not [name for name in vars(Transaction) if name in {"commit", "rollback"}]
