<%!
# Mako comments never reach the generated file, so this is where the template says why it
# diverges from `alembic init -t async`. Everything below exists because a generated file
# that has to be hand-fixed before it can be committed is a template that is wrong.
#
#   * the stock template omits `from __future__ import annotations` (I002), sorts
#     `import sqlalchemy` after `from alembic import op` (I001), and types the four
#     revision identifiers with `typing.Union`/`typing.Sequence` (UP007, UP035);
#   * `repr()` renders a revision id in single quotes, which `ruff format` rewrites;
#   * a stock `Revises:` line with no parent ends in a space (W291);
#   * `pass` beneath a docstring is PIE790 — a docstring is already a body.
#
# `message` goes through `sentence()` so the summary ends in a period: pydocstyle's
# D400/D415 run on migrations too, and the alternative is remembering to type a period
# into every `-m` argument.
#
# `import sqlalchemy as sa` and `from alembic import op` are emitted unconditionally, so a
# revision with an empty body fails F401 until it has one. That is deliberate: PLAN-01 §4
# requires every migration to be read before it is committed, and an empty migration is
# not something to commit.

def sentence(text):
    stripped = " ".join(text.split())
    return stripped if stripped.endswith((".", "!", "?")) else stripped + "."


def literal(value):
    """Render a revision identifier the way `ruff format` would have written it."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return '"%s"' % value
    return repr(tuple(value))
%>"""${sentence(message)}

Revision ID: ${up_revision}
% if down_revision:
Revises: ${down_revision | comma,n}
% endif
Create Date: ${create_date}
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
revision: str = ${literal(up_revision)}
down_revision: str | None = ${literal(down_revision)}
branch_labels: str | tuple[str, ...] | None = ${literal(branch_labels)}
depends_on: str | tuple[str, ...] | None = ${literal(depends_on)}


def upgrade() -> None:
    """Apply this revision."""
% if upgrades:
    ${upgrades}
% endif


def downgrade() -> None:
    """Revert this revision."""
% if downgrades:
    ${downgrades}
% endif
