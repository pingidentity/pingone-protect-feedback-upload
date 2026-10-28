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
"""PingOne Protect risk feedback bulk upload script.

Companion script for the thin ``pingcli protect risk-feedback`` command:
reads CSV/NDJSON label files, validates each row locally, groups the valid
rows into batches of at most 20 items, invokes pingcli per batch, applies
the retry matrix (429 / 5xx / transport retries, 401/403 abort, no retry on
partial failure), and prints a terminal report.
"""

__version__ = "0.1.0"
