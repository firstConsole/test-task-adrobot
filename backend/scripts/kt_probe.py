"""One-off reconnaissance against a live Keitaro tracker: what the specification cannot say.

The published specification (`docs/keitaro-openapi.json`) says what the endpoints are;
stage 2 asks the tracker how they behave. This file is the instrument and
`docs/keitaro-api-notes.md` (2.6) is the result, which is why the run ends by printing its
findings as the markdown rows that document is made of: every claim there can name the
request that produced it.

Why it is shaped the way it is:

*   **Reading and writing are separate registries.** A bare run, and `make probe`, does
    the read-only probes and nothing else; a writing probe has to be named. What one
    creates carries the `ADROBOT-TEST` prefix, belongs to the `ADROBOT-TEST` campaign
    group and is appended to `/.scratch/kt-probe/created.json` — the ledger 2.7 deletes
    from, kept outside the per-run directory so that an interrupted run still leaves one
    list of everything this script has ever made on this tracker.
*   **A request body is printed before it is sent**, so what reached somebody's live
    tracker is in the terminal and in the dump, not inferred from the outcome. Nothing is
    retried: a creating POST that times out has an unknown outcome, and guessing is how
    two campaigns get made (§3.1).
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

    make probe                 # every read-only probe, with the .env at the root
    make probe P="offers"      # one of them
    make probe P="create"      # the part 1 rehearsal — this one writes
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
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal
from zoneinfo import ZoneInfo

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

# The group every writing probe stays inside, and the ledger of what they have made. The
# ledger sits beside the run directories rather than in one of them: cleanup that can only
# see its own run is how test data survives on somebody's tracker.
TEST_GROUP: Final = "ADROBOT-TEST"
LEDGER: Final = SCRATCH_ROOT / "created.json"

# The shape part 1 has to produce, from the task: flow 1 catches one country and redirects
# to Google, flow 2 rotates offers. `country` is Keitaro's own filter name — the
# `catalogues` probe prints the catalogue it comes from.
GOOGLE_URL: Final = "https://google.com"
GEO_FILTER: Final = "country"
DEFAULT_GEO: Final = "MX"
# Keitaro's own key for an HTTP redirect. Overridable, because the catalogue it comes from
# is `/streams_actions` and this is the one value in the writing probe that is a guess.
DEFAULT_ACTION_TYPE: Final = "http"
FLOW_ONE: Final = "Flow 1"
FLOW_TWO: Final = "Flow 2"

# Question 8: a name long enough to find the limit, sent whole so the answer is either the
# limit itself or "longer than this".
LONG_NAME_LENGTH: Final = 200

# Questions 1 and 2, the ones the editor stands on. Three offers, with shares that are
# distinct and do not sum to 100 after one is dropped: a tracker that silently normalises
# what it was sent has nowhere to hide.
PUT_FLOW: Final = "Offers"
PUT_SHARES: Final = (50, 30, 20)

# Question 4. The two dimensions are the whole stats screen: clicks per flow and clicks per
# offer (PLAN-BACKEND §3). The wide list is sent once, on its own, to find out which names
# this build knows — a rejected column is named in the error body, which the dump keeps.
REPORT_MEASURE: Final = "clicks"
REPORT_DIMENSIONS: Final = ("stream_id", "offer_id")
WIDE_MEASURES: Final = ("clicks", "campaign_unique_clicks", "conversions", "sales", "revenue")

ROWS_SHOWN: Final = 10
CELL_WIDTH: Final = 30
BODY_PREVIEW: Final = 400

Verdict = Literal["confirmed", "refuted", "open"]


def _stamp() -> str:
    """Return the UTC stamp that names a run directory and every entity a run creates."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True, slots=True)
class Options:
    """What the command line asked for, in the one object the probes read it from."""

    rows_shown: int
    geo: str
    offer_id: int | None
    action_type: str
    campaign_id: int | None


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
    sent: object | None


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


def _field(observation: Observation, name: str) -> object:
    """Return one field of an object body, or None when the answer was not an object."""
    return observation.body.get(name) if isinstance(observation.body, dict) else None


def _int_field(observation: Observation, name: str) -> int | None:
    value = _field(observation, name)
    return value if isinstance(value, int) else None


def _named(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("name") == name), None)


def _payload_values(raw: object) -> list[str]:
    """Return a stream filter's payload as a list, whichever of the two shapes it arrived in.

    The spec types `Filter.payload` as a string while `FilterStreamRequest.payload` is an
    array of strings, so the mapper at 4.6 has to survive both. Which one this build sends
    back is question 5's other half, and the finding says so.
    """
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]
    return []


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


def _print_values(observation: Observation, *, limit: int) -> None:
    """Print a list of scalars: some catalogues answer with bare strings, not objects."""
    body = observation.body
    if not isinstance(body, list) or not body or any(isinstance(item, dict) for item in body):
        return
    shown = [str(item) for item in body[:limit]]
    tail = f" ... and {len(body) - len(shown)} more" if len(body) > len(shown) else ""
    print(f"    {', '.join(shown)}{tail}")


class Session:
    """Everything a probe is handed: the client, the dump directory and the run's tally."""

    def __init__(
        self, client: httpx.AsyncClient, dump_dir: Path, options: Options, *, timezone: str
    ) -> None:
        self._client = client
        self._dump_dir = dump_dir
        self.options = options
        # Configuration rather than a constant: a report's day boundary is the tracker's
        # own, and "clicks today" being off by an hour is exactly the kind of wrong number
        # the task is judged on. 2.5 reads the tracker's zone and this value is what it is
        # then compared against.
        self.timezone = timezone
        self.calls = 0
        self.arrays = 0
        self.listings = 0
        self.successes = 0
        self.expected_failures = 0
        self.test_group_id: int | None = None
        self.redirects: list[str] = []
        self.failures: list[str] = []
        self.findings: list[Finding] = []

    async def get(
        self,
        path: str,
        *,
        label: str,
        params: dict[str, Any] | None = None,
        listing: bool = True,
        may_fail: bool = False,
    ) -> Observation:
        """Read one endpoint. `listing=False` says this one answers with a single object.

        Only listings are counted towards the "bare JSON array" finding: `GET /offers/{id}`
        returning an object is not evidence against it.
        """
        observation = await self._request(
            "GET", path, label=label, params=params, may_fail=may_fail
        )
        if listing and observation.ok:
            self.listings += 1
            if isinstance(observation.body, list):
                self.arrays += 1
        return observation

    async def post(
        self, path: str, *, label: str, payload: dict[str, Any], may_fail: bool = False
    ) -> Observation:
        """Create something. Never retried on a timeout: the outcome of one is unknown."""
        return await self._request("POST", path, label=label, payload=payload, may_fail=may_fail)

    async def put(
        self, path: str, *, label: str, payload: dict[str, Any], may_fail: bool = False
    ) -> Observation:
        """Replace something. The body always carries `action_type` and `schema` (§3.1)."""
        return await self._request("PUT", path, label=label, payload=payload, may_fail=may_fail)

    async def _request(  # noqa: PLR0913 — six axes of one HTTP call, not six concerns
        self,
        method: str,
        path: str,
        *,
        label: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        may_fail: bool = False,
    ) -> Observation:
        """Perform one call, dump both halves of it, print a redacted line about it.

        `may_fail` marks a call whose refusal is the answer it went looking for — a filter
        the endpoint may not accept, a path that may not exist. It is still printed and
        still dumped; it is not counted as something that went wrong, because a run that
        exits non-zero every time is a run nobody reads the exit code of.

        A leading slash on `path` does not reset the `/admin_api/v1` prefix: httpx merges
        it onto the client's base path, unlike `urljoin`, which would discard it.
        """
        if payload is not None:
            # Printed whole and before the fact: this is the one file that changes somebody
            # else's tracker, and "what did it send" must not be a question for the dump.
            print(f"  {method} {path} <- {json.dumps(redact(payload), ensure_ascii=False)}")
        started = time.perf_counter()
        response = await self._client.request(method, path, params=params, json=payload)
        # Timed here rather than read off `response.elapsed`: httpx stamps that when the
        # response stream closes, and a body that was never streamed — a mocked answer in
        # a smoke run — leaves the attribute unset and the probe raising instead of
        # reporting. What this measures is what the caller waited for, which is the
        # number the offer-catalogue finding below is about.
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        observation = Observation(
            method=method,
            path=response.request.url.raw_path.decode(),
            status=response.status_code,
            ok=response.is_success,
            elapsed_ms=elapsed_ms,
            headers=dict(response.headers),
            body=_decode(response),
            sent=payload,
        )
        self.calls += 1
        dump = self._dump(label, observation)

        print(
            f"  {method} {observation.path} -> {observation.status} "
            f"in {observation.elapsed_ms} ms, {_shape(observation.body)}  [{dump}]"
        )
        if response.is_redirect:
            self.redirects.append(f"{observation.path} -> {observation.status}")
            print(f"    ! redirect to {response.headers.get('location', '(no Location)')}")
        if observation.ok:
            self.successes += 1
        else:
            preview = json.dumps(redact(observation.body), ensure_ascii=False, default=repr)
            print(f"    ! body: {preview[:BODY_PREVIEW]}")
            if may_fail:
                self.expected_failures += 1
            else:
                self.failures.append(f"{method} {observation.path} -> {observation.status}")
        return observation

    def finding(self, claim: str, verdict: Verdict, evidence: str) -> None:
        """Record what the tracker did about a claim, in the words 2.6 will quote."""
        self.findings.append(Finding(claim=claim, verdict=verdict, evidence=evidence))

    def record_created(self, kind: str, entity_id: object, name: str) -> None:
        """Append one created entity to the ledger, before anything else can go wrong.

        Written on every call rather than once at the end: the entity exists on the tracker
        from the moment the POST answered, so a crash between that and the end of the run
        must not be what makes it unfindable.
        """
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        if LEDGER.exists():
            loaded = json.loads(LEDGER.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                entries = [row for row in loaded if isinstance(row, dict)]
        entries.append({"kind": kind, "id": entity_id, "name": name, "at": _stamp()})
        LEDGER.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    + created {kind} {entity_id}: {name!r} — in the ledger, {len(entries)} so far")

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
    _print_rows(_rows(typed), ("id", "name", "position", "type"), limit=session.options.rows_shown)

    present = TEST_GROUP in {str(row.get("name")) for row in _rows(typed)}
    print(f"    {TEST_GROUP}: {'present' if present else 'absent — 2.2 creates it'}")

    # The spec marks `type` required *and* gives it a default, which is a contradiction
    # only the tracker can settle: that pair is how a generator writes "optional" when the
    # form it was generated from had a value preselected.
    untyped = await session.get("/groups", label="groups-no-type", may_fail=True)
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
    _print_rows(
        _rows(sources), ("id", "name", "template_name", "state"), limit=session.options.rows_shown
    )


async def probe_domains(session: Session) -> None:
    """Read the domains, since a campaign is only reachable through one that is usable."""
    domains = await session.get("/domains", label="domains")
    rows = _rows(domains)
    _print_rows(
        rows,
        ("id", "name", "is_ssl", "state", "default_campaign_id", "catch_not_found"),
        limit=session.options.rows_shown,
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
        limit=session.options.rows_shown,
    )
    print(f"    {len(rows)} offers in {full.elapsed_ms} ms")

    # The spec gives GET /offers no query parameters at all, which is why 5.4 mirrors the
    # catalogue locally and 7.7 searches that mirror. An ignored parameter and a rejected
    # one both confirm it; an honoured one would delete a table and a use case.
    narrowed = await session.get(
        "/offers",
        label="offers-with-query",
        params={"limit": 1, "search": "zzz-no-such-offer"},
        may_fail=True,
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


async def probe_catalogues(session: Session) -> None:
    """Read the flow catalogues a writing probe takes its action and filter names from."""
    actions = await session.get("/streams_actions", label="streams-actions")
    _print_rows(_rows(actions), ("key", "name", "field", "type"), limit=session.options.rows_shown)
    _print_values(actions, limit=session.options.rows_shown)

    # The spec says /streams_actions; two published clients say /stream_actions. One of
    # them is writing against a path this tracker does not have, and it is cheaper to find
    # out here than while reading someone else's adapter at 4.5.
    singular = await session.get("/stream_actions", label="stream-actions-singular", may_fail=True)
    session.finding(
        "The action catalogue is /streams_actions; /stream_actions does not exist",
        "confirmed" if actions.ok and not singular.ok else "refuted",
        f"/streams_actions: {actions.status}, /stream_actions: {singular.status}",
    )

    for path, label, columns in (
        ("/stream_schemas", "stream-schemas", ("key", "name")),
        ("/stream_types", "stream-types", ("key", "name")),
    ):
        listed = await session.get(path, label=label)
        _print_rows(_rows(listed), columns, limit=session.options.rows_shown)
        _print_values(listed, limit=session.options.rows_shown)

    filters = await session.get("/stream_filters", label="stream-filters")
    _print_rows(_rows(filters), ("value", "group", "tooltip"), limit=session.options.rows_shown)
    if filters.ok:
        known = {str(row.get("value")) for row in _rows(filters)}
        related = sorted(name for name in known if "country" in name or "geo" in name)
        session.finding(
            f"The filter a geo flow is built with is named {GEO_FILTER!r}",
            "confirmed" if GEO_FILTER in known else "refuted",
            f"{len(known)} filters; the country-like ones: {', '.join(related) or 'none'}",
        )


async def _ensure_test_group(session: Session) -> int | None:
    """Return the id of the ADROBOT-TEST campaign group, creating it on the first run.

    Cached on the session: two writing probes in one run must not each decide the group is
    missing and create it. A tracker that indexes a new group asynchronously would answer
    the second read with the old list, and the second create would succeed.
    """
    if session.test_group_id is not None:
        return session.test_group_id
    listed = await session.get("/groups", label="w-groups", params={"type": "campaigns"})
    existing = _named(_rows(listed), TEST_GROUP)
    if existing is not None:
        found = existing.get("id")
        print(f"    {TEST_GROUP} group: id={found}")
        session.test_group_id = found if isinstance(found, int) else None
        return session.test_group_id
    created = await session.post(
        "/groups", label="w-group-create", payload={"name": TEST_GROUP, "type": "campaigns"}
    )
    if not created.ok:
        print(f"    {TEST_GROUP} could not be created; nothing else in this probe can run")
        return None
    group_id = _int_field(created, "id")
    session.record_created("group", group_id, TEST_GROUP)
    session.test_group_id = group_id
    return group_id


async def _pick(session: Session, path: str, *, label: str, what: str) -> dict[str, Any] | None:
    """Return the first usable row of a reference list, naming the one it took.

    A row with no `state` counts as usable: not every reference object in this API has
    one, and skipping those would leave a campaign without a domain for no reason.
    """
    listed = await session.get(path, label=label)
    usable = [row for row in _rows(listed) if row.get("state") in (None, "active")]
    if not usable:
        print(f"    no usable {what}; the campaign is created without one")
        return None
    chosen = usable[0]
    print(f"    {what}: id={chosen.get('id')} {str(chosen.get('name'))!r}")
    return chosen


async def _pick_offer(session: Session) -> int | None:
    """Return the offer flow 2 rotates: the one asked for, or the first active one."""
    wanted = session.options.offer_id
    if wanted is None:
        chosen = await _pick(session, "/offers", label="w-offers", what="offer")
        found = chosen.get("id") if chosen is not None else None
        return found if isinstance(found, int) else None
    looked_up = await session.get(f"/offers/{wanted}", label="w-offer", listing=False)
    if not looked_up.ok:
        print(f"    offer {wanted} could not be read; flow 2 would have nothing to rotate")
        return None
    print(f"    offer: id={wanted} {str(_field(looked_up, 'name'))!r}")
    return wanted


async def _create_probe_campaign(
    session: Session, *, group_id: int, name: str, tag: str, extra: dict[str, Any] | None = None
) -> Observation:
    """Create one throwaway campaign in the test group and put it in the ledger.

    Every writing probe wants the same five fields and a different reason; the shape of a
    create is written once so that a later probe cannot quietly drift from it.
    """
    payload: dict[str, Any] = {
        "name": name,
        # Lower case, because an alias becomes the path of a public link; stamped, because
        # a colliding alias is one of the 406s stage 6 has to tell apart from the others.
        "alias": f"adrobot-test-{tag}-{_stamp().lower()}",
        # A string, as CampaignRequest declares it, although a campaign reads its group
        # back as an integer. If this build wants an integer, the finding says so and 6.2
        # sends what it wants rather than what the spec says.
        "group_id": str(group_id),
        "state": "active",
        **(extra or {}),
    }
    created = await session.post("/campaigns", label=f"w-campaign-{tag}", payload=payload)
    if created.ok:
        session.record_created("campaign", _int_field(created, "id"), name)
    return created


def _campaign_findings(session: Session, campaign: Observation) -> None:
    """Record what the create itself said: its status, its group_id and its token."""
    sent = campaign.sent if isinstance(campaign.sent, dict) else {}
    session.finding(
        "Creating a campaign answers 200, not 201",
        "confirmed" if campaign.status == HTTPStatus.OK else "refuted",
        f"POST /campaigns answered {campaign.status}",
    )
    echoed = _field(campaign, "group_id")
    session.finding(
        "A campaign takes group_id as a string and reads it back as an integer",
        "confirmed" if isinstance(echoed, int) else "refuted",
        f"sent {sent.get('group_id')!r}, read back {echoed!r} ({type(echoed).__name__})",
    )
    if isinstance(campaign.body, dict):
        session.finding(
            "A created campaign carries the Click API token, so no 2xx body may be logged",
            "confirmed" if "token" in campaign.body else "refuted",
            f"the created object has the keys: {', '.join(sorted(campaign.body))}",
        )


async def _action_payload_finding(
    session: Session, flow: dict[str, Any], sent: dict[str, Any]
) -> None:
    """Answer question 3, and try the fallback part 1 would need if the answer is bad."""
    claim = "POST /streams stores action_payload, although no request schema declares it"
    if flow.get("action_payload") == GOOGLE_URL:
        session.finding(claim, "confirmed", f"{FLOW_ONE} read back with the URL it was made with")
        return
    session.finding(
        claim,
        "refuted",
        f"read back as {flow.get('action_payload')!r}: part 1 would need POST then PUT",
    )
    stream_id = flow.get("id")
    if not isinstance(stream_id, int):
        return
    # The two-step part 1 would fall back to. The body resends `action_type` and `schema`
    # because nothing promises that PUT is partial — the same rule 4.5 will follow.
    repaired = await session.put(
        f"/streams/{stream_id}",
        label="w-flow-one-put",
        payload={**sent, "action_payload": GOOGLE_URL},
    )
    after = await session.get(f"/streams/{stream_id}", label="w-flow-one-after", listing=False)
    session.finding(
        "PUT /streams/{id} sets the action_payload that POST dropped",
        "confirmed" if _field(after, "action_payload") == GOOGLE_URL else "refuted",
        f"PUT answered {repaired.status}; the flow then read back "
        f"{_field(after, 'action_payload')!r}",
    )


def _filter_findings(session: Session, flow: dict[str, Any]) -> None:
    """Answer question 5: what a geo filter looks like coming back out of the tracker."""
    raw = flow.get("filters")
    filters = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    geo = next((item for item in filters if item.get("name") == GEO_FILTER), None)
    if geo is None:
        session.finding(
            f"A {GEO_FILTER!r} filter sent with the create survives it",
            "refuted",
            f"the flow read back with {len(filters)} filters, none of them {GEO_FILTER!r}",
        )
        return
    session.finding(
        "A stream filter's payload comes back in the shape it was sent, an array",
        "confirmed" if isinstance(geo.get("payload"), list) else "refuted",
        f"payload came back as a {type(geo.get('payload')).__name__}",
    )
    values = _payload_values(geo.get("payload"))
    sent = session.options.geo
    claim = "Keitaro keeps a geo filter's payload in the case it was sent"
    if values == [sent]:
        session.finding(claim, "confirmed", f"sent [{sent!r}], read back the same")
    elif [value.casefold() for value in values] == [sent.casefold()]:
        session.finding(
            claim, "refuted", f"sent [{sent!r}], read back {values}: compare geo case-insensitively"
        )
    else:
        session.finding(claim, "refuted", f"sent [{sent!r}], read back {values}")


def _offer_findings(session: Session, flow: dict[str, Any]) -> None:
    """Answer question 9: whether one read per campaign is enough to draw the editor."""
    raw = flow.get("offers")
    offers = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    claim = "GET /campaigns/{id}/streams nests each flow's offers as whole objects"
    if not offers:
        session.finding(
            claim,
            "refuted",
            f"{FLOW_TWO} read back with offers as {type(raw).__name__}: the editor screen "
            f"would need one more read per flow",
        )
        return
    keys = sorted(offers[0])
    session.finding(claim, "confirmed", f"offers[0] has the keys: {', '.join(keys)}")
    session.finding(
        "A stream offer row carries its own id and created_at, which the tie-break reads",
        "confirmed" if {"id", "created_at"} <= set(keys) else "refuted",
        f"offers[0] has the keys: {', '.join(keys)}",
    )


async def _read_back(session: Session, campaign_id: int, flow_one: dict[str, Any]) -> None:
    """Read the campaign's flows back, which is where questions 3, 5 and 9 are answered."""
    streams = await session.get(f"/campaigns/{campaign_id}/streams", label="w-streams")
    rows = _rows(streams)
    _print_rows(
        rows,
        ("id", "name", "schema", "action_type", "position", "state"),
        limit=session.options.rows_shown,
    )
    first = _named(rows, FLOW_ONE)
    second = _named(rows, FLOW_TWO)
    if first is not None:
        await _action_payload_finding(session, first, flow_one)
        _filter_findings(session, first)
    if second is not None:
        _offer_findings(session, second)


async def probe_create(session: Session) -> None:
    """Rehearse part 1 end to end: a campaign in ADROBOT-TEST with both of its flows."""
    group_id = await _ensure_test_group(session)
    if group_id is None:
        return
    domain = await _pick(session, "/domains", label="w-domains", what="domain")
    source = await _pick(session, "/traffic_sources", label="w-sources", what="traffic source")
    offer_id = await _pick_offer(session)

    references: dict[str, Any] = {}
    if domain is not None:
        references["domain_id"] = domain.get("id")
    if source is not None:
        references["traffic_source_id"] = source.get("id")

    campaign = await _create_probe_campaign(
        session,
        group_id=group_id,
        name=f"{TEST_GROUP} 2.2 {_stamp()}",
        tag="flows",
        extra=references,
    )
    if not campaign.ok:
        print("    the campaign was refused; there is nothing to hang a flow on")
        return
    _campaign_findings(session, campaign)
    campaign_id = _int_field(campaign, "id")
    if campaign_id is None:
        return

    flow_one: dict[str, Any] = {
        "campaign_id": campaign_id,
        "type": "regular",
        "name": FLOW_ONE,
        "position": 1,
        "schema": "action",
        "action_type": session.options.action_type,
        "action_payload": GOOGLE_URL,
        "filters": [{"name": GEO_FILTER, "mode": "accept", "payload": [session.options.geo]}],
    }
    first = await session.post("/streams", label="w-flow-one", payload=flow_one)
    if first.ok:
        session.record_created("stream", _int_field(first, "id"), FLOW_ONE)

    flow_two: dict[str, Any] = {
        "campaign_id": campaign_id,
        "type": "regular",
        "name": FLOW_TWO,
        "position": 2,
        "schema": "landings",
    }
    if offer_id is not None:
        flow_two["offers"] = [{"offer_id": offer_id, "share": 100, "state": "active"}]
    # Sent without `action_type` on purpose: the spec marks it required on POST /streams
    # even for a flow that performs no action. A second, deliberate attempt after a
    # definite refusal is not the retry §3.1 forbids — that one is about a request whose
    # outcome is unknown.
    second = await session.post("/streams", label="w-flow-two", payload=flow_two, may_fail=True)
    claim = "POST /streams demands action_type even from a flow that only rotates offers"
    if second.ok:
        session.finding(
            claim, "refuted", f"a flow with no action_type was accepted ({second.status})"
        )
    else:
        session.finding(
            claim, "confirmed", f"without action_type: {second.status}, so it was resent"
        )
        flow_two["action_type"] = session.options.action_type
        second = await session.post("/streams", label="w-flow-two-retry", payload=flow_two)
    if second.ok:
        session.record_created("stream", _int_field(second, "id"), FLOW_TWO)

    await _read_back(session, campaign_id, flow_one)


async def probe_name_limit(session: Session) -> None:
    """Find the length a campaign name is cut at, before a user's title finds it for us."""
    group_id = await _ensure_test_group(session)
    if group_id is None:
        return
    name = (f"{TEST_GROUP} {_stamp()} " + "x" * LONG_NAME_LENGTH)[:LONG_NAME_LENGTH]
    created = await _create_probe_campaign(session, group_id=group_id, name=name, tag="long-name")
    claim = f"A campaign name of {LONG_NAME_LENGTH} characters is accepted whole"
    if not created.ok:
        session.finding(
            claim,
            "refuted",
            f"POST /campaigns answered {created.status}: the limit is below {LONG_NAME_LENGTH}",
        )
        return
    echoed = _field(created, "name")
    length = len(echoed) if isinstance(echoed, str) else 0
    if length == LONG_NAME_LENGTH:
        session.finding(claim, "confirmed", f"it read back at its full {length} characters")
    else:
        session.finding(
            claim,
            "refuted",
            f"accepted and silently cut to {length} characters: validate at our own boundary",
        )


def _report_rows(observation: Observation) -> list[dict[str, Any]]:
    """Return a report's rows, which arrive under `rows` rather than at the top level."""
    raw = _field(observation, "rows")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _report_body(
    session: Session,
    *,
    dimension: str,
    measures: tuple[str, ...] = (REPORT_MEASURE,),
    dialect: Literal["spec", "clients"] = "spec",
    campaign_id: int | None = None,
) -> dict[str, Any]:
    """Build one /report/build body in either dialect.

    The two differ in two key names and in nothing else, which is what makes comparing
    them worth anything: if one is refused, the names are the reason.
    """
    grouping, metrics = ("dimensions", "measures") if dialect == "spec" else ("grouping", "metrics")
    body: dict[str, Any] = {
        "range": {"interval": "today", "timezone": session.timezone},
        grouping: [dimension],
        metrics: list(measures),
    }
    if campaign_id is not None:
        # FilterRequest — `{name, operator, expression}` — and not the `{name, mode,
        # payload}` a flow filter uses. The two schemas are a word apart in the spec and
        # mixing them up would only show up on the stats screen.
        body["filters"] = [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}]
    return body


async def _pick_campaign(session: Session) -> int | None:
    """Return the campaign a report is scoped to: the one asked for, or the first listed."""
    if session.options.campaign_id is not None:
        return session.options.campaign_id
    listed = await session.get("/campaigns", label="report-campaigns", params={"limit": 1})
    rows = _rows(listed)
    session.finding(
        "GET /campaigns takes offset and limit, where GET /offers takes nothing at all",
        "confirmed" if listed.ok and len(rows) <= 1 else "refuted",
        f"?limit=1 answered {listed.status} and returned {len(rows)}",
    )
    found = rows[0].get("id") if rows else None
    return found if isinstance(found, int) else None


def _dialect_finding(session: Session, spec: Observation, clients: Observation) -> None:
    """Answer question 4, on which the whole statistics adapter rests."""
    claim = "/report/build speaks the spec's dialect: dimensions and measures"
    spec_rows, client_rows = len(_report_rows(spec)), len(_report_rows(clients))
    if spec.ok and not clients.ok:
        session.finding(
            claim,
            "confirmed",
            f"dimensions/measures: {spec.status}; grouping/metrics: {clients.status}",
        )
    elif spec.ok and clients.ok:
        agree = "the same report" if spec_rows == client_rows else "DIFFERENT reports"
        session.finding(
            claim,
            "confirmed",
            f"both were accepted and they are {agree}: {spec_rows} rows against {client_rows}",
        )
    elif clients.ok:
        session.finding(
            claim,
            "refuted",
            f"dimensions/measures: {spec.status}; grouping/metrics: {clients.status} — this "
            f"build wants what the two working clients send, and the spec is wrong",
        )
    else:
        session.finding(claim, "open", f"both were refused: {spec.status} and {clients.status}")


def _envelope_findings(session: Session, report: Observation) -> None:
    """Record the shape stage 8 parses: the envelope, and what one row is made of."""
    body = report.body if isinstance(report.body, dict) else {}
    session.finding(
        "A report answers with an object — rows, total, meta — and not with a bare array",
        "confirmed" if {"rows", "total"} <= set(body) else "refuted",
        f"the body has the keys: {', '.join(sorted(map(str, body))) or 'none'}",
    )
    rows = _report_rows(report)
    claim = "A report row is an object, although the spec types rows as an array of strings"
    if not rows:
        session.finding(
            claim, "open", "the tracker had no clicks today, so there was no row to look at"
        )
        return
    session.finding(claim, "confirmed", f"rows[0] has the keys: {', '.join(sorted(rows[0]))}")


async def _report_query_findings(
    session: Session,
    dialect: Literal["spec", "clients"],
    campaign_id: int | None,
    baseline: int,
) -> None:
    """Try the four things stage 8 needs beyond the dialect, one variable per call."""
    by_offer = await session.post(
        "/report/build",
        label="report-by-offer",
        payload=_report_body(session, dimension=REPORT_DIMENSIONS[1], dialect=dialect),
        may_fail=True,
    )
    session.finding(
        f"{' and '.join(REPORT_DIMENSIONS)} are both usable dimensions, which is the stats screen",
        "confirmed" if by_offer.ok else "refuted",
        f"grouping by {REPORT_DIMENSIONS[1]} answered {by_offer.status}",
    )

    if campaign_id is not None:
        filtered = await session.post(
            "/report/build",
            label="report-filtered",
            payload=_report_body(
                session, dimension=REPORT_DIMENSIONS[0], dialect=dialect, campaign_id=campaign_id
            ),
            may_fail=True,
        )
        session.finding(
            "A report filter is {name, operator, expression} and scopes a report to a campaign",
            "confirmed" if filtered.ok else "refuted",
            f"campaign_id EQUALS {campaign_id} answered {filtered.status} with "
            f"{len(_report_rows(filtered))} rows against {baseline} unfiltered",
        )

    today = datetime.now(ZoneInfo(session.timezone)).date().isoformat()
    explicit = _report_body(session, dimension=REPORT_DIMENSIONS[0], dialect=dialect)
    explicit["range"] = {"from": today, "to": today, "timezone": session.timezone}
    dated = await session.post(
        "/report/build", label="report-explicit-range", payload=explicit, may_fail=True
    )
    session.finding(
        "A range can be given as explicit from/to dates instead of a named interval",
        "confirmed" if dated.ok else "refuted",
        f"from={today} to={today} in {session.timezone} answered {dated.status}",
    )

    wide = await session.post(
        "/report/build",
        label="report-wide-measures",
        payload=_report_body(
            session, dimension=REPORT_DIMENSIONS[0], measures=WIDE_MEASURES, dialect=dialect
        ),
        may_fail=True,
    )
    session.finding(
        f"This build knows every measure the screen might want: {', '.join(WIDE_MEASURES)}",
        "confirmed" if wide.ok else "refuted",
        f"the wide list answered {wide.status}"
        + ("" if wide.ok else "; the dump holds the body naming the column it rejected"),
    )

    sorted_body = _report_body(session, dimension=REPORT_DIMENSIONS[0], dialect=dialect)
    sorted_body["sort"] = [{"name": REPORT_MEASURE, "order": "DESC"}]
    ordered = await session.post(
        "/report/build", label="report-sorted", payload=sorted_body, may_fail=True
    )
    session.finding(
        "A report takes sort as {name, order}, so the screen does not have to sort itself",
        "confirmed" if ordered.ok else "refuted",
        f"sorting by {REPORT_MEASURE} DESC answered {ordered.status}",
    )


async def probe_report(session: Session) -> None:
    """Settle which dialect /report/build speaks, and what one row of a report looks like.

    A POST among the read-only probes, because it creates nothing: the report builder is a
    query that happens to carry a body. Every call is scoped to today, so it is also a
    cheap question to ask of somebody's live tracker.
    """
    campaign_id = await _pick_campaign(session)

    # The two dialects first, on the smallest body either of them accepts and with no
    # filter: whatever else is wrong, it cannot be what makes one of these fail.
    spec = await session.post(
        "/report/build",
        label="report-spec-dialect",
        payload=_report_body(session, dimension=REPORT_DIMENSIONS[0]),
        may_fail=True,
    )
    clients = await session.post(
        "/report/build",
        label="report-client-dialect",
        payload=_report_body(session, dimension=REPORT_DIMENSIONS[0], dialect="clients"),
        may_fail=True,
    )
    _dialect_finding(session, spec, clients)

    dialect: Literal["spec", "clients"] = "spec" if spec.ok else "clients"
    working = spec if spec.ok else clients
    if not working.ok:
        print("    neither dialect was accepted; the rest of this probe has nothing to stand on")
        return
    rows = _report_rows(working)
    print(f"    dialect: {dialect}, {len(rows)} rows today")
    _print_rows(rows, (REPORT_DIMENSIONS[0], REPORT_MEASURE), limit=session.options.rows_shown)
    _envelope_findings(session, working)
    await _report_query_findings(session, dialect, campaign_id, len(rows))


def _offer_rows(observation: Observation) -> dict[int, dict[str, Any]]:
    """Return a flow's offers keyed by offer_id — the key the mirror uses, not the row id."""
    raw = _field(observation, "offers")
    rows = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return {row["offer_id"]: row for row in rows if isinstance(row.get("offer_id"), int)}


async def _pick_offers(session: Session, count: int) -> list[int]:
    """Return several usable offer ids: this probe needs a row it can afford to drop."""
    listed = await session.get("/offers", label="w-offers-many")
    ids = [
        row["id"]
        for row in _rows(listed)
        if isinstance(row.get("id"), int) and row.get("state") in (None, "active")
    ]
    chosen = ids[:count]
    print(f"    offers: {chosen}")
    return chosen


OFFER_COLUMNS: Final = ("id", "offer_id", "share", "state", "created_at")


def _survivor_findings(
    session: Session,
    before: dict[int, dict[str, Any]],
    after: dict[int, dict[str, Any]],
    dropped: int,
) -> None:
    """Answer questions 1 and 2 out of the same pair of reads."""
    claim = "PUT /streams/{id} with a shorter offers[] deletes the rows the body leaves out"
    if dropped not in after:
        session.finding(claim, "confirmed", f"offer {dropped} was gone after a PUT that omitted it")
    else:
        row = after[dropped]
        session.finding(
            claim,
            "refuted",
            f"offer {dropped} survived as share={row.get('share')} state={row.get('state')}: "
            f"the array is merged, so a removal has to travel as a disabled row",
        )

    survivors = [offer_id for offer_id in before if offer_id != dropped and offer_id in after]
    if not survivors:
        return
    kept_id = [one for one in survivors if before[one].get("id") == after[one].get("id")]
    session.finding(
        "A row that survives a PUT keeps its own stream_offer id",
        "confirmed" if len(kept_id) == len(survivors) else "refuted",
        f"{len(kept_id)} of {len(survivors)} kept it: "
        f"{[before[one].get('id') for one in survivors]} became "
        f"{[after[one].get('id') for one in survivors]}",
    )
    kept_birth = [
        one for one in survivors if before[one].get("created_at") == after[one].get("created_at")
    ]
    session.finding(
        "A row that survives a PUT keeps its created_at, which the tie-break rule orders by",
        "confirmed" if len(kept_birth) == len(survivors) else "refuted",
        f"{len(kept_birth)} of {len(survivors)} kept it; a created_at the tracker resets on "
        f"every push would leave our own mirror the only usable order",
    )


def _share_findings(
    session: Session, after: dict[int, dict[str, Any]], expected: dict[int, int]
) -> None:
    """Check the invariant that what Keitaro returns is never normalised, against Keitaro."""
    read_back = {offer_id: row.get("share") for offer_id, row in after.items()}
    total = sum(value for value in read_back.values() if isinstance(value, int))
    session.finding(
        "Keitaro keeps the shares it was sent and does not normalise them back to 100",
        "confirmed" if all(read_back.get(one) == expected[one] for one in expected) else "refuted",
        f"sent {expected}, read back {read_back}, summing to {total}",
    )


async def _disabled_row_finding(
    session: Session,
    stream_id: int,
    body: dict[str, Any],
    rows: list[dict[str, Any]],
    dropped: int,
) -> None:
    """Try the shape a push sends a removed row in: `{share: 0, state: disabled}`.

    PLAN-01 §4 asserts this is right whether the array is replaced or merged, and it is
    the one claim there that only a live tracker can settle. The whole object goes out,
    the removed row among it, which is what 6.6 will do.
    """
    claim = "A removed offer can be pushed as {offer_id, share: 0, state: disabled}"
    kept = [row for row in rows if row["offer_id"] != dropped]
    payload = {**body, "offers": [*kept, {"offer_id": dropped, "share": 0, "state": "disabled"}]}
    sent = await session.put(f"/streams/{stream_id}", label="w-put-disabled", payload=payload)
    if not sent.ok:
        session.finding(claim, "refuted", f"the PUT answered {sent.status}")
        return
    read = await session.get(f"/streams/{stream_id}", label="w-put-disabled-after", listing=False)
    row = _offer_rows(read).get(dropped)
    if row is None:
        session.finding(
            claim,
            "confirmed",
            "the tracker kept no row at all, which is also an offer that receives nothing",
        )
    elif row.get("state") == "disabled" and row.get("share") == 0:
        session.finding(claim, "confirmed", f"offer {dropped} reads back disabled at share 0")
    else:
        session.finding(
            claim,
            "refuted",
            f"offer {dropped} reads back share={row.get('share')} state={row.get('state')}",
        )


async def _partial_put_finding(
    session: Session, stream_id: int, campaign_id: int, before: Observation
) -> None:
    """Ask directly whether a PUT is a replacement or a patch. Last: it can break the flow.

    §3.1 resends `action_type` and `schema` on every update because nothing promises the
    call is partial. This is that question asked of the tracker instead of assumed: a body
    with nothing but `campaign_id` and `offers`, and then a look at what is left.
    """
    claim = "PUT /streams/{id} replaces the whole flow: a field the body omits is lost"
    stripped = await session.put(
        f"/streams/{stream_id}",
        label="w-put-partial",
        payload={"campaign_id": campaign_id, "offers": []},
        may_fail=True,
    )
    if not stripped.ok:
        session.finding(
            claim,
            "open",
            f"a body carrying only campaign_id and offers was refused with {stripped.status}, "
            f"so every field has to be resent either way",
        )
        return
    after = await session.get(f"/streams/{stream_id}", label="w-put-partial-after", listing=False)
    survived = [
        field
        for field in ("name", "type", "schema", "action_type")
        if _field(after, field) == _field(before, field)
    ]
    session.finding(
        claim,
        "confirmed" if not survived else "refuted",
        f"after a body naming none of them, these still match the original: "
        f"{', '.join(survived) or 'none of them'}",
    )


async def probe_put_semantics(session: Session) -> None:
    """Settle the key question: what PUT /streams/{id} does with a shortened offers[]."""
    group_id = await _ensure_test_group(session)
    if group_id is None:
        return
    offers = await _pick_offers(session, len(PUT_SHARES))
    if len(offers) < 2:  # noqa: PLR2004 — one offer cannot be both kept and dropped
        print("    this probe needs at least two offers in the catalogue")
        return
    campaign = await _create_probe_campaign(
        session, group_id=group_id, name=f"{TEST_GROUP} 2.3 {_stamp()}", tag="put"
    )
    campaign_id = _int_field(campaign, "id")
    if campaign_id is None:
        return

    rows: list[dict[str, Any]] = [
        {"offer_id": offer_id, "share": share, "state": "active"}
        for offer_id, share in zip(offers, PUT_SHARES, strict=False)
    ]
    body: dict[str, Any] = {
        "campaign_id": campaign_id,
        "type": "regular",
        "name": PUT_FLOW,
        "position": 1,
        "schema": "landings",
        "action_type": session.options.action_type,
        "offers": rows,
    }
    created = await session.post("/streams", label="w-put-create", payload=body)
    stream_id = _int_field(created, "id")
    if stream_id is None:
        print("    the flow was not created; there is nothing to PUT against")
        return
    session.record_created("stream", stream_id, PUT_FLOW)

    before = await session.get(f"/streams/{stream_id}", label="w-put-before", listing=False)
    rows_before = _offer_rows(before)
    _print_rows(list(rows_before.values()), OFFER_COLUMNS, limit=session.options.rows_shown)

    # The whole object again, one row shorter: only offers[] differs between the create
    # and this call, so whatever changes is the answer and nothing else can be blamed.
    dropped = offers[len(rows) - 1]
    shortened = await session.put(
        f"/streams/{stream_id}",
        label="w-put-shortened",
        payload={**body, "offers": rows[:-1]},
    )
    if not shortened.ok:
        session.finding(
            "PUT /streams/{id} accepts the body that created the flow, one offer shorter",
            "refuted",
            f"it answered {shortened.status}; questions 1 and 2 stay open",
        )
        return
    after = await session.get(f"/streams/{stream_id}", label="w-put-after", listing=False)
    rows_after = _offer_rows(after)
    _print_rows(list(rows_after.values()), OFFER_COLUMNS, limit=session.options.rows_shown)

    _survivor_findings(session, rows_before, rows_after, dropped)
    _share_findings(session, rows_after, {row["offer_id"]: row["share"] for row in rows[:-1]})
    await _disabled_row_finding(session, stream_id, body, rows, dropped)
    await _partial_put_finding(session, stream_id, campaign_id, before)


PROBES: Final = {
    "groups": probe_groups,
    "sources": probe_sources,
    "domains": probe_domains,
    "offers": probe_offers,
    "catalogues": probe_catalogues,
    "report": probe_report,
}

# Apart from PROBES and never in the default set: a bare `kt_probe.py` must not create
# anything on somebody's tracker. Naming one is the whole gate. A prompt would make the
# script unusable from the Makefile, and a --write flag on top of a name typed on purpose
# is ceremony rather than a second opinion.
WRITE_PROBES: Final = {
    "create": probe_create,
    "put-semantics": probe_put_semantics,
    "name-limit": probe_name_limit,
}
ALL_PROBES: Final = {**PROBES, **WRITE_PROBES}


def _summary(probe: object) -> str:
    """Return a probe's one-line docstring, which is also its line in --help."""
    return (probe.__doc__ or "").splitlines()[0]


def _close_out(session: Session) -> None:
    """Record the two findings that are about the run as a whole rather than one endpoint."""
    if session.listings:
        session.finding(
            "Every list endpoint answers with a bare JSON array: no envelope, no pagination "
            "metadata",
            "confirmed" if session.arrays == session.listings else "refuted",
            f"{session.arrays} of {session.listings} successful list reads were top-level arrays",
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


async def _run(settings: Settings, names: tuple[str, ...], options: Options) -> int:
    run_dir = SCRATCH_ROOT / _stamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"tracker: {settings.keitaro_base_url}")
    print(f"dumps:   {run_dir}")
    writing = [name for name in names if name in WRITE_PROBES]
    if writing:
        print(f"writing: {', '.join(writing)} — what they create is named {TEST_GROUP}*, goes")
        print(f"         into the {TEST_GROUP} group, and is listed in {LEDGER} for 2.7")

    async with httpx.AsyncClient(
        base_url=str(settings.keitaro_base_url),
        # Unwrapped straight into the header and never bound to a name, the rule
        # AGENTS.md states for the service: a traceback renderer that captures locals
        # prints what a local holds.
        headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
        timeout=TIMEOUT,
        follow_redirects=False,
    ) as client:
        session = Session(client, run_dir, options, timezone=settings.keitaro_timezone)
        # Sequentially, and without the service's semaphore: this points at somebody's
        # working tracker, and the order of the output is what makes it readable.
        for name in names:
            probe = ALL_PROBES[name]
            print(f"\n== {name}: {_summary(probe)}")
            try:
                await probe(session)
            except httpx.HTTPError as exc:
                # One unreachable endpoint must not cost the findings of the others.
                session.failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"  ! {type(exc).__name__}: {exc}")

    _close_out(session)
    _print_findings(session)
    asked = f", {session.expected_failures} refused as asked" if session.expected_failures else ""
    print(
        f"\n{session.calls} calls, {len(session.failures)} failed{asked}. "
        f"{session.write_findings()}"
    )
    for failure in session.failures:
        print(f"  ! {failure}")
    return 1 if session.failures else 0


def _parse_args(argv: Sequence[str] | None) -> tuple[tuple[str, ...], Options]:
    reading = "\n".join(f"  {name:14s} {_summary(probe)}" for name, probe in PROBES.items())
    writing = "\n".join(f"  {name:14s} {_summary(probe)}" for name, probe in WRITE_PROBES.items())
    parser = argparse.ArgumentParser(
        prog="kt_probe.py",
        description="Reconnaissance against a live Keitaro tracker (stage 2).",
        epilog=(
            f"read-only probes, run when none is named:\n{reading}\n\n"
            f"writing probes, run only when named:\n{writing}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("probes", nargs="*", metavar="PROBE", help="which probes to run")
    parser.add_argument(
        "--rows",
        type=int,
        default=ROWS_SHOWN,
        help=f"rows printed per table; the dump always holds all of them (default {ROWS_SHOWN})",
    )
    parser.add_argument(
        "--geo",
        default=DEFAULT_GEO,
        help=f"country code for the geo flow the writing probe builds (default {DEFAULT_GEO})",
    )
    parser.add_argument(
        "--offer-id",
        type=int,
        default=None,
        help="offer for flow 2; the first active offer in the catalogue by default",
    )
    parser.add_argument(
        "--campaign-id",
        type=int,
        default=None,
        help="campaign the report probe scopes to; the first one listed by default",
    )
    parser.add_argument(
        "--action-type",
        default=DEFAULT_ACTION_TYPE,
        help=f"action key for the redirect flow, from /streams_actions "
        f"(default {DEFAULT_ACTION_TYPE})",
    )
    args = parser.parse_args(argv)
    names: tuple[str, ...] = tuple(args.probes) or tuple(PROBES)
    unknown = [name for name in names if name not in ALL_PROBES]
    if unknown:
        parser.error(f"unknown probe: {', '.join(unknown)}. Known: {', '.join(ALL_PROBES)}")
    options = Options(
        rows_shown=int(args.rows),
        geo=str(args.geo).strip(),
        offer_id=int(args.offer_id) if args.offer_id is not None else None,
        action_type=str(args.action_type),
        campaign_id=int(args.campaign_id) if args.campaign_id is not None else None,
    )
    return names, options


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected probes and return the exit code: 1 if any call failed, 2 if unconfigured."""
    names, options = _parse_args(argv)
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
    return asyncio.run(_run(settings, names, options))


if __name__ == "__main__":
    raise SystemExit(main())
