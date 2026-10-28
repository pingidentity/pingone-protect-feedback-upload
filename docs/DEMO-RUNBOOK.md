# Demo runbook — PingOne Protect risk feedback bulk upload

**Audience:** Yuri (and anyone evaluating the deliverable)
**Arc:** ~10 minutes. The story: *pingcli stays an atomic tool — the script drives its generic `api` command, the workflow lives in its own repo, and nothing changes in any pingcli repo.*

**Prerequisites (check before starting):**

| Thing | Value | Check |
|---|---|---|
| pingcli (shipped) | any recent install — no custom binary needed | `pingcli --version` |
| Script | installed via `pip install .` or run from the repo | `protect-feedback-upload --version` |
| Profile with auth | any pingcli profile with worker-app auth (profiles live in `~/.pingcli/config.yaml`, secrets in Keychain) | `pingcli config profiles list` |
| Environment (FF on) | a PingOne environment UUID with the risk feedback v2 bulk endpoint enabled | your own environment — substitute `<env-id>` throughout |
| Demo CSV | `/tmp/thin-e2e.csv` (2 valid rows + 1 bad row) | `cat /tmp/thin-e2e.csv` |

> **Profile choice:** use a profile whose worker app is configured for the auth style pingcli sends (some worker apps reject Basic auth with "Unsupported authentication method" — if you hit that, switch profiles).
>
> **Nothing in this demo touches the pingcli source repo:** the script drives the generic `pingcli pingone api` command per batch (auth, profiles, and region routing are pingcli's own), carrying the Feedback API v2 bulk media type. A future thin `protect risk-feedback` command would streamline the invocation, but is not required.

> If `/tmp` was wiped: recreate the CSV and JSON assets from the snippets at the bottom.

## S0 — the one-slide story (30s)

- The deliverable is **this repo only**: a standalone, pip-installable script owning the workflow (CSV parsing, ≤20 batching, retry matrix, row-level reporting).
- Each batch is one raw bulk POST through pingcli's **generic `pingone api` command** — authentication, profiles, and region routing are pingcli's own, already shipped.
- **No changes to any pingcli repo**: no PR, no review, no SDK-tag dependency. Works with today's shipped pingcli.

## S1 — the raw bulk POST, by hand (2 min)

Show that the workflow is just one API call that pingcli already knows how to make — the generic `pingcli pingone api` command with the Feedback API v2 bulk media type (auth, profile, and region all injected by pingcli):

```bash
pingcli pingone api -P <profile> \
  --http-method POST \
  --header "Content-Type: application/vnd.pingidentity.risk.feedback.bulk.v2+json" \
  --data /tmp/bulk-envelope.json \
  -O json \
  environments/<env-id>/riskFeedback | python3 -m json.tool | head -30
# → status: success, data.summary: {requested: 2, successful: 2, ...}, per-item responses
```

> The `--data` file must be the items-envelope `{"items": [...]}` — a bare
> NDJSON/CSV label file is the *script's* input, not a bulk body. Feeding
> one straight to the API fails with "items must not be null".

Point out the envelope: `meta.api.httpStatus`, `data` = the raw bulk response — this is exactly the contract the script consumes per batch.

## S2 — the script's install story (1 min)

```bash
pip install pingone-protect-feedback-upload   # zero dependencies, Python 3.10+
protect-feedback-upload --help
```

One line: *"The customer installs pingcli, authenticates once (`pingcli pingone init`), pip-installs this script — done. Then it's one command: file in, report out."*

## S3 — the main event: CSV → validate → batch → upload (3 min)

```bash
cat /tmp/thin-e2e.csv     # 2 valid rows + 1 bad row (verdict NOPE, bad timestamp)

protect-feedback-upload upload /tmp/thin-e2e.csv \
  --environment-id <env-id> \
  -P <profile>
# → Upload complete: 2 requested, 2 successful, 0 failed, 0 skipped
#   batch 1: 2 items, HTTP 200, 2 successful, 0 failed
#   SKIPPED item 3 (line 4) field 'verdict': verdict 'NOPE' must be one of ...
#   SKIPPED item 3 (line 4) field 'occurred_from': timestamp 'bad': ...
```

Points to make while it runs:
- The bad row was **rejected locally, before anything was sent** — with its line number and both violations.
- The retry matrix is the API contract's twin: 429 → pause+retry, 500/transport → 3 attempts, 401/403 → abort, **partial 200 → never retried** (the POST is not idempotent — re-sending the file duplicates successful labels).
- Exit codes are automation-friendly: 0 clean, 1 failures/skips, 2 aborted, 3 input error. `-O json` gives the same report as structured data.

## S4 — safety rails (2 min)

Abort on auth failure (no pointless retries):

```bash
protect-feedback-upload upload /tmp/thin-e2e.csv \
  --environment-id <env-id> \
  -P nonexistent-profile
# → ABORTED: INVALID_ARGUMENT: Authentication is not configured for this profile.
```

Dry run (validates and batches, sends nothing, no network):

```bash
protect-feedback-upload upload /tmp/thin-e2e.csv \
  --environment-id <env-id> \
  --dry-run
```

## S5 — architecture recap + what's pending (1 min)

- **This repo is the whole deliverable:** reader/validator/batcher/retry/report, 53 unit tests, zero runtime dependencies. It drives pingcli's generic `api` command per batch — no changes to any pingcli repo, works with today's shipped pingcli.
- **Future enhancement (parked, local branch only):** a thin `protect risk-feedback` pass-through command in pingcli would make each batch a friendlier invocation — but it is not required for this workflow to ship.
- **Pending:** repo maintainer access (DEVHELP-46753) to push the script.

---

## Rebuilding demo assets if /tmp was wiped

CSV:
```bash
cat > /tmp/thin-e2e.csv <<'EOF'
subject_type,subject_value,verdict,occurred_from,occurred_to,trigger,flow_type,confirmed_at,reason,analyst,confidence,fraud_type,attributes
EXTERNAL_TRANSACTION_ID,THIN-E2E-1,FRAUD,2026-10-22T09:00:00.000Z,,MANUAL_REVIEW,TRANSACTION,,E2E via script row 1,analyst@acme.com,HIGH,,
EXTERNAL_TRANSACTION_ID,THIN-E2E-2,SUSPECTED_FRAUD,2026-10-22T09:05:00.000Z,,CUSTOMER_COMPLAINT,AUTHENTICATION,,E2E via script row 2,analyst@acme.com,MEDIUM,,
EXTERNAL_TRANSACTION_ID,THIN-E2E-BAD,NOPE,bad,,,,,,,,,
EOF
```

Bulk/single/oversize JSON (S1 needs the items-envelope — a raw NDJSON file is NOT a valid bulk body):
```bash
cat > /tmp/bulk-envelope.json <<'EOF'
{"items":[{"subject":{"type":"EXTERNAL_TRANSACTION_ID","value":"GENAPI-PROBE-1"},"verdict":"FRAUD","occurredFrom":"2026-10-22T10:00:00.000Z","flowType":"TRANSACTION","reason":"generic api probe"},{"subject":{"type":"IP_ADDRESS","value":"198.51.100.78"},"verdict":"FRAUD","occurredFrom":"2026-10-22T10:01:00.000Z","flowType":"ACCESS","reason":"generic api probe 2"}]}
EOF
```

Test data written during the demo (cleanup list): any labels uploaded with the demo `subject_value` values above — search the environment for them if cleanup is needed.
