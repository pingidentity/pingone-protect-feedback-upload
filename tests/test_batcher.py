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
"""Unit tests for the retry matrix and batching (ports of the pingcli batcher cases)."""

import unittest
from unittest import mock

from protect_feedback_upload.batcher import (
    ErrorObject,
    build_failed_items,
    post_batch_with_retry,
    split_into_batches,
)


class FakePoster:
    """Scripted poster: pops one scripted result per call."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def post_bulk(self, batch):
        self.calls.append(batch)
        if not self.results:
            raise AssertionError("unexpected extra post_bulk call")
        return self.results.pop(0)


def ok(summary=None, responses=None):
    body = {"summary": summary or {"requested": 1, "successful": 1, "errors": 0, "skipped": 0}}
    if responses is not None:
        body["responses"] = responses
    return (200, body, None)


def status(code, p1=None):
    return (code, None, p1 or ErrorObject(code=f"HTTP_{code}", message=f"HTTP {code}"))


class TestSplitIntoBatches(unittest.TestCase):
    def test_batching(self):
        items = list(range(43))
        batches = split_into_batches(items, 20)
        self.assertEqual([len(b) for b in batches], [20, 20, 3])
        self.assertEqual(batches[0][0], 0)
        self.assertEqual(batches[-1][-1], 42)

    def test_exact_multiple(self):
        self.assertEqual([len(b) for b in split_into_batches(list(range(40)), 20)], [20, 20])

    def test_edge_cases(self):
        self.assertEqual(split_into_batches([], 20), [])
        self.assertEqual([len(b) for b in split_into_batches(list(range(3)), 0)], [])


class TestRetryMatrix(unittest.TestCase):
    def _run(self, results, items=None):
        poster = FakePoster(results)
        outcome = post_batch_with_retry(poster, items or [object()], [1, 2])
        return poster, outcome

    def test_happy_path(self):
        _, outcome = self._run([ok()])
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.successful, 1)
        self.assertEqual(outcome.batch.http_status, 200)

    def test_429_then_success(self):
        _, outcome = self._run([status(429), ok()])
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.successful, 1)

    @mock.patch("protect_feedback_upload.batcher._wait")
    def test_429_exhausted(self, _wait):
        results = [status(429)] * 27
        _, outcome = self._run(results)
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.http_status, 429)
        self.assertEqual(outcome.batch.error.code, "RATE_LIMITED")

    def test_500_retries_then_success(self):
        _, outcome = self._run([status(500), status(500), ok()])
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.successful, 1)

    def test_500_exhausted(self):
        _, outcome = self._run([status(500)] * 4)
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.http_status, 500)
        self.assertEqual(outcome.batch.failed, 1)

    def test_transport_retries_then_success(self):
        _, outcome = self._run(
            [(0, None, ErrorObject(message="conn refused")), ok()]
        )
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.successful, 1)

    def test_transport_exhausted(self):
        _, outcome = self._run([(0, None, ErrorObject(message="conn refused"))] * 4)
        self.assertFalse(outcome.abort)
        self.assertEqual(outcome.batch.http_status, 0)
        self.assertIn("transport error after 3 attempts", outcome.batch.error.message)

    def test_401_aborts(self):
        _, outcome = self._run([status(401)])
        self.assertTrue(outcome.abort)
        self.assertEqual(outcome.batch.http_status, 401)

    def test_403_aborts(self):
        _, outcome = self._run([status(403)])
        self.assertTrue(outcome.abort)

    def test_400_skips_without_retry(self):
        poster, outcome = self._run([status(400, ErrorObject(code="INVALID_DATA", message="bad"))])
        self.assertFalse(outcome.abort)
        self.assertEqual(len(poster.calls), 1)  # no retry
        self.assertEqual(outcome.batch.failed, 1)
        self.assertEqual(outcome.batch.error.code, "INVALID_DATA")

    def test_partial_failure_never_retried(self):
        summary = {"requested": 2, "successful": 1, "errors": 1, "skipped": 0}
        responses = [
            {"status": "SUCCESS", "body": {"id": "x"}},
            {"status": "FAILED", "body": {"p1Error": {"code": "INVALID_DATA", "message": "nope"}}},
        ]
        poster, outcome = self._run([ok(summary, responses)], items=[object(), object()])
        self.assertEqual(len(poster.calls), 1)  # NOT retried
        self.assertEqual(outcome.batch.successful, 1)
        self.assertEqual(outcome.batch.failed, 1)
        failed = build_failed_items(outcome.parsed, [1, 2], [2, 3])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].input_index, 2)
        self.assertEqual(failed[0].error.code, "INVALID_DATA")


class TestBuildFailedItems(unittest.TestCase):
    def test_missing_responses(self):
        self.assertEqual(build_failed_items({"summary": {}}, [1], [2]), [])

    def test_success_elements_skipped(self):
        parsed = {"responses": [{"status": "SUCCESS", "body": {"id": "x"}}]}
        self.assertEqual(build_failed_items(parsed, [1], [2]), [])

    def test_failed_element_maps_position(self):
        parsed = {
            "responses": [
                {"status": "SUCCESS", "body": {"id": "x"}},
                {"status": "FAILED", "body": {"p1Error": {"code": "C", "message": "m"}}},
            ]
        }
        failed = build_failed_items(parsed, [7, 8], [8, 9])
        self.assertEqual(failed[0].input_index, 8)
        self.assertEqual(failed[0].source_line, 9)


if __name__ == "__main__":
    unittest.main()
