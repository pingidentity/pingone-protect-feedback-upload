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
"""Unit tests for the CSV/NDJSON readers and format sniffing."""

import unittest

from protect_feedback_upload.reader import parse_csv, parse_ndjson, sniff_format


class TestSniffFormat(unittest.TestCase):
    def test_by_extension(self):
        self.assertEqual(sniff_format("a.csv", "subject_type"), "csv")
        self.assertEqual(sniff_format("a.ndjson", ""), "ndjson")
        self.assertEqual(sniff_format("a.jsonl", ""), "ndjson")
        self.assertEqual(sniff_format("-", "{"), "ndjson")
        self.assertEqual(sniff_format("-", "x"), "csv")
        self.assertEqual(sniff_format("noext", "{"), "ndjson")


CSV_INPUT = (
    "subject_type,subject_value,verdict,occurred_from,occurred_to,trigger,flow_type,"
    "confirmed_at,reason,analyst,confidence,fraud_type,attributes\n"
    'RISK_EVALUATION_ID,15f1d245-33b3-4dba-849d-4df4b64b2d42,FRAUD,2026-05-10T08:03:17.000Z,,'
    'CHARGEBACK,REGISTRATION,2026-05-15T10:30:00.000Z,Chargeback received,fraud-analyst@acme.com,'
    'HIGH,BOT_FARM,"{""src_ip"": ""192.0.2.45""}"\n'
    "TIME_RANGE,,SUSPECTED_FRAUD,2026-01-25T08:51:01.000Z,2026-01-27T17:26:04.000Z,,"
    "AUTHENTICATION,2026-02-02T09:42:25.000Z,Card testing,security@acme.com,MEDIUM,BOT_FARM,\n"
)


class TestParseCSV(unittest.TestCase):
    def test_canonical_two_rows(self):
        items, errs = parse_csv(CSV_INPUT)
        self.assertEqual(errs, [])
        self.assertEqual(len(items), 2)

        first = items[0].item
        self.assertEqual(first.subject_type, "RISK_EVALUATION_ID")
        self.assertEqual(first.attributes, {"src_ip": "192.0.2.45"})
        # positions: header = line 1
        self.assertEqual(items[0].source_line, 2)
        self.assertEqual(items[1].source_line, 3)

    def test_free_column_order(self):
        shuffled = (
            "verdict,subject_type,subject_value,occurred_from,occurred_to,trigger,flow_type,"
            "confirmed_at,reason,analyst,confidence,fraud_type,attributes\n"
            "FRAUD,RISK_EVALUATION_ID,15f1d245-33b3-4dba-849d-4df4b64b2d42,2026-05-10T08:03:17.000Z,,"
            "CHARGEBACK,REGISTRATION,2026-05-15T10:30:00.000Z,Chargeback,fraud-analyst@acme.com,"
            'HIGH,BOT_FARM,"{""src_ip"": ""1.2.3.4""}"\n'
        )
        items, errs = parse_csv(shuffled)
        self.assertEqual(errs, [])
        self.assertEqual(items[0].item.verdict, "FRAUD")
        self.assertEqual(items[0].item.attributes, {"src_ip": "1.2.3.4"})

    def test_missing_required_column_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parse_csv("subject_type,verdict,occurred_from\nA,FRAUD,2026-05-10T08:03:17.000Z\n")
        self.assertIn('missing required column "subject_value"', str(ctx.exception))

    def test_wrong_field_count_row(self):
        bad = CSV_INPUT + "RISK_EVALUATION_ID\n"
        items, errs = parse_csv(bad)
        self.assertEqual(len(errs), 1)
        self.assertIn("fields", errs[0].message)
        self.assertEqual(errs[0].source_line, 4)

    def test_bad_attributes_cell(self):
        bad = CSV_INPUT.replace('"{""src_ip"": ""192.0.2.45""}"', "not-json")
        items, errs = parse_csv(bad)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].field, "attributes")

    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            parse_csv("")


class TestParseNDJSON(unittest.TestCase):
    def test_two_lines(self):
        content = (
            '{"subject":{"type":"EXTERNAL_TRANSACTION_ID","value":"TXN-1"},'
            '"verdict":"FRAUD","occurredFrom":"2026-05-10T08:03:17.000Z"}\n'
            '{"subject":{"type":"TIME_RANGE"},'
            '"verdict":"SUSPECTED_FRAUD","occurredFrom":"2026-01-25T08:51:01.000Z",'
            '"occurredTo":"2026-01-27T17:26:04.000Z"}\n'
        )
        items, errs = parse_ndjson(content)
        self.assertEqual(errs, [])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source_line, 1)
        self.assertEqual(items[1].item.occurred_to, "2026-01-27T17:26:04.000Z")

    def test_blank_lines_skipped_and_line_numbers_preserved(self):
        content = (
            '\n{"subject":{"type":"EXTERNAL_TRANSACTION_ID","value":"TXN-1"},'
            '"verdict":"FRAUD","occurredFrom":"2026-05-10T08:03:17.000Z"}\n\n'
        )
        items, errs = parse_ndjson(content)
        self.assertEqual(errs, [])
        self.assertEqual(items[0].source_line, 2)

    def test_malformed_line_does_not_hide_later_lines(self):
        content = (
            "{not json\n"
            '{"subject":{"type":"EXTERNAL_TRANSACTION_ID","value":"TXN-1"},'
            '"verdict":"FRAUD","occurredFrom":"2026-05-10T08:03:17.000Z"}\n'
        )
        items, errs = parse_ndjson(content)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].source_line, 1)
        self.assertIn("malformed JSON", errs[0].message)

    def test_non_object_line(self):
        items, errs = parse_ndjson('"just a string"\n')
        self.assertEqual(items, [])
        self.assertEqual(len(errs), 1)
        self.assertIn("not a JSON object", errs[0].message)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_ndjson("\n \n")


if __name__ == "__main__":
    unittest.main()
