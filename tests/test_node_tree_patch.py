from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.protocol import node_patch as patch_schema  # noqa: E402


def sample_patch() -> dict:
    return {
        "schema": patch_schema.PATCH_SCHEMA,
        "tree_ref": {
            "tree_type": "ShaderNodeTree",
            "owner": {"kind": "MATERIAL", "name": "Hero"},
        },
        "base_revision": "sha256:" + "a" * 64,
        "capabilities": ["graph", "layout", "annotation", "dynamic", "id_reference"],
        "operations": [
            {
                "op": "add_node",
                "id": "image",
                "node_type": "ShaderNodeTexImage",
                "name": "Reference",
                "properties": {
                    "image": {"$type": "ID", "id_type": "Image", "name": "Plate"}
                },
                "layout": {"location": [-400.0, 100.0]},
            },
            {
                "op": "set_node_property",
                "node": "Render Layers",
                "property": "layer",
                "value": {
                    "$type": "ViewLayer",
                    "scene": "Shot 010",
                    "name": "Foreground",
                },
            },
            {
                "op": "add_node",
                "id": "frame",
                "node_type": "NodeFrame",
            },
            {
                "op": "set_annotation",
                "node": "frame",
                "text": "Human-readable stage",
            },
            {
                "op": "set_color_ramp",
                "node": "Existing Ramp",
                "interpolation": "EASE",
                "elements": [
                    {"position": 0.0, "color": [0.0, 0.0, 0.0, 1.0]},
                    {"position": 1.0, "color": [1.0, 1.0, 1.0, 1.0]},
                ],
            },
        ],
    }


class NodeTreePatchTests(unittest.TestCase):
    def test_valid_patch_is_copied(self):
        value = sample_patch()
        accepted = patch_schema.assert_valid_patch(value)
        self.assertEqual(accepted, value)
        self.assertIsNot(accepted, value)

    def test_capabilities_must_cover_operations_and_typed_ids(self):
        value = sample_patch()
        value["capabilities"] = ["graph", "layout", "annotation", "dynamic"]
        diagnostics = patch_schema.validate_patch_structure(value)
        self.assertIn("undeclared_capability", {item["code"] for item in diagnostics})

    def test_non_finite_numbers_and_wrong_socket_direction_are_rejected(self):
        value = sample_patch()
        value["operations"] = [
            {
                "op": "set_socket_default",
                "node": "Principled BSDF",
                "socket": "output:0:BSDF",
                "value": math.inf,
            }
        ]
        value["capabilities"] = ["graph"]
        diagnostics = patch_schema.validate_patch_structure(value)
        codes = {item["code"] for item in diagnostics}
        self.assertIn("non_finite_number", codes)
        self.assertIn("invalid_socket_id", codes)

    def test_dynamic_structures_require_ordered_bounded_values(self):
        value = sample_patch()
        value["operations"] = [{
            "op": "set_color_ramp",
            "node": "Ramp",
            "elements": [
                {"position": 0.8, "color": [0, 0, 0, 1]},
                {"position": 0.2, "color": [1, 1, 1, 1]},
            ],
        }]
        value["capabilities"] = ["dynamic"]
        diagnostics = patch_schema.validate_patch_structure(value)
        self.assertIn(
            "unordered_ramp_positions", {item["code"] for item in diagnostics}
        )

    def test_patch_path_is_workspace_bounded_and_size_limited(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "patch.json"
            source.write_text(json.dumps(sample_patch()), encoding="utf-8")
            self.assertEqual(
                patch_schema.read_patch_json("patch.json", temp_dir), sample_patch()
            )
            with self.assertRaises(patch_schema.NodeTreeSchemaError):
                patch_schema.read_patch_json("../patch.json", temp_dir)
            source.write_bytes(b" " * (patch_schema.MAX_PATCH_BYTES + 1))
            with self.assertRaises(patch_schema.NodeTreeSchemaError):
                patch_schema.read_patch_json("patch.json", temp_dir)

    def test_unknown_fields_and_unsupported_operations_fail_closed(self):
        value = sample_patch()
        value["unexpected"] = True
        value["operations"] = [{"op": "execute_python", "code": "pass"}]
        diagnostics = patch_schema.validate_patch_structure(value)
        self.assertEqual(
            {item["code"] for item in diagnostics},
            {"unknown_field", "unsupported_operation"},
        )

    def test_operation_count_limit_is_enforced(self):
        value = sample_patch()
        value["operations"] = [
            {
                "op": "rename_node",
                "node": f"Node {index}",
                "name": f"Renamed {index}",
            }
            for index in range(patch_schema.MAX_OPERATIONS + 1)
        ]
        value["capabilities"] = ["graph"]
        diagnostics = patch_schema.validate_patch_structure(value)
        self.assertIn("too_many_operations", {item["code"] for item in diagnostics})


if __name__ == "__main__":
    unittest.main()
