"""Verify linked Geometry Node trees stay read-only for validate and apply."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
import tempfile
import traceback

import bpy


PREFIX = "__BLENDER_MCP_GN_LINKED_TEST__"
RESULT_PREFIX = "BLENDER_MCP_GN_LINKED_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def cleanup():
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def run_test():
    cleanup()
    namespace = runpy.run_path(
        str(REPO_ROOT / "addon.py"),
        run_name="blender_mcp_addon_linked_test",
    )
    server = namespace["BlenderMCPServer"]()

    with tempfile.TemporaryDirectory(prefix="blender-mcp-linked-") as temp_dir:
        library_path = Path(temp_dir) / "geometry_nodes_library.blend"
        source = bpy.data.node_groups.new(PREFIX + "LibraryTree", "GeometryNodeTree")
        node = source.nodes.new("GeometryNodeMeshCube")
        node.location = (10.0, 20.0)
        node_name = node.name
        source_name = source.name
        bpy.data.libraries.write(str(library_path), {source})
        bpy.data.node_groups.remove(source)

        with bpy.data.libraries.load(str(library_path), link=True) as (data_from, data_to):
            assert_true(source_name in data_from.node_groups, "Node tree missing from library")
            data_to.node_groups = [source_name]
        linked = bpy.data.node_groups[source_name]

        snapshot = server.export_geometry_node_tree(linked.name, "all")
        assert_true(snapshot["tree"]["library"], "Linked snapshot omitted library path")
        assert_true(not snapshot["tree"]["editable"], "Linked snapshot was marked editable")
        patch = {
            "schema": "blender-geometry-nodes-patch/1",
            "tree_name": linked.name,
            "base_revision": snapshot["revision"],
            "operations": [
                {"op": "set_node_layout", "node": node_name, "width": 180.0},
            ],
        }
        groups_before = len(bpy.data.node_groups)
        validation = server.validate_geometry_node_patch(patch)
        application = server.apply_geometry_node_patch(patch, keep_backup=True)
        assert_true(not validation["valid"], "Linked tree patch validated")
        assert_true(
            any(item["code"] == "tree_not_editable" for item in validation["diagnostics"]),
            "Linked tree editability diagnostic missing",
        )
        assert_true(application["status"] == "rejected", "Linked tree patch was applied")
        assert_true(not application["mutated"], "Linked rejection reported mutation")
        assert_true(len(bpy.data.node_groups) == groups_before, "Linked rejection leaked a copy")
        assert_true(
            server.export_geometry_node_tree(linked.name, "all") == snapshot,
            "Linked rejection changed the source",
        )

        return {
            "blender_version": list(bpy.app.version[:3]),
            "library": snapshot["tree"]["library"],
            "validation_code": next(
                item["code"] for item in validation["diagnostics"]
                if item["code"] == "tree_not_editable"
            ),
            "application_status": application["status"],
        }


try:
    result = run_test()
    print(RESULT_PREFIX + json.dumps({"ok": True, **result}, sort_keys=True))
except Exception as exc:
    traceback.print_exc()
    print(
        RESULT_PREFIX
        + json.dumps(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            sort_keys=True,
        )
    )
    sys.exit(1)
finally:
    cleanup()
