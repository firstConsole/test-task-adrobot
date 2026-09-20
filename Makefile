# Developer entry points. Everything here is a one-line reminder of a command that
# already works; nothing is implemented in this file.
#
# Two rules the commands encode, both learned the hard way:
#   * every Python tool runs with cwd = backend/. ruff resolves `src`, mypy resolves
#     `files` and `mypy_path`, and coverage finds its config, all relative to the process
#     working directory — not to the file they are configured in.
#   * compose runs from the repository root, because that is where .env and
#     docker-compose.yml live and where the build context ./backend resolves from.

BACKEND := backend
COMPOSE ?= docker compose
POETRY  ?= poetry
RUN     := $(POETRY) run

.DEFAULT_GOAL := help
.PHONY: help install hooks up down migrate revision test cov lint typecheck imports probe

help: ## Show this list
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# --- development environment -----------------------------------------------------------

# Only needed to run the checks below on the host; `make up` builds its own environment
# inside the image. There is no `env_file` in Settings on purpose (the monorepo keeps .env
# at the root while every backend tool runs from backend/), so running the service by hand
# outside compose needs `set -a; . .env; set +a` first.
install: ## Install the backend, with its development dependencies
	$(POETRY) -C $(BACKEND) install

# Once per clone. The hooks are ruff and gitleaks; both also run in CI, so this only buys
# the seconds between writing a mistake and hearing about it — except for gitleaks, where
# it is the difference between a secret that never entered history and one that did.
hooks: ## Install the git pre-commit hooks
	cd $(BACKEND) && $(RUN) pre-commit install

# --- the stack ------------------------------------------------------------------------

up: ## Build and start the stack, waiting until it is healthy
	$(COMPOSE) up -d --wait --build

down: ## Stop the stack and keep the database
	$(COMPOSE) down

# The one-shot migrator, run against a stack that is already up. `pull_policy: never` in
# compose means this fails with image-not-found on a machine that has never run `make up`
# — the price of building the shared image once instead of twice.
migrate: ## Apply migrations to the running stack
	$(COMPOSE) run --rm migrator

# Numbered, not hashed: `make revision REV=0002 M="draft tables"` writes
# alembic/versions/0002_draft_tables.py. Both arguments are required, because a revision
# without a number breaks the ordering and one without a message becomes its own summary.
revision: ## Create a migration: make revision REV=0002 M="draft tables"
	@test -n "$(REV)" || { echo 'REV is required, e.g. make revision REV=0002 M="draft tables"'; exit 1; }
	@test -n "$(M)"   || { echo 'M is required, e.g. make revision REV=0002 M="draft tables"'; exit 1; }
	cd $(BACKEND) && $(RUN) alembic revision --rev-id "$(REV)" -m "$(M)"

# --- checks ---------------------------------------------------------------------------

test: ## Run the test suite
	cd $(BACKEND) && $(RUN) pytest

lint: ## Lint and check formatting
	cd $(BACKEND) && $(RUN) ruff check .
	cd $(BACKEND) && $(RUN) ruff format --check .

typecheck: ## Type-check src, tests, alembic and scripts under mypy strict
	cd $(BACKEND) && $(RUN) mypy

# Red until sub-stage 4.2, and deliberately not chained into any other target: the three
# contracts name adrobot.domain, adrobot.application and
# infrastructure/keitaro/schemas.py, and one erroring contract aborts the whole run. Until
# those packages exist this prints `Module 'adrobot.domain' does not exist.` and exits 1.
imports: ## Check the layer boundaries (red until stage 4.2)
	cd $(BACKEND) && $(RUN) lint-imports --no-logo --no-cache

# --- the tracker ----------------------------------------------------------------------

# Stage 2 reconnaissance against a live Keitaro, so unlike everything above it needs real
# credentials — the .env at the repository root. Sourced on the same line as the command
# because a variable exported by one recipe line does not survive into the next, and the
# file is read here rather than by Settings for the reason AGENTS.md gives: every backend
# tool runs from backend/ while .env lives one directory up.
#
# `make probe` alone only reads. The writing probes — `create`, `name-limit` — have to be
# named, and what they create carries the ADROBOT-TEST prefix and is listed in
# .scratch/kt-probe/created.json, which the cleanup at 2.7 works from.
probe: ## Probe the live Keitaro API (stage 2): make probe P="groups offers"
	@test -f .env || { echo 'no .env: cp .env.example .env and put the real tracker URL and key in it'; exit 1; }
	set -a; . ./.env; set +a; cd $(BACKEND) && $(RUN) python scripts/kt_probe.py $(P)

# Two gates, because one number cannot say both things (PLAN-BACKEND §2.1): the share
# arithmetic is the file this project is judged on, and the rings around it are held to a
# lower bar. Both are red until the packages they name exist — `coverage report` over an
# `--include` that matches nothing prints "No data to report." and exits 1, which is the
# right answer and not a reason to soften the gate.
cov: ## Run the suite under coverage and enforce both gates (red until stage 3)
	cd $(BACKEND) && $(RUN) pytest --cov --cov-report=term-missing
	cd $(BACKEND) && $(RUN) coverage report --include='src/adrobot/domain/shares.py' --fail-under=100
	cd $(BACKEND) && $(RUN) coverage report --include='src/adrobot/domain/*,src/adrobot/application/*' --fail-under=90
