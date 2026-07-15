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
        if command == "export_node_tree" and params.get("allow_large_response"):
            return {
                "tree_ref": params["tree_ref"],
                "view": params["view"],
                "revision": "sha256:" + "a" * 64,
                "stats": {"node_count": 1},
            }
        return {"command": command, "params": params}


class NodeTreeToolTests(unittest.TestCase):
    def setUp(self):
        self.original_connection = server.get_blender_connection
        self.original_telemetry = telemetry_decorator.get_telemetry
        self.fake = _FakeBlenderConnection()
        server.get_blender_connection = lambda: self.fake
        telemetry_decorator.get_telemetry = lambda: _SilentTelemetry()
        self.tree_ref = {
            "tree_type": "ShaderNodeTree",
            "owner": {"kind": "MATERIAL", "name": "Hero"},
        }

    def tearDown(self):
        server.get_blender_connection = self.original_connection
        telemetry_decorator.get_telemetry = self.original_telemetry

    def test_list_forwards_domain_and_owner_filters(self):
        response = json.loads(server.list_node_trees(
            None,
            tree_types=["ShaderNodeTree"],
            owner_kinds=["MATERIAL", "WORLD"],
        ))
        self.assertEqual(response["command"], "list_node_trees")
        self.assertEqual(response["params"], {
            "tree_types": ["ShaderNodeTree"],
            "owner_kinds": ["MATERIAL", "WORLD"],
        })

    def test_export_forwards_structured_reference_and_targeting(self):
        response = json.loads(server.export_node_tree(
            None,
            tree_ref=self.tree_ref,
            view="all",
            node_names=["Principled BSDF"],
            neighbor_depth=2,
        ))
        self.assertEqual(response["command"], "export_node_tree")
        self.assertEqual(response["params"], {
            "tree_ref": self.tree_ref,
            "view": "all",
            "node_names": ["Principled BSDF"],
            "neighbor_depth": 2,
        })

    def test_bridge_log_redaction_hides_claims_code_and_nested_credentials(self):
        redacted = server._redact_command_params({
            "_claim_token": "claim-secret",
            "code": "print('private')",
            "nested": {"api_key": "provider-secret", "name": "safe"},
        })
        self.assertEqual(redacted["_claim_token"], "<redacted>")
        self.assertEqual(redacted["code"], "<redacted>")
        self.assertEqual(redacted["nested"]["api_key"], "<redacted>")
        self.assertEqual(redacted["nested"]["name"], "safe")

    def test_file_export_explicitly_requests_the_complete_snapshot(self):
        original_writer = server.write_node_tree_snapshot_json
        server.write_node_tree_snapshot_json = lambda _result, path: Path(path).resolve()
        try:
            response = json.loads(server.export_node_tree(
                None,
                tree_ref=self.tree_ref,
                view="auto",
                output_path="complete.json",
            ))
        finally:
            server.write_node_tree_snapshot_json = original_writer
        self.assertEqual(response["status"], "written")
        self.assertTrue(self.fake.commands[-1][1]["allow_large_response"])

    def test_scene_compositor_initialization_requires_explicit_flag(self):
        response = json.loads(server.ensure_scene_compositor_tree(
            None,
            scene_name="Scene",
            create_if_missing=True,
        ))
        self.assertEqual(response["command"], "ensure_scene_compositor_tree")
        self.assertEqual(response["params"], {
            "scene_name": "Scene",
            "create_if_missing": True,
        })

    def test_index_forwards_structured_reference_and_paging(self):
        response = json.loads(server.get_node_tree_index(
            None,
            tree_ref=self.tree_ref,
            query="Principled",
            offset=3,
            limit=20,
        ))
        self.assertEqual(response["command"], "get_node_tree_index")
        self.assertEqual(response["params"], {
            "tree_ref": self.tree_ref,
            "query": "Principled",
            "offset": 3,
            "limit": 20,
        })

    def test_schema_forwards_exact_owner_context(self):
        response = json.loads(server.get_node_type_schema(
            None,
            tree_type="CompositorNodeTree",
            node_type="CompositorNodeRLayers",
            owner_kind="SCENE",
            detail="compact",
        ))
        self.assertEqual(response["command"], "get_node_type_schema")
        self.assertEqual(response["params"], {
            "tree_type": "CompositorNodeTree",
            "node_type": "CompositorNodeRLayers",
            "owner_kind": "SCENE",
            "detail": "compact",
        })

    def test_validation_runs_structure_gate_then_forwards(self):
        patch = {
            "schema": "blender-node-tree-patch/1",
            "tree_ref": self.tree_ref,
            "base_revision": "sha256:" + "a" * 64,
            "capabilities": ["graph"],
            "operations": [{
                "op": "rename_node",
                "node": "Principled BSDF",
                "name": "Surface",
            }],
        }
        response = json.loads(server.validate_node_tree_patch(None, patch=patch))
        self.assertEqual(response["command"], "validate_node_tree_patch")
        self.assertEqual(response["params"], {"patch": patch})

        invalid = dict(patch)
        invalid["capabilities"] = ["layout"]
        rejected = json.loads(server.validate_node_tree_patch(None, patch=invalid))
        self.assertFalse(rejected["valid"])
        self.assertEqual(rejected["stage"], "structure")
        self.assertIn(
            "undeclared_capability",
            {item["code"] for item in rejected["diagnostics"]},
        )

    def test_apply_repeats_structure_gate_and_forwards_backup_policy(self):
        patch = {
            "schema": "blender-node-tree-patch/1",
            "tree_ref": self.tree_ref,
            "base_revision": "sha256:" + "a" * 64,
            "capabilities": ["graph"],
            "operations": [{
                "op": "rename_node",
                "node": "Principled BSDF",
                "name": "Surface",
            }],
        }
        response = json.loads(server.apply_node_tree_patch(
            None, patch=patch, keep_backup=False
        ))
        self.assertEqual(response["command"], "apply_node_tree_patch")
        self.assertEqual(response["params"], {
            "patch": patch,
            "keep_backup": False,
        })

    def test_all_generic_tools_are_registered(self):
        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertTrue({
            "list_node_trees",
            "ensure_scene_compositor_tree",
            "export_node_tree",
            "get_node_tree_index",
            "get_node_type_schema",
            "validate_node_tree_patch",
            "apply_node_tree_patch",
        }.issubset(names))


if __name__ == "__main__":
    unittest.main()
