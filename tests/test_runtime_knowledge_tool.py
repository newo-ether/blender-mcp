from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.observability import decorators as telemetry_decorator  # noqa: E402
from blender_mcp.tools import geometry_nodes as server  # noqa: E402
from blender_mcp.tools import scene as scene_server  # noqa: E402


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
        self.original_scene_connection = scene_server.get_blender_connection
        self.original_telemetry = telemetry_decorator.get_telemetry
        self.fake = _FakeBlenderConnection()
        server.get_blender_connection = lambda: self.fake
        scene_server.get_blender_connection = lambda: self.fake
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()

    def tearDown(self):
        server.get_blender_connection = self.original_connection
        scene_server.get_blender_connection = self.original_scene_connection
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

    def test_geometry_modifier_bootstrap_forwards_explicit_mutation_flags(self):
        response = json.loads(server.ensure_geometry_nodes_modifier(
            None,
            object_name="PCB Host",
            node_group_name="PCB Generator",
            modifier_name="Circuit Board",
            create_object_if_missing=True,
            create_modifier_if_missing=True,
            assign_if_different=False,
            location=[1.0, 2.0, 3.0],
        ))
        self.assertEqual(response["command"], "ensure_geometry_nodes_modifier")
        self.assertEqual(response["params"], {
            "object_name": "PCB Host",
            "node_group_name": "PCB Generator",
            "modifier_name": "Circuit Board",
            "create_object_if_missing": True,
            "create_modifier_if_missing": True,
            "assign_if_different": False,
            "location": [1.0, 2.0, 3.0],
        })

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

    def test_runtime_automation_context_uses_read_only_bridge_command(self):
        response = json.loads(scene_server.get_runtime_automation_context(None))
        self.assertEqual(response["command"], "get_runtime_automation_context")
        self.assertIsNone(response["params"])

    def test_node_asset_search_forwards_filters_and_detail(self):
        response = json.loads(server.search_blender_node_assets(
            None,
            query="Cloth",
            library="dynamics",
            tree_type="GeometryNodeTree",
            detail="full",
            scope="USER",
            offset=2,
            limit=10,
        ))
        self.assertEqual(response["command"], "search_blender_node_assets")
        self.assertEqual(response["params"], {
            "query": "Cloth",
            "library": "dynamics",
            "tree_type": "GeometryNodeTree",
            "detail": "full",
            "scope": "USER",
            "offset": 2,
            "limit": 10,
        })

    def test_node_asset_import_forwards_exact_identity_and_conflict_policy(self):
        response = json.loads(server.import_blender_node_asset(
            None,
            source_path=r"D:\Assets\nodes.blend",
            asset_name="Index Field",
            tree_type="GeometryNodeTree",
            scope="USER",
            library="My Assets",
            conflict_policy="RENAME",
        ))
        self.assertEqual(response["command"], "import_blender_node_asset")
        self.assertEqual(response["params"], {
            "source_path": r"D:\Assets\nodes.blend",
            "asset_name": "Index Field",
            "tree_type": "GeometryNodeTree",
            "scope": "USER",
            "library": "My Assets",
            "conflict_policy": "RENAME",
        })


if __name__ == "__main__":
    unittest.main()
