# Copyright © 2026 Ping Identity Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Write rejected rows to a file for the fix-and-resubmit workflow.

The bulk POST is not idempotent, so after a partial success the analyst
must re-submit only the rejected rows. This module produces that file:

- CSV input → CSV output: the 13 standard columns (fix in place) plus
  diagnostic columns. The diagnostics are ignored on re-read: the CSV
  reader locates every standard column by name and the field-count check
  compares against the same file's header, so the file runs through the
  tool unchanged.
- NDJSON input → NDJSON output: the camelCase item fields (when the line
  parsed) plus ``errorField``/``errorMessage`` diagnostics, which
  ``_item_from_json`` ignores on re-read. Lines that were not valid JSON
  carry their original text under ``rawLine``.

Server-rejected rows (per-item failures inside a 200 response) are
written from the parsed item, keyed by input index. Rows that failed to
parse have no item — their raw cells/line are preserved in ``raw_row`` /
``rawLine`` so nothing is lost.
"""

from __future__ import annotations

import csv
import json

from .batcher import FailedItem
from .model import CSV_COLUMNS, RiskFeedback, ValidationError

DIAG_COLUMNS = ("input_index", "source_line", "error_field", "error_message", "raw_row")


def _normalized_errors(
    skipped: list[ValidationError], failed: list[FailedItem]
) -> list[tuple[int, int, str, str]]:
    """Merge local skips and server-side failures into one error stream,
    ordered by input position: (input_index, source_line, field, message)."""
    merged = [
        (s.input_index, s.source_line, s.field, s.message) for s in skipped
    ]
    merged += [
        (f.input_index, f.source_line, "", str(f.error)) for f in failed
    ]
    merged.sort(key=lambda e: e[0])
    return merged


def _item_to_row(item: RiskFeedback) -> list[str]:
    attrs = json.dumps(item.attributes, ensure_ascii=False) if item.attributes else ""
    return [
        item.subject_type,
        item.subject_value,
        item.verdict,
        item.occurred_from,
        item.occurred_to,
        item.trigger,
        item.flow_type,
        item.confirmed_at,
        item.reason,
        item.analyst,
        item.confidence,
        item.fraud_type,
        attrs,
    ]


def write_errors_csv(
    path: str,
    skipped: list[ValidationError],
    failed: list[FailedItem],
    items_by_index: dict[int, RiskFeedback],
    raw_records: dict[int, list[str]],
) -> int:
    """Write rejected rows as a re-runnable CSV. Returns the row count."""
    errors = _normalized_errors(skipped, failed)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(CSV_COLUMNS) + list(DIAG_COLUMNS))
        for input_index, source_line, field, message in errors:
            item = items_by_index.get(input_index)
            if item is not None:
                data = _item_to_row(item)
                raw = ""
            else:
                record = raw_records.get(input_index, [])
                data = [""] * len(CSV_COLUMNS)
                raw = ",".join(record)
            writer.writerow(
                data + [input_index, source_line, field, message, raw]
            )
    return len(errors)


def write_errors_ndjson(
    path: str,
    skipped: list[ValidationError],
    failed: list[FailedItem],
    items_by_index: dict[int, RiskFeedback],
    raw_lines: dict[int, str],
) -> int:
    """Write rejected rows as re-runnable NDJSON. Returns the line count."""
    errors = _normalized_errors(skipped, failed)
    with open(path, "w", encoding="utf-8") as fh:
        for input_index, source_line, field, message in errors:
            obj: dict = {}
            item = items_by_index.get(input_index)
            if item is not None:
                obj.update(item.to_wire())
            else:
                raw = raw_lines.get(input_index)
                if raw is not None:
                    obj["rawLine"] = raw
            obj["inputIndex"] = input_index
            obj["sourceLine"] = source_line
            if field:
                obj["errorField"] = field
            obj["errorMessage"] = message
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return len(errors)


def write_errors(
    path: str,
    fmt: str,
    skipped: list[ValidationError],
    failed: list[FailedItem],
    items_by_index: dict[int, RiskFeedback],
    raw_rows: dict,
) -> int:
    """Write rejected rows to ``path`` in the input's format."""
    if fmt == "ndjson":
        return write_errors_ndjson(path, skipped, failed, items_by_index, raw_rows)
    return write_errors_csv(path, skipped, failed, items_by_index, raw_rows)
