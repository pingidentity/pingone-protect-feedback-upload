# Example label files

Ready-to-run inputs for `protect-feedback-upload`. Column and field rules
are documented in [../docs/input-format.md](../docs/input-format.md); the
full guide is [../docs/GETTING-STARTED.md](../docs/GETTING-STARTED.md).

| File | Rows | What it demonstrates |
|---|---|---|
| `labels-demo.csv` | 4 valid (CSV) | All three verdicts, four subject/trigger combinations, a successful upload |
| `labels-demo.ndjson` | 3 valid (NDJSON) | The JSON input format, including a TIME_RANGE subject (no `subject.value`) |
| `labels-demo-invalid.csv` | 5 invalid | Local validation: every row is rejected with its line number and reason — nothing is sent |

## Try them

```bash
# Validate and batch only — nothing is sent
protect-feedback-upload upload examples/labels-demo.csv --dry-run

# Upload (environment ID comes from your pingcli profile)
protect-feedback-upload upload examples/labels-demo.csv

# Watch local validation reject every row before anything is sent
protect-feedback-upload upload examples/labels-demo-invalid.csv
```

The demo files use recognizable `subject_value` values (`TXN-DEMO-*`,
`NDJSON-DEMO-*`, and the documentation-reserved IP `198.51.100.78`) so test
uploads are easy to identify later.

Exit codes: `0` all rows uploaded · `1` some rows failed or were skipped ·
`2` aborted (authentication/transport) · `3` input error.
