# pingone-protect-feedback-upload

Feedback API v2 Bulk Upload CLI — a standalone script for bulk-uploading
PingOne Protect risk feedback labels.

**Start here: [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)** — install,
first run, report format, and troubleshooting.

## Why this exists

PingOne Protect's Feedback API v2 accepts at most **20 labels per bulk
request**, returns **per-item errors inside a 200 response**, and the POST
is **not idempotent** — re-sending a file after a partial failure
duplicates successful labels. The bulk workflow logic lives here:

- reads CSV or NDJSON label files (see [docs/input-format.md](docs/input-format.md))
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
  to its input line

## Requirements

- Python 3.10+
- [Ping CLI](https://developer.pingidentity.com/pingcli/1.10/install/macos.html)
  (installation guides for all platforms), authenticated for the target
  environment (`pingcli pingone init`)

## Usage

```bash
# Install (one command, stdlib-only — no dependency tree)
pip install pingone-protect-feedback-upload
# or from a clone of this repo:
pip install .

# Validate and batch only — nothing is sent
protect-feedback-upload upload labels.csv --dry-run

# Upload — the environment ID is taken from the pingcli profile
protect-feedback-upload upload labels.csv -P <profile>

# Explicit override, if the profile's environment differs from the target
protect-feedback-upload upload labels.csv --environment-id <uuid> -P <profile>

# Machine-readable report
protect-feedback-upload upload labels.csv -O json

# Write rejected rows to a fix-and-resubmit file
protect-feedback-upload upload labels.csv --errors-out fix-these.csv
```

Exit codes: `0` all rows uploaded · `1` some rows failed or were skipped ·
`2` aborted (auth/transport) · `3` input error.

## Never re-send the whole file

After a partial success, fix only the rejected rows and re-submit them
individually. The bulk POST is not idempotent: re-sending the original
file duplicates the labels that already succeeded.

## Development

```bash
python -m unittest discover tests
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
