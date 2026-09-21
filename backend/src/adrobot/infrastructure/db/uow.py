"""The unit of work: one session per caller, one transaction per block, and the only commit.

*   **One session for the life of the unit of work — one per request — and not one per
    block.** It is the premise `repositories._FRESH` is written on: a push runs two
    transactions on one session with the tracker call between them, and `populate_existing=True`
    on every read is what stops the third phase being answered from the first's identity map.
    Carrying the session across that call costs the pool nothing: measured,
    `pool.checkedout()` is 1 inside a block and 0 between two, with the backend `idle` and out
    of its transaction, and an empty block opens no connection at all.
*   **`begin()` is `session.begin()`**, and neither of the two candidates beside it. Measured,
    `begin_nested()` answers a failed block by releasing a savepoint and lets the outer
    transaction commit anyway — a failed phase swallowed — and a hand-written
    `try: yield; await commit() except Exception: await rollback()` does not see a cancelled
    request: a `CancelledError` mid-block left the session in a transaction, one pool
    connection checked out and a backend `idle in transaction`, where `session.begin()` left
    nothing behind.
*   **A block opened inside a block is refused by SQLAlchemy and not by a guard of ours.**
    Measured, `InvalidRequestError: A transaction is already begun on this Session.`, one
    frame from the line that did it; caught, the outer transaction survives, and uncaught it
    rolls back, which is what a programming error deserves. A refusal of our own would restate
    that in our words and add a second mechanism for something already enforced.

The session is closed by `unit_of_work()`, its owner, and by nothing else.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

from adrobot.application.ports.persistence import UnitOfWork
from adrobot.infrastructure.db.repositories import repositories

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from adrobot.application.ports.persistence import Transaction


class SqlAlchemyUnitOfWork(UnitOfWork):
    """The port over one `AsyncSession`, whose work it owns and whose lifetime it does not.

    Constructed from a session rather than from a sessionmaker so that the `db` fixtures can
    hand in one joined to a transaction they roll back after the test: the code under test
    then commits exactly as it does in production and still leaves nothing behind.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    def begin(self) -> AbstractAsyncContextManager[Transaction]:
        """Open one short transaction; leaving the block commits and raising rolls it back.

        A plain `def` returning the context manager below, rather than
        `@asynccontextmanager` on this method. Both satisfy mypy strict and the ABC, but
        measured, contextlib's `@wraps` makes `inspect.signature` render
        `(self) -> 'AsyncIterator[Transaction]'` where the port renders
        `(self) -> 'AbstractAsyncContextManager[Transaction]'` — and comparing exactly those
        two strings is what `test_no_implementation_drifts_from_the_signature_it_promised`
        already does to every port implementation in this project.
        """
        return self._block()

    @asynccontextmanager
    async def _block(self) -> AsyncIterator[Transaction]:
        """Run the body inside `session.begin()`, bundling this transaction's repositories.

        The bundle is built per block and never kept on `self`: it is the six repositories of
        *this* transaction, so one built in `__init__` would be a session-lifetime object
        whose whole meaning is transaction-lifetime, and it would hand a caller something to
        store. Measured, the six cost microseconds to bundle.
        """
        async with self._session.begin():
            yield repositories(self._session)


@asynccontextmanager
async def unit_of_work(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[UnitOfWork]:
    """Open one session for the life of a caller's block — a request — and close it after.

    `autobegin=False` is what makes "the object a block yields dies with the block" a fact
    rather than advice. The repositories hold the session, so a `Transaction` kept past its
    block is a live object; measured, with the default it silently opens a second transaction,
    holds a pool connection until the session closes and **loses its writes** when it does.
    With the flag the same call raises `Autobegin is disabled on this Session; please call
    session.begin() to start a new transaction`.

    Asked for here rather than in `build_sessionmaker` because it is this object's invariant
    and not the session's — only a unit of work may start a transaction — so it holds for any
    sessionmaker a caller hands in, the fixtures' included.
    """
    async with sessions(autobegin=False) as session:
        yield SqlAlchemyUnitOfWork(session)
