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
"""Command-line entry point for the bulk upload script.

Usage:
    python -m protect_feedback_upload upload labels.csv \
        --environment-id <uuid> [--profile <profile>] [--batch-size 20] \
        [--dry-run] [--output-format text|json] [--pingcli-path pingcli]
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .batcher import ErrorObject
from .pingcli_client import PingcliPoster
from .report import UploadReport
from .uploader import run_upload

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_ABORTED = 2
EXIT_INPUT_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="protect-feedback-upload",
        description=(
            "Bulk upload PingOne Protect risk feedback labels. Reads CSV/NDJSON, "
            "validates rows locally, batches into groups of at most 20, and calls "
            "pingcli per batch with the retry matrix."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_upload = sub.add_parser("upload", help="upload a CSV/NDJSON labels file")
    p_upload.add_argument("input", help="path to the labels file (.csv, .ndjson, .jsonl), or - for stdin")
    p_upload.add_argument(
        "--environment-id",
        required=True,
        help="PingOne environment ID (the bulk endpoint's path parameter)",
    )
    p_upload.add_argument("-P", "--profile", default=None, help="pingcli profile to authenticate with")
    p_upload.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="items per bulk request, 1-20 (default 20; the API enforces the cap)",
    )
    p_upload.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and batch only — no rows are sent",
    )
    p_upload.add_argument(
        "-O",
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="report format (default text)",
    )
    p_upload.add_argument(
        "--pingcli-path",
        default="pingcli",
        help="path to the pingcli binary (default: PATH lookup)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command != "upload":  # pragma: no cover - argparse enforces
        return EXIT_INPUT_ERROR

    if not 1 <= args.batch_size <= 20:
        print("--batch-size must be between 1 and 20", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if args.dry_run:
        poster = None
    else:
        poster = PingcliPoster(
            environment_id=args.environment_id,
            profile=args.profile,
            pingcli_path=args.pingcli_path,
        )
    try:
        report = run_upload(
            input_path=args.input,
            environment_id=args.environment_id,
            poster=poster,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if args.output_format == "json":
        print(report.render_json())
    else:
        print(report.render_text())

    if report.aborted:
        return EXIT_ABORTED
    if report.failed or report.skipped:
        return EXIT_FAILURES
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
