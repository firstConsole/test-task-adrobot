"""Write the country list the campaign form's geo select is built from.

    cd backend && poetry run python scripts/dump_countries.py [path]

ISO 3166-1 is reference data, not an API: it changes about once a decade and never while a
browser is open. So the frontend ships the list instead of fetching it — the select opens
with no request, no spinner and no empty state, and stays usable while the tracker behind us
does not answer.

What that would normally cost is a second copy of the list, drifting away from the one
`CountryCode` validates against until the form offers a geo the API refuses. The copy is
therefore generated from `adrobot.domain.geo`, and CI runs this again with
`git diff --exit-code` — the same pair of moves that keeps the frontend's API types honest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from adrobot.domain.geo import COUNTRY_NAMES

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "frontend" / "src" / "shared" / "lib" / "countries.gen.ts"

HEADER = """\
/**
 * This file was generated from adrobot.domain.geo by backend/scripts/dump_countries.py.
 * Do not make direct changes to the file.
 */

export type Country = {
  /** ISO 3166-1 alpha-2, upper-case — exactly what the API takes as a campaign's geo. */
  code: string
  name: string
}

export const COUNTRIES: readonly Country[] = [
"""


def render() -> str:
    """Spell the mapping as a TypeScript array, ordered by code as the domain keeps it."""
    rows = "".join(
        f"  {{ code: {_string(code)}, name: {_string(name)} }},\n"
        for code, name in sorted(COUNTRY_NAMES.items())
    )
    return f"{HEADER}{rows}]\n"


def _string(value: str) -> str:
    """Quote a value as a TypeScript string. `ensure_ascii=False` keeps Åland as Åland."""
    return json.dumps(value, ensure_ascii=False)


def main(argv: Sequence[str]) -> int:
    """Render the list to `argv[0]`, or to the frontend's shared/lib."""
    Path(argv[0] if argv else DEFAULT_OUTPUT).write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
