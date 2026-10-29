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
"""Upload orchestration: read → validate → batch → post with retry → report."""

from __future__ import annotations

from .batcher import (
    BatchResult,
    ErrorObject,
    build_failed_items,
    post_batch_with_retry,
    split_into_batches,
)
from .model import MAX_BATCH_SIZE, RiskFeedback
from .reader import read_input
from .report import UploadReport
from .validate import validate_item


def run_upload(
    input_path: str,
    environment_id: str | None,
    poster,
    batch_size: int = MAX_BATCH_SIZE,
    dry_run: bool = False,
) -> UploadReport:
    """Run the full upload flow.

    ``poster`` is anything with ``post_bulk(batch)``; production uses
    :class:`.pingcli_client.PingcliPoster`. With ``dry_run`` the poster is
    never called — the report carries validation results only.
    """
    parsed, parse_errors, fmt, raw_rows = read_input(input_path)

    report = UploadReport()
    report.skipped_items = list(parse_errors)
    report.raw_rows = raw_rows
    report.input_format = fmt

    # Local pre-validation: a fail-fast convenience for the user — the API
    # remains authoritative. Items with violations are never sent.
    items = []
    for tagged in parsed:
        violations = validate_item(tagged.item, tagged.input_index, tagged.source_line)
        if violations:
            report.skipped_items.extend(violations)
        else:
            items.append(tagged)
    report.requested = len(items)
    # Every parsed row (valid or not) keyed by input position — used by
    # --errors-out to reconstruct rejected rows with their data intact.
    report.items_by_index = {t.input_index: t.item for t in parsed}

    if not items:
        # Nothing valid to send; the report still lists the skipped rows.
        report.skipped = len(report.skipped_items)
        return report

    if dry_run:
        # Dry run: report what WOULD be sent, batched, without network I/O.
        report.batches = [
            BatchResult(index=i + 1, items=len(batch), http_status=0)
            for i, batch in enumerate(split_into_batches(items, batch_size))
        ]
        return report

    source_line_by_index = {t.input_index: t.source_line for t in items}

    batches = split_into_batches(items, batch_size)
    for batch_idx, batch in enumerate(batches):
        item_indices = [t.input_index for t in batch]
        source_lines = [t.source_line for t in batch]
        payloads = [t.item for t in batch]

        outcome = post_batch_with_retry(poster, payloads, item_indices)

        if outcome is None:  # pragma: no cover - defensive; post_batch_with_retry always returns
            break

        batch_result = outcome.batch or BatchResult(index=batch_idx + 1, items=len(payloads))
        batch_result.index = batch_idx + 1
        report.batches.append(batch_result)

        report.successful += batch_result.successful
        report.failed += batch_result.failed
        report.skipped += batch_result.skipped

        if outcome.abort:
            report.aborted = True
            report.abort_error = outcome.abort_error
            # Items of this batch and all later batches were not uploaded.
            remaining = report.requested - report.successful - report.failed - report.skipped
            report.skipped += remaining
            # The aborting batch and every later batch were never accepted —
            # capture them for --errors-out.
            report.batch_failures.append((batch_result, item_indices, source_lines, payloads))
            for later in batches[batch_idx + 1 :]:
                report.batch_failures.append(
                    (
                        BatchResult(
                            index=0, items=len(later), error=ErrorObject(message="not attempted (run aborted)")
                        ),
                        [t.input_index for t in later],
                        [t.source_line for t in later],
                        [t.item for t in later],
                    )
                )
            break

        # A batch that failed as a whole (4xx rejection, 500 after retries,
        # exhausted 429s, transport failure): its items never landed.
        if batch_result.failed and not outcome.parsed:
            report.batch_failures.append((batch_result, item_indices, source_lines, payloads))

        if outcome.parsed is not None:
            report.failed_items.extend(build_failed_items(outcome.parsed, item_indices, source_lines))

    return report
