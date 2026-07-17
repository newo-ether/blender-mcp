"""Headless regression for two RNA edge cases that made properties unwritable.

Both were reported from a real session and are easy to reintroduce:

1. ``FunctionNodeInputVector.vector`` — Blender's RNA ``array_length`` reports 4,
   but the instance holds 3 components and RNA rejects a 4-array with
   "expected 3". Validating against ``array_length`` made the property
   impossible to set: a 3-array was rejected ("Expected an array with 4 values")
   and a 4-array was rejected by Blender. The validator must trust the live
   value's length, so a 3-array applies and a 4-array is rejected with the
   correct expected count.
2. ``NodeSocketMenu.default_value`` — the menu's items are runtime-resolved and
   ``prop.enum_items`` is empty, so a static membership check rejected every
   legal value with "Expected one of: " (empty list). A menu default must now
   validate and apply, while an out-of-range value is still rejected — with the
   real option list, which the dry-run RNA assignment supplies.
"""

from __future__ import annotations

import json
import runpy
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_GN_WRITABLE_EDGES__"
RESULT_PREFIX = "BLENDER_MCP_GN_WRITABLE_EDGES_RESULT="
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
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_addon_writable_edges_test",
    )
    server = namespace["BlenderMCPServer"]()

    created = server.create_node_group(PREFIX + "G", "GeometryNodeTree")
    revision = created["revision"]

    # --- P1-3: 3-component vector applies; 4-component is rejected with "3".
    vector_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": PREFIX + "G",
        "base_revision": revision,
        "operations": [{
            "op": "add_node",
            "id": "v",
            "node_type": "FunctionNodeInputVector",
            "properties": {"vector": [0, 0, 1]},
        }],
    }
    vector_validation = server.validate_geometry_node_patch(vector_patch)
    assert_true(vector_validation["valid"], vector_validation)
    vector_application = server.apply_geometry_node_patch(vector_patch, keep_backup=False)
    assert_true(vector_application["status"] == "applied", vector_application)
    tree = bpy.data.node_groups[PREFIX + "G"]
    vector_node = next(
        node for node in tree.nodes if node.bl_idname == "FunctionNodeInputVector"
    )
    # Snapshot the value now: every later apply below swaps the tree
    # transactionally, which invalidates this StructRNA pointer. Reading
    # vector_node.vector after that is a use-after-free (a hard crash, not a
    # Python error) — the same pointer-staleness the patch protocol exists to
    # hide from callers.
    applied_vector = list(vector_node.vector)
    assert_true(applied_vector == [0.0, 0.0, 1.0], applied_vector)
    revision = vector_application["revision"]

    four = dict(vector_patch, base_revision=revision)
    four["operations"] = [{
        "op": "add_node", "id": "v2", "node_type": "FunctionNodeInputVector",
        "properties": {"vector": [0, 0, 1, 0]},
    }]
    four_validation = server.validate_geometry_node_patch(four)
    assert_true(not four_validation["valid"], four_validation)
    assert_true(
        any(
            "3 values" in (item.get("message") or "")
            for item in four_validation.get("diagnostics", [])
        ),
        f"4-array rejection must name the real expected length: {four_validation}",
    )

    # --- P1-4: a menu default validates, applies, and a bogus value is rejected
    #     with the real option list.
    resample_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": PREFIX + "G",
        "base_revision": revision,
        "operations": [
            {"op": "add_node", "id": "rc", "node_type": "GeometryNodeResampleCurve"},
        ],
    }
    resample_application = server.apply_geometry_node_patch(resample_patch, keep_backup=False)
    assert_true(resample_application["status"] == "applied", resample_application)
    revision = resample_application["revision"]
    resample_name = next(iter(resample_application["created_nodes"].values()))

    mode_socket_id = "input:2:Mode"
    menu_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": PREFIX + "G",
        "base_revision": revision,
        "operations": [{
            "op": "set_socket_default",
            "node": resample_name,
            "socket": mode_socket_id,
            "value": "Length",
        }],
    }
    menu_validation = server.validate_geometry_node_patch(menu_patch)
    assert_true(menu_validation["valid"], menu_validation)
    menu_application = server.apply_geometry_node_patch(menu_patch, keep_backup=False)
    assert_true(menu_application["status"] == "applied", menu_application)
    tree = bpy.data.node_groups[PREFIX + "G"]
    resample = tree.nodes[resample_name]
    applied_mode = resample.inputs["Mode"].default_value
    assert_true(applied_mode == "Length", repr(applied_mode))
    revision = menu_application["revision"]

    bogus_patch = dict(menu_patch, base_revision=revision)
    bogus_patch["operations"] = [{
        "op": "set_socket_default",
        "node": resample_name,
        "socket": mode_socket_id,
        "value": "Bogus",
    }]
    bogus_validation = server.validate_geometry_node_patch(bogus_patch)
    assert_true(not bogus_validation["valid"], bogus_validation)
    assert_true(
        any(
            "not found in" in (item.get("message") or "")
            for item in bogus_validation.get("diagnostics", [])
        ),
        f"Bogus menu value must be rejected with the real options: {bogus_validation}",
    )

    result = {
        "blender": list(bpy.app.version[:3]),
        "vector_applied": applied_vector,
        "menu_default": applied_mode,
        "ok": True,
    }
    cleanup()
    return result


if __name__ == "__main__":
    try:
        print(RESULT_PREFIX + json.dumps(run_test(), sort_keys=True))
    except Exception:
        traceback.print_exc()
        cleanup()
        raise
