from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.protocol import node_tree as schema


def sample_snapshot() -> dict:
    return {
        "schema": schema.SNAPSHOT_SCHEMA,
        "blender_version": [5, 2, 0],
        "view": "semantic",
        "tree_ref": {
            "tree_type": "ShaderNodeTree",
            "owner": {"kind": "MATERIAL", "name": "Hero"},
        },
        "tree": {
            "name": "Shader Nodetree",
            "bl_idname": "ShaderNodeTree",
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
        "users": [],
        "stats": {
            "node_count": 0,
            "link_count": 0,
            "interface_item_count": 0,
            "json_bytes": 0,
        },
        "revision": "sha256:" + "a" * 64,
    }


class NodeTreeSchemaTests(unittest.TestCase):
    def test_all_concrete_views_write_atomically(self):
        self.assertEqual(
            schema.VIEWS,
            {"semantic", "operations", "layout", "all"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for view in sorted(schema.VIEWS):
                with self.subTest(view=view):
                    snapshot = sample_snapshot()
                    snapshot["view"] = view
                    destination = schema.write_snapshot_json(
                        snapshot, f"graphs/{view}.json", temp_dir
                    )
                    self.assertEqual(
                        destination.resolve(),
                        (Path(temp_dir) / "graphs" / f"{view}.json").resolve(),
                    )
                    self.assertEqual(
                        json.loads(destination.read_text(encoding="utf-8")),
                        snapshot,
                    )
            self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_rejects_non_concrete_views_with_complete_choices(self):
        expected = "snapshot.view must be one of: all, layout, operations, semantic"
        for view in ("auto", "summary", "unknown"):
            with self.subTest(view=view):
                snapshot = sample_snapshot()
                snapshot["view"] = view
                with self.assertRaises(schema.NodeTreeSchemaError) as error:
                    schema.validate_snapshot_structure(snapshot)
                self.assertEqual(str(error.exception), expected)

    def test_path_must_stay_in_workspace_and_use_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(schema.NodeTreeSchemaError):
                schema.write_snapshot_json(sample_snapshot(), "../escape.json", temp_dir)
            with self.assertRaises(schema.NodeTreeSchemaError):
                schema.write_snapshot_json(sample_snapshot(), "graph.txt", temp_dir)

    def test_rejects_tree_ref_type_mismatch_and_bad_revision(self):
        mismatch = sample_snapshot()
        mismatch["tree"]["bl_idname"] = "CompositorNodeTree"
        with self.assertRaises(schema.NodeTreeSchemaError):
            schema.validate_snapshot_structure(mismatch)
        invalid_revision = sample_snapshot()
        invalid_revision["revision"] = "sha256:not-a-revision"
        with self.assertRaises(schema.NodeTreeSchemaError):
            schema.validate_snapshot_structure(invalid_revision)

    def test_rejects_invalid_owner_reference(self):
        invalid = sample_snapshot()
        invalid["tree_ref"]["owner"]["kind"] = "OBJECT"
        with self.assertRaises(schema.NodeTreeSchemaError):
            schema.validate_snapshot_structure(invalid)


if __name__ == "__main__":
    unittest.main()
