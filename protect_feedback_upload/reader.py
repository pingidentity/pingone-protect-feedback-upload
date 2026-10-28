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
"""CSV/NDJSON input readers for Risk Feedback v2 label files.

Port of the pingcli readers: format sniffing by extension then content,
RFC 4180 CSV with free column order, NDJSON with per-line error recovery.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from dataclasses import dataclass

from .model import CSV_COLUMNS, RiskFeedback, ValidationError

_FORMAT_CSV = "csv"
_FORMAT_NDJSON = "ndjson"


@dataclass
class TaggedItem:
    """A parsed item plus its input position.

    input_index is the 1-based item index across the whole input;
    source_line is the CSV row number (header = row 1) or NDJSON line number.
    """

    item: RiskFeedback
    input_index: int
    source_line: int


def sniff_format(path: str, content: str) -> str:
    """Choose the input format from the file extension, falling back to
    content sniffing: first non-space byte ``{`` means NDJSON.

    ``path`` may be ``-`` (stdin), in which case only content sniffing
    applies.
    """
    lower = path.lower()
    if lower.endswith(".csv"):
        return _FORMAT_CSV
    if lower.endswith(".ndjson") or lower.endswith(".jsonl"):
        return _FORMAT_NDJSON

    for ch in content:
        if ch in " \t\r\n":
            continue
        if ch == "{":
            return _FORMAT_NDJSON
        break

    return _FORMAT_CSV


def _parse_attributes(raw: str) -> tuple[dict[str, str] | None, str | None]:
    trimmed = raw.strip()
    if not trimmed:
        return None, None  # an empty attributes cell means "no attributes"
    try:
        attrs = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        return None, f"attributes must be a flat JSON object with string values: {exc}"
    if not isinstance(attrs, dict) or not all(isinstance(v, str) for v in attrs.values()):
        return None, "attributes must be a flat JSON object with string values"
    return attrs, None


def _parse_csv_record(record: list[str], col_index: dict[str, int], header_len: int) -> tuple[RiskFeedback, list[ValidationError]]:
    if len(record) != header_len:
        return RiskFeedback(), [
            ValidationError(
                input_index=0,
                source_line=0,
                field="",
                message=f"record has {len(record)} fields, header declares {header_len}",
            )
        ]

    attrs, attr_err = _parse_attributes(record[col_index["attributes"]].strip())
    if attr_err:
        return RiskFeedback(), [
            ValidationError(input_index=0, source_line=0, field="attributes", message=attr_err)
        ]

    item = RiskFeedback(
        subject_type=record[col_index["subject_type"]].strip(),
        subject_value=record[col_index["subject_value"]].strip(),
        verdict=record[col_index["verdict"]].strip(),
        occurred_from=record[col_index["occurred_from"]].strip(),
        occurred_to=record[col_index["occurred_to"]].strip(),
        trigger=record[col_index["trigger"]].strip(),
        flow_type=record[col_index["flow_type"]].strip(),
        confirmed_at=record[col_index["confirmed_at"]].strip(),
        reason=record[col_index["reason"]].strip(),
        analyst=record[col_index["analyst"]].strip(),
        confidence=record[col_index["confidence"]].strip(),
        fraud_type=record[col_index["fraud_type"]].strip(),
        attributes=attrs or {},
    )
    return item, []


def parse_csv(content: str) -> tuple[list[TaggedItem], list[ValidationError]]:
    """Parse RFC 4180 CSV.

    The first record is the header; column order is free but every record
    must carry exactly the header's columns.
    """
    reader = csv.reader(io.StringIO(content))
    records = list(reader)

    if len(records) < 1:
        raise ValueError("CSV input is empty: expected a header row and at least one data row")

    header = records[0]
    col_index = {name.strip(): i for i, name in enumerate(header)}

    for col in CSV_COLUMNS:
        if col not in col_index:
            raise ValueError(f'CSV header is missing required column "{col}"')

    items: list[TaggedItem] = []
    errs: list[ValidationError] = []

    for i, record in enumerate(records[1:]):
        input_index = i + 1
        source_line = i + 2  # header is line 1

        item, row_errs = _parse_csv_record(record, col_index, len(header))
        for row_err in row_errs:
            row_err.input_index = input_index
            row_err.source_line = source_line
            errs.append(row_err)

        if not row_errs:
            items.append(TaggedItem(item=item, input_index=input_index, source_line=source_line))

    return items, errs


def parse_ndjson(content: str) -> tuple[list[TaggedItem], list[ValidationError]]:
    """Parse newline-delimited JSON, one object per line.

    Blank lines are skipped. A malformed line yields a validation error for
    that line and parsing continues, so a single bad line does not hide the
    errors on later lines.
    """
    items: list[TaggedItem] = []
    errs: list[ValidationError] = []

    input_index = 0
    for line_no, raw_line in enumerate(content.split("\n")):
        line = raw_line.strip()
        if not line:
            continue

        input_index += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errs.append(
                ValidationError(
                    input_index=input_index,
                    source_line=line_no + 1,
                    field="",
                    message=f"malformed JSON: {exc}",
                )
            )
            continue

        if not isinstance(obj, dict):
            errs.append(
                ValidationError(
                    input_index=input_index,
                    source_line=line_no + 1,
                    field="",
                    message="line is not a JSON object",
                )
            )
        else:
            items.append(TaggedItem(item=_item_from_json(obj), input_index=input_index, source_line=line_no + 1))

    if input_index == 0:
        raise ValueError("NDJSON input is empty: expected at least one JSON object")

    return items, errs


def _item_from_json(obj: dict) -> RiskFeedback:
    """Map a camelCase wire object onto a RiskFeedback (NDJSON + create path)."""
    subject = obj.get("subject") or {}
    if not isinstance(subject, dict):
        subject = {}
    return RiskFeedback(
        subject_type=subject.get("type") or "",
        subject_value=subject.get("value") or "",
        verdict=obj.get("verdict") or "",
        occurred_from=obj.get("occurredFrom") or "",
        occurred_to=obj.get("occurredTo") or "",
        trigger=obj.get("trigger") or "",
        flow_type=obj.get("flowType") or "",
        confirmed_at=obj.get("confirmedAt") or "",
        reason=obj.get("reason") or "",
        analyst=obj.get("analyst") or "",
        confidence=obj.get("confidence") or "",
        fraud_type=obj.get("fraudType") or "",
        attributes=obj.get("attributes") or {},
    )


def read_input(path: str) -> tuple[list[TaggedItem], list[ValidationError], str]:
    """Read a label file (or stdin with ``-``), sniff the format, parse it.

    Returns (valid items, validation errors, format name). Raises ValueError
    for structurally broken input (empty file, missing CSV columns).
    """
    if path == "-":
        content = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()

    fmt = sniff_format(path, content)
    if fmt == _FORMAT_NDJSON:
        items, errs = parse_ndjson(content)
    else:
        items, errs = parse_csv(content)
    return items, errs, fmt
