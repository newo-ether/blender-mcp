from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "blender_mcp"
    / "geometry_nodes_schema.py"
)
SPEC = importlib.util.spec_from_file_location("geometry_nodes_schema_test", MODULE_PATH)
schema = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(schema)


def sample_snapshot() -> dict:
    return {
        "schema": schema.SNAPSHOT_SCHEMA,
        "blender_version": [5, 1, 2],
        "view": "all",
        "scope": {
            "kind": "full",
            "requested_nodes": [],
            "neighbor_depth": 0,
            "included_nodes": ["Cube"],
            "content_revision": "sha256:" + "0" * 64,
        },
        "tree": {
            "name": "Example",
            "bl_idname": "GeometryNodeTree",
            "editable": True,
            "library": None,
            "interface": [],
            "nodes": {
                "Cube": {
                    "id": "Cube",
                    "name": "Cube",
                    "label": "",
                    "bl_idname": "GeometryNodeMeshCube",
                    "properties": {},
                    "inputs": [],
                    "outputs": [],
                }
            },
            "links": [],
        },
        "users": [],
        "stats": {
            "node_count": 1,
            "link_count": 0,
            "interface_item_count": 0,
            "json_bytes": 0,
        },
    }


def sample_patch() -> dict:
    return {
        "schema": schema.PATCH_SCHEMA,
        "tree_name": "Example",
        "base_revision": "sha256:" + "a" * 64,
        "operations": [
            {
                "op": "add_node",
                "id": "new_cube",
                "node_type": "GeometryNodeMeshCube",
                "layout": {"location": [10.0, 20.0]},
            },
            {
                "op": "set_socket_default",
                "node": "new_cube",
                "socket": "input:0:Size",
                "value": [1.0, 2.0, 3.0],
            },
        ],
    }


class GeometryNodesSchemaTests(unittest.TestCase):
    def test_canonical_json_is_order_independent(self):
        left = {"b": [2, 1], "a": {"z": True, "x": None}}
        right = {"a": {"x": None, "z": True}, "b": [2, 1]}
        self.assertEqual(schema.canonical_json(left), schema.canonical_json(right))

    def test_revision_excludes_observation_metadata(self):
        first = schema.finalize_snapshot(sample_snapshot())
        second_input = sample_snapshot()
        second_input["users"] = [{"kind": "MODIFIER", "name": "Object/GeometryNodes"}]
        second_input["stats"]["json_bytes"] = 99999
        second = schema.finalize_snapshot(second_input)
        self.assertEqual(first["revision"], second["revision"])

        second["tree"]["nodes"]["Cube"]["properties"]["domain"] = "POINT"
        self.assertNotEqual(first["revision"], schema.snapshot_revision(second))

    def test_filtered_snapshot_cannot_invent_source_revision(self):
        snapshot = sample_snapshot()
        snapshot["view"] = "semantic"
        snapshot["scope"]["kind"] = "subgraph"
        snapshot["scope"]["included_nodes"] = ["Cube"]
        with self.assertRaises(schema.GeometryNodesSchemaError):
            schema.snapshot_revision(snapshot)
        with self.assertRaises(schema.GeometryNodesSchemaError):
            schema.finalize_snapshot(snapshot)

    def test_invalid_view_and_structure_are_rejected(self):
        snapshot = sample_snapshot()
        snapshot["view"] = "everything"
        with self.assertRaises(schema.GeometryNodesSchemaError):
            schema.finalize_snapshot(snapshot)

        snapshot = sample_snapshot()
        del snapshot["tree"]["nodes"]
        with self.assertRaises(schema.GeometryNodesSchemaError):
            schema.finalize_snapshot(snapshot)

    def test_workspace_path_rejects_escape_and_non_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"
            root.mkdir()
            resolved = schema.resolve_workspace_json_path("graphs/tree.json", root)
            self.assertEqual(resolved, (root / "graphs" / "tree.json").resolve())

            with self.assertRaises(schema.GeometryNodesSchemaError):
                schema.resolve_workspace_json_path("../outside.json", root)
            with self.assertRaises(schema.GeometryNodesSchemaError):
                schema.resolve_workspace_json_path("graphs/tree.txt", root)

    def test_snapshot_write_round_trips(self):
        finalized = schema.finalize_snapshot(sample_snapshot())
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = schema.write_snapshot_json(
                finalized,
                "exports/tree.json",
                temp_dir,
            )
            with destination.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.assertEqual(loaded, finalized)
            self.assertTrue(destination.read_bytes().endswith(b"\n"))

    def test_valid_patch_has_no_structural_diagnostics(self):
        patch = sample_patch()
        self.assertEqual(schema.validate_patch_structure(patch), [])
        self.assertEqual(schema.assert_valid_patch(patch), patch)

    def test_patch_diagnostics_are_path_addressed(self):
        patch = sample_patch()
        patch["base_revision"] = "stale"
        patch["operations"].append({
            "op": "add_link",
            "from_node": "new_cube",
            "from_socket": "input:0:wrong-direction",
            "to_node": "Cube",
            "to_socket": "output:0:wrong-direction",
            "unexpected": True,
        })
        diagnostics = schema.validate_patch_structure(patch)
        keyed = {(item["code"], item["path"]) for item in diagnostics}
        self.assertIn(("invalid_revision", "/base_revision"), keyed)
        self.assertIn(("invalid_socket_id", "/operations/2/from_socket"), keyed)
        self.assertIn(("invalid_socket_id", "/operations/2/to_socket"), keyed)
        self.assertIn(("unknown_field", "/operations/2/unexpected"), keyed)

        patch = sample_patch()
        patch["operations"][0]["layout"]["mystery"] = 1
        diagnostics = schema.validate_patch_structure(patch)
        self.assertIn(
            ("unknown_field", "/operations/0/layout/mystery"),
            {(item["code"], item["path"]) for item in diagnostics},
        )

    def test_patch_file_read_is_workspace_bound_and_validated(self):
        patch = sample_patch()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "patches" / "valid.json"
            path.parent.mkdir()
            path.write_text(json.dumps(patch), encoding="utf-8")
            self.assertEqual(schema.read_patch_json("patches/valid.json", root), patch)

            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(schema.GeometryNodesSchemaError):
                schema.read_patch_json("patches/valid.json", root)

            path.write_text(json.dumps({"schema": "bad"}), encoding="utf-8")
            self.assertEqual(
                schema.read_patch_json("patches/valid.json", root),
                {"schema": "bad"},
            )

    def test_single_user_copy_requires_explicit_target(self):
        patch = sample_patch()
        patch["shared_tree_policy"] = "single_user_copy"
        diagnostics = schema.validate_patch_structure(patch)
        self.assertIn("missing_target_user", {item["code"] for item in diagnostics})

        patch["target_user"] = {
            "kind": "MODIFIER",
            "object": "Cube",
            "modifier": "GeometryNodes",
        }
        self.assertEqual(schema.validate_patch_structure(patch), [])


if __name__ == "__main__":
    unittest.main()
