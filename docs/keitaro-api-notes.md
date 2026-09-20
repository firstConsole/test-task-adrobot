# Keitaro Admin API: what the schema says, and what the tracker does

AD Robot is a wrapper around somebody else's API, and the expensive mistakes in a wrapper
come from believing a document. Two sources are believed here, and no row ever mixes them:

* **The published schema**, `docs/keitaro-openapi.json` — 87 paths, 69 schemas, downloaded
  and kept in the repository. Every claim in §1 and §2 is asserted by
  `backend/tests/test_keitaro_spec.py`, so a newer download that changes one of them turns
  the build red instead of ageing quietly in this file.
* **The tracker itself**, asked by `backend/scripts/kt_probe.py`. §3 is that script's own
  output: it prints each finding as a markdown row — claim, verdict, evidence — and those
  rows are pasted here rather than retyped.

A claim the schema settles is not re-asked of the tracker, and a claim about behaviour is
never answered from the schema. That division is the whole point of the file.

## 1. What the schema settles

Code may be written against these without guessing.

| Claim | Where in the schema |
|---|---|
| The base path is `/admin_api/v1`, and the key travels in an `Api-Key` header | `servers`, `components.securitySchemes.ApiKeyAuth` |
| Creating a campaign, a flow or a group answers **200**, never 201 | `responses` of `POST /campaigns`, `/streams`, `/groups` |
| Those three creates declare **406** for a validation failure, next to 400/401/402/500 | same |
| A flow is updated with **`PUT /streams/{id}`**; there is no `POST /streams/{id}` | `paths./streams/{id}` |
| `PUT /streams/{id}` declares **404 and no 406** — the opposite of the create | `paths./streams/{id}.put.responses` |
| **`GET /offers` takes no query parameters at all** — no search, no paging | `paths./offers.get.parameters` is absent |
| `GET /campaigns` does take `offset` and `limit` | `paths./campaigns.get.parameters` |
| `POST /campaigns` requires exactly `alias` and `name` | `CampaignCreateRequired.required` |
| `cost_type` is narrower on write (`CPC`/`CPUC`/`CPM`) than on read (8 values) | `CampaignRequest` vs `Campaign` |
| `group_id` is a **string on write and an integer on read** | `CampaignRequest` vs `Campaign` |
| A campaign object carries `token` — the Click API token | `Campaign.token` |
| A flow filter is `{name, mode: accept\|reject, payload: string[]}`, `name` and `mode` required | `FilterStreamRequest` |
| An offer inside a flow is `{offer_id, share, state: active\|disabled}`, `offer_id` and `share` required | `OfferStreamRequest` |
| **`share` is an integer** on write and on read | `OfferStreamRequest`, `OfferStream` |
| A stream offer row has its own `id`, `stream_id`, `created_at` and `updated_at` | `OfferStream` |
| A flow's `schema` is `landings`/`redirect`/`action`; its `type` is `regular`/`forced`/`default` | `StreamObject` |
| `POST /streams` requires `campaign_id`, `schema`, `type`, `name`, `action_type` | `StreamRequest.allOf[1].required` |
| The action catalogue is **`/streams_actions`**, with the `s` | `paths` |
| `GET /stream_filters` is a catalogue of filter *types* (`value`, `tooltip`, `modes`, `group`) — it neither reads nor writes a flow's filters | `StreamFilter` |
| A report is `POST /report/build` with `{range, dimensions, measures, filters, sort}` | `ReportsRequest` |
| A range is `{from, to, timezone, interval}` | `RangeRequest` |
| A **report** filter is `{name, operator, expression}`, `name` and `operator` required | `FilterRequest` |
| An offer has `preview_path`, `local_path` and `group_id` | `Offer` |

**`share` being an integer is the single most useful fact in this table.** There are no
fractional shares in this API, which is what makes `divmod` the right tool in
`domain/shares.py` rather than an approximation of one.

## 2. Where the schema is wrong, contradictory, or silent

Each of these is a defect in the published document, and each one is a bug in the making
for anyone who reads it literally.

| What the schema says | Why it is a trap | What this project does |
|---|---|---|
| `action_payload` is in the flow **response** and in no request schema | A redirect flow is nothing but its payload: part 1's first flow sends traffic to Google | Send it on the create anyway, and check the read-back (§3, `create`) |
| `StreamRequestPut` is a bare `$ref` to `StreamObject` with **no required fields**, while `StreamRequest` requires five | Nothing promises that a PUT is partial, and the two schemas disagree about the same object | Every PUT resends `action_type` and `schema`, whatever else it carries |
| `Filter.payload` (read) is typed `string`; `FilterStreamRequest.payload` (write) is `string[]` | A mapper written from one of them breaks on the other | `infrastructure/keitaro/schemas.py` accepts both shapes on read |
| `FilterRequest` and `FilterStreamRequest` are different schemas with similar names | A report filter is `{name, operator, expression}`, a flow filter is `{name, mode, payload}`; confusing them surfaces only on the statistics screen | Two separate wire models, never shared |
| `Report.rows` is typed as an array of **strings** | Report rows are objects; a generated client would be useless | The report shape is taken from §3, not from the schema |
| `DELETE /campaigns/{id}` is **"Move campaign to archive"**, answers **201**, and declares its body as `Domain` | It is not a delete, its success code is unlike every other endpoint's, and its declared type belongs to another resource | Cleanup archives and then empties the archive; see §5 |
| `GET /groups` marks `type` as `required: true` **and** gives it `default: campaigns` | Generated clients will disagree about whether it may be omitted | Always sent explicitly |
| `GET /offers` has no parameters at all | There is no server-side search to build an autocomplete on | The offer catalogue is mirrored locally and searched there |
| Two published clients call the action catalogue `/stream_actions`, the schema calls it `/streams_actions` | One of them is writing against a path that does not exist | Settled by the `catalogues` probe (§3) |

## 3. What only the tracker can settle

> **No verdict in this section has been filled in yet: the probe has not been run against a
> live tracker.** `kt_probe.py` prints exactly these rows, with a verdict and the request
> that produced it; replace each table below with the block it prints. Until then every row
> reads `pending`, which is not the same as "true".

### `groups`, `offers`, `catalogues` — the reference data (read-only)

| Claim | Verdict | Evidence |
|---|---|---|
| GET /groups rejects a request without the `type` parameter | pending | |
| GET /offers takes no query parameters, so the catalogue has to be mirrored locally | pending | |
| A full GET /offers fits inside the service's 10 s read timeout | pending | |
| The action catalogue is /streams_actions; /stream_actions does not exist | pending | |
| The filter a geo flow is built with is named 'country' | pending | |
| Every list endpoint answers with a bare JSON array: no envelope, no pagination metadata | pending | |
| The Admin API answers directly, without redirecting | pending | |

### `report` — the statistics dialect (read-only)

| Claim | Verdict | Evidence |
|---|---|---|
| /report/build speaks the spec's dialect: dimensions and measures | pending | |
| A report answers with an object — rows, total, meta — and not with a bare array | pending | |
| A report row is an object, although the spec types rows as an array of strings | pending | |
| stream_id and offer_id are both usable dimensions, which is the stats screen | pending | |
| A report filter is {name, operator, expression} and scopes a report to a campaign | pending | |
| A range can be given as explicit from/to dates instead of a named interval | pending | |
| This build knows every measure the screen might want: clicks, campaign_unique_clicks, conversions, sales, revenue | pending | |
| A report takes sort as {name, order}, so the screen does not have to sort itself | pending | |
| GET /campaigns takes offset and limit, where GET /offers takes nothing at all | pending | |

### `settings` — version, zone and clock (read-only)

| Claim | Verdict | Evidence |
|---|---|---|
| GET /settings answers, although the published spec has no such path | pending | |
| GET /settings names the build, which is what decides whether click_id is event_id | pending | |
| The tracker's own zone is the one ADROBOT_KEITARO_TIMEZONE is set to | pending | |
| The tracker's clock and this machine's agree to within a minute | pending | |

### `create` — part 1, rehearsed (writes)

| Claim | Verdict | Evidence |
|---|---|---|
| Creating a campaign answers 200, not 201 | pending | |
| A campaign takes group_id as a string and reads it back as an integer | pending | |
| A created campaign carries the Click API token, so no 2xx body may be logged | pending | |
| The tracker writes its timestamps in the zone ADROBOT_KEITARO_TIMEZONE names | pending | |
| POST /streams demands action_type even from a flow that only rotates offers | pending | |
| POST /streams stores action_payload, although no request schema declares it | pending | |
| PUT /streams/{id} sets the action_payload that POST dropped | pending | |
| A 'country' filter sent with the create survives it | pending | |
| A stream filter's payload comes back in the shape it was sent, an array | pending | |
| Keitaro keeps a geo filter's payload in the case it was sent | pending | |
| GET /campaigns/{id}/streams nests each flow's offers as whole objects | pending | |
| A stream offer row carries its own id and created_at, which the tie-break reads | pending | |

### `put-semantics` — the key question (writes)

| Claim | Verdict | Evidence |
|---|---|---|
| PUT /streams/{id} accepts the body that created the flow, one offer shorter | pending | |
| PUT /streams/{id} with a shorter offers[] deletes the rows the body leaves out | pending | |
| A row that survives a PUT keeps its own stream_offer id | pending | |
| A row that survives a PUT keeps its created_at, which the tie-break rule orders by | pending | |
| Keitaro keeps the shares it was sent and does not normalise them back to 100 | pending | |
| A removed offer can be pushed as {offer_id, share: 0, state: disabled} | pending | |
| PUT /streams/{id} replaces the whole flow: a field the body omits is lost | pending | |

### `name-limit` (writes)

| Claim | Verdict | Evidence |
|---|---|---|
| A campaign name of 200 characters is accepted whole | pending | |

### `cleanup` — taking it back (writes)

| Claim | Verdict | Evidence |
|---|---|---|
| DELETE /streams/{id} answers 200 and takes the flow out of its campaign | pending | |
| DELETE /campaigns/{id} archives a campaign and answers 201, alone in this API | pending | |
| The test group can be deleted once the campaigns inside it are archived | pending | |

## 4. What hangs on which answer

Four of the rows above change the design rather than a line of code. They are the reason
this reconnaissance happens before the adapter and not after it.

* **`PUT` with a shorter `offers[]`.** If the array is *replaced*, removing an offer is a
  short body. If it is *merged*, a short body silently leaves the offer in place and the
  shares climb past 100 — so a removal has to travel as `{offer_id, share: 0, state:
  "disabled"}`. The push sends that shape either way, which is correct in both worlds; the
  verdict decides whether it is necessary or merely careful.
* **Whether a surviving row keeps its `created_at`.** The rounding remainder goes to the
  most recently activated row, and for rows read from the tracker that order is
  `stream_offer.created_at`. A tracker that recreates rows on every push resets that
  order — and our own mirror becomes the only place the real one exists.
* **`action_payload` on `POST /streams`.** If the create drops it, part 1 is a create
  followed by an update, and every failure path in it doubles.
* **The report dialect.** `dimensions`/`measures` or `grouping`/`metrics` decides the
  statistics adapter, and a wrong guess fails at the point where the numbers are read.

## 5. Running the probe

The reading probes are safe against a live tracker: they are GETs, plus the report
builder, which is a query with a body. The writing probes are not in the default set and
have to be named.

```sh
cp .env.example .env          # then the real ADROBOT_KEITARO_BASE_URL and _API_KEY
make probe                    # every read-only probe
make probe P="catalogues"     # confirm action_type and the geo filter name first
make probe P="create"         # part 1, rehearsed
make probe P="put-semantics"  # the key question
make probe P="name-limit"
make probe P="cleanup"        # take it all back
```

Everything a writing probe creates is named `ADROBOT-TEST*`, belongs to the `ADROBOT-TEST`
campaign group, and is appended to `.scratch/kt-probe/created.json` as it is created. Raw
request and response bodies are dumped under `.scratch/kt-probe/<run>/`, which
`.gitignore` covers because a campaign object carries a Click API token; what reaches the
terminal has been through the service's own redactor.

`cleanup` takes it back from the ledger and from nothing else: flows first, then
campaigns, then the group. A flow is removed only after being read and found on a campaign
the ledger itself created, and a campaign only if its recorded name begins with
`ADROBOT-TEST` — a ledger is a file, and a file can be edited by hand. Each removal is
written back as it happens, so the probe is safe to interrupt and safe to re-run; anything
it refuses stays in the ledger with the reason beside it.

What it will not do is empty the archive. Per §2 a delete here is an archive, and the only
way to empty one is `POST /campaigns/clean_archive`, which empties **all** of it —
campaigns this project never touched included. That is an operator's decision, made in the
tracker, and the script does not make it.
