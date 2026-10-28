# Getting started — bulk-uploading risk feedback labels

A 5-minute guide to running `protect-feedback-upload`.

## What it does

Reads a CSV or NDJSON file of PingOne Protect risk feedback labels,
validates every row locally, batches them into groups of at most 20 (the
API's per-request cap), uploads each batch through the Ping CLI, and
prints a report mapping every rejected row to its input line.

Nothing in your Ping CLI installation is modified — the script calls
`pingcli` as a subprocess and reuses its authentication.

## Prerequisites

1. **Python 3.10+** (`python3 --version`)
2. **Ping CLI installed and authenticated:**

   Install from [developer.pingidentity.com](https://developer.pingidentity.com/pingcli/1.10/install/macos.html)
   (guides for all platforms), then run the one-time wizard:

   ```bash
   pingcli pingone init
   ```

   Note the profile name you configure (shown by `pingcli config profiles list`).
3. **A labels file** — CSV or NDJSON. The exact columns/fields are in
   [input-format.md](input-format.md); a ready-to-edit example ships at
   [labels-sample.csv](labels-sample.csv).

## Install

```bash
pip install git+https://github.com/pingidentity/pingone-protect-feedback-upload.git
```

(or `pip install .` from a local copy of this repository — zero runtime
dependencies beyond Python itself)

## Run it

**Always start with a dry run — it validates and batches, sends nothing:**

```bash
protect-feedback-upload upload labels.csv --dry-run
```

Fix anything the dry run rejects, then upload for real:

```bash
protect-feedback-upload upload labels.csv
```

The environment ID comes from your pingcli profile, so you don't pass it.
If the profile's environment isn't the target — or your profile has none
configured — pass it explicitly:

```bash
protect-feedback-upload upload labels.csv --environment-id <uuid> -P <profile>
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--dry-run` | Validate + show the batch plan, send nothing |
| `-P <profile>` | Which pingcli profile to authenticate with (omit for the active one) |
| `--environment-id <uuid>` | Target environment (optional — resolved from the profile when omitted) |
| `--errors-out <file>` | Write all rejected rows to a file, ready to fix and re-submit (see below) |
| `-O json` | Machine-readable report (same data as the text report) |
| `--batch-size 1-20` | Items per request (default 20) |

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

Exit codes: `0` all rows uploaded · `1` some rows failed or skipped ·
`2` aborted (authentication/transport) · `3` input error.

## Important: never re-send the whole file

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

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `ABORTED: ... Authentication is not configured` | The pingcli profile has no working auth | Run `pingcli pingone init` / check `-P` profile name |
| `HTTP 401 / 403` in the report | The worker lacks permission or the credentials are stale | Re-authenticate the profile; check the worker's grants |
| `HTTP 404` on every batch | The environment doesn't have the risk feedback v2 bulk endpoint enabled | Contact Ping support to enable the endpoint for your environment |
| `CSV header is missing required column "X"` | Column names must match exactly | Compare with [input-format.md](input-format.md) — column order is free, names are not |
| Timestamps rejected: `...SSSZ format` | The API requires millisecond precision, UTC | `2026-01-02T15:04:05.000Z`, not `2026-01-02T15:04:05Z` |
| `pingcli: command not found` | Ping CLI not installed or not on PATH | Install Ping CLI first — the script drives it |

## Code status

Implemented and live-verified (unit-tested; run against a production
environment).
