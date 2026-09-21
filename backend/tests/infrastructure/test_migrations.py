"""The revision chain itself, read off the files. Needs no PostgreSQL.

Two migrations sharing a `down_revision` make a branch, and alembic then refuses to upgrade
with "Multiple head revisions are present" — at deploy time, on somebody else's machine.
"""

from __future__ import annotations

import inspect

from alembic.config import Config
from alembic.script import ScriptDirectory

EXPECTED = ("0001", "0002", "0003", "0004")


def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_the_revisions_are_one_linear_chain() -> None:
    directory = scripts()
    assert directory.get_heads() == [EXPECTED[-1]]
    walked = tuple(reversed([revision.revision for revision in directory.walk_revisions()]))
    assert walked == EXPECTED


def test_every_revision_undoes_something() -> None:
    # A downgrade nobody wrote is discovered by the person who needs it most, and an empty
    # one passes `alembic downgrade base` while leaving the schema behind.
    for revision in scripts().walk_revisions():
        downgrade = revision.module.downgrade
        assert "op." in inspect.getsource(downgrade), revision.revision
