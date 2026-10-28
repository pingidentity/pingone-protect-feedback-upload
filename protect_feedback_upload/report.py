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
"""Terminal report for an upload run: text for humans, JSON for machines."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .batcher import BatchResult, ErrorObject, FailedItem
from .model import ValidationError


@dataclass
class UploadReport:
    """The aggregate report for an upload run."""

    requested: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    batches: list[BatchResult] = field(default_factory=list)
    failed_items: list[FailedItem] = field(default_factory=list)
    skipped_items: list[ValidationError] = field(default_factory=list)
    aborted: bool = False
    abort_error: ErrorObject | None = None
    raw_rows: dict = field(default_factory=dict)
    items_by_index: dict = field(default_factory=dict)
    input_format: str = "csv"

    def write_errors_file(self, path: str) -> int:
        """Write all rejected rows (local skips + API failures) to ``path``
        in the input's format. Returns the row count written."""
        from .errors_out import write_errors

        return write_errors(
            path, self.input_format, self.skipped_items, self.failed_items,
            self.items_by_index, self.raw_rows,
        )

    def to_wire(self) -> dict:
        out: dict = {
            "summary": {
                "requested": self.requested,
                "successful": self.successful,
                "failed": self.failed,
                "skipped": self.skipped,
            },
            "batches": [b.to_wire() for b in self.batches],
        }
        if self.failed_items:
            out["failedItems"] = [f.to_wire() for f in self.failed_items]
        if self.skipped_items:
            out["skippedItems"] = [s.to_wire() for s in self.skipped_items]
        if self.aborted:
            out["aborted"] = True
            if self.abort_error:
                out["abortError"] = self.abort_error.to_wire()
        return out

    def render_text(self) -> str:
        lines: list[str] = []
        if self.aborted:
            lines.append(f"ABORTED: {self.abort_error}")
        lines.append(
            f"Upload complete: {self.requested} requested, "
            f"{self.successful} successful, {self.failed} failed, "
            f"{self.skipped} skipped"
        )
        for batch in self.batches:
            line = (
                f"  batch {batch.index}: {batch.items} items, HTTP {batch.http_status}, "
                f"{batch.successful} successful, {batch.failed} failed"
            )
            if batch.skipped:
                line += f", {batch.skipped} skipped"
            if batch.error:
                line += f" — {batch.error}"
            lines.append(line)
        for item in self.failed_items:
            lines.append(f"  FAILED item {item.input_index} (line {item.source_line}): {item.error}")
        for item in self.skipped_items:
            lines.append(f"  SKIPPED {item}")
        if self.failed or self.skipped or self.aborted:
            lines.append("Non-zero exit: fix the rejected rows and re-submit them individually.")
            lines.append("The bulk POST is not idempotent — never re-send the whole file.")
        return "\n".join(lines)

    def render_json(self) -> str:
        return json.dumps(self.to_wire(), indent=2)
