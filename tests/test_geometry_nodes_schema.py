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
        "view": "semantic",
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


if __name__ == "__main__":
    unittest.main()
