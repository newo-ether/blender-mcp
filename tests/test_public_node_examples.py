from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.protocol.node_patch import validate_patch_structure
from blender_mcp.protocol.node_tree import validate_snapshot_structure


class PublicNodeExampleTests(unittest.TestCase):
    def load(self, relative_path: str) -> dict:
        return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))

    def test_shader_and_compositor_snapshots_are_valid(self):
        for domain in ("shader", "compositor"):
            with self.subTest(domain=domain):
                snapshot = self.load(f"examples/{domain}-node-tree-snapshot.json")
                validate_snapshot_structure(snapshot)
                self.assertEqual(
                    snapshot["tree_ref"]["tree_type"],
                    snapshot["tree"]["bl_idname"],
                )

    def test_shader_and_compositor_patches_are_valid(self):
        for domain in ("shader", "compositor"):
            with self.subTest(domain=domain):
                patch = self.load(f"examples/{domain}-node-tree-patch.json")
                self.assertEqual(validate_patch_structure(patch), [])

    def test_examples_conform_to_published_json_schemas(self):
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest("jsonschema is not installed")

        schemas = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "schemas").glob("*.json")
        ]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in schemas
            if "$id" in schema
        )
        for domain in ("shader", "compositor"):
            cases = (
                ("node-tree-v1.json", f"examples/{domain}-node-tree-snapshot.json"),
                ("node-tree-patch-v1.json", f"examples/{domain}-node-tree-patch.json"),
            )
            for schema_name, example_path in cases:
                with self.subTest(example=example_path):
                    schema = self.load(f"schemas/{schema_name}")
                    Draft202012Validator(schema, registry=registry).validate(
                        self.load(example_path)
                    )


if __name__ == "__main__":
    unittest.main()
