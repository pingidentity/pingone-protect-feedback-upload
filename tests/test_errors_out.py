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
"""Tests for the --errors-out fix-and-resubmit file, including round-trips."""

import json
import tempfile
import unittest

from protect_feedback_upload.batcher import BatchResult, ErrorObject, FailedItem
from protect_feedback_upload.errors_out import DIAG_COLUMNS, write_errors
from protect_feedback_upload.model import CSV_COLUMNS, RiskFeedback, ValidationError
from protect_feedback_upload.reader import parse_csv, parse_ndjson
from protect_feedback_upload.validate import validate_item


def skip(idx, line, field, msg):
    return ValidationError(input_index=idx, source_line=line, field=field, message=msg)


CSV_INPUT = (
    "subject_type,subject_value,verdict,occurred_from,occurred_to,trigger,flow_type,"
    "confirmed_at,reason,analyst,confidence,fraud_type,attributes\n"
    "EXTERNAL_TRANSACTION_ID,TXN-1,NOPE,2026-05-10T08:03:17.000Z,,,,,,,,,\n"  # bad verdict
    "EXTERNAL_TRANSACTION_ID,TXN-2,FRAUD,bad-ts,,,,,,,,,\n"  # bad timestamp
    "TIME_RANGE,,FRAUD,2026-01-25T08:51:01.000Z,,,,,,,,,\n"  # TIME_RANGE needs occurred_to
)

NDJSON_INPUT = (
    '{"subject":{"type":"EXTERNAL_TRANSACTION_ID","value":"N-1"},"verdict":"NOPE","occurredFrom":"2026-05-10T08:03:17.000Z"}\n'
    "{broken json\n"
)


def validate_all(parsed):
    """Mirror the uploader: parse errors + per-item validation violations."""
    all_errors = []
    for t in parsed:
        all_errors.extend(validate_item(t.item, t.input_index, t.source_line))
    return all_errors


class _WriterMixin:
    def _write(self, fmt, skipped, failed=(), items=None, raw=None, batch_failures=None):
        path = tempfile.mktemp(suffix=".csv" if fmt == "csv" else ".ndjson")
        count = write_errors(path, fmt, skipped, list(failed), items or {}, raw or {}, batch_failures)
        return path, count


class TestErrorsOutCsv(unittest.TestCase, _WriterMixin):
    def test_roundtrip_rejected_csv_is_parseable(self):
        """The fix-and-resubmit contract: the errors file re-parses with
        every standard column present."""
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        errs = validate_all(parsed)
        self.assertEqual(len(errs), 3)  # bad verdict, bad ts, missing occurred_to
        items = {t.input_index: t.item for t in parsed}
        path, count = self._write("csv", errs, items=items, raw=raw)
        self.assertEqual(count, 3)

        re_items, re_errs, re_raw = parse_csv(open(path).read())
        self.assertEqual(re_errs, [])  # structure OK
        self.assertEqual(len(re_items), 3)
        # The original bad data must survive the round trip.
        re_by_line = {t.source_line: t.item for t in re_items}
        self.assertEqual(re_by_line[2].verdict, "NOPE")
        self.assertEqual(re_by_line[3].occurred_from, "bad-ts")

    def test_diagnostics_present_but_ignored(self):
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        errs = validate_all(parsed)
        items = {t.input_index: t.item for t in parsed}
        path, _ = self._write("csv", errs, items=items, raw=raw)
        header = open(path).readline().strip().split(",")
        for col in CSV_COLUMNS:
            self.assertIn(col, header)
        for col in DIAG_COLUMNS:
            self.assertIn(col, header)

    def test_unparseable_row_preserved_in_raw_row(self):
        # A row with the wrong field count has no parsed item; raw_row must
        # carry its cells so the analyst can recover the data.
        broken = CSV_INPUT + "just-one-cell\n"
        parsed, parse_errs, raw = parse_csv(broken)
        self.assertEqual(len(parse_errs), 1)  # the structural one
        errs = validate_all(parsed) + parse_errs
        items = {t.input_index: t.item for t in parsed}
        path, count = self._write("csv", errs, items=items, raw=raw)
        self.assertEqual(count, 4)
        last = open(path).read().strip().splitlines()[-1]
        self.assertIn("just-one-cell", last)

    def test_sorted_by_input_index_with_failures_merged(self):
        errs = [skip(3, 4, "verdict", "bad verdict"), skip(1, 2, "verdict", "bad verdict")]
        failed = [
            FailedItem(input_index=2, source_line=3, error=ErrorObject(code="INVALID_DATA", message="nope"))
        ]
        items = {1: RiskFeedback(), 2: RiskFeedback(), 3: RiskFeedback()}
        path, count = self._write("csv", errs, failed, items, {})
        self.assertEqual(count, 3)
        rows = open(path).read().strip().splitlines()[1:]
        self.assertEqual(len(rows), 3)
        # input_index column strictly ascending
        idxs = [int(r.split(",")[-5]) for r in rows]
        self.assertEqual(idxs, [1, 2, 3])


class TestErrorsOutNdjson(unittest.TestCase, _WriterMixin):
    def test_roundtrip_rejected_ndjson_is_parseable(self):
        parsed, parse_errs, raw = parse_ndjson(NDJSON_INPUT)
        self.assertEqual(len(parse_errs), 1)  # the broken line
        errs = validate_all(parsed) + parse_errs
        items = {t.input_index: t.item for t in parsed}
        path, count = self._write("ndjson", errs, items=items, raw=raw)
        self.assertEqual(count, 2)

        re_items, re_errs, _ = parse_ndjson(open(path).read())
        self.assertEqual(len(re_items), 2)  # both lines are JSON objects now
        self.assertEqual(re_errs, [])
        re_by_line = {t.source_line: t.item for t in re_items}
        self.assertEqual(re_by_line[1].verdict, "NOPE")  # data survived

    def test_broken_json_line_carries_rawline(self):
        parsed, parse_errs, raw = parse_ndjson(NDJSON_INPUT)
        errs = validate_all(parsed) + parse_errs
        items = {t.input_index: t.item for t in parsed}
        path, _ = self._write("ndjson", errs, items=items, raw=raw)
        lines = [json.loads(l) for l in open(path).read().strip().splitlines()]
        self.assertIn("rawLine", lines[1])
        self.assertIn("broken json", lines[1]["rawLine"])
        self.assertEqual(lines[1]["errorMessage"], parse_errs[0].message)

    def test_parse_ok_but_invalid_line_carries_item_fields(self):
        parsed, parse_errs, raw = parse_ndjson(NDJSON_INPUT)
        errs = validate_all(parsed)
        items = {t.input_index: t.item for t in parsed}
        path, _ = self._write("ndjson", errs, items=items, raw=raw)
        first = json.loads(open(path).readline())
        self.assertEqual(first["verdict"], "NOPE")
        self.assertEqual(first["errorField"], "verdict")


if __name__ == "__main__":
    unittest.main()


class TestBatchFailures(unittest.TestCase, _WriterMixin):
    """Whole-batch failures (4xx, exhausted retries, abort) must land in
    the errors file too — their rows never landed anywhere."""

    def test_batch_rejection_rows_written(self):
        # All 3 rows valid locally (nothing to validate-skip); the API
        # rejected the whole batch with HTTP 400 → all 3 rows in the file.
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        self.assertEqual(len(parse_errs), 0)
        items = {t.input_index: t.item for t in parsed}
        batch_result = BatchResult(
            index=1, items=3, http_status=400, failed=3,
            error=ErrorObject(code="INVALID_DATA", message="batch rejected"),
        )
        batch_failures = [(batch_result, [1, 2, 3], [2, 3, 4], [])]
        path, count = self._write("csv", [], items=items, raw=raw, batch_failures=batch_failures)
        self.assertEqual(count, 3)
        rows = open(path).read().strip().splitlines()[1:]
        self.assertEqual(len(rows), 3)
        self.assertIn("batch rejected", open(path).read())

    def test_local_skip_message_wins_over_batch_overlap(self):
        # A row skipped locally can never be in a batch (it was never sent),
        # but if both sources ever carried it, the specific local message
        # must win over the batch-level one.
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        errs = validate_all(parsed) + parse_errs
        items = {t.input_index: t.item for t in parsed}
        batch_failures = [
            (BatchResult(index=1, items=3, http_status=400, failed=3, error=ErrorObject(message="batch rejected")), [1, 2, 3], [2, 3, 4], [])
        ]
        path, count = self._write("csv", errs, items=items, raw=raw, batch_failures=batch_failures)
        self.assertEqual(count, 3)  # merged, one row per item
        body = open(path).read()
        self.assertIn("must be one of FRAUD", body)  # local message kept
        self.assertNotIn("batch rejected", body)

    def test_per_item_message_beats_batch_message(self):
        # Same item failed per-item (specific reason) and appears in a
        # batch-failure list (generic reason) — the specific message wins.
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        items = {t.input_index: t.item for t in parsed}
        failed = [FailedItem(input_index=1, source_line=2, error=ErrorObject(code="INVALID_DATA", message="specific reason"))]
        batch_failures = [
            (BatchResult(index=1, items=3, http_status=400, failed=3, error=ErrorObject(message="batch rejected")), [1, 2, 3], [2, 3, 4], [])
        ]
        path, count = self._write("csv", [], failed, items=items, raw=raw, batch_failures=batch_failures)
        self.assertEqual(count, 3)
        body = open(path).read()
        self.assertIn("specific reason", body)
        # the other two rows carry the batch-level message
        self.assertIn("batch rejected", body)

    def test_abort_captures_unattempted_batches(self):
        # Simulate an abort: batch 1 (items 1-2) got 401, batches 2+ never ran.
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        items = {t.input_index: t.item for t in parsed}
        abort_batch = BatchResult(
            index=1, items=2, http_status=401, failed=2,
            error=ErrorObject(code="HTTP_401", message="Unauthorized"),
        )
        not_attempted = BatchResult(index=0, items=1, error=ErrorObject(message="not attempted (run aborted)"))
        batch_failures = [
            (abort_batch, [1, 2], [2, 3], []),
            (not_attempted, [3], [4], []),
        ]
        path, count = self._write("csv", [], items=items, raw=raw, batch_failures=batch_failures)
        self.assertEqual(count, 3)
        body = open(path).read()
        self.assertIn("Unauthorized", body)
        self.assertIn("not attempted (run aborted)", body)

    def test_rows_with_data_survive_batch_failure(self):
        # Batch-failed rows must still carry their original data columns,
        # not blanks — the analyst fixes and re-runs them.
        parsed, parse_errs, raw = parse_csv(CSV_INPUT)
        items = {t.input_index: t.item for t in parsed}
        batch_failures = [
            (BatchResult(index=1, items=3, http_status=400, failed=3, error=ErrorObject(message="rejected")), [1, 2, 3], [2, 3, 4], [])
        ]
        path, _ = self._write("csv", [], items=items, raw=raw, batch_failures=batch_failures)
        re_items, re_errs, _ = parse_csv(open(path).read())
        self.assertEqual(re_errs, [])
        re_by_line = {t.source_line: t.item for t in re_items}
        self.assertEqual(re_by_line[2].verdict, "NOPE")  # data intact
        self.assertEqual(re_by_line[3].occurred_from, "bad-ts")
