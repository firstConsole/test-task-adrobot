"""What the repositories promise structurally, asserted without a database.

The behaviour — the statement counts, the locks, the tombstone sweeps — needs PostgreSQL and
is asserted at 5.10 with the `db`-marked session fixture. What is here is the class of defect
a running test would not catch anyway: a port method that grew an implementation with the
wrong signature, a commit that crept in, or an index predicate that drifted from the
enumeration it was spelled from.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from adrobot.application.ports import persistence as ports
from adrobot.domain.draft import LIVE_DRAFT_STATUSES
from adrobot.infrastructure.db import repositories

if TYPE_CHECKING:
    from abc import ABCMeta

PAIRS = (
    (ports.CampaignRepository, repositories.SqlCampaignRepository),
    (ports.StreamRepository, repositories.SqlStreamRepository),
    (ports.PinRepository, repositories.SqlPinRepository),
    (ports.DraftRepository, repositories.SqlDraftRepository),
    (ports.PushAttemptRepository, repositories.SqlPushAttemptRepository),
    (ports.OfferCatalogueRepository, repositories.SqlOfferCatalogueRepository),
)


def source() -> ast.Module:
    return ast.parse(Path(repositories.__file__).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("port", "concrete"), PAIRS, ids=lambda pair: getattr(pair, "__name__", "")
)
def test_every_port_method_has_an_implementation(port: ABCMeta, concrete: type) -> None:
    # A port method added at stage 7 and left unimplemented is an abstract class the
    # composition root cannot build, which fails at start-up and never in a unit test.
    assert not inspect.isabstract(concrete)
    assert port.__abstractmethods__ <= set(dir(concrete))


@pytest.mark.parametrize(
    ("port", "concrete"), PAIRS, ids=lambda pair: getattr(pair, "__name__", "")
)
def test_no_implementation_drifts_from_the_signature_it_promised(
    port: ABCMeta, concrete: type
) -> None:
    drift = {
        name: (
            str(inspect.signature(getattr(port, name))),
            str(inspect.signature(getattr(concrete, name))),
        )
        for name in port.__abstractmethods__
        if str(inspect.signature(getattr(port, name)))
        != str(inspect.signature(getattr(concrete, name)))
    }
    assert not drift, drift


def test_a_transaction_holds_one_session_s_six_repositories() -> None:
    bundled = repositories.repositories(session=None)  # type: ignore[arg-type]  # nothing is called
    assert isinstance(bundled, ports.Transaction)
    assert all(
        isinstance(getattr(bundled, field), port)
        for field, (port, _) in zip(
            ("campaigns", "streams", "pins", "drafts", "pushes", "offers"), PAIRS, strict=True
        )
    )


def test_no_repository_commits_or_opens_a_savepoint() -> None:
    # The unit of work commits. A commit here would end a transaction the caller is still
    # building, and a savepoint would hide an integrity error the caller must see.
    forbidden = {"commit", "rollback", "begin", "begin_nested", "close"}
    called = {
        node.func.attr
        for node in ast.walk(source())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden
    }
    assert not called, called


def test_the_live_draft_predicate_is_spelled_from_the_domain_s_own_list() -> None:
    # It is the arbiter of an ON CONFLICT clause and has to name exactly what the partial
    # index names; a parameterised predicate is inferred from only under a custom plan.
    rendered = str(repositories._LIVE_DRAFT)
    for status in LIVE_DRAFT_STATUSES:
        assert f"'{status.value}'" in rendered, status
    assert rendered.count("'") == 2 * len(LIVE_DRAFT_STATUSES)


def test_every_read_overwrites_what_the_session_already_holds() -> None:
    # A push runs two transactions on one session with an HTTP call between them, and
    # expire_on_commit=False means the second block is otherwise answered from the first.
    assert repositories._FRESH == {"populate_existing": True}
    text = Path(repositories.__file__).read_text(encoding="utf-8")
    assert text.count("_FRESH") >= 2
