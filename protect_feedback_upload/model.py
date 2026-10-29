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
"""Wire model and contract constants for Risk Feedback v2.

The constants were verified live against the Feedback API v2 bulk endpoint
(PND-9928) — see RISK-FEEDBACK-V2-CONTRACT-VERIFIED.md in the task repo.
The wire field is ``trigger`` (NOT ``triggerType``: both are accepted with
200, but the created-object echo silently drops ``triggerType``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Per-request item cap for the bulk endpoint. Verified live: the API
# enforces "items size must be between 1 and 20".
MAX_BATCH_SIZE = 20

# Retry matrix bounds (ported from the pingcli batcher).
MAX_429_RETRIES = 25
PAUSE_429_SECONDS = 1.0
MAX_500_ATTEMPTS = 3

# Length limits from the Feedback API v2 validation rules.
REASON_MAX_LEN = 200
ANALYST_MAX_LEN = 50
FRAUD_TYPE_MAX_LEN = 50

# attributes object limits.
ATTRIBUTES_MAX_KEYS = 20
ATTRIBUTE_KEY_MAX_LEN = 100
ATTRIBUTE_VALUE_MAX_LEN = 300

# Maximum span from occurredFrom to occurredTo for TIME_RANGE subjects.
MAX_TIME_RANGE_DAYS = 3

SUBJECT_TYPES = (
    "RISK_EVALUATION_ID",
    "EXTERNAL_TRANSACTION_ID",
    "PINGONE_USER_ID",
    "EXTERNAL_USER_ID",
    "IP_ADDRESS",
    "TIME_RANGE",
)

VERDICTS = ("FRAUD", "SUSPECTED_FRAUD", "LEGITIMATE")

TRIGGERS = ("CHARGEBACK", "MANUAL_REVIEW", "CUSTOMER_COMPLAINT", "THIRD_PARTY_SIGNAL")

FLOW_TYPES = ("REGISTRATION", "AUTHENTICATION", "ACCESS", "AUTHORIZATION", "TRANSACTION")

CONFIDENCES = ("HIGH", "MEDIUM", "LOW")

# CSV column names (snake_case), header order free, all required in the
# header even when a column is empty throughout.
CSV_COLUMNS = (
    "subject_type",
    "subject_value",
    "verdict",
    "occurred_from",
    "occurred_to",
    "trigger",
    "flow_type",
    "confirmed_at",
    "reason",
    "analyst",
    "confidence",
    "fraud_type",
    "attributes",
)

# JSON keys are camelCase; CSV columns are snake_case. The mapping is used
# by the CSV reader and the report layer.
CSV_TO_JSON = {
    "subject_type": "subject.type",
    "subject_value": "subject.value",
    "verdict": "verdict",
    "occurred_from": "occurredFrom",
    "occurred_to": "occurredTo",
    "trigger": "trigger",
    "flow_type": "flowType",
    "confirmed_at": "confirmedAt",
    "reason": "reason",
    "analyst": "analyst",
    "confidence": "confidence",
    "fraud_type": "fraudType",
    "attributes": "attributes",
}


@dataclass
class RiskFeedback:
    """One RiskFeedback v2 label — one items[] element in the bulk request.

    Timestamps are strings already in the wire format
    YYYY-MM-DDThh:mm:ss.SSSZ (validated by :mod:`.validate`).
    """

    subject_type: str = ""
    subject_value: str = ""
    verdict: str = ""
    occurred_from: str = ""
    occurred_to: str = ""
    trigger: str = ""
    flow_type: str = ""
    confirmed_at: str = ""
    reason: str = ""
    analyst: str = ""
    confidence: str = ""
    fraud_type: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    def to_wire(self) -> dict:
        """Render the camelCase wire shape of one items[] element.

        subject.value is omitted for TIME_RANGE subjects; empty optionals
        are omitted entirely.
        """
        item: dict = {
            "subject": {"type": self.subject_type},
            "verdict": self.verdict,
            "occurredFrom": self.occurred_from,
        }
        if self.subject_value:
            item["subject"]["value"] = self.subject_value
        if self.occurred_to:
            item["occurredTo"] = self.occurred_to
        if self.trigger:
            item["trigger"] = self.trigger
        if self.flow_type:
            item["flowType"] = self.flow_type
        if self.confirmed_at:
            item["confirmedAt"] = self.confirmed_at
        if self.reason:
            item["reason"] = self.reason
        if self.analyst:
            item["analyst"] = self.analyst
        if self.confidence:
            item["confidence"] = self.confidence
        if self.fraud_type:
            item["fraudType"] = self.fraud_type
        if self.attributes:
            item["attributes"] = dict(self.attributes)
        return item


@dataclass
class ValidationError:
    """One local pre-validation failure, carrying the input position.

    inputIndex is the 1-based item index across the whole input; sourceLine
    is the CSV row number (header = row 1) or NDJSON line number.
    """

    input_index: int
    source_line: int
    field: str
    message: str

    def to_wire(self) -> dict:
        return {
            "inputIndex": self.input_index,
            "sourceLine": self.source_line,
            "field": self.field,
            "message": self.message,
        }

    def __str__(self) -> str:
        return f"item {self.input_index} (line {self.source_line}) field {self.field!r}: {self.message}"
