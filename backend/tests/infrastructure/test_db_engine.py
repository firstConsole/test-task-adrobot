"""The engine and the unit of work, as far as they can be checked without a database.

What is asserted here is the shape: that nothing is built at import time, that the two
modules' numbers agree with each other and with the tracker's client, and that the unit of
work is a valid implementation of its port. The behaviour — the connection held inside a
block and released between two — needs PostgreSQL and is asserted at 5.10.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from adrobot.application.ports.persistence import UnitOfWork
from adrobot.infrastructure.db import engine as engine_module
from adrobot.infrastructure.db import uow as uow_module
from adrobot.infrastructure.keitaro import transport

if TYPE_CHECKING:
    from types import ModuleType

    from adrobot.settings import Settings

BUILDERS = frozenset({"create_async_engine", "async_sessionmaker", "build_engine"})


@pytest.mark.parametrize("module", [engine_module, uow_module], ids=lambda m: m.__name__)
def test_nothing_is_built_at_import_time(module: ModuleType) -> None:
    # A pooled asyncpg connection belongs to the event loop that opened it, so a
    # module-level engine hands a second loop the first loop's connection.
    tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
    built = [
        call.func.id
        for statement in tree.body
        if isinstance(statement, ast.Assign | ast.AnnAssign)
        for call in ast.walk(statement)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in BUILDERS
    ]
    assert not built, built


def test_the_unit_of_work_implements_its_port_without_drift() -> None:
    assert not inspect.isabstract(uow_module.SqlAlchemyUnitOfWork)
    # The plain `def` returning a private context manager is what keeps these two equal:
    # contextlib's @wraps would render the return type here as AsyncIterator.
    assert str(inspect.signature(UnitOfWork.begin)) == str(
        inspect.signature(uow_module.SqlAlchemyUnitOfWork.begin)
    )
    assert not inspect.iscoroutinefunction(uow_module.SqlAlchemyUnitOfWork.begin)


async def test_a_session_keeps_what_a_push_reads_after_the_tracker_call(
    settings: Settings,
) -> None:
    built = engine_module.build_engine(settings)
    try:
        sessions = engine_module.build_sessionmaker(built)
    finally:
        # Never connected — create_async_engine is lazy — but an engine merely dropped is a
        # SAWarning under filterwarnings = ["error"], in whichever test the collector fires.
        await built.dispose()
    # expire_on_commit=False and repositories._FRESH are one decision: without the first,
    # phase 3 emits SQL outside any transaction; without the second it reads phase 1's rows.
    assert sessions.kw["expire_on_commit"] is False
    # Nothing in repositories.py calls session.add, so there is nothing to flush; this is the
    # guard for the day somebody does.
    assert sessions.kw["autoflush"] is False
    # What lets the 5.10 fixtures bind a session to an open transaction and still roll back:
    # the unit of work's COMMIT becomes a RELEASE SAVEPOINT there.
    assert sessions.kw["join_transaction_mode"] == "create_savepoint"


def test_a_waiter_for_a_row_gives_up_before_a_waiter_for_a_connection() -> None:
    # Otherwise one wedged FOR UPDATE becomes pool timeouts on unrelated requests before it
    # becomes the error that names the real cause.
    assert float(engine_module.LOCK_TIMEOUT.removesuffix("s")) < engine_module.POOL_TIMEOUT_SECONDS


def test_the_database_and_the_tracker_give_up_on_a_connection_at_the_same_moment() -> None:
    # One number for two resources: an operator reading a start-up failure should not have to
    # learn two budgets.
    assert transport.KEITARO_TIMEOUT.connect == engine_module.CONNECT_TIMEOUT_SECONDS


def test_the_pool_stays_a_small_fraction_of_the_cluster() -> None:
    # The compose cluster allows 100 connections, three of them reserved. The push holds none
    # during the tracker call, so the pool bounds work in flight and not work waiting.
    assert engine_module.POOL_SIZE + engine_module.MAX_OVERFLOW <= 20
