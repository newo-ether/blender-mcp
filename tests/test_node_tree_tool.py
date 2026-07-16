from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.observability import decorators as telemetry_decorator  # noqa: E402
from blender_mcp.tools import node_trees as server  # noqa: E402
from blender_mcp.transport.connection import _redact_command_params  # noqa: E402


class _SilentTelemetry:
    def record_event(self, **_kwargs):
        return None


class _FakeBlenderConnection:
    def __init__(self):
        self.commands = []

    @staticmethod
    def _complete_snapshot(params):
        requested_view = params["view"]
        view = (
            "semantic" if params.get("node_names") else "operations"
        ) if requested_view == "auto" else requested_view
        return {
            "schema": "blender-node-tree/1",
            "blender_version": [5, 2, 0],
            "view": view,
            "tree_ref": params["tree_ref"],
            "owner": {
                "kind": "MATERIAL",
                "name": "Hero",
                "id_type": "Material",
                "library": None,
                "editable": True,
                "user_count": 1,
            },
            "capabilities": {
                "read": True,
                "index": True,
                "export": True,
                "schema": True,
                "validate": True,
                "apply": True,
                "editable": True,
                "mutation_reason": "available",
                "transaction_adapter": "embedded_shader_owner",
                "interface": True,
            },
            "tree": {
                "name": "Shader Nodetree",
                "bl_idname": params["tree_ref"]["tree_type"],
                "editable": True,
                "library": None,
                "interface": [],
                "nodes": {},
                "links": [],
            },
            "scope": {
                "kind": "full",
                "requested_nodes": [],
                "neighbor_depth": 0,
                "included_nodes": [],
                "content_revision": "sha256:" + "b" * 64,
            },
            "revision": "sha256:" + "a" * 64,
            "users": [],
            "diagnostics": [],
            "stats": {
                "node_count": 0,
                "link_count": 0,
                "interface_item_count": 0,
                "json_bytes": 0,
            },
        }

    def send_command(self, command, params=None):
        self.commands.append((command, params))
        if command == "export_node_tree" and params.get("allow_large_response"):
            return self._complete_snapshot(params)
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

    def test_create_node_group_forwards_exact_bootstrap_contract(self):
        response = json.loads(server.create_node_group(
            None,
            name="PCB Generator",
            tree_type="GeometryNodeTree",
            geometry_is_modifier=True,
            description="Structured procedural circuit traces",
            reuse_existing=True,
        ))
        self.assertEqual(response["command"], "create_node_group")
        self.assertEqual(response["params"], {
            "name": "PCB Generator",
            "tree_type": "GeometryNodeTree",
            "geometry_is_modifier": True,
            "description": "Structured procedural circuit traces",
            "reuse_existing": True,
        })

    def test_editor_context_forwards_stale_guards_and_bound(self):
        response = json.loads(server.get_node_editor_context(
            None,
            expected_file_session_id="file-one",
            expected_context_revision="sha256:" + "a" * 64,
            max_editors=8,
        ))
        self.assertEqual(response["command"], "get_node_editor_context")
        self.assertEqual(response["params"], {
            "expected_file_session_id": "file-one",
            "expected_context_revision": "sha256:" + "a" * 64,
            "max_editors": 8,
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
        redacted = _redact_command_params({
            "_claim_token": "claim-secret",
            "code": "print('private')",
            "nested": {"api_key": "provider-secret", "name": "safe"},
        })
        self.assertEqual(redacted["_claim_token"], "<redacted>")
        self.assertEqual(redacted["code"], "<redacted>")
        self.assertEqual(redacted["nested"]["api_key"], "<redacted>")
        self.assertEqual(redacted["nested"]["name"], "safe")

    def test_file_export_validates_and_writes_complete_auto_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(
                os.environ,
                {"BLENDER_MCP_WORKSPACE": temp_dir},
            ):
                response = json.loads(server.export_node_tree(
                    None,
                    tree_ref=self.tree_ref,
                    view="auto",
                    output_path="complete.json",
                ))
            destination = Path(response["path"])
            self.assertEqual(response["status"], "written")
            # Resolve both sides: on Windows, tempfile may yield an 8.3 short
            # path (e.g. NEWOET~1) when TEMP points at a short-name form, while
            # the tool canonicalizes via Path.resolve() to the long name. Comparing
            # unresolved paths spuriously fails when the username contains a space.
            self.assertEqual(destination.resolve(), (Path(temp_dir) / "complete.json").resolve())
            self.assertTrue(destination.is_file())
            snapshot = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["view"], "operations")
            self.assertEqual(snapshot["tree_ref"], self.tree_ref)
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])
        command, params = self.fake.commands[-1]
        self.assertEqual(command, "export_node_tree")
        self.assertEqual(params["view"], "auto")
        self.assertTrue(params["allow_large_response"])

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

    def test_query_forwards_complete_bounded_contract(self):
        response = json.loads(server.query_node_graph(
            None,
            tree_ref=self.tree_ref,
            query_type="shortest_path",
            node_names=["Principled BSDF"],
            from_node="Principled BSDF",
            to_node="Material Output",
            direction="downstream",
            limit=25,
        ))
        self.assertEqual(response["command"], "query_node_graph")
        self.assertEqual(response["params"], {
            "tree_ref": self.tree_ref,
            "query_type": "shortest_path",
            "node_names": ["Principled BSDF"],
            "from_node": "Principled BSDF",
            "to_node": "Material Output",
            "attribute_name": "",
            "socket_id": "",
            "direction": "downstream",
            "fields": [],
            "limit": 25,
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

    def test_interface_panel_and_metadata_require_interface_capability(self):
        patch = {
            "schema": "blender-node-tree-patch/1",
            "tree_ref": self.tree_ref,
            "base_revision": "sha256:" + "a" * 64,
            "capabilities": ["interface"],
            "operations": [
                {
                    "op": "add_interface_panel",
                    "id": "routing",
                    "name": "Routing",
                    "description": "Trace controls",
                    "default_closed": True,
                },
                {
                    "op": "set_interface_item",
                    "identifier": "routing",
                    "property": "description",
                    "value": "Shortest-path routing controls",
                },
            ],
        }
        response = json.loads(server.validate_node_tree_patch(None, patch=patch))
        self.assertEqual(response["command"], "validate_node_tree_patch")
        self.assertEqual(response["params"], {"patch": patch})

        patch["capabilities"] = ["graph"]
        rejected = json.loads(server.validate_node_tree_patch(None, patch=patch))
        self.assertFalse(rejected["valid"])
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
            "get_node_editor_context",
            "create_node_group",
            "list_node_trees",
            "ensure_scene_compositor_tree",
            "export_node_tree",
            "get_node_tree_index",
            "query_node_graph",
            "get_node_type_schema",
            "validate_node_tree_patch",
            "apply_node_tree_patch",
        }.issubset(names))

    def test_query_tool_description_lists_every_contract(self):
        tool = next(
            item for item in server.mcp._tool_manager.list_tools()
            if item.name == "query_node_graph"
        )
        for query_type in (
            "fields",
            "socket_links",
            "named_attributes",
            "shortest_path",
            "upstream/downstream",
            "slice",
        ):
            with self.subTest(query_type=query_type):
                self.assertIn(query_type, tool.description)

    def test_editor_context_description_rejects_implicit_selection(self):
        tool = next(
            item for item in server.mcp._tool_manager.list_tools()
            if item.name == "get_node_editor_context"
        )
        for phrase in (
            "MULTIPLE_EDITORS",
            "STALE_CONTEXT",
            "never chosen",
            "tree_ref",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, tool.description)


if __name__ == "__main__":
    unittest.main()
