"""A failed patch application must never over-claim what it knows about Blender state.

Regression cover for a field report: `apply_geometry_node_patch` added 4 interface
panels and 18 sockets, the commit landed, then the post-commit re-export raised
"StructRNA of type GeometryNodeTree has been removed". The server's catch-all turned
that into `applied:false, mutated:false` -- a claim it had no basis for. A caller that
believes it resends the patch and adds all 18 sockets a second time.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.observability import decorators as telemetry_decorator  # noqa: E402
from blender_mcp.protocol.errors import (  # noqa: E402
    UNRESOLVED_MUTATION,
    unresolved_application_result,
)
from blender_mcp.tools import geometry_nodes as gn_server  # noqa: E402
from blender_mcp.tools import node_trees as nt_server  # noqa: E402


class _SilentTelemetry:
    def record_event(self, **_kwargs):
        return None


class _ExplodingConnection:
    """Fails the way the field report did: after the addon already committed."""

    def send_command(self, _command, _params):
        raise RuntimeError("StructRNA of type GeometryNodeTree has been removed")


class _UnreachableConnection:
    """Fails before any command is dispatched."""

    def __init__(self):
        self.dispatched = False

    def send_command(self, _command, _params):  # pragma: no cover - never reached
        self.dispatched = True
        raise AssertionError("must not dispatch")


_GEOMETRY_PATCH = {
    "schema": "blender-geometry-nodes-patch/1",
    "tree_name": "Geometry Nodes",
    "base_revision": "sha256:" + "a" * 64,
    "operations": [
        {"op": "add_interface_socket", "id": "s0", "in_out": "INPUT",
         "name": "Substeps", "socket_type": "NodeSocketInt"},
    ],
}

_NODE_TREE_PATCH = {
    "schema": "blender-node-tree-patch/1",
    "tree_ref": {"tree_type": "ShaderNodeTree", "owner": {"kind": "MATERIAL", "name": "Hero"}},
    "base_revision": "sha256:" + "b" * 64,
    "capabilities": ["graph"],
    "operations": [
        {"op": "add_node", "id": "n0", "node_type": "ShaderNodeMath"},
    ],
}


class UnresolvedApplicationResultTests(unittest.TestCase):
    def test_undispatched_failure_may_assert_nothing_was_mutated(self):
        result = unresolved_application_result(
            "blender-geometry-nodes-patch-application/1",
            dispatched=False,
            error=RuntimeError("connection refused"),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIs(result["mutated"], False)
        self.assertIs(result["applied"], False)
        self.assertEqual(result["diagnostics"][0]["code"], "application_transport_error")
        self.assertIn("never reached Blender", result["diagnostics"][0]["message"])

    def test_dispatched_failure_refuses_to_assert_the_outcome(self):
        result = unresolved_application_result(
            "blender-geometry-nodes-patch-application/1",
            dispatched=True,
            error=RuntimeError("StructRNA of type GeometryNodeTree has been removed"),
        )
        self.assertEqual(result["status"], UNRESOLVED_MUTATION)
        self.assertEqual(result["mutated"], UNRESOLVED_MUTATION)
        self.assertIs(result["applied"], False)
        self.assertEqual(
            result["diagnostics"][0]["code"], "application_unresolved_after_dispatch",
        )

    def test_unknown_is_truthy_so_naive_callers_degrade_toward_reading_back(self):
        # `if result["mutated"]:` is the obvious caller test. Under the old code it was
        # False and callers retried into a double-apply; "unknown" must push the other way.
        result = unresolved_application_result(
            "blender-geometry-nodes-patch-application/1",
            dispatched=True,
            error=RuntimeError("boom"),
        )
        self.assertTrue(result["mutated"])

    def test_dispatched_failure_tells_the_caller_to_read_back_before_resending(self):
        message = unresolved_application_result(
            "blender-geometry-nodes-patch-application/1",
            dispatched=True,
            error=RuntimeError("boom"),
        )["diagnostics"][0]["message"]
        self.assertIn("boom", message)
        self.assertIn("read the tree back", message.lower())
        self.assertIn("duplicates", message.lower())


class GeometryPatchApplicationHonestyTests(unittest.TestCase):
    def setUp(self):
        self.original_connection = gn_server.get_blender_connection
        self.original_telemetry = telemetry_decorator.get_telemetry
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()

    def tearDown(self):
        gn_server.get_blender_connection = self.original_connection
        telemetry_decorator.get_telemetry = self.original_telemetry

    def test_post_dispatch_failure_reports_unknown_not_false(self):
        gn_server.get_blender_connection = lambda: _ExplodingConnection()
        result = json.loads(gn_server.apply_geometry_node_patch(None, patch=_GEOMETRY_PATCH))
        self.assertEqual(result["status"], UNRESOLVED_MUTATION)
        self.assertEqual(result["mutated"], UNRESOLVED_MUTATION)
        self.assertIn("StructRNA", result["diagnostics"][0]["message"])

    def test_failure_before_dispatch_still_reports_not_mutated(self):
        unreachable = _UnreachableConnection()

        def _refuse():
            raise RuntimeError("no registered instances")

        gn_server.get_blender_connection = _refuse
        result = json.loads(gn_server.apply_geometry_node_patch(None, patch=_GEOMETRY_PATCH))
        self.assertEqual(result["status"], "failed")
        self.assertIs(result["mutated"], False)
        self.assertFalse(unreachable.dispatched)

    def test_structure_rejection_is_unaffected_and_still_asserts_false(self):
        gn_server.get_blender_connection = lambda: _ExplodingConnection()
        result = json.loads(gn_server.apply_geometry_node_patch(None))
        self.assertEqual(result["status"], "rejected")
        self.assertIs(result["mutated"], False)


class NodeTreePatchApplicationHonestyTests(unittest.TestCase):
    def setUp(self):
        self.original_connection = nt_server.get_blender_connection
        self.original_telemetry = telemetry_decorator.get_telemetry
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()

    def tearDown(self):
        nt_server.get_blender_connection = self.original_connection
        telemetry_decorator.get_telemetry = self.original_telemetry

    def test_post_dispatch_failure_reports_unknown_not_false(self):
        nt_server.get_blender_connection = lambda: _ExplodingConnection()
        result = json.loads(nt_server.apply_node_tree_patch(None, patch=_NODE_TREE_PATCH))
        self.assertEqual(result["status"], UNRESOLVED_MUTATION)
        self.assertEqual(result["mutated"], UNRESOLVED_MUTATION)


class ApplicationSchemaAllowsUnknownTests(unittest.TestCase):
    def test_both_application_schemas_admit_the_unknown_outcome(self):
        root = Path(__file__).resolve().parents[1] / "schemas"
        for name in (
            "geometry-nodes-patch-application-v1.json",
            "node-tree-patch-application-v1.json",
        ):
            with self.subTest(schema=name):
                doc = json.loads((root / name).read_text(encoding="utf-8"))
                self.assertIn("unknown", doc["properties"]["status"]["enum"])
                self.assertEqual(
                    doc["properties"]["mutated"],
                    {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                )


if __name__ == "__main__":
    unittest.main()
