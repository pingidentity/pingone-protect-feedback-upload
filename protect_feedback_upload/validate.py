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
"""Local pre-validation for Risk Feedback v2 items.

Direct port of the pingcli ``validateItem`` rules: it is a fail-fast
convenience for the user — the API remains authoritative.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime, timedelta, timezone

from .model import (
    ANALYST_MAX_LEN,
    ATTRIBUTE_KEY_MAX_LEN,
    ATTRIBUTES_MAX_KEYS,
    ATTRIBUTE_VALUE_MAX_LEN,
    CONFIDENCES,
    FLOW_TYPES,
    FRAUD_TYPE_MAX_LEN,
    MAX_TIME_RANGE_DAYS,
    REASON_MAX_LEN,
    SUBJECT_TYPES,
    TRIGGERS,
    VERDICTS,
    RiskFeedback,
    ValidationError,
)

# RFC 3339 UTC with millisecond precision — the exact wire format from the
# label contract. Values without milliseconds are rejected as INVALID_DATA
# by the API.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

_TRUNCATE_FOR_LOG_MAX = 60


def _truncate(value: str) -> str:
    if len(value) <= _TRUNCATE_FOR_LOG_MAX:
        return value
    return value[:_TRUNCATE_FOR_LOG_MAX] + "..."


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a wire timestamp; None when it is not in the exact format."""
    if not _TIMESTAMP_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        # Regex passed but the date is not real (e.g. month 13).
        return None


def _validate_timestamp(value: str) -> str | None:
    if _parse_timestamp(value) is None:
        return (
            f"timestamp {value!r}: timestamp must be RFC 3339 UTC in "
            "YYYY-MM-DDThh:mm:ss.SSSZ format"
        )
    return None


def _validate_attributes(attrs: dict[str, str] | None) -> str | None:
    if not attrs:
        return None
    if len(attrs) > ATTRIBUTES_MAX_KEYS:
        return f"attributes has too many keys: {len(attrs)} keys, max {ATTRIBUTES_MAX_KEYS}"
    for key, value in attrs.items():
        if len(key) > ATTRIBUTE_KEY_MAX_LEN:
            return f"attribute key is too long: {_truncate(key)!r} is {len(key)} chars, max {ATTRIBUTE_KEY_MAX_LEN}"
        if len(value) > ATTRIBUTE_VALUE_MAX_LEN:
            return (
                f"attribute value is too long for key {_truncate(key)!r}: "
                f"{len(value)} chars, max {ATTRIBUTE_VALUE_MAX_LEN}"
            )
    return None


def _validate_subject_value(item: RiskFeedback) -> str | None:
    stype = item.subject_type
    value = item.subject_value

    if stype == "TIME_RANGE":
        if value:
            return "subject.value must be empty when subject.type is TIME_RANGE"
        return None

    if stype in ("RISK_EVALUATION_ID", "PINGONE_USER_ID"):
        if not value:
            return f"subject.value is required for this subject.type (got type {stype})"
        try:
            uuid.UUID(value)
        except ValueError:
            return f"subject.value {value!r} must be a UUID for subject.type {stype}"
        return None

    if stype == "IP_ADDRESS":
        if not value:
            return f"subject.value is required for this subject.type (got type {stype})"
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return f"subject.value {value!r} must be an IPv4 or IPv6 address"
        return None

    if stype in ("EXTERNAL_TRANSACTION_ID", "EXTERNAL_USER_ID"):
        if not value:
            return f"subject.value is required for this subject.type (got type {stype})"
        return None

    # Unknown or empty subject.type is reported by the enum check.
    return None


def validate_item(item: RiskFeedback, input_index: int, source_line: int) -> list[ValidationError]:
    """Check one parsed item and return every violation (not just the first).

    Each violation is tied to the item's input position.
    """
    errs: list[ValidationError] = []

    def add_msg(field: str, message: str) -> None:
        errs.append(
            ValidationError(input_index=input_index, source_line=source_line, field=field, message=message)
        )

    # subject.type — required, one of six enum values.
    if not item.subject_type:
        add_msg("subject.type", "subject.type is required")
    elif item.subject_type not in SUBJECT_TYPES:
        add_msg("subject.type", f"subject.type {item.subject_type!r} must be one of {', '.join(SUBJECT_TYPES)}")

    # subject.value — format depends on the type; omitted for TIME_RANGE.
    msg = _validate_subject_value(item)
    if msg:
        add_msg("subject.value", msg)

    # verdict — required, one of three enum values.
    if not item.verdict:
        add_msg("verdict", "verdict is required")
    elif item.verdict not in VERDICTS:
        add_msg("verdict", f"verdict {item.verdict!r} must be one of {', '.join(VERDICTS)}")

    # occurred_from — required, RFC 3339 UTC with millisecond precision.
    if not item.occurred_from:
        add_msg("occurred_from", "occurred_from is required")
    elif _validate_timestamp(item.occurred_from):
        add_msg("occurred_from", _validate_timestamp(item.occurred_from))

    # occurred_to — optional, same format, within 3 days of occurred_from.
    if item.occurred_to:
        msg = _validate_timestamp(item.occurred_to)
        if msg:
            add_msg("occurred_to", msg)
        elif (
            _parse_timestamp(item.occurred_from) is not None
            and _parse_timestamp(item.occurred_to) is not None
        ):
            span = _parse_timestamp(item.occurred_to) - _parse_timestamp(item.occurred_from)
            if span > timedelta(days=MAX_TIME_RANGE_DAYS):
                add_msg(
                    "occurred_to",
                    f"occurred_to must be no more than {MAX_TIME_RANGE_DAYS} days after occurred_from",
                )
    elif item.subject_type == "TIME_RANGE":
        add_msg("occurred_to", "occurred_to is required when subject.type is TIME_RANGE")

    # confirmed_at — optional, same format.
    if item.confirmed_at:
        msg = _validate_timestamp(item.confirmed_at)
        if msg:
            add_msg("confirmed_at", msg)

    # Optional enums.
    if item.trigger and item.trigger not in TRIGGERS:
        add_msg("trigger", f"trigger {item.trigger!r} must be one of {', '.join(TRIGGERS)}")

    if item.flow_type and item.flow_type not in FLOW_TYPES:
        add_msg("flow_type", f"flow_type {item.flow_type!r} must be one of {', '.join(FLOW_TYPES)}")

    if item.confidence and item.confidence not in CONFIDENCES:
        add_msg("confidence", f"confidence {item.confidence!r} must be one of {', '.join(CONFIDENCES)}")

    # Length limits.
    if len(item.reason) > REASON_MAX_LEN:
        add_msg("reason", f"reason is {len(item.reason)} chars, max {REASON_MAX_LEN}")

    if len(item.analyst) > ANALYST_MAX_LEN:
        add_msg("analyst", f"analyst is {len(item.analyst)} chars, max {ANALYST_MAX_LEN}")

    if len(item.fraud_type) > FRAUD_TYPE_MAX_LEN:
        add_msg("fraud_type", f"fraud_type is {len(item.fraud_type)} chars, max {FRAUD_TYPE_MAX_LEN}")

    # attributes limits.
    msg = _validate_attributes(item.attributes)
    if msg:
        add_msg("attributes", msg)

    return errs
