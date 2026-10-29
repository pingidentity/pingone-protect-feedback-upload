---
name: protect-feedback-upload
description: Use this skill when a user wants to bulk-upload PingOne Protect risk feedback labels from a CSV or NDJSON file, asks about the protect-feedback-upload command or its flags, reports skipped/failed rows from a label upload, or wants to fix and re-submit rejected rows. Covers the safe upload workflow — the bulk POST is not idempotent, so never re-send a file after a partial success.
summary: Safe bulk upload of PingOne Protect risk feedback labels via protect-feedback-upload.
---

# protect-feedback-upload — safe bulk upload of risk feedback labels

Bulk-uploads PingOne Protect risk feedback labels from CSV/NDJSON through
pingcli: local per-row validation first, batches of ≤20, one bulk POST per
batch, retry matrix, and a per-row report. Zero runtime dependencies;
auth, profiles, and region routing come from the user's pingcli profile.

## THE critical rule — read before doing anything

The bulk POST is **not idempotent**. Re-sending a file after a partial
success **duplicates every label that already landed** — there is no bulk
delete to undo it.

- If some rows succeeded and some failed: **never re-run the original
  file.** Re-submit only the rejected rows (see the errors-out loop below).
- If an agent asks to "retry the upload," that means the rejected rows
  only. Never the whole file.
- A dry run (`--dry-run`) is always safe and sends nothing — start there.

## The workflow

```bash
# 1. ALWAYS dry-run first: validates + batches, sends nothing, no network
protect-feedback-upload upload labels.csv --dry-run
#    Fix any "SKIPPED item N (line M)" rows in the file, re-run until clean.

# 2. Upload — one command; environment ID comes from the pingcli profile
protect-feedback-upload upload labels.csv
```

If rows are rejected: fix **only those rows** and re-submit only them —
the report names each rejected row with its input line and reason, and
`--errors-out <file>` writes the rejected rows (data included) to a file
ready to fix and re-run. Never the original file.

## Command reference

```
protect-feedback-upload upload <file.csv|file.ndjson|-> [flags]
```

| Flag | Meaning |
|---|---|
| `--dry-run` | Validate + batch only, send nothing (always the first step) |
| `--errors-out <file>` | Write every row that did not land to a file ready to fix and re-submit |
| `-P <profile>` | pingcli profile for auth (omit = the active profile) |
| `--environment-id <uuid>` | Target environment (omit = resolved from the profile; pass only to deliberately cross environments) |
| `-O json` | Machine-readable report (`summary`, `batches[]`, `failedItems[]`, `skippedItems[]`) |
| `--batch-size 1-20` | Items per request (default 20 = the API cap) |
| `--pingcli-path <path>` | Custom pingcli binary path |

The input is CSV (13 snake_case columns, order free, names exact) or
NDJSON (camelCase, one object per line); `-` reads stdin. Full column
rules: the repo README's "Input format reference".

## Exit codes — they mean different remediations

| Code | Meaning | What to do |
|---|---|---|
| `0` | Everything uploaded (or skipped rows were only pre-existing local rejects in a dry run) | Done |
| `1` | Some rows failed or were skipped | Read the per-row lines; fix ONLY those rows; re-submit them via the errors-out loop — never the original file |
| `2` | Aborted (auth/transport) | Fix the pingcli profile (`pingcli pingone init`), then re-run; nothing was uploaded past the abort point |
| `3` | Input error (missing file, bad header, unresolvable environment ID) | Fix the input/command; nothing was sent |

## Prerequisites and common errors

- Python 3.10+ and [Ping CLI](https://developer.pingidentity.com/pingcli/1.10/install/macos.html),
  authenticated once with `pingcli pingone init` (interactive — type it
  alone, nothing after it).
- `environment ID is required` → the profile has no environment: pass
  `--environment-id <uuid>` or configure the profile.
- `HTTP 401/403` → worker credentials stale or lacking grants —
  re-authenticate the profile.
- `HTTP 404` on every batch → the environment lacks the risk feedback v2
  bulk endpoint — contact Ping support.
- Timestamps must be RFC 3339 UTC **with milliseconds**:
  `2026-01-02T15:04:05.000Z`, not `2026-01-02T15:04:05Z`.
