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
        self.original_client = server.BlenderDocumentationClient
        self.original_telemetry = telemetry_decorator.get_telemetry
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()

    def tearDown(self):
        server.get_blender_connection = self.original_connection
        server.BlenderDocumentationClient = self.original_client
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

    def test_invalid_request_is_truthful_typed_failure(self):
        server.get_blender_connection = lambda: (_ for _ in ()).throw(
            AssertionError("invalid request unexpectedly connected to Blender")
        )
        with self.assertRaises(server.BlenderMCPError) as captured:
            server.get_blender_documentation_context(
                None,
                version="https://example.com",
            )
        self.assertEqual(captured.exception.code, "tool_execution_failed")
        self.assertNotIn("Traceback", captured.exception.message)

    def test_search_tool_defaults_to_manual_and_returns_json(self):
        calls = []

        class FakeClient:
            def search(self, context, *, query, limit, snippet_mode):
                calls.append((context, query, limit, snippet_mode))
                return {
                    "schema": "blender-documentation-search/1",
                    "query": query,
                    "results": [],
                    "errors": [],
                }

        server.BlenderDocumentationClient = FakeClient
        server.get_blender_connection = lambda: (_ for _ in ()).throw(
            AssertionError("explicit version unexpectedly connected to Blender")
        )
        response = server.search_blender_docs(
            None,
            query="Geometry Nodes",
            version="5.1",
            limit=3,
        )
        parsed = json.loads(response)
        self.assertEqual(parsed["schema"], "blender-documentation-search/1")
        context, query, limit, snippet_mode = calls[0]
        self.assertEqual(context["requested"]["sources"], ["manual"])
        self.assertEqual(query, "Geometry Nodes")
        self.assertEqual(limit, 3)
        self.assertEqual(snippet_mode, "top")

    def test_page_tool_normalizes_source_alias_before_client(self):
        calls = []

        class FakeClient:
            def get_page(self, context, **kwargs):
                calls.append((context, kwargs))
                return {
                    "schema": "blender-documentation-page/1",
                    "content": "Node reference",
                }

        server.BlenderDocumentationClient = FakeClient
        response = server.get_blender_doc_page(
            None,
            page="bpy.types.Node",
            version="5.1",
            source="api",
            heading="Inherited Properties",
            max_chars=2_000,
        )
        parsed = json.loads(response)
        self.assertEqual(parsed["schema"], "blender-documentation-page/1")
        context, kwargs = calls[0]
        self.assertEqual(context["requested"]["sources"], ["python_api"])
        self.assertEqual(kwargs["source"], "python_api")
        self.assertEqual(kwargs["heading"], "Inherited Properties")
        self.assertEqual(kwargs["max_chars"], 2_000)


if __name__ == "__main__":
    unittest.main()
