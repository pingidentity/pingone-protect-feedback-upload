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
"""Unit tests for the pingcli adapter: environment-ID resolution."""

import json
import unittest
from unittest import mock

from protect_feedback_upload.pingcli_client import (
    ENVIRONMENT_ID_KEY,
    resolve_environment_id,
)


def config_envelope(value):
    """A successful 'pingcli config get' envelope for the env-ID key."""
    return {
        "schemaVersion": "1.1",
        "status": "success",
        "data": {"values": {ENVIRONMENT_ID_KEY: value}},
        "errors": [],
    }


def run_stub(returncode=0, stdout="", stderr="", side_effect=None):
    """Build a mock for subprocess.run with canned output.

    The canned attributes sit on ``return_value`` because
    ``subprocess.run`` returns a CompletedProcess — the code reads
    ``proc.returncode`` / ``proc.stdout`` from what the call returned.
    """
    if side_effect is not None:
        return mock.Mock(side_effect=side_effect)
    return mock.Mock(
        return_value=mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)
    )


class TestResolveEnvironmentId(unittest.TestCase):
    def _resolve(self, run_mock, profile=None, pingcli_path="pingcli"):
        with mock.patch(
            "protect_feedback_upload.pingcli_client.subprocess.run", run_mock
        ):
            return resolve_environment_id(profile=profile, pingcli_path=pingcli_path)

    def test_returns_configured_value(self):
        stdout = json.dumps(config_envelope("f606ebfd-628d-4fd3-a3cd-dd0d5b925e48"))
        run_mock = run_stub(stdout=stdout)
        self.assertEqual(
            self._resolve(run_mock), "f606ebfd-628d-4fd3-a3cd-dd0d5b925e48"
        )

    def test_empty_value_returns_none(self):
        stdout = json.dumps(config_envelope(""))
        run_mock = run_stub(stdout=stdout)
        self.assertIsNone(self._resolve(run_mock))

    def test_nonzero_exit_returns_none(self):
        run_mock = run_stub(returncode=1, stdout="", stderr="boom")
        self.assertIsNone(self._resolve(run_mock))

    def test_invalid_json_returns_none(self):
        run_mock = run_stub(stdout="not json at all")
        self.assertIsNone(self._resolve(run_mock))

    def test_unexpected_shape_returns_none(self):
        run_mock = run_stub(stdout=json.dumps({"unexpected": "shape"}))
        self.assertIsNone(self._resolve(run_mock))

    def test_pingcli_binary_missing_returns_none(self):
        run_mock = run_stub(side_effect=OSError("binary not found"))
        self.assertIsNone(self._resolve(run_mock))

    def test_profile_flag_is_passed_through(self):
        stdout = json.dumps(config_envelope("env-from-demo"))
        run_mock = run_stub(stdout=stdout)
        self.assertEqual(self._resolve(run_mock, profile="demo"), "env-from-demo")
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[cmd.index("-P") + 1], "demo")

    def test_no_profile_flag_when_profile_omitted(self):
        stdout = json.dumps(config_envelope("env"))
        run_mock = run_stub(stdout=stdout)
        self._resolve(run_mock)
        cmd = run_mock.call_args[0][0]
        self.assertNotIn("-P", cmd)

    def test_custom_pingcli_path_is_used(self):
        stdout = json.dumps(config_envelope("env"))
        run_mock = run_stub(stdout=stdout)
        self._resolve(run_mock, pingcli_path="/opt/pingcli")
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[0], "/opt/pingcli")


if __name__ == "__main__":
    unittest.main()
