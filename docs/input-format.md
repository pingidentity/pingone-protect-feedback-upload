# Risk feedback input format reference

Complete reference for the label files accepted by
`python -m protect_feedback_upload upload`. Use it to answer "what columns
does the file need?" and to diagnose skipped rows.

## Accepted formats

| Format | Detection | Shape |
|---|---|---|
| **CSV** | `.csv` extension, or content sniffing (first non-space byte not `{`) | RFC 4180: first record is the header; column order is free; every record must carry every column (empty optional fields kept, trailing commas required). Fields quoted only when necessary; embedded quotes doubled. |
| **NDJSON** | `.ndjson`/`.jsonl` extension, or first non-space byte is `{` | One JSON object per line, camelCase keys. |

`-` reads stdin; stdin content is always sniffed (never assumed by
extension).

## CSV columns

First record is the header with these column names (order free, all required in the
header even when a column is empty throughout):

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

¹ Required for every subject type except TIME_RANGE (where the cell must be empty).
² Required for TIME_RANGE subjects; optional (usually empty) otherwise.

CSV column names are snake_case; JSON keys are camelCase — the script converts
(`occurred_from` → `occurredFrom`, `flow_type` → `flowType`, …).

## JSON item shape (NDJSON lines)

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

Optional fields may be omitted (rather than empty). For TIME_RANGE subjects the
`subject.value` key should be omitted. `attributes` is a flat string-to-string map.

## Enumerated values

**subject.type** — `RISK_EVALUATION_ID`, `EXTERNAL_TRANSACTION_ID`, `PINGONE_USER_ID`,
`EXTERNAL_USER_ID`, `IP_ADDRESS`, `TIME_RANGE`

**verdict** — `FRAUD`, `SUSPECTED_FRAUD`, `LEGITIMATE`

**trigger** — `CHARGEBACK`, `MANUAL_REVIEW`, `CUSTOMER_COMPLAINT`, `THIRD_PARTY_SIGNAL`

**flowType** — `REGISTRATION`, `AUTHENTICATION`, `ACCESS`, `AUTHORIZATION`,
`TRANSACTION`

**confidence** — `HIGH`, `MEDIUM`, `LOW`

The wire field is `trigger` (not `triggerType`).

## Timestamp format

RFC 3339 UTC with **millisecond precision**: `YYYY-MM-DDThh:mm:ss.SSSZ`
(e.g. `2026-02-02T06:21:21.000Z`). Values without milliseconds are rejected as
`INVALID_DATA` by the API — the local validator enforces the same rule up front.

## Validation rules summary

- `subject.value` required (format per subject type: UUID for
  `RISK_EVALUATION_ID`/`PINGONE_USER_ID`, IP address for `IP_ADDRESS`,
  non-empty string for the `EXTERNAL_*` types), except TIME_RANGE where it must
  be empty/omitted.
- TIME_RANGE subjects require `occurredTo`, at most **3 days** after `occurredFrom`.
- `reason` ≤ 200 chars; `analyst` ≤ 50 chars; `fraudType` ≤ 50 chars.
- `attributes`: flat object, ≤ **20 keys**, key ≤ 100 chars, value ≤ 300 chars.
- All enum fields must match the lists above exactly (case-sensitive).

## What happens to invalid rows

Every row is validated locally before anything is sent. Rejected rows are
listed in the terminal report (text) or `skippedItems[]` (JSON) with their
`inputIndex` (1-based item index across the input), `sourceLine` (CSV row
number with header = line 1, or NDJSON line number), `field`, and `message`.
Only valid rows are batched and sent. Rows rejected **by the API** inside a
200 response are terminal — fix and re-submit them individually (the bulk
POST is not idempotent, so never re-send the whole file).

`--errors-out <file>` writes all rejected rows (local skips and API
failures) to a file in the input's format, with the original data in the
standard columns plus error diagnostics — fix it and re-run the tool on
that file. See [GETTING-STARTED.md](GETTING-STARTED.md).

## Example

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
