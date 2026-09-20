# AD Robot backend. The build context is backend/, the directory this file lives in:
#
#     docker build -t adrobot-api backend/
#
# Two stages. Poetry, the lock file and the wheel it builds stay in `builder`; the runtime
# layer receives one finished virtualenv and nothing that was needed to make it
# (PLAN-BACKEND §9: no Poetry and no compilers in the runtime layer).

# One ARG for both stages, so they cannot drift apart: the virtualenv that crosses the
# stage boundary holds cp313 extension modules under a literal lib/python3.13 path, so a
# builder and a runtime on two different minors fail at import. A tag and not a digest —
# what this project pins is poetry.lock, and a digest would freeze the base's security
# updates behind a bump nothing here is set up to perform.
#
# Debian slim rather than alpine, which was measured at 113 MB against this image's 187 MB.
# All five compiled dependencies do ship musllinux cp313 wheels, so alpine builds without a
# compiler too; glibc is taken because it is the interpreter this service's dependencies
# are tested against everywhere else, and 74 MB is not what this project is judged on.
# Switching is a one-line change to this ARG.
ARG PYTHON_VERSION=3.13


# --- builder --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

# The version poetry.lock was written by; pyproject.toml says `requires-poetry = ">=2.2"`.
ARG POETRY_VERSION=2.4.1

# VIRTUAL_ENV is the whole mechanism: Poetry installs into an activated environment, so
# this is what puts the dependencies somewhere the next stage can name — an environment
# Poetry creates for itself lives under a hash in its cache, which no COPY can address.
# What guards that assumption is the import assertion in the runtime stage, not a second
# environment variable here.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Poetry in an interpreter of its own, so its own thirty-odd dependencies never meet the
# application's.
RUN python -m venv /opt/poetry \
 && /opt/poetry/bin/pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
 && python -m venv "${VIRTUAL_ENV}"

WORKDIR /app

# Dependencies first and alone, so this layer is keyed on the two files that describe them
# and survives every edit under src/. `--only main` leaves the nine dev tools out;
# `--no-root` leaves the project itself out, because Poetry installs it as a path pointing
# at /app/src, which the runtime layer does not have.
#
# No `poetry check --lock` in front of this: `poetry install` already refuses a lock file
# that no longer matches pyproject.toml, with that same message.
COPY pyproject.toml poetry.lock ./
RUN /opt/poetry/bin/poetry install --only main --no-root

# The project as a real wheel rather than src/ on PYTHONPATH: api/app.py reads
# `importlib.metadata.version("adrobot")`, which raises PackageNotFoundError for a package
# that is merely importable.
#
# WORKDIR rather than `poetry -C backend build`: poetry-core resolves
# `packages = [{include = "adrobot", from = "src"}]` against the process cwd, so `-C` looks
# for backend/src/src/adrobot and fails. Here the cwd is the directory holding
# pyproject.toml, which is the shape poetry-core supports.
#
# compileall, because Poetry's installer does not byte-compile and the runtime cannot: the
# venv is root-owned and the process is not, so without this every container start
# recompiles all of site-packages in memory and throws the result away.
COPY src ./src
RUN /opt/poetry/bin/poetry build --format wheel \
 && pip install --no-cache-dir --no-deps dist/*.whl \
 && python -m compileall -q "${VIRTUAL_ENV}/lib"


# --- runtime ----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# PATH is what makes `python`, `uvicorn` and (from 1.9) `alembic` mean the virtualenv's.
# PYTHONUNBUFFERED changes nothing about today's log stream — logging.py writes through a
# logging.StreamHandler, which flushes every record — it is for the tools that print, and
# the first of those is 1.9's `alembic upgrade head` in this same image.
ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# A fixed uid, because a number is what a bind mount, a CI artefact or a Kubernetes
# runAsUser sees, and one well above the range a distribution allocates to itself.
RUN groupadd --gid 10001 adrobot \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin adrobot

# Root-owned: the process runs as adrobot and has no business rewriting its own code.
COPY --from=builder /opt/venv /opt/venv

# Two assertions, and neither is decoration.
#
# `Settings` has validate_default=True and resolves its "UTC" default through zoneinfo, so
# an image with no tz database dies inside `Settings()` complaining about an IANA name,
# which reads like a typo and is not. python:3.13-slim carries Debian's tzdata and
# `tzdata` is not in poetry.lock, so there is no fallback — this is what notices the day a
# base image stops shipping it.
#
# The import proves the virtualenv crossed the stage boundary intact and that `adrobot` is
# an installed distribution rather than a directory on the path. It runs as root, so it
# also leaves behind the stdlib .pyc for exactly the modules the application imports,
# which the non-root process could not write for itself.
RUN python -c "import zoneinfo; zoneinfo.ZoneInfo('UTC')" \
 && python -c "import adrobot.main, importlib.metadata as m; m.version('adrobot')"

# Created explicitly rather than left to WORKDIR. A WORKDIR whose directory does not exist
# is created at build time by Docker, owned by root, and at run time by buildah/podman,
# owned by and writable to the container user — so the same file would otherwise produce
# two different images. 1.9 puts alembic.ini and alembic/ here.
RUN install --directory --owner=root --group=root --mode=0755 /app
WORKDIR /app
USER adrobot

# nginx proxies to api:8000 at 9.8 (PLAN-00 §5.2). Documentation; it publishes nothing.
EXPOSE 8000

# /healthz and never /readyz: from 5.8 readiness reports the database, and a database that
# is briefly gone must not restart the container. urllib, because this base carries neither
# curl nor wget and the interpreter is already resident; the failure is caught so that the
# health log holds a sentence rather than six frames of traceback. Both health paths are in
# AccessLogMiddleware's quiet_paths, so the probe leaves no access line.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request as u\ntry:\n    u.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()\nexcept Exception as exc:\n    print('healthz:', exc)\n    sys.exit(1)\n"]

# The factory, because main.py deliberately exports no module-level `app`: `Settings()` has
# to run in a call and not at import. No --log-config and no --no-access-log — logging.py
# takes uvicorn's loggers over, and it can only do that after uvicorn has imported this
# factory. CMD and no ENTRYPOINT, so that 1.8's one-shot `migrator` replaces the command
# whole instead of appending to it. Exec form, so uvicorn is pid 1 and takes SIGTERM itself.
CMD ["uvicorn", "adrobot.main:create_asgi_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
