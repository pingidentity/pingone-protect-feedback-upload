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
"""Unit tests for the local validator (ports of the pingcli validateItem cases)."""

import unittest

from protect_feedback_upload.model import RiskFeedback
from protect_feedback_upload.validate import validate_item


def valid_item(**overrides) -> RiskFeedback:
    base = RiskFeedback(
        subject_type="EXTERNAL_TRANSACTION_ID",
        subject_value="TXN-42",
        verdict="FRAUD",
        occurred_from="2026-02-02T06:21:21.000Z",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class TestValidateItem(unittest.TestCase):
    def test_valid_minimal(self):
        self.assertEqual(validate_item(valid_item(), 1, 2), [])

    def test_valid_full(self):
        item = valid_item(
            occurred_to="2026-02-03T21:53:52.000Z",
            trigger="CHARGEBACK",
            flow_type="REGISTRATION",
            confirmed_at="2026-05-15T10:30:00.000Z",
            reason="Chargeback received",
            analyst="analyst@acme.com",
            confidence="HIGH",
            fraud_type="BOT_FARM",
            attributes={"src_ip": "192.0.2.45"},
        )
        self.assertEqual(validate_item(item, 1, 2), [])

    def test_missing_required_fields(self):
        errs = validate_item(RiskFeedback(), 1, 2)
        fields = {e.field for e in errs}
        # subject.value has no error here: the empty subject.type is reported
        # by the enum check, and value-format checking is deferred (same as
        # the Go validator's default branch).
        self.assertEqual(fields, {"subject.type", "verdict", "occurred_from"})

    def test_unknown_enums(self):
        errs = validate_item(
            valid_item(subject_type="NOPE", verdict="MAYBE", trigger="WHAT", flow_type="WHERE", confidence="SORTA"),
            1,
            2,
        )
        fields = {e.field for e in errs}
        self.assertEqual(
            fields,
            {"subject.type", "verdict", "trigger", "flow_type", "confidence"},
        )

    def test_timestamp_requires_millis(self):
        errs = validate_item(valid_item(occurred_from="2026-02-02T06:21:21Z"), 1, 2)
        self.assertEqual(len(errs), 1)
        self.assertIn("YYYY-MM-DDThh:mm:ss.SSSZ", errs[0].message)

    def test_timestamp_rejects_non_utc(self):
        errs = validate_item(valid_item(occurred_from="2026-02-02T06:21:21.000+00:00"), 1, 2)
        self.assertEqual(len(errs), 1)

    def test_timestamp_rejects_impossible_date(self):
        errs = validate_item(valid_item(occurred_from="2026-13-02T06:21:21.000Z"), 1, 2)
        self.assertEqual(len(errs), 1)

    def test_subject_value_uuid_types(self):
        for stype in ("RISK_EVALUATION_ID", "PINGONE_USER_ID"):
            with self.subTest(stype=stype):
                errs = validate_item(valid_item(subject_type=stype, subject_value="TXN-42"), 1, 2)
                self.assertEqual(len(errs), 1)
                self.assertIn("must be a UUID", errs[0].message)

    def test_subject_value_ip(self):
        errs = validate_item(valid_item(subject_type="IP_ADDRESS", subject_value="not-an-ip"), 1, 2)
        self.assertEqual(len(errs), 1)
        self.assertIn("IPv4 or IPv6", errs[0].message)

        self.assertEqual(validate_item(valid_item(subject_type="IP_ADDRESS", subject_value="192.0.2.45"), 1, 2), [])
        self.assertEqual(validate_item(valid_item(subject_type="IP_ADDRESS", subject_value="2001:db8::1"), 1, 2), [])

    def test_time_range_rules(self):
        # TIME_RANGE: value must be empty, occurred_to required, ≤ 3 days.
        errs = validate_item(
            valid_item(subject_type="TIME_RANGE", subject_value="X", occurred_to=None), 1, 2
        )
        fields = [e.field for e in errs]
        self.assertIn("subject.value", fields)
        self.assertIn("occurred_to", fields)

        errs = validate_item(
            valid_item(
                subject_type="TIME_RANGE",
                subject_value="",
                occurred_to="2026-02-06T06:21:21.000Z",
            ),
            1,
            2,
        )
        self.assertEqual([e.field for e in errs], ["occurred_to"])

        self.assertEqual(
            validate_item(
                valid_item(
                    subject_type="TIME_RANGE",
                    subject_value="",
                    occurred_to="2026-02-05T06:21:21.000Z",
                ),
                1,
                2,
            ),
            [],
        )

    def test_length_limits(self):
        errs = validate_item(
            valid_item(reason="x" * 201, analyst="y" * 51, fraud_type="z" * 51), 1, 2
        )
        self.assertEqual({e.field for e in errs}, {"reason", "analyst", "fraud_type"})

    def test_attributes_limits(self):
        errs = validate_item(valid_item(attributes={f"k{i}": "v" for i in range(21)}), 1, 2)
        self.assertIn("too many keys", errs[0].message)

        errs = validate_item(valid_item(attributes={"k" * 101: "v"}), 1, 2)
        self.assertIn("key is too long", errs[0].message)

        errs = validate_item(valid_item(attributes={"k": "v" * 301}), 1, 2)
        self.assertIn("value is too long", errs[0].message)

    def test_multiple_violations_reported(self):
        errs = validate_item(valid_item(verdict="", occurred_from="bad"), 1, 2)
        self.assertEqual({e.field for e in errs}, {"verdict", "occurred_from"})


if __name__ == "__main__":
    unittest.main()
