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
"""Batching and the retry matrix for bulk uploads.

Port of the pingcli batcher. The generated SDK client / thin pingcli
command does NOT retry this operation — its retry loop returns on every
error-status branch — so this script owns the full matrix:

- 429: pause 1s, retry the same batch (bounded)
- 500 or transport error: retry up to 3 total attempts
- 200 with partial failure: never retried (the POST is not idempotent)
- 400 / other 4xx: log, skip the batch, count its items as failed
- 401 / 403: abort the run
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from .model import MAX_429_RETRIES, MAX_500_ATTEMPTS, PAUSE_429_SECONDS, RiskFeedback


@dataclass
class ErrorObject:
    """PingOne error shape (code + message) for reports and logs."""

    code: str = ""
    message: str = ""

    def to_wire(self) -> dict:
        out: dict = {}
        if self.code:
            out["code"] = self.code
        if self.message:
            out["message"] = self.message
        return out

    def __str__(self) -> str:
        if self.code and self.message:
            return f"{self.code}: {self.message}"
        if self.message:
            return self.message
        return self.code


@dataclass
class BatchResult:
    """The outcome of one bulk POST."""

    index: int = 0
    items: int = 0
    http_status: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    error: ErrorObject | None = None

    def to_wire(self) -> dict:
        out = {
            "index": self.index,
            "items": self.items,
            "httpStatus": self.http_status,
            "successful": self.successful,
            "failed": self.failed,
        }
        if self.skipped:
            out["skipped"] = self.skipped
        if self.error:
            out["error"] = self.error.to_wire()
        return out


@dataclass
class FailedItem:
    """One API-rejected item with its input position."""

    input_index: int
    source_line: int
    error: ErrorObject

    def to_wire(self) -> dict:
        return {
            "inputIndex": self.input_index,
            "sourceLine": self.source_line,
            "error": self.error.to_wire(),
        }


class Poster(Protocol):
    """Dispatches one bulk POST and returns the classified response.

    Returns (http_status, response_json_or_None, p1_error_or_None).
    p1_error is populated for non-2xx responses whose body carried a PingOne
    error object; for transport failures, status is 0 and p1_error carries
    the exception text.
    """

    def post_bulk(self, batch: list[RiskFeedback]) -> tuple[int, dict | None, ErrorObject | None]: ...


@dataclass
class _RetryOutcome:
    batch: BatchResult | None
    parsed: dict | None = None
    abort: bool = False
    abort_error: ErrorObject | None = None


def split_into_batches(items: list, batch_size: int = 20) -> list[list]:
    """Cut items into consecutive batches of at most batch_size items,
    preserving input order; the trailing partial batch is sent as-is."""
    if batch_size < 1 or not items:
        return []
    return [items[start : start + batch_size] for start in range(0, len(items), batch_size)]


def _wait(seconds: float) -> None:
    time.sleep(seconds)


def post_batch_with_retry(
    poster: Poster,
    batch: list,
    item_indices: list[int],
    log=lambda msg: None,
) -> _RetryOutcome | None:
    """Post one batch under the retry matrix.

    item_indices carries the 1-based input positions of this batch's items
    so per-item report entries point back at the input file. Returns None
    only when the run was aborted.
    """

    def batch_label() -> str:
        if not item_indices:
            return "?"
        if len(item_indices) == 1:
            return f"item {item_indices[0]}"
        return f"items {item_indices[0]}-{item_indices[-1]}"

    for attempt in range(1, 10000):
        result_status, parsed, p1_error = poster.post_bulk(batch)

        # Transport failure: status 0 — treat as 500, bounded retries.
        if result_status == 0:
            if attempt < MAX_500_ATTEMPTS:
                _wait(PAUSE_429_SECONDS)
                continue
            return _RetryOutcome(
                batch=BatchResult(
                    items=len(batch),
                    http_status=0,
                    failed=len(batch),
                    error=ErrorObject(message=f"transport error after {attempt} attempts: {p1_error}"),
                )
            )

        if result_status == 429:
            if attempt > MAX_429_RETRIES:
                return _RetryOutcome(
                    batch=BatchResult(
                        items=len(batch),
                        http_status=429,
                        failed=len(batch),
                        error=ErrorObject(
                            code="RATE_LIMITED",
                            message=f"still receiving 429 after {MAX_429_RETRIES} retries",
                        ),
                    )
                )
            _wait(PAUSE_429_SECONDS)
            continue

        if result_status == 500:
            if attempt < MAX_500_ATTEMPTS:
                continue
            return _RetryOutcome(
                batch=BatchResult(
                    items=len(batch),
                    http_status=500,
                    failed=len(batch),
                    error=ErrorObject(message=f"HTTP 500 after {attempt} attempts"),
                )
            )

        if result_status in (401, 403) or result_status < 0:
            # 401/403: auth rejection. Negative status: deterministic
            # command failure (auth/config) — no HTTP round trip happened,
            # so retries cannot help. Both abort the run.
            err = p1_error or ErrorObject(
                code=f"HTTP_{result_status}" if result_status > 0 else "ABORTED",
                message=f"HTTP {result_status}" if result_status > 0 else "aborted",
            )
            return _RetryOutcome(
                abort=True,
                batch=BatchResult(
                    items=len(batch),
                    http_status=max(result_status, 0),
                    failed=len(batch),
                    error=err,
                ),
                abort_error=err,
            )

        if 200 <= result_status < 300:
            return _summarize_bulk_success(result_status, parsed, len(batch))

        # 400 and other 4xx: log, skip the batch, count items failed.
        err = p1_error or ErrorObject(code=f"HTTP_{result_status}", message=f"HTTP {result_status}")
        log(f"risk-feedback batch {batch_label()}: API rejected batch (HTTP {result_status}): {err}")

        return _RetryOutcome(
            batch=BatchResult(
                items=len(batch),
                http_status=result_status,
                failed=len(batch),
                error=err,
            )
        )


def _summarize_bulk_success(status: int, parsed: dict | None, n_items: int) -> _RetryOutcome:
    """Count per-item outcomes from the decoded bulk response.

    The API remains authoritative: a partial-failure response is never
    retried.
    """
    batch = BatchResult(items=n_items, http_status=status)

    if parsed and isinstance(parsed.get("summary"), dict):
        summary = parsed["summary"]
        batch.successful = int(summary.get("successful", 0))
        batch.failed = int(summary.get("errors", 0))
        batch.skipped = int(summary.get("skipped", 0))

    return _RetryOutcome(batch=batch, parsed=parsed)


def build_failed_items(parsed: dict | None, item_indices: list[int], source_lines: list[int]) -> list[FailedItem]:
    """Map a bulk response's per-item error entries onto input positions.

    The responses[] array is order-aligned with items[] per the API
    contract; a failed element is {status, body} where body carries the
    PingOne error object; a SUCCESS element's body is the created object.
    """
    if not parsed or not isinstance(parsed.get("responses"), list):
        return []

    failed: list[FailedItem] = []
    for i, resp in enumerate(parsed["responses"]):
        if i >= len(item_indices):
            break
        if resp.get("status") == "SUCCESS":
            continue

        body = resp.get("body") or {}
        err = body.get("p1Error") or {}
        if not err:
            continue

        failed.append(
            FailedItem(
                input_index=item_indices[i],
                source_line=source_lines[i],
                error=ErrorObject(code=err.get("code", ""), message=err.get("message", "")),
            )
        )

    return failed
