"""One-off reconnaissance against a live Keitaro tracker. Sub-stage 2.1: read-only.

The published specification (`docs/keitaro-openapi.json`) says what the endpoints are;
stage 2 asks the tracker how they behave. This file is the instrument and
`docs/keitaro-api-notes.md` (2.6) is the result, which is why the run ends by printing its
findings as the markdown rows that document is made of: every claim there can name the
request that produced it.

Why it is shaped the way it is:

*   **Every call in this sub-stage is a GET.** The writing probes arrive at 2.2, in their
    own commit, confined to the `ADROBOT-TEST` campaign group and cleaned up at 2.7.
*   **Raw answers are written to `.scratch/`, never to the terminal.** Keitaro returns a
    campaign's Click API token inside a campaign object and a postback URL inside a
    traffic source, which is why `/.scratch/` is in `.gitignore`. What reaches stdout goes
    through `adrobot.logging.redact` first — the service's own redactor, exercised here a
    stage before anything depends on it.
*   **`follow_redirects=False`**, as in the transport 4.3 builds: httpx strips
    `Authorization` when a redirect crosses hosts but would carry a custom `Api-Key`
    header to wherever it points. A 3xx is a finding, not something to chase.
*   It sits under `backend/` rather than in the repository-root `scripts/` of
    PLAN-BACKEND §1 so that ruff, mypy and the CI jobs cover it: all three run with
    `cwd=backend/`. It is also the one file outside `src/adrobot/` that unwraps the admin
    key — `tests/test_secret_containment.py` scans the package, and the key has to reach
    an `Api-Key` header somehow.

    make probe                 # every probe, against the .env at the repository root
    make probe P="offers"      # one of them
    cd backend && poetry run python scripts/kt_probe.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import httpx

from adrobot.logging import redact
from adrobot.settings import MissingSettingError, Settings, UnknownSettingError

if TYPE_CHECKING:
    from collections.abc import Sequence

# parents[2] and not the working directory: `/.scratch/` in .gitignore is anchored at the
# repository root, so a run started from backend/ must still write there or the dumps —
# which hold tokens — become untracked files nothing ignores.
REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SCRATCH_ROOT: Final = REPO_ROOT / ".scratch" / "kt-probe"

# Read is three times the service's own (§3.1) on purpose: the service gives up on a slow
# tracker, while the probe wants to see the whole offer catalogue and then say how long it
# took. The gap between the two numbers is itself a measurement — see `probe_offers`.
TIMEOUT: Final = httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=5.0)
SERVICE_READ_TIMEOUT_MS: Final = 10_000

# The group every writing probe from 2.2 onwards stays inside. Named here because 2.1 is
# what reports whether it already exists.
TEST_GROUP: Final = "ADROBOT-TEST"

ROWS_SHOWN: Final = 10
CELL_WIDTH: Final = 30
BODY_PREVIEW: Final = 400

Verdict = Literal["confirmed", "refuted", "open"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One row of the table 2.6 writes: a claim, what the tracker said about it, and how."""

    claim: str
    verdict: Verdict
    evidence: str


@dataclass(frozen=True, slots=True)
class Observation:
    """One request and the answer to it, in the shape the dump file keeps."""

    method: str
    path: str
    status: int
    ok: bool
    elapsed_ms: int
    headers: dict[str, str]
    body: object


def _decode(response: httpx.Response) -> object:
    """Return the parsed body, or the raw text when the answer is not JSON after all."""
    try:
        return response.json()
    except ValueError:
        return response.text


def _rows(observation: Observation) -> list[dict[str, Any]]:
    """Return the list body as rows, or nothing at all when the answer was not a list."""
    if not isinstance(observation.body, list):
        return []
    return [row for row in observation.body if isinstance(row, dict)]


def _shape(body: object) -> str:
    if isinstance(body, list):
        return f"array of {len(body)}"
    if isinstance(body, dict):
        return f"object, keys {', '.join(sorted(map(str, body))[:6])}"
    return f"{type(body).__name__} of {len(str(body))} chars"


def _cell(row: dict[str, Any], column: str) -> str:
    value = row.get(column)
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ",".join(str(item) for item in value) or "-"
    text = str(value)
    return text if len(text) <= CELL_WIDTH else text[: CELL_WIDTH - 2] + ".."


def _print_rows(rows: list[dict[str, Any]], columns: tuple[str, ...], *, limit: int) -> None:
    """Print the head of a list body as a fixed-width table, redacted.

    The columns are named by the caller and none of them is a secret, so `redact` here is
    belt and braces — but it is free, and it is what makes "the terminal never shows a raw
    Keitaro body" a property of this file rather than of every probe in it.
    """
    cleaned = redact(rows[:limit])
    shown = [row for row in cleaned if isinstance(row, dict)] if isinstance(cleaned, list) else []
    if not shown:
        return
    cells = [[_cell(row, column) for column in columns] for row in shown]
    widths = [
        max(len(column), *(len(row[index]) for row in cells))
        for index, column in enumerate(columns)
    ]
    header = "  ".join(column.ljust(width) for column, width in zip(columns, widths, strict=True))
    print(f"    {header.rstrip()}")
    print(f"    {'-' * len(header)}")
    for row in cells:
        line = "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        print(f"    {line.rstrip()}")
    if len(rows) > len(shown):
        print(f"    ... and {len(rows) - len(shown)} more (all of them are in the dump)")


class Session:
    """Everything a probe is handed: the client, the dump directory and the run's tally."""

    def __init__(self, client: httpx.AsyncClient, dump_dir: Path, *, rows_shown: int) -> None:
        self._client = client
        self._dump_dir = dump_dir
        self.rows_shown = rows_shown
        self.calls = 0
        self.arrays = 0
        self.successes = 0
        self.redirects: list[str] = []
        self.failures: list[str] = []
        self.findings: list[Finding] = []

    async def get(
        self, path: str, *, label: str, params: dict[str, Any] | None = None
    ) -> Observation:
        """Perform one GET, dump the raw answer, print a redacted line about it.

        A leading slash on `path` does not reset the `/admin_api/v1` prefix: httpx merges
        it onto the client's base path, unlike `urljoin`, which would discard it.
        """
        started = time.perf_counter()
        response = await self._client.get(path, params=params)
        # Timed here rather than read off `response.elapsed`: httpx stamps that when the
        # response stream closes, and a body that was never streamed — a mocked answer in
        # a smoke run — leaves the attribute unset and the probe raising instead of
        # reporting. What this measures is what the caller waited for, which is the
        # number the offer-catalogue finding below is about.
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        observation = Observation(
            method="GET",
            path=response.request.url.raw_path.decode(),
            status=response.status_code,
            ok=response.is_success,
            elapsed_ms=elapsed_ms,
            headers=dict(response.headers),
            body=_decode(response),
        )
        self.calls += 1
        dump = self._dump(label, observation)

        print(
            f"  GET {observation.path} -> {observation.status} "
            f"in {observation.elapsed_ms} ms, {_shape(observation.body)}  [{dump}]"
        )
        if isinstance(observation.body, list):
            self.arrays += 1
        if response.is_redirect:
            self.redirects.append(f"{observation.path} -> {observation.status}")
            print(f"    ! redirect to {response.headers.get('location', '(no Location)')}")
        if observation.ok:
            self.successes += 1
        else:
            self.failures.append(f"GET {observation.path} -> {observation.status}")
            preview = json.dumps(redact(observation.body), ensure_ascii=False, default=repr)
            print(f"    ! body: {preview[:BODY_PREVIEW]}")
        return observation

    def finding(self, claim: str, verdict: Verdict, evidence: str) -> None:
        """Record what the tracker did about a claim, in the words 2.6 will quote."""
        self.findings.append(Finding(claim=claim, verdict=verdict, evidence=evidence))

    def write_findings(self) -> Path:
        """Write the findings beside the dumps, so a run survives a closed terminal."""
        path = self._dump_dir / "findings.json"
        payload = [asdict(finding) for finding in self.findings]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _dump(self, label: str, observation: Observation) -> str:
        name = f"{self.calls:02d}-{label}.json"
        (self._dump_dir / name).write_text(
            json.dumps(asdict(observation), ensure_ascii=False, indent=2, default=repr),
            encoding="utf-8",
        )
        return name


async def probe_groups(session: Session) -> None:
    """Read the campaign groups, and settle whether `type` is required or merely defaulted."""
    typed = await session.get("/groups", label="groups-campaigns", params={"type": "campaigns"})
    _print_rows(_rows(typed), ("id", "name", "position", "type"), limit=session.rows_shown)

    present = TEST_GROUP in {str(row.get("name")) for row in _rows(typed)}
    print(f"    {TEST_GROUP}: {'present' if present else 'absent — 2.2 creates it'}")

    # The spec marks `type` required *and* gives it a default, which is a contradiction
    # only the tracker can settle: that pair is how a generator writes "optional" when the
    # form it was generated from had a value preselected.
    untyped = await session.get("/groups", label="groups-no-type")
    claim = "GET /groups rejects a request without the `type` parameter"
    if not untyped.ok:
        session.finding(
            claim,
            "confirmed",
            f"with ?type=campaigns: {typed.status}; with no query: {untyped.status}",
        )
    elif _rows(untyped) == _rows(typed):
        session.finding(
            claim,
            "refuted",
            f"no query answered {untyped.status} with the same {len(_rows(typed))} rows — "
            f"the spec's `required: true` is really the `campaigns` default",
        )
    else:
        session.finding(
            claim,
            "refuted",
            f"no query answered {untyped.status}, but with {len(_rows(untyped))} rows "
            f"against {len(_rows(typed))} for ?type=campaigns",
        )


async def probe_sources(session: Session) -> None:
    """Read the traffic sources, one of which part 1 puts on every campaign it creates."""
    # `postback_url` and `parameters` are deliberately not among the columns: a postback
    # URL carries the tracker's own token. They are in the dump, which is gitignored.
    sources = await session.get("/traffic_sources", label="traffic-sources")
    _print_rows(_rows(sources), ("id", "name", "template_name", "state"), limit=session.rows_shown)


async def probe_domains(session: Session) -> None:
    """Read the domains, since a campaign is only reachable through one that is usable."""
    domains = await session.get("/domains", label="domains")
    rows = _rows(domains)
    _print_rows(
        rows,
        ("id", "name", "is_ssl", "state", "default_campaign_id", "catch_not_found"),
        limit=session.rows_shown,
    )
    if rows:
        active = [row for row in rows if row.get("state") == "active"]
        with_ssl = [row for row in active if row.get("is_ssl")]
        print(f"    {len(active)} active of {len(rows)}, {len(with_ssl)} of those with SSL")


async def probe_offers(session: Session) -> None:
    """Read the whole offer catalogue, and test whether the tracker can filter it at all."""
    full = await session.get("/offers", label="offers")
    rows = _rows(full)
    _print_rows(
        rows,
        ("id", "name", "country", "state", "group_id", "action_type"),
        limit=session.rows_shown,
    )
    print(f"    {len(rows)} offers in {full.elapsed_ms} ms")

    # The spec gives GET /offers no query parameters at all, which is why 5.4 mirrors the
    # catalogue locally and 7.7 searches that mirror. An ignored parameter and a rejected
    # one both confirm it; an honoured one would delete a table and a use case.
    narrowed = await session.get(
        "/offers",
        label="offers-with-query",
        params={"limit": 1, "search": "zzz-no-such-offer"},
    )
    claim = "GET /offers takes no query parameters, so the catalogue has to be mirrored locally"
    if not narrowed.ok:
        session.finding(
            claim, "confirmed", f"?limit=1&search=... was rejected with {narrowed.status}"
        )
    elif len(_rows(narrowed)) == len(rows):
        session.finding(
            claim,
            "confirmed",
            f"?limit=1&search=... answered {narrowed.status} with the same {len(rows)} offers",
        )
    else:
        session.finding(
            claim,
            "refuted",
            f"?limit=1&search=... answered {narrowed.status} with {len(_rows(narrowed))} offers "
            f"against {len(rows)} — this build filters server-side",
        )

    if full.ok and full.elapsed_ms > SERVICE_READ_TIMEOUT_MS:
        session.finding(
            "A full GET /offers fits inside the service's 10 s read timeout (§3.1)",
            "refuted",
            f"{len(rows)} offers took {full.elapsed_ms} ms — the catalogue sync needs a "
            f"timeout of its own",
        )


PROBES: Final = {
    "groups": probe_groups,
    "sources": probe_sources,
    "domains": probe_domains,
    "offers": probe_offers,
}


def _close_out(session: Session) -> None:
    """Record the two findings that are about the run as a whole rather than one endpoint."""
    if session.successes:
        session.finding(
            "Every reference endpoint answers with a bare JSON array: no envelope, no "
            "pagination metadata",
            "confirmed" if session.arrays == session.successes else "refuted",
            f"{session.arrays} of {session.successes} successful reads were top-level arrays",
        )
    if session.redirects:
        session.finding(
            "The Admin API answers directly, without redirecting",
            "refuted",
            f"redirected: {'; '.join(session.redirects)} — follow_redirects=False is what "
            f"keeps the Api-Key header off the new host",
        )
    elif session.calls:
        session.finding(
            "The Admin API answers directly, without redirecting",
            "confirmed",
            f"{session.calls} requests, no 3xx",
        )


def _print_findings(session: Session) -> None:
    if not session.findings:
        print("\n== no findings: nothing got far enough to be evidence of anything")
        return
    print("\n== findings — the rows docs/keitaro-api-notes.md (2.6) is made of\n")
    print("| Claim | Verdict | Evidence |")
    print("|---|---|---|")
    for finding in session.findings:
        print(f"| {finding.claim} | {finding.verdict} | {finding.evidence} |")


async def _run(settings: Settings, names: tuple[str, ...], rows_shown: int) -> int:
    run_dir = SCRATCH_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"tracker: {settings.keitaro_base_url}")
    print(f"dumps:   {run_dir}")

    async with httpx.AsyncClient(
        base_url=str(settings.keitaro_base_url),
        # Unwrapped straight into the header and never bound to a name, the rule
        # AGENTS.md states for the service: a traceback renderer that captures locals
        # prints what a local holds.
        headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
        timeout=TIMEOUT,
        follow_redirects=False,
    ) as client:
        session = Session(client, run_dir, rows_shown=rows_shown)
        # Sequentially, and without the service's semaphore: this points at somebody's
        # working tracker, and the order of the output is what makes it readable.
        for name in names:
            probe = PROBES[name]
            print(f"\n== {name}: {(probe.__doc__ or '').splitlines()[0]}")
            try:
                await probe(session)
            except httpx.HTTPError as exc:
                # One unreachable endpoint must not cost the findings of the others.
                session.failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"  ! {type(exc).__name__}: {exc}")

    _close_out(session)
    _print_findings(session)
    print(f"\n{session.calls} calls, {len(session.failures)} failed. {session.write_findings()}")
    for failure in session.failures:
        print(f"  ! {failure}")
    return 1 if session.failures else 0


def _parse_args(argv: Sequence[str] | None) -> tuple[tuple[str, ...], int]:
    listed = "\n".join(
        f"  {name:8s} {(probe.__doc__ or '').splitlines()[0]}" for name, probe in PROBES.items()
    )
    parser = argparse.ArgumentParser(
        prog="kt_probe.py",
        description="Read-only reconnaissance against a live Keitaro (stage 2.1).",
        epilog=f"probes (all of them by default):\n{listed}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("probes", nargs="*", metavar="PROBE", help="which probes to run")
    parser.add_argument(
        "--rows",
        type=int,
        default=ROWS_SHOWN,
        help=f"rows printed per table; the dump always holds all of them (default {ROWS_SHOWN})",
    )
    args = parser.parse_args(argv)
    names: tuple[str, ...] = tuple(args.probes) or tuple(PROBES)
    unknown = [name for name in names if name not in PROBES]
    if unknown:
        parser.error(f"unknown probe: {', '.join(unknown)}. Known: {', '.join(PROBES)}")
    return names, int(args.rows)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected probes and return the exit code: 1 if any call failed, 2 if unconfigured."""
    names, rows_shown = _parse_args(argv)
    try:
        settings = Settings()
    except (UnknownSettingError, MissingSettingError) as exc:
        # The probe reads the service's own environment, so it inherits the boot guards
        # and their messages; what it has to add is where the values come from, since
        # `Settings` deliberately has no env_file and nothing has exported .env here.
        print(
            f"{exc}\n\nThe probe reads the same environment as the service. From the",
            file=sys.stderr,
        )
        print("repository root: set -a; . .env; set +a — or use `make probe`.", file=sys.stderr)
        return 2
    return asyncio.run(_run(settings, names, rows_shown))


if __name__ == "__main__":
    raise SystemExit(main())
