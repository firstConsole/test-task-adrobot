# Project rules

Rules for anyone — human or model — writing code in this repository. Every one of them
is here because breaking it produces a defect that is expensive to find later, and most
of them name the thing that enforces them. A rule with no enforcement is marked as such.

This file is self-contained on purpose: the design documents it was distilled from are
not part of the repository.

## Running anything

**Every Python tool runs with `cwd = backend/`.** ruff resolves `src`, mypy resolves
`files` and `mypy_path`, and coverage finds its configuration relative to the *process
working directory*, not to the file they are configured in. The `Makefile` targets encode
this; run `make help` for the list.

`poetry build` must also run from `backend/` — `poetry -C backend build` resolves
`packages = [{include = "adrobot", from = "src"}]` against the caller's directory and
looks for `backend/src/src/adrobot`. `lock`, `check`, `install` and `sync` are fine with
`-C`.

Configuration is read from the environment only. There is no `env_file` in `Settings`:
the repository keeps `.env` at its root while every backend tool runs from `backend/`, so
no relative path would be right from both. To run the service outside compose:
`set -a; . .env; set +a`.

## Architecture

Three rings. `domain` knows nothing; `application` knows `domain`; `infrastructure` and
`api` implement and drive them.

* **`domain/` imports nothing but the standard library.** Enforced by
  `backend/.importlinter`, whose first contract forbids everything and grants a named list
  of standard-library modules — so a dependency added in a later stage is caught on the
  day it reaches `domain/`, with no list of forbidden names to keep up to date.
* **`application/` imports neither `infrastructure` nor `api`.** Enforced by the second
  contract, including indirect imports: a use case that reaches `composition.py` reaches
  infrastructure through it, and that breaks the contract too.
* **The Keitaro wire models are imported only by `mapping.py`.** Enforced by the third
  contract. The `Kt*` models are the wire format, not a domain type; convert in
  `mapping.py` and pass the domain object on.
* **`src/adrobot/__init__.py` stays empty.** Not enforced by anything, and it has to be
  discipline: `import adrobot.domain.shares` executes the package `__init__` first, so a
  re-export there would drag third-party imports into the domain ring at runtime while
  the contract — which only looks at direct edges — stays green.
* Two boundaries the contracts do **not** cover: nothing stops `api/` importing
  `infrastructure` directly, and nothing keeps SQLAlchemy or FastAPI out of
  `application/` (an `AsyncSession` in a port signature touches no first-party module, so
  contract two cannot see it). Both are discipline until someone adds a fourth contract.

## Share arithmetic

This is what the project is judged on.

* **No share is computed outside `domain/shares.py`.** Not in a router, not in a use case,
  not on the frontend. The API deliberately exposes no endpoint that accepts rows with
  shares already filled in; the only way a client influences a share is by pinning one,
  and the pinned value is validated server-side.
* **Integers and `divmod` only.** No `round()`, no floats — they produce 99%.
* **The rounding remainder goes to the most recently activated row**, where "activated"
  means the last add or the last `BRING BACK`, and for rows read from the tracker, the
  most recent `created_at`. This rule was chosen by testing three candidates against four
  states taken from the reference tool; the other two disagree with it.
* **A pin neither dirties the draft nor triggers a recalculation.** This looks like a bug
  and is not; it is what the reference tool does.
* **State read from Keitaro is not normalised.** The shares of a clean stream can sum to
  50%. Normalising it would be inventing data.
* Tests assert a **dict** of offer id to share, never a tuple: the multiset of values
  matches under the wrong tie-break rule too.

## Secrets

Two assets are protected: the Keitaro Admin API key, and the ability to spend somebody's
advertising budget.

* **Never bind `.get_secret_value()` to a local variable.** pytest runs with
  `--showlocals`, and `SecretStr`'s repr is the only thing keeping the key out of a
  failure report.
* The key is unwrapped in exactly one module. `backend/tests/test_secret_containment.py`
  greps the sources against an allowlist of modules that may name `keitaro_api_key` at
  all; naming it anywhere else fails the build. The scan is textual, so a *comment*
  mentioning it in a module outside the list turns the build red too. That is intended —
  widen the allowlist deliberately, in the commit that needs it.
* **A field and its `.env.example` line move in the same commit.** The unknown-variable
  guard can only say "this prefixed name is a typo" while the two lists agree;
  `test_env_example_declares_exactly_the_model_fields` holds them together.
* **A raw tracker response reaches neither a terminal nor a tracked file.**
  `scripts/kt_probe.py` writes them to `/.scratch/`, which `.gitignore` covers, and
  prints through `redact`. It is also the one file outside `src/adrobot/` that unwraps
  the admin key: the containment test scans the package, and a probe has to
  authenticate somehow.
* Never log a Keitaro response body on 2xx. On a non-2xx, pass it through
  `adrobot.logging.redact` first — Keitaro returns a campaign's Click API token inside the
  campaign object.

## Database

* **A database transaction is never open across an HTTP call.** Read, close, call the
  tracker, reopen.
* **Every ORM relationship is `lazy="raise_on_sql"`.** Lazy loading turns an N+1 into a
  failing test instead of a slow endpoint.
* **Repositories never commit.** The unit of work commits.
* **Every generated migration is read before it is committed.** `alembic revision
  --autogenerate` proposes, it does not decide; `make revision REV=0002 M="..."` numbers
  it. `alembic upgrade head --sql` prints the SQL without running it.
* The mirror of somebody else's data is tolerant — `text`, `text[]`, no CHECK. Our own
  tables are strict. There is no foreign key from `stream_offers.offer_id` to `offers.id`:
  an offer can appear in a stream before it appears in our catalogue.
* There is no CHECK asserting that shares sum to 100. It is false for the mirror and false
  for a draft in which every row is pinned.

## HTTP layer

* **Routers contain no `try/except` and no `HTTPException`.** Errors are rendered by
  exception handlers registered in `create_app`.
* `api/deps.py` exports **only** `Annotated` dependency aliases. The ruff configuration
  exempts that module from the type-checking rules on exactly that assumption.
* Health paths are `/healthz` and `/readyz`, spelled once in
  `api/routers/health.py` and imported everywhere else — including the Dockerfile
  `HEALTHCHECK`. `/readyz` never calls Keitaro: an unreachable tracker does not make this
  service unready.
* To enumerate the routes of an app — for instance to assert that no endpoint escaped its
  authentication — use `app.openapi()["paths"]`. **`app.routes` does not work**:
  `include_router` appends one lazy router object rather than flattening, so filtering it
  for `APIRoute` returns an empty set and the check passes while inspecting nothing.
* Wire models are built with `model_validate(raw)`, never `Model(**raw)`.

## Tests

* mypy runs strict over `src`, `tests`, `alembic` and `scripts`. ruff's annotation rules
  are off for `tests/**`, so **mypy is the only thing requiring a `-> None` on a test** —
  it still requires it.
* `conftest.py` holds fixtures only. Shared constants and helpers live in
  `tests/helpers.py` and are imported as `from tests.helpers import ...`.
* There are no `__init__.py` files under `tests/`. pytest collects with
  `--import-mode=importlib` and mypy needs `explicit_package_bases`; both are configured.
* `filterwarnings = ["error"]`. `fastapi.testclient.TestClient` is therefore unusable —
  it emits a deprecation under httpx 0.x. API tests go through `httpx.ASGITransport`.
* Async tests need no decorator and no `pytestmark`: `anyio_mode = "auto"`.
* A bare `pytest` on a fresh clone is green. Tests that need PostgreSQL carry the `db`
  marker and skip with a visible reason.

## Style

* English in code, docstrings, OpenAPI and error messages. Russian only in the README and
  in pull request descriptions.
* **A docstring explains a decision.** If it restates the function's name, delete it.
  Module, package, dunder and `__init__` docstrings are not required and mostly should not
  exist.
* `# noqa` always carries a reason.
* Exception class names end in `Error` — including the leaves (`NotFoundError`, not
  `NotFound`).
* Put `@typing.override` on every port implementation.
* Log events are dotted, lower-case and noun-first: `http.request`, `keitaro.request`.
* The access log carries no query string in any form, and no request or response body.

## Things that are true and surprising

Each of these was measured, and each contradicts something a reasonable person would
assume.

* `extra="forbid"` on `BaseSettings` does **not** catch a misspelled environment variable.
  It applies to keys read from a `.env` *file*; the environment source looks up one
  variable per declared field and never enumerates the process environment. The guard in
  `settings.py` does that by hand.
* Raising a `ValueError` from a `mode="before"` model validator makes pydantic print the
  whole merged settings dict as `input_value=` — with the secrets in it, because nothing
  has wrapped them in `SecretStr` yet. That is why `UnknownSettingError` and
  `MissingSettingError` inherit `Exception`, and why the validator unbinds its argument
  before raising.
* `ruff check --fix` on a freshly generated migration deletes `import sqlalchemy as sa`
  and `from alembic import op` as unused. The pre-commit hook therefore reports and does
  not fix.
* `runtime-evaluated-base-classes` in the ruff configuration is matched against the base
  named in the `class` statement and is **not** inherited through a subclass in another
  module — which is why the project's own declarative base is listed next to SQLAlchemy's.
