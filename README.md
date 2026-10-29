# pingone-protect-feedback-upload

Feedback API v2 Bulk Upload CLI — a standalone script for bulk-uploading
PingOne Protect risk feedback labels.

**Contents:** [Why this exists](#why-this-exists) ·
[Profiles and environments](#profiles-and-environments) ·
[Requirements](#requirements) · [Run guide](#run-guide) ·
[Example files](#example-files) · [Reading the report](#reading-the-report) ·
[Fix and re-submit: --errors-out](#fix-and-resubmit---errors-out) ·
[Input format reference](#input-format-reference) ·
[Troubleshooting](#troubleshooting) · [Development](#development) ·
[License](#license)

## Why this exists

PingOne Protect's Feedback API v2 accepts at most **20 labels per bulk
request**, returns **per-item errors inside a 200 response**, and the POST
is **not idempotent** — re-sending a file after a partial failure
duplicates successful labels. The bulk workflow logic lives here:

- reads CSV or NDJSON label files (see the [input format
  reference](#input-format-reference))
- validates every row locally before anything is sent (enums, timestamp
  format `YYYY-MM-DDThh:mm:ss.SSSZ`, length limits, TIME_RANGE rules) —
  the API remains authoritative
- groups valid rows into batches of at most 20
- invokes `pingcli pingone api` per batch — a raw bulk POST with the
  Feedback API v2 media type, with authentication, profiles, and region
  routing supplied by pingcli (no changes to any pingcli repo required;
  works with today's shipped Ping CLI)
- applies the retry matrix: 429 → pause and retry; 500/transport → up to
  3 attempts; 401/403 → abort; 4xx → skip the batch and log; 200 with
  per-item failures → never retried
- prints a terminal report (text or JSON) mapping every rejected row back
  to its input line, and can write rejected rows to a fix-and-resubmit file

## Profiles and environments

Two concepts explain every flag you will (rarely) need:

**PingOne environment** — your isolated workspace inside the PingOne
platform. It has a UUID and is the container for everything: users,
applications, Protect risk policies, and the risk feedback labels this
tool uploads. Every API call is scoped to one environment — that's why the
bulk endpoint's URL itself is `POST /environments/{envId}/riskFeedback`.

**pingcli profile** — a named, local configuration on your machine
(`~/.pingcli/config.yaml`) that says **how to authenticate** and **where
to point**:

| What | Example |
|---|---|
| Worker app credentials | client ID + secret (stored in the OS keychain) |
| The environment it's scoped to | `service.pingOne.endpoint.environmentID` |
| API region routing | `rootDomain` (e.g. `pingone.com`) |

One profile ↔ one environment: `pingcli pingone init` wires the worker
app and its environment together into the profile. So the profile already
"knows" the environment, and the tool exploits that.

**Resolution order** for the target environment:

1. `--environment-id` given → use it verbatim (explicit override)
2. no flag → read it from the profile (`pingcli config get
   service.pingOne.endpoint.environmentID`, with `-P <profile>` if given,
   otherwise the active profile)
3. neither → exit 3 with a message naming both remedies

That's why the simplest command is just `protect-feedback-upload upload
labels.csv` — credentials and environment both come from the active
profile.

**When to pass them explicitly:**

- **Multiple environments**: a `demo` and a `prod` profile. Push the same
  file to each with `-P demo` then `-P prod` — the profile selects the
  destination.
- **Cross-environment writes**: a worker's grants may span environments;
  authenticate via profile A and write to environment B with
  `--environment-id <B> -P A`. The one case where the flag is required.
- **Profile without an environment** (e.g. a bare `default`): pass
  `--environment-id` explicitly; auth still comes from the profile.

⚠️ The override is powerful: nothing stops `-P demo --environment-id
<prod-env>` from writing test labels into production if the worker's
grants allow it. Rule of thumb: **let the profile decide the environment**.

## Requirements

- Python 3.10+ (`python3 --version`)
- [Ping CLI](https://developer.pingidentity.com/pingcli/1.10/install/macos.html)
  (installation guides for all platforms), authenticated with
  `pingcli pingone init` — a one-time interactive wizard; type it alone,
  nothing after it
- a labels file (see the [input format reference](#input-format-reference))

```bash
pip install git+https://github.com/pingidentity/pingone-protect-feedback-upload.git
```

(or `pip install .` from a local copy of this repository — zero runtime
dependencies beyond Python itself)

Check: `protect-feedback-upload --version`

## Run guide

**Always start with a dry run — it validates and batches, sends nothing.**
The commands use the shipped example file; swap in your own labels file
when ready:

```bash
protect-feedback-upload upload examples/labels-demo.csv --dry-run
```

Fix anything the dry run rejects, then upload for real:

```bash
protect-feedback-upload upload examples/labels-demo.csv
```

To watch local validation in action, run the invalid example — every row
is rejected with its line number and reason, and nothing is sent:

```bash
protect-feedback-upload upload examples/labels-demo-invalid.csv
```

The environment ID comes from your pingcli profile, so you don't pass it.
If the profile's environment isn't the target — or your profile has none
configured — pass it explicitly:

```bash
protect-feedback-upload upload labels.csv --environment-id <uuid> -P <profile>
```

**Useful flags**

| Flag | Meaning |
|---|---|
| `--dry-run` | Validate + show the batch plan, send nothing |
| `-P <profile>` | Which pingcli profile to authenticate with (omit for the active one) |
| `--environment-id <uuid>` | Target environment (optional — resolved from the profile when omitted) |
| `--errors-out <file>` | Write all rejected rows to a file, ready to fix and re-submit (see [below](#fix-and-resubmit---errors-out)) |
| `-O json` | Machine-readable report (same data as the text report) |
| `--batch-size 1-20` | Items per request (default 20) |

## Example files

Ready-to-run inputs ship in [examples/](examples/). Column and field rules
are in the [input format reference](#input-format-reference).

| File | Rows | What it demonstrates |
|---|---|---|
| `labels-demo.csv` | 4 valid (CSV) | All three verdicts, four subject/trigger combinations, a successful upload |
| `labels-demo.ndjson` | 3 valid (NDJSON) | The JSON input format, including a TIME_RANGE subject (no `subject.value`) |
| `labels-demo-invalid.csv` | 5 invalid | Local validation: every row is rejected with its line number and reason — nothing is sent |
| `labels-sample.csv` | 5 valid (CSV) | A starting point for your own file, including an `attributes` cell |

The example files use recognizable `subject_value` values (`TXN-DEMO-*`,
`NDJSON-DEMO-*`, and the documentation-reserved IP `198.51.100.78`) so
test uploads are easy to identify later.

## Reading the report

```text
Upload complete: 3 requested, 3 successful, 0 failed, 0 skipped
  batch 1: 3 items, HTTP 200, 3 successful, 0 failed
  SKIPPED item 4 (line 5) field 'verdict': verdict 'NOPE' must be one of FRAUD, SUSPECTED_FRAUD, LEGITIMATE
```

- `Upload complete` — the aggregate: how many rows were requested and what
  happened to each.
- `batch N` — one line per HTTP request (a 25-row file becomes 20+5).
- `SKIPPED item N (line M)` — a row that was **not sent**, with its line in
  your file and the reason. Fix those rows and re-submit **only them**.
- `FAILED item N (line M)` — a row the **API** rejected inside a 200
  response; terminal, never retried.

Exit codes: `0` all rows uploaded · `1` some rows failed or skipped ·
`2` aborted (authentication/transport) · `3` input error.

`-O json` gives the same report as structured data (`summary`,
`batches[]`, `failedItems[]`, `skippedItems[]`).

## Fix and re-submit: --errors-out

The bulk POST is **not idempotent** — re-uploading the original file after
a partial success duplicates the labels that already landed. After fixing
rejected rows, submit only those rows.

`--errors-out` makes that easy: it writes every rejected row (local
validation skips and API per-item failures) to a file in the same format
as the input, with the error reason alongside the data:

```bash
protect-feedback-upload upload labels.csv --errors-out fix-these.csv
```

The rejected rows keep their original data in the standard columns — fix
them in place — plus diagnostic columns (`input_index`, `source_line`,
`error_field`, `error_message`) that the tool ignores when you re-run the
fixed file:

```bash
protect-feedback-upload upload fix-these.csv
```

Never re-send the original file: only the rows that failed.

## Input format reference

Accepted formats:

| Format | Detection | Shape |
|---|---|---|
| **CSV** | `.csv` extension, or content sniffing (first non-space byte not `{`) | RFC 4180: first record is the header; column order is free; every record must carry every column (empty optional fields kept, trailing commas required). Fields quoted only when necessary; embedded quotes doubled. |
| **NDJSON** | `.ndjson`/`.jsonl` extension, or first non-space byte is `{` | One JSON object per line, camelCase keys. |

`-` reads stdin; stdin content is always sniffed (never assumed by
extension).

### CSV columns

First record is the header with these column names (order free, all
required in the header even when a column is empty throughout):

| Column | Maps to | Required | Rules |
|---|---|---|---|
| `subject_type` | `subject.type` | ✅ | Enum — see below |
| `subject_value` | `subject.value` | ✅¹ | Format depends on type; omitted for TIME_RANGE |
| `verdict` | `verdict` | ✅ | Enum |
| `occurred_from` | `occurredFrom` | ✅ | Timestamp |
| `occurred_to` | `occurredTo` | ² | Timestamp; ≤ 3 days after `occurred_from` |
| `trigger` | `trigger` | — | Enum |
| `flow_type` | `flowType` | — | Enum |
| `confirmed_at` | `confirmedAt` | — | Timestamp |
| `reason` | `reason` | — | ≤ 200 chars |
| `analyst` | `analyst` | — | ≤ 50 chars |
| `confidence` | `confidence` | — | Enum |
| `fraud_type` | `fraudType` | — | ≤ 50 chars |
| `attributes` | `attributes` | — | Flat JSON object serialized as a string in one cell (e.g. `"{""src_ip"": ""192.0.2.45""}"`) |

¹ Required for every subject type except TIME_RANGE (where the cell must
be empty).
² Required for TIME_RANGE subjects; optional (usually empty) otherwise.

CSV column names are snake_case; JSON keys are camelCase — the script
converts (`occurred_from` → `occurredFrom`, `flow_type` → `flowType`, …).

### JSON item shape (NDJSON lines)

```json
{
  "subject": { "type": "EXTERNAL_TRANSACTION_ID", "value": "TXN-42" },
  "verdict": "SUSPECTED_FRAUD",
  "occurredFrom": "2026-02-02T06:21:21.000Z",
  "occurredTo": "2026-02-03T21:53:52.000Z",
  "trigger": "THIRD_PARTY_SIGNAL",
  "flowType": "TRANSACTION",
  "confirmedAt": "2026-02-04T02:18:08.000Z",
  "reason": "Known malicious ASN observed initiating registration flow",
  "analyst": "red@acme.com",
  "confidence": "LOW",
  "fraudType": "STOLEN_IDENTITY",
  "attributes": { "src_ip": "192.0.2.45" }
}
```

Optional fields may be omitted (rather than empty). For TIME_RANGE
subjects the `subject.value` key should be omitted. `attributes` is a
flat string-to-string map.

### Enumerated values

**subject.type** — `RISK_EVALUATION_ID`, `EXTERNAL_TRANSACTION_ID`,
`PINGONE_USER_ID`, `EXTERNAL_USER_ID`, `IP_ADDRESS`, `TIME_RANGE`

**verdict** — `FRAUD`, `SUSPECTED_FRAUD`, `LEGITIMATE`

**trigger** — `CHARGEBACK`, `MANUAL_REVIEW`, `CUSTOMER_COMPLAINT`,
`THIRD_PARTY_SIGNAL`

**flowType** — `REGISTRATION`, `AUTHENTICATION`, `ACCESS`,
`AUTHORIZATION`, `TRANSACTION`

**confidence** — `HIGH`, `MEDIUM`, `LOW`

The wire field is `trigger` (not `triggerType`).

### Timestamp format

RFC 3339 UTC with **millisecond precision**: `YYYY-MM-DDThh:mm:ss.SSSZ`
(e.g. `2026-02-02T06:21:21.000Z`). Values without milliseconds are
rejected as `INVALID_DATA` by the API — the local validator enforces the
same rule up front.

### Validation rules summary

- `subject.value` required (format per subject type: UUID for
  `RISK_EVALUATION_ID`/`PINGONE_USER_ID`, IP address for `IP_ADDRESS`,
  non-empty string for the `EXTERNAL_*` types), except TIME_RANGE where it
  must be empty/omitted.
- TIME_RANGE subjects require `occurredTo`, at most **3 days** after
  `occurredFrom`.
- `reason` ≤ 200 chars; `analyst` ≤ 50 chars; `fraudType` ≤ 50 chars.
- `attributes`: flat object, ≤ **20 keys**, key ≤ 100 chars, value ≤ 300
  chars.
- All enum fields must match the lists above exactly (case-sensitive).

### What happens to invalid rows

Every row is validated locally before anything is sent. Rejected rows are
listed in the terminal report (text) or `skippedItems[]` (JSON) with their
`inputIndex` (1-based item index across the input), `sourceLine` (CSV row
number with header = line 1, or NDJSON line number), `field`, and
`message`. Only valid rows are batched and sent. Rows rejected **by the
API** inside a 200 response are terminal — fix and re-submit them
individually (the bulk POST is not idempotent, so never re-send the whole
file). `--errors-out <file>` writes all rejected rows to a file ready to
fix and re-run — see [Fix and re-submit](#fix-and-resubmit---errors-out).

### Example

CSV (header + 2 rows, canonical column order):

```csv
subject_type,subject_value,verdict,occurred_from,occurred_to,trigger,flow_type,confirmed_at,reason,analyst,confidence,fraud_type,attributes
RISK_EVALUATION_ID,15f1d245-33b3-4dba-849d-4df4b64b2d42,FRAUD,2026-05-10T08:03:17.000Z,,CHARGEBACK,REGISTRATION,2026-05-15T10:30:00.000Z,Chargeback received within 24h of registration,fraud-analyst@acme.com,HIGH,BOT_FARM,"{""src_ip"": ""192.0.2.45""}"
TIME_RANGE,,SUSPECTED_FRAUD,2026-01-25T08:51:01.000Z,2026-01-27T17:26:04.000Z,,AUTHENTICATION,2026-02-02T09:42:25.000Z,Card testing pattern across small-value transactions,security@acme.com,MEDIUM,BOT_FARM,
```

Equivalent NDJSON lines:

```json
{"subject":{"type":"RISK_EVALUATION_ID","value":"15f1d245-33b3-4dba-849d-4df4b64b2d42"},"verdict":"FRAUD","occurredFrom":"2026-05-10T08:03:17.000Z","trigger":"CHARGEBACK","flowType":"REGISTRATION","confirmedAt":"2026-05-15T10:30:00.000Z","reason":"Chargeback received within 24h of registration","analyst":"fraud-analyst@acme.com","confidence":"HIGH","fraudType":"BOT_FARM","attributes":{"src_ip":"192.0.2.45"}}
{"subject":{"type":"TIME_RANGE"},"verdict":"SUSPECTED_FRAUD","occurredFrom":"2026-01-25T08:51:01.000Z","occurredTo":"2026-01-27T17:26:04.000Z","flowType":"AUTHENTICATION","confirmedAt":"2026-02-02T09:42:25.000Z","reason":"Card testing pattern across small-value transactions","analyst":"security@acme.com","confidence":"MEDIUM","fraudType":"BOT_FARM"}
```

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `ABORTED: ... Authentication is not configured` | The pingcli profile has no working auth | Run `pingcli pingone init` / check `-P` profile name |
| `environment ID is required` at startup | The profile has no environment and no flag was given | Pass `--environment-id <uuid>` or configure the environment on the profile (`pingcli pingone init`) |
| `HTTP 401 / 403` in the report | The worker lacks permission or the credentials are stale | Re-authenticate the profile; check the worker's grants |
| `HTTP 404` on every batch | The environment doesn't have the risk feedback v2 bulk endpoint enabled | Contact Ping support to enable the endpoint for your environment |
| `CSV header is missing required column "X"` | Column names must match exactly | Compare with the [input format reference](#input-format-reference) — column order is free, names are not |
| Timestamps rejected: `...SSSZ format` | The API requires millisecond precision, UTC | `2026-01-02T15:04:05.000Z`, not `2026-01-02T15:04:05Z` |
| `pingcli: command not found` | Ping CLI not installed or not on PATH | Install Ping CLI first — the script drives it |

## Development

```bash
python -m unittest discover tests
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
