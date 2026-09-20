"""That the two ports are abstract in the way a second implementation will rely on.

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

if TYPE_CHECKING:
    from abc import ABCMeta

PORTS = (KeitaroAdminPort, KeitaroReportsPort)


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


@pytest.mark.parametrize("port", PORTS)
def test_every_method_of_a_port_is_awaitable(port: ABCMeta) -> None:
    synchronous = [
        name for name, member in _declared(port).items() if not inspect.iscoroutinefunction(member)
    ]

    assert not synchronous, f"{synchronous} would be awaited by the caller and is not async"
