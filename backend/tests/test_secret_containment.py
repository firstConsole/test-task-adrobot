"""PLAN-BACKEND §3.1 as a check rather than a claim: where the tracker key may appear.

The key is all-agent and can spend money, which makes "it reached a second module and
then a log line" the one security bug on this project that costs something real.

Written at 1.x, when `settings.py` was the only file that named the field. Since 4.3 both
allowances are used, and the assertions below are in both directions: a module outside the
list naming the key fails, and so does the list naming a module that has stopped. The
second half is what stops the rule quietly becoming vacuous — a refactor that moved the
header into a composition root would otherwise leave a green test guarding nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

SOURCE_ROOT: Final = Path(__file__).resolve().parents[1] / "src" / "adrobot"

FIELD: Final = "keitaro_api_key"

# An allowlist rather than §3.1's literal "no more than two files": a count of two is
# satisfied by settings.py plus api/deps.py while transport.py sits outside it, which is
# the exact shape the rule exists to prevent. A set also names the newcomer instead of
# printing a number.
MAY_NAME_THE_FIELD: Final = frozenset({"settings.py", "infrastructure/keitaro/transport.py"})

# The stronger half, and the one a reviewer actually cares about: naming the field is
# harmless, unwrapping it is what puts a key in a log line. `api/security.py` is here for
# the shared access token's `compare_digest` at 6.8 — a different secret, same rule.
MAY_UNWRAP_A_SECRET: Final = frozenset({"infrastructure/keitaro/transport.py", "api/security.py"})


def _modules_containing(needle: str) -> set[str]:
    return {
        module.relative_to(SOURCE_ROOT).as_posix()
        for module in sorted(SOURCE_ROOT.rglob("*.py"))
        if needle in module.read_text(encoding="utf-8")
    }


def test_the_scan_is_looking_at_the_source_tree() -> None:
    # Without this, every assertion below passes on an empty set — which is the exact
    # failure mode a test like this exists to prevent.
    assert SOURCE_ROOT.is_dir()
    assert {"settings.py", "logging.py", "main.py"} <= {
        module.name for module in SOURCE_ROOT.rglob("*.py")
    }


def test_only_the_allowed_modules_name_the_tracker_key() -> None:
    naming = _modules_containing(FIELD)

    assert "settings.py" in naming, (
        f"{FIELD} is declared nowhere under {SOURCE_ROOT}: the field was renamed and this "
        f"test quietly stopped checking anything."
    )
    assert naming <= MAY_NAME_THE_FIELD, (
        f"{FIELD} reached {sorted(naming - MAY_NAME_THE_FIELD)}. It is read in "
        f"transport.py and nowhere else; pass a built client, not the key."
    )


def test_the_module_that_is_supposed_to_read_the_key_still_does() -> None:
    # The other direction, and the one that keeps the rule from becoming vacuous: if the
    # key stopped being read where the allowlist says it is read, the allowlist is now
    # describing a module that does not exist and the real one is unlisted.
    reading = _modules_containing(FIELD) & _modules_containing("get_secret_value")

    assert "infrastructure/keitaro/transport.py" in reading, (
        "the tracker key is unwrapped in transport.py and this allowlist is what says so; "
        "if the header is now built somewhere else, that somewhere else is unguarded"
    )


def test_no_secret_is_unwrapped_outside_the_two_modules_that_may() -> None:
    # The scan is textual, so a *comment* spelling out either literal counts. That is
    # intended: a comment about the admin key in a third module is itself a sign it is
    # spreading. It is worth knowing before a mysterious red build.
    unwrapping = _modules_containing("get_secret_value")

    assert unwrapping <= MAY_UNWRAP_A_SECRET, (
        f"a secret is unwrapped in {sorted(unwrapping - MAY_UNWRAP_A_SECRET)}; §3.1 keeps "
        f"that to transport.py, and to api/security.py for the shared token."
    )
