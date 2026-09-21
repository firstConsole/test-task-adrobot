"""That a use case's answer cannot be edited by whoever receives it.

Both checks walk the module rather than listing its types, so the draft commands of stage 7
are held to the same two rules on the day they are written.

The rules matter for one reason each. A result that is not frozen can be adjusted in a
router — a share rounded, a link rewritten — and the adjustment would be invisible in the
use case that is supposed to own the answer. And a frozen dataclass holding a `list` is not
frozen at all: the field cannot be rebound, while `result.campaigns.append(...)` works, so
every collection here is a tuple.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from adrobot.application import dto

DTOS = [
    member
    for member in vars(dto).values()
    if dataclasses.is_dataclass(member)
    and isinstance(member, type)
    and member.__module__ == dto.__name__
]

MUTABLE_CONTAINERS = ("list[", "dict[", "set[", "List[", "Dict[", "Set[")


def test_the_module_declares_something() -> None:
    # The two walks below pass vacuously on an empty list, and an import that stopped
    # resolving would look exactly like a module with nothing in it.
    assert DTOS


@pytest.mark.parametrize("cls", DTOS, ids=lambda cls: str(cls.__name__))
def test_a_dto_cannot_be_changed_or_extended(cls: type[Any]) -> None:
    assert cls.__dataclass_params__.frozen
    assert cls.__dataclass_params__.kw_only
    # Frozen is about rebinding a field; slots are what stop a caller answering a question
    # the use case did not answer by writing an attribute nobody declared.
    assert "__slots__" in vars(cls)


@pytest.mark.parametrize("cls", DTOS, ids=lambda cls: str(cls.__name__))
def test_a_dto_holds_no_mutable_container(cls: type[Any]) -> None:
    # `from __future__ import annotations` leaves every annotation a string, which is what
    # this reads: resolving them would only turn the same text into the same names.
    declared = {field.name: str(field.type) for field in dataclasses.fields(cls)}
    assert not [
        name for name, annotation in declared.items() if annotation.startswith(MUTABLE_CONTAINERS)
    ], declared
