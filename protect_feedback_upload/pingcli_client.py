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
"""Adapter driving the generic ``pingcli pingone api`` command per batch.

The deliverable is script-only (nothing lands in the pingcli source repo):
batching/retry/reporting live here, and each batch is one raw bulk POST
through pingcli's generic API command — which injects authentication,
profile selection, and region routing from the CLI config. Per batch:

    pingcli pingone api -P <profile> -f \
        --http-method POST \
        --header "Content-Type: application/vnd.pingidentity.risk.feedback.bulk.v2+json" \
        --data <batch.json> -O json \
        environments/<environment-id>/riskFeedback

CONTRACT (live-probed 2026-10-22, shipped pingcli, PROD env):

- Success: envelope status "success", meta.api.httpStatus=200, the raw bulk
  response body (summary/responses) under "data".
- Batch rejection (API 4xx): status "error", meta.api.httpStatus = the HTTP
  status, data null, and the API's own error object under
  errors[0].details (code/message — errors[0].code/message are the CLI's
  SERVICE_ERROR wrapper).
- Auth/config failure: status "error", NO meta.api key, errors[0].message
  carries the CLI's auth error. No HTTP round trip happened.
- Exit codes are 0 for every HTTP outcome unless -f is passed — so -f is
  ALWAYS passed; the script classifies from the envelope, and a non-zero
  process exit means pingcli itself failed (bad flags, missing binary).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from typing import NamedTuple

from .batcher import ErrorObject

# The Feedback API v2 bulk media type (PND-9928; see
# RISK-FEEDBACK-V2-CONTRACT-VERIFIED.md in the task repo).
BULK_MEDIA_TYPE = "application/vnd.pingidentity.risk.feedback.bulk.v2+json"


class BulkPostResult(NamedTuple):
    """Classified result of one bulk POST through pingcli.

    Unpacks as ``(http_status, parsed, p1_error)`` — the batcher's Poster
    protocol — while fields stay accessible by name.

    http_status is 0 for a transport/command failure, and -1 for a
    deterministic auth/config failure (no HTTP round trip; the batcher
    aborts on both 401/403 and negative statuses).
    """

    http_status: int
    parsed: dict | None
    p1_error: ErrorObject | None


def _extract_p1_error(payload: dict) -> ErrorObject | None:
    """Extract the API's error object from a pingcli JSON envelope.

    For HTTP rejections the API's own error object (code/message) rides in
    errors[0].details; errors[0].code/message are the CLI's wrapper
    ("Custom request failed: HTTP 400"). For auth/config failures there is
    no meta.api and the CLI's message is the only detail available.
    """
    if not isinstance(payload, dict):
        return None

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if not isinstance(first, dict):
            return None

        details = first.get("details")
        if isinstance(details, dict) and isinstance(details.get("message"), str):
            code = details.get("code", "")
            nested = details.get("details")
            # Batch body validation nests the field-level detail one level
            # deeper (e.g. items.verdict INVALID_VALUE); prefer it when the
            # top-level detail is generic.
            if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                inner = nested[0]
                return ErrorObject(
                    code=str(inner.get("code") or code),
                    message=str(inner.get("message") or details["message"]),
                )
            return ErrorObject(code=str(code), message=details["message"])

        if isinstance(first.get("message"), str):
            return ErrorObject(code=str(first.get("code", "")), message=first["message"])

    return None


def _extract_bulk_response(payload: dict) -> dict | None:
    """Locate the bulk response body (summary/responses) under ``data``."""
    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if isinstance(data, dict) and (
        isinstance(data.get("summary"), dict) or isinstance(data.get("responses"), list)
    ):
        return data

    if isinstance(payload.get("summary"), dict) or isinstance(payload.get("responses"), list):
        return payload

    return None


def _extract_http_status(payload: dict) -> int:
    """HTTP status from the envelope's meta.api.httpStatus; -1 when absent.

    Absent meta.api means no HTTP round trip happened (auth/config
    failure) — deterministic, so the batcher must abort rather than retry.
    """
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            api = meta.get("api")
            if isinstance(api, dict) and isinstance(api.get("httpStatus"), int):
                return api["httpStatus"]

    return -1


class PingcliPoster:
    """Poster implementation driving the generic pingcli api command."""

    def __init__(
        self,
        environment_id: str | None,
        profile: str | None = None,
        pingcli_path: str = "pingcli",
    ) -> None:
        if not environment_id:
            raise ValueError(
                "environment ID is required: the generic api command has no "
                "profile-based environment resolution"
            )
        self.environment_id = environment_id
        self.profile = profile
        self.pingcli_path = pingcli_path

    def _build_command(self, batch_path: str) -> list[str]:
        cmd = [
            self.pingcli_path,
            "pingone",
            "api",
            # -f makes HTTP failures exit non-zero; the script classifies
            # from the envelope either way.
            "-f",
            "--http-method",
            "POST",
            "--header",
            f"Content-Type: {BULK_MEDIA_TYPE}",
            "--data",
            batch_path,
            "-O",
            "json",
        ]
        if self.profile:
            cmd.extend(["-P", self.profile])
        cmd.append(f"environments/{self.environment_id}/riskFeedback")
        return cmd

    def post_bulk(self, batch: list) -> BulkPostResult:
        """Write the batch to a temp file, invoke pingcli, classify the result."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=True
        ) as batch_file:
            body = {"items": [item.to_wire() for item in batch]}
            json.dump(body, batch_file)
            batch_file.flush()

            try:
                proc = subprocess.run(
                    self._build_command(batch_file.name),
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as exc:
                return BulkPostResult(0, None, ErrorObject(message=str(exc)))

            stdout = proc.stdout
            stderr = proc.stderr

        payload: dict = {}
        if stdout.strip():
            try:
                payload = json.loads(stdout)
                if not isinstance(payload, dict):
                    payload = {}
            except json.JSONDecodeError:
                payload = {}

        status = _extract_http_status(payload)

        if proc.returncode == 0 and status == 200:
            body = _extract_bulk_response(payload)
            if body is not None:
                return BulkPostResult(200, body, None)
            # 200 without a recognizable body: treat as transport-level
            # anomaly so the retry matrix retries it like a 500.
            return BulkPostResult(
                0,
                None,
                ErrorObject(
                    message=f"pingcli exited 0 but output carries no bulk response: {stdout[:200]}"
                ),
            )

        p1 = _extract_p1_error(payload)
        detail = p1.message if p1 else (stderr.strip() or stdout.strip() or f"exit code {proc.returncode}")
        if p1 is None:
            p1 = ErrorObject(code=f"HTTP_{status}" if status > 0 else "PINGCLI_ERROR", message=detail)
        return BulkPostResult(status, None, p1)
