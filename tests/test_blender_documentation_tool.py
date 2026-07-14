from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp import server  # noqa: E402
from blender_mcp import telemetry_decorator  # noqa: E402


class _SilentTelemetry:
    def record_event(self, **_kwargs):
        return None


class _FakeBlenderConnection:
    def __init__(self, result: dict):
        self.result = result
        self.commands: list[tuple[str, object]] = []

    def send_command(self, command: str, params=None):
        self.commands.append((command, params))
        return self.result


class BlenderDocumentationToolTests(unittest.TestCase):
    def setUp(self):
        self.original_connection = server.get_blender_connection
        self.original_telemetry = telemetry_decorator.get_telemetry
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()

    def tearDown(self):
        server.get_blender_connection = self.original_connection
        telemetry_decorator.get_telemetry = self.original_telemetry

    def test_explicit_version_does_not_connect_to_blender(self):
        def fail_connection():
            raise AssertionError("explicit version unexpectedly connected to Blender")

        server.get_blender_connection = fail_connection
        response = server.get_blender_documentation_context(
            None,
            version="4.2.3",
            language="en",
            sources=["manual"],
        )
        parsed = json.loads(response)
        self.assertEqual(parsed["resolved"]["version"], "4.2")
        self.assertIsNone(parsed["detected_blender"])

    def test_auto_requests_exact_build_context_from_addon(self):
        fake = _FakeBlenderConnection({
            "schema": "blender-version-context/1",
            "version": [5, 2, 0],
            "version_string": "5.2.0 LTS Release Candidate",
            "version_cycle": "rc",
            "is_prerelease": True,
            "is_lts": True,
            "build": {
                "branch": "blender-v5.2-release",
                "hash": "710df102694f",
                "date": "2026-07-09",
                "time": "08:45",
                "platform": "Windows",
                "type": "Release",
                "commit_timestamp": 1783586733,
            },
        })
        server.get_blender_connection = lambda: fake
        response = server.get_blender_documentation_context(
            None,
            version="auto",
            language="zh_CN",
        )
        parsed = json.loads(response)
        self.assertEqual(fake.commands, [("get_blender_version_context", None)])
        self.assertEqual(parsed["resolved"]["version"], "5.2")
        self.assertEqual(parsed["sources"][0]["channel"], "dev")
        self.assertEqual(parsed["sources"][0]["language"], "zh-hans")

    def test_invalid_request_is_bounded_error_text(self):
        server.get_blender_connection = lambda: (_ for _ in ()).throw(
            AssertionError("invalid request unexpectedly connected to Blender")
        )
        response = server.get_blender_documentation_context(
            None,
            version="https://example.com",
        )
        self.assertTrue(response.startswith("Error resolving Blender documentation context:"))
        self.assertNotIn("Traceback", response)


if __name__ == "__main__":
    unittest.main()
