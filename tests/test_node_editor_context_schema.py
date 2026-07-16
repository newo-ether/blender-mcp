from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sample_context() -> dict:
    tree_ref = {
        "tree_type": "GeometryNodeTree",
        "owner": {"kind": "NODE_GROUP", "name": "Graph"},
    }
    tree = {
        "name": "Graph",
        "bl_idname": "GeometryNodeTree",
        "library": None,
        "tree_ref": tree_ref,
        "tree_ref_candidates": [tree_ref],
    }
    return {
        "schema": "blender-node-editor-context/1",
        "file_session_id": "file-one",
        "context_revision": "sha256:" + "a" * 64,
        "state": "UNIQUE_EDITOR",
        "observed_state": "UNIQUE_EDITOR",
        "selected_context_id": "window:1/area:2",
        "next_action": "use_selected_tree_ref",
        "stale_reasons": [],
        "total_editors": 1,
        "editors": [{
            "context_id": "window:1/area:2",
            "window_id": "window:1",
            "area_id": "area:2",
            "screen": "Layout",
            "workspace": "Geometry Nodes",
            "area": {"x": 0, "y": 0, "width": 800, "height": 600, "ui_type": "GeometryNodeTree"},
            "pinned": False,
            "space_tree_type": "GeometryNodeTree",
            "shader_type": None,
            "owner": {"id_type": "GeometryNodeTree", "name": "Graph", "library": None},
            "node_tree": tree,
            "edit_tree": tree,
            "path": [tree],
            "tree_ref": tree_ref,
            "tree_ref_candidates": [tree_ref],
            "active_node": "Output",
            "selected_nodes": ["Input", "Output"],
        }],
        "truncated": False,
    }


class NodeEditorContextSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from jsonschema import Draft202012Validator
        except ImportError as exc:
            raise unittest.SkipTest("jsonschema is not installed") from exc
        schema = json.loads(
            (ROOT / "schemas" / "node-editor-context-v1.json").read_text(
                encoding="utf-8"
            )
        )
        cls.validator = Draft202012Validator(schema)

    def test_unique_and_stale_payloads_validate(self):
        self.validator.validate(sample_context())
        stale = sample_context()
        stale.update({
            "state": "STALE_CONTEXT",
            "selected_context_id": None,
            "next_action": "refresh_node_editor_context",
            "stale_reasons": ["context_changed"],
        })
        self.validator.validate(stale)

    def test_unknown_fields_and_states_fail_closed(self):
        unknown = sample_context()
        unknown["unexpected"] = True
        self.assertTrue(list(self.validator.iter_errors(unknown)))
        invalid_state = sample_context()
        invalid_state["state"] = "ACTIVE_EDITOR"
        self.assertTrue(list(self.validator.iter_errors(invalid_state)))


if __name__ == "__main__":
    unittest.main()
