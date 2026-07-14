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
    def __init__(self):
        self.commands = []

    def send_command(self, command, params=None):
        self.commands.append((command, params))
        return {"command": command, "params": params}


class RuntimeKnowledgeToolTests(unittest.TestCase):
    def setUp(self):
        self.original_connection = server.get_blender_connection
        self.original_telemetry = telemetry_decorator.get_telemetry
        self.fake = _FakeBlenderConnection()
        server.get_blender_connection = lambda: self.fake
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()

    def tearDown(self):
        server.get_blender_connection = self.original_connection
        telemetry_decorator.get_telemetry = self.original_telemetry

    def test_schema_defaults_to_compact_and_forwards_full(self):
        compact = json.loads(server.get_geometry_node_type_schema(
            None,
            node_type="GeometryNodeJoinGeometry",
        ))
        full = json.loads(server.get_geometry_node_type_schema(
            None,
            node_type="GeometryNodeJoinGeometry",
            detail="full",
        ))
        self.assertEqual(compact["params"]["detail"], "compact")
        self.assertEqual(full["params"]["detail"], "full")

    def test_node_type_search_forwards_paging(self):
        response = json.loads(server.search_geometry_node_types(
            None,
            query="XPBD",
            offset=10,
            limit=25,
        ))
        self.assertEqual(response["command"], "search_geometry_node_types")
        self.assertEqual(response["params"], {
            "query": "XPBD",
            "offset": 10,
            "limit": 25,
        })

    def test_node_asset_search_forwards_filters_and_detail(self):
        response = json.loads(server.search_blender_node_assets(
            None,
            query="Cloth",
            library="dynamics",
            tree_type="GeometryNodeTree",
            detail="full",
            offset=2,
            limit=10,
        ))
        self.assertEqual(response["command"], "search_blender_node_assets")
        self.assertEqual(response["params"], {
            "query": "Cloth",
            "library": "dynamics",
            "tree_type": "GeometryNodeTree",
            "detail": "full",
            "offset": 2,
            "limit": 10,
        })


if __name__ == "__main__":
    unittest.main()
