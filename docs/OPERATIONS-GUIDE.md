# Operations guide — risk feedback bulk upload (PND-10346)

**Owner:** Yaron Shaul · **Last verified:** 2026-10-25 (full run from zero)
**Audience:** anyone running or demoing the upload flow — self-contained; every referenced file is listed in §1.

---

## 1. File map — everything that exists, and where

| Artifact | Path | Purpose | Exists? |
|---|---|---|---|
| **Source repo** | `~/work/pingid/pingone-protect-feedback-upload` (branch `pnd-10346-upload-script`) | The deliverable: script + docs + 53 tests. 8 commits, local only (push blocked by DEVHELP-46753) | ✅ |
| **Installed script** | `protect-feedback-upload` on PATH (v0.1.0, installed via `pip install .`) | The CLI itself | ✅ reinstall: `pip install ~/work/pingid/pingone-protect-feedback-upload` |
| **Demo kit** | `/tmp/demo-fresh/` — `labels-25.csv`, `labels-mixed.csv`, `labels.ndjson`, `bulk-envelope.json` | All demo inputs; regenerable (§4) | ✅ regenerable |
| **Wheel (for Yuri)** | `/tmp/wheel-dist/pingone_protect_feedback_upload-0.1.0-py3-none-any.whl` | Installable artifact — no repo access needed | ✅ rebuild: `pip wheel ~/work/pingid/pingone-protect-feedback-upload --no-deps -w /tmp/wheel-dist` |
| **Template CSV (for Yuri)** | `/tmp/wheel-dist/labels-template.csv` | 5 valid example rows; passes dry-run as-is | ✅ content in §7 |
| **In-repo docs** | `docs/GETTING-STARTED.md` (user guide), `docs/input-format.md` (columns/enums), `docs/DEMO-RUNBOOK.md` (demo script), `docs/OPERATIONS-GUIDE.md` (this file) | Ship with the repo | ✅ |
| **PROD environment** | `f606ebfd-628d-4fd3-a3cd-dd0d5b925e48`, pingcli profile **`demo`** | The FF-enabled env used for live verification | ✅ |

⚠ Anything under `/tmp` is wiped by the OS periodically — both demo kit and
wheel are rebuildable in under a minute (§4, §5).

---

## 2. How the pieces work (processing model, short version)

```
labels.csv/ndjson ─▶ read+sniff format ─▶ validate every row locally ─▶ split into ≤20 batches
                                                                                        │
                                                            per batch: write {"items":[...]} temp file
                                                            → ONE pingcli subprocess (auth+HTTP+envelope)
                                                            → classify by meta.api.httpStatus
                                                                                        │
                                              200 → count summary + map per-item errors to input lines
                                              429 → pause 1s, retry same batch (≤25)
                                              500/transport → ≤3 attempts
                                              401/403 / no meta.api → ABORT run
                                              other 4xx → skip batch, log, continue
                                              200-with-item-failures → NEVER retried (not idempotent)
                                                                                        │
                                                               aggregate report (text or -O json)
```

Key facts:
- The script **never touches credentials** — pingcli owns auth, profiles, region.
- The script **always passes `-f`** to pingcli (the generic command exits 0 on HTTP failures without it) and classifies from the JSON envelope only.
- Validation is a convenience; **the API remains authoritative**.
- The bulk POST is **not idempotent** — never re-send a file after partial success.

---

## 3. Standard operating procedure (the 5 commands)

```bash
cd /tmp/demo-fresh   # or wherever the labels file is

# 1. Always start here — validates, batches, sends NOTHING:
protect-feedback-upload upload <file>.csv --dry-run --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo

# 2. Fix any SKIPPED rows, re-check until clean, then send for real:
protect-feedback-upload upload <file>.csv --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo

# 3. Machine report for automation:
protect-feedback-upload upload <file>.csv --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo -O json

# Exit codes: 0 clean · 1 failures/skips · 2 aborted (auth/transport) · 3 input error
```

---

## 4. Rebuild the demo kit (from zero, ~5 seconds)

```bash
mkdir -p /tmp/demo-fresh && cd /tmp/demo-fresh && python3 - <<'EOF'
import csv, json

TS = "2026-10-25T{:02d}:{:02d}:00.000Z"
HEADER = ["subject_type","subject_value","verdict","occurred_from","occurred_to",
          "trigger","flow_type","confirmed_at","reason","analyst","confidence","fraud_type","attributes"]

def blank(): return {c: "" for c in HEADER}
def row(**kw):
    r = blank(); r.update(**kw); return [r[c] for c in HEADER]

rows = []
for i in range(1, 26):
    r = blank()
    r["subject_type"] = "EXTERNAL_TRANSACTION_ID"
    r["subject_value"] = f"FRESH-1025-{i:03d}"
    r["verdict"] = ["FRAUD","SUSPECTED_FRAUD","LEGITIMATE"][i % 3]
    r["occurred_from"] = TS.format(8 + (i % 8), (i * 7) % 60)
    r["flow_type"] = ["TRANSACTION","AUTHENTICATION","REGISTRATION","ACCESS","AUTHORIZATION"][i % 5]
    r["reason"] = f"Fresh demo row {i} - mixed subject types"
    r["analyst"] = "demo@pingidentity.com"
    r["confidence"] = ["HIGH","MEDIUM","LOW"][i % 3]
    rows.append([r[c] for c in HEADER])
with open("labels-25.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(HEADER); w.writerows(rows)

valid = [
    row(subject_type="EXTERNAL_TRANSACTION_ID", subject_value="FRESH-1025-M1", verdict="FRAUD",
        occurred_from="2026-10-25T09:00:00.000Z", flow_type="TRANSACTION",
        reason="mixed-file valid row 1", analyst="demo@pingidentity.com", confidence="HIGH"),
    row(subject_type="IP_ADDRESS", subject_value="198.51.100.201", verdict="SUSPECTED_FRAUD",
        occurred_from="2026-10-25T09:05:00.000Z", flow_type="ACCESS",
        reason="mixed-file valid row 2", analyst="demo@pingidentity.com", confidence="MEDIUM"),
    row(subject_type="TIME_RANGE", verdict="LEGITIMATE",
        occurred_from="2026-10-20T08:00:00.000Z", occurred_to="2026-10-22T17:00:00.000Z",
        flow_type="AUTHENTICATION", reason="mixed-file valid TIME_RANGE row",
        analyst="demo@pingidentity.com", confidence="LOW"),
]
errs = [
    row(subject_type="EXTERNAL_TRANSACTION_ID", subject_value="FRESH-1025-E1", verdict="NOPE",
        occurred_from="2026-10-25T09:00:00.000Z", reason="bad verdict"),
    row(subject_type="EXTERNAL_TRANSACTION_ID", subject_value="FRESH-1025-E2", verdict="FRAUD",
        occurred_from="2026-10-25T09:00:00Z", reason="timestamp without millis"),
    row(subject_type="RISK_EVALUATION_ID", subject_value="not-a-uuid", verdict="FRAUD",
        occurred_from="2026-10-25T09:00:00.000Z", reason="non-UUID for RISK_EVALUATION_ID"),
    row(subject_type="IP_ADDRESS", subject_value="999.1.2.3", verdict="FRAUD",
        occurred_from="2026-10-25T09:00:00.000Z", reason="malformed IP"),
    row(subject_type="TIME_RANGE", subject_value="should-be-empty", verdict="SUSPECTED_FRAUD",
        occurred_from="2026-10-25T09:00:00.000Z", reason="TIME_RANGE with value, no occurred_to"),
    row(subject_type="EXTERNAL_TRANSACTION_ID", subject_value="FRESH-1025-E6", verdict="FRAUD",
        occurred_from="2026-10-25T09:00:00.000Z", reason="x" * 201),
]
with open("labels-mixed.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(HEADER); w.writerows(valid + errs)

nd = [
    {"subject":{"type":"EXTERNAL_TRANSACTION_ID","value":"FRESH-1025-N1"},"verdict":"FRAUD",
     "occurredFrom":"2026-10-25T10:00:00.000Z","flowType":"TRANSACTION","reason":"NDJSON demo row 1",
     "attributes":{"src_ip":"198.51.100.240"}},
    {"subject":{"type":"TIME_RANGE"},"verdict":"LEGITIMATE",
     "occurredFrom":"2026-10-20T08:00:00.000Z","occurredTo":"2026-10-22T17:00:00.000Z",
     "flowType":"AUTHENTICATION","reason":"NDJSON TIME_RANGE row"},
]
with open("labels.ndjson", "w") as f:
    for obj in nd:
        f.write(json.dumps(obj) + "\n")
open("bulk-envelope.json","w").write(json.dumps({"items": nd}))

print("ready:", *sorted(__import__("os").listdir(".")))
EOF
```

## 5. Rebuild the wheel + template (for Yuri, ~30 seconds)

```bash
mkdir -p /tmp/wheel-dist
pip wheel ~/work/pingid/pingone-protect-feedback-upload --no-deps -w /tmp/wheel-dist
cp ~/work/pingid/pingone-protect-feedback-upload/docs/labels-template.csv /tmp/wheel-dist/ 2>/dev/null || true
# If the template is missing from the repo, paste it from §7.
```

Template content (§7) — save as `labels-template.csv` if absent.

## 6. Demo sequence (all steps verified live 2026-10-25)

| # | Command (from /tmp/demo-fresh) | Shows | Writes? |
|---|---|---|---|
| 1 | `pingcli pingone api -P demo --http-method POST --header "Content-Type: application/vnd.pingidentity.risk.feedback.bulk.v2+json" --data bulk-envelope.json -O json environments/f606ebfd-628d-4fd3-a3cd-dd0d5b925e48/riskFeedback` | The raw bulk API by hand — why a workflow layer exists | ✅ (2 items) |
| 2 | `protect-feedback-upload upload labels-25.csv --dry-run --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo` | Batching: 25 → 20+5 | ❌ |
| 3 | `protect-feedback-upload upload labels-mixed.csv --dry-run --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo` | Validation: 6 rejections with line numbers | ❌ |
| 4 | `protect-feedback-upload upload labels-mixed.csv --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo` | Live: 3 sent, 6 rejected locally | ✅ (3 items) |
| 5 | `protect-feedback-upload upload labels.ndjson --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P wrong-profile` | Abort on auth — instant, no retries | ❌ |
| 6 | `protect-feedback-upload upload labels.ndjson --environment-id f606ebfd-628d-4fd3-a3cd-dd0d5b925e48 -P demo -O json` | Machine report | ✅ (2 items) |

Zero-write demo: steps 2, 3, 5 only.

## 7. Template CSV content (`labels-template.csv`)

```csv
subject_type,subject_value,verdict,occurred_from,occurred_to,trigger,flow_type,confirmed_at,reason,analyst,confidence,fraud_type,attributes
EXTERNAL_TRANSACTION_ID,TXN-1001,FRAUD,2026-01-15T08:03:17.000Z,,CHARGEBACK,TRANSACTION,2026-01-20T10:30:00.000Z,Chargeback received within 24h of the transaction,analyst@yourcompany.com,HIGH,CARD_TESTING,"{""src_ip"": ""192.0.2.45"",""amount"": ""149.99""}"
IP_ADDRESS,198.51.100.22,SUSPECTED_FRAUD,2026-01-16T00:00:00.000Z,2026-01-16T06:00:00.000Z,,ACCESS,,IP part of known bot farm ASN,analyst@yourcompany.com,HIGH,CREDENTIAL_STUFFING,
TIME_RANGE,,LEGITIMATE,2026-10-20T08:00:00.000Z,2026-10-22T17:00:00.000Z,,AUTHENTICATION,,User behavior verified legitimate for this window,analyst@yourcompany.com,MEDIUM,,
EXTERNAL_TRANSACTION_ID,TXN-1004,FRAUD,2026-01-17T14:22:05.000Z,,MANUAL_REVIEW,REGISTRATION,,Multiple signups from same device fingerprint,analyst@yourcompany.com,MEDIUM,BOT_FARM,
PINGONE_USER_ID,a1b2c3d4-0000-4000-8000-000000001234,LEGITIMATE,2026-01-18T09:15:00.000Z,,,,,False positive - verified legitimate customer,analyst@yourcompany.com,LOW,,
```

## 8. Yuri's zero-access install (the wheel path)

```bash
pip install ~/Downloads/pingone_protect_feedback_upload-0.1.0-py3-none-any.whl
protect-feedback-upload upload ~/Downloads/labels-template.csv --dry-run --environment-id <ENV-ID> -P <PROFILE>
# then drop --dry-run to upload for real
```

## 9. Test-data ledger (PROD env f606ebfd) — cleanup list

| Prefix/tag | Written by | Count |
|---|---|---|
| `TXN-PROD-*`, `TXN-ME-*`, `TXN-BATCH-*`, `SDKFIX-TXN-*`, `DEMO-TXN-*`, `TXN-TRIG-*` | Earlier sessions (2026-10-06→13) | ~110 |
| `THIN-DEMO-001`, `THIN-DEMO-B1/B2`, `THIN-E2E-1/2` | Thin-command verification (2026-10-22) | 6 |
| `GENAPI-PROBE-1/2` | Generic-command contract probes | 2 (+1 rejected) |
| `FRESH-1025-001…025` | Demo labels-25.csv (rehearsals) | 25 (dups across runs) |
| `FRESH-1025-M1/M2`, TIME_RANGE row | Demo labels-mixed.csv | 3 |
| `FRESH-1025-N1/N2` | Demo labels.ndjson + manual step | 2 (dups) |

No DELETE exists in the bulk API v2 — cleanup TBD (user decision), tags above make the test data identifiable.

## 10. Known issues / watch items

1. **Push access blocked** (DEVHELP-46753, owner James Richardson) — the branch
   `pnd-10346-upload-script` is local-only. Re-check: `git push --dry-run origin pnd-10346-upload-script`.
2. **Profile `pingone-dev` 401s** — its worker app rejects Basic auth; use `demo`.
3. **The generic `api` command exits 0 on HTTP errors without `-f`** — the script
   always passes `-f`; if you ever hand-run a batch, add `-f` and read
   `meta.api.httpStatus` from the envelope.
4. **`--data` must be the `{"items":[...]}` envelope** — raw CSV/NDJSON is script
   input, not an API body (fails "items must not be null").
5. **error-parity golden test fails locally** (pingcli repo, scratch branch) — Go
   1.27 vs CI's 1.26 wording; expected artifact, file is pinned to CI's version.
