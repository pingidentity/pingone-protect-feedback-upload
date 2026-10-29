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
"""End-to-end tests for the upload flow with a fake poster, plus the CLI."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from protect_feedback_upload.__main__ import main
from protect_feedback_upload.report import UploadReport
from protect_feedback_upload.uploader import run_upload


def make_csv(n_rows, invalid_row=None):
    header = (
        "subject_type,subject_value,verdict,occurred_from,occurred_to,trigger,flow_type,"
        "confirmed_at,reason,analyst,confidence,fraud_type,attributes"
    )
    rows = []
    for i in range(n_rows):
        if invalid_row is not None and i == invalid_row:
            rows.append(f"EXTERNAL_TRANSACTION_ID,TXN-{i},NOT_A_VERDICT,2026-05-10T08:03:17.000Z,,,,,,,,,")
        else:
            rows.append(f"EXTERNAL_TRANSACTION_ID,TXN-{i},FRAUD,2026-05-10T08:03:17.000Z,,,,,,,,,")
    return header + "\n" + "\n".join(rows) + "\n"


class FakePoster:
    def __init__(self, results):
        self.results = list(results)

    def post_bulk(self, batch):
        return self.results.pop(0)


def ok_batch(n):
    return (
        200,
        {"summary": {"requested": n, "successful": n, "errors": 0, "skipped": 0}},
        None,
    )


class TestRunUpload(unittest.TestCase):
    def _write(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(lambda: None)  # OS cleans tmp
        return f.name

    def test_dry_run_sends_nothing(self):
        path = self._write(make_csv(43))
        poster = FakePoster([])
        report = run_upload(path, "env-id", poster, dry_run=True)
        self.assertEqual(len(poster.results), 0)  # never called
        self.assertEqual([b.items for b in report.batches], [20, 20, 3])
        self.assertEqual(report.requested, 43)
        self.assertEqual(report.successful, 0)

    def test_validation_skips_invalid_rows(self):
        path = self._write(make_csv(5, invalid_row=2))
        poster = FakePoster([ok_batch(4)])
        report = run_upload(path, "env-id", poster)
        self.assertEqual(report.requested, 4)  # only valid rows requested
        self.assertEqual(len(report.skipped_items), 1)
        self.assertEqual(report.skipped_items[0].source_line, 4)  # header = line 1
        self.assertEqual(report.successful, 4)
        self.assertEqual(report.failed, 0)

    def test_all_invalid_rows_no_post(self):
        # every row invalid via verdict typo
        content = make_csv(3, invalid_row=0).replace("FRAUD", "NOPE")
        path = self._write(content)
        poster = FakePoster([])
        report = run_upload(path, "env-id", poster)
        self.assertEqual(report.requested, 0)
        self.assertEqual(len(report.skipped_items), 3)
        self.assertEqual(report.batches, [])

    def test_batch_counts_aggregate(self):
        path = self._write(make_csv(25))
        poster = FakePoster([ok_batch(20), ok_batch(5)])
        report = run_upload(path, "env-id", poster)
        self.assertEqual(report.successful, 25)
        self.assertEqual(len(report.batches), 2)

    def test_401_abort_counts_remaining_as_skipped(self):
        path = self._write(make_csv(25))
        results = [
            ok_batch(20),
            (
                401,
                None,
                __import__(
                    "protect_feedback_upload.batcher", fromlist=["ErrorObject"]
                ).ErrorObject(code="HTTP_401", message="Unauthorized"),
            ),
        ]
        poster = FakePoster(results)
        report = run_upload(path, "env-id", poster)
        self.assertTrue(report.aborted)
        self.assertEqual(report.successful, 20)
        self.assertEqual(report.failed, 5)
        self.assertEqual(report.skipped, 0)

    def test_failed_items_mapped_back_to_lines(self):
        path = self._write(make_csv(3))
        parsed = {
            "summary": {"requested": 3, "successful": 2, "errors": 1, "skipped": 0},
            "responses": [
                {"status": "SUCCESS", "body": {}},
                {"status": "FAILED", "body": {"p1Error": {"code": "INVALID_DATA", "message": "bad timestamp"}}},
                {"status": "SUCCESS", "body": {}},
            ],
        }
        poster = FakePoster([(200, parsed, None)])
        report = run_upload(path, "env-id", poster)
        self.assertEqual(len(report.failed_items), 1)
        self.assertEqual(report.failed_items[0].input_index, 2)
        self.assertEqual(report.failed_items[0].source_line, 3)


class TestCLI(unittest.TestCase):
    def _run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_text_report(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(make_csv(2))
        f.close()
        code, out, _ = self._run_main(
            ["upload", f.name, "--environment-id", "e", "--dry-run"]
        )
        self.assertEqual(code, 0)
        self.assertIn("2 requested", out)
        self.assertIn("batch 1", out)

    def test_dry_run_json_report(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(make_csv(2))
        f.close()
        code, out, _ = self._run_main(
            ["upload", f.name, "--environment-id", "e", "--dry-run", "-O", "json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["summary"]["requested"], 2)

    def test_invalid_batch_size(self):
        code, _, err = self._run_main(
            ["upload", "x.csv", "--environment-id", "e", "--dry-run", "--batch-size", "21"]
        )
        self.assertEqual(code, 3)
        self.assertIn("between 1 and 20", err)

    def test_missing_file(self):
        code, _, err = self._run_main(
            ["upload", "/nonexistent/file.csv", "--environment-id", "e", "--dry-run"]
        )
        self.assertEqual(code, 3)
        self.assertIn("Input error", err)

    def test_live_run_with_stubbed_poster(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(make_csv(2))
        f.close()

        captured = {}

        class CapPoster:
            def post_bulk(self, batch):
                captured["n"] = len(batch)
                return (
                    200,
                    {"summary": {"requested": len(batch), "successful": len(batch), "errors": 0, "skipped": 0}},
                    None,
                )

        with mock.patch(
            "protect_feedback_upload.__main__.PingcliPoster", return_value=CapPoster()
        ) as poster_cls:
            code, out, _ = self._run_main(
                ["upload", f.name, "--environment-id", "e"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["n"], 2)
        self.assertIn("2 requested, 2 successful", out)
        # The explicit flag must reach the poster untouched.
        self.assertEqual(poster_cls.call_args.kwargs["environment_id"], "e")

    def test_live_run_resolves_env_id_from_profile(self):
        # No --environment-id: the CLI must resolve it from the pingcli
        # profile (mocked — the test never shells out to real pingcli).
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(make_csv(2))
        f.close()

        captured = {}

        class CapPoster:
            def post_bulk(self, batch):
                captured["n"] = len(batch)
                return (
                    200,
                    {"summary": {"requested": len(batch), "successful": len(batch), "errors": 0, "skipped": 0}},
                    None,
                )

        with mock.patch(
            "protect_feedback_upload.__main__.resolve_environment_id",
            return_value="env-from-profile",
        ), mock.patch(
            "protect_feedback_upload.__main__.PingcliPoster", return_value=CapPoster()
        ) as poster_cls:
            code, out, _ = self._run_main(["upload", f.name])
        self.assertEqual(code, 0)
        self.assertEqual(captured["n"], 2)
        self.assertIn("2 requested, 2 successful", out)
        # The profile-resolved ID must reach the poster, not the flag default.
        self.assertEqual(poster_cls.call_args.kwargs["environment_id"], "env-from-profile")

    def test_live_run_fails_cleanly_when_env_id_unresolvable(self):
        # Neither flag nor profile supplies an environment ID: input error,
        # exit 3, and a message naming both remedies. The resolver is mocked
        # so this path is hermetic on machines with and without pingcli.
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        f.write(make_csv(2))
        f.close()

        with mock.patch(
            "protect_feedback_upload.__main__.resolve_environment_id",
            return_value=None,
        ):
            code, out, err = self._run_main(["upload", f.name])
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("--environment-id", err)
        self.assertIn("profile", err)
        self.assertIn("pingcli pingone init", err)


if __name__ == "__main__":
    unittest.main()
