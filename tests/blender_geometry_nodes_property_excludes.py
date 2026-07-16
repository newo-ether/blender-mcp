"""Headless Blender regression test for node snapshot context economy.

Locks in three related guarantees:

1. Static ``bl_*`` display metadata (``bl_label``, ``bl_description``,
   ``bl_icon``, ``bl_width_default``/``min``/``max``, ``bl_height_default``/
   ``min``/``max``) is NOT serialized into operation/semantic/all snapshots,
   while operation-defining enums and genuine per-instance state still are.
2. The ``slim`` reading view stays smaller than ``operations``, keeps operation
   enums, emits string links whose endpoints all resolve, omits only Frames, and
   reports that omission instead of hiding it.
3. ``semantic`` sockets do not restate ``index``/``direction``/``identifier``
   (all recoverable from the socket id) nor default flags, while keeping the
   human-facing ``name``.

Every view shares one source revision, so each assertion below also guards
against a view silently diverging from the revision the patch protocol uses.

Background: those ``bl_*`` identifiers are writable, non-hidden, non-skip-save
RNA properties whose ``prop.default`` is the base-type default (``''`` or ``0.0``),
not the subclass value. Without an explicit exclude they slipped past the
non-default filter in ``_gn_operation_properties`` / ``_gn_rna_properties`` and
inflated every node record with static display metadata that has no value for
patch round-trips.
"""

from __future__ import annotations

import json
import runpy
import sys
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_GN_PROP_EXCLUDES_TEST__"
RESULT_PREFIX = "BLENDER_MCP_GN_PROP_EXCLUDES_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]

# Static display metadata that must never appear in serialized node properties.
_DISPLAY_METADATA = {
    "bl_label", "bl_description", "bl_icon",
    "bl_width_default", "bl_width_min", "bl_width_max",
    "bl_height_default", "bl_height_min", "bl_height_max",
}


def remove_fixtures():
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def build_fixture():
    tree = bpy.data.node_groups.new(PREFIX + "Main", "GeometryNodeTree")
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group_input = tree.nodes.new("NodeGroupInput")
    group_output = tree.nodes.new("NodeGroupOutput")
    cube = tree.nodes.new("GeometryNodeMeshCube")
    # A Math node carries an operation-defining enum plus a non-default input.
    math_node = tree.nodes.new("ShaderNodeMath")
    math_node.operation = "MULTIPLY"
    math_node.inputs[0].default_value = 3.0
    transform = tree.nodes.new("GeometryNodeTransform")
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    tree.links.new(group_input.outputs["Geometry"], transform.inputs["Geometry"])
    tree.links.new(cube.outputs["Mesh"], join.inputs["Geometry"])
    tree.links.new(transform.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], group_output.inputs["Geometry"])
    return tree, math_node.name, transform.name, join.name


def _property_keys(snapshot):
    keys = set()
    for node in snapshot["tree"]["nodes"].values():
        keys.update(node.get("properties", {}).keys())
    return keys


def run_test():
    remove_fixtures()
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_addon_prop_excludes_test",
    )
    server = namespace["BlenderMCPServer"]()
    tree, math_name, transform_name, join_name = build_fixture()

    operations = server.export_geometry_node_tree(tree.name, "operations")
    semantic = server.export_geometry_node_tree(tree.name, "semantic")
    full = server.export_geometry_node_tree(tree.name, "all")

    # Revision is computed from an internal view="all" pass that shares the same
    # exclude set, so it must still be consistent across public views.
    assert_true(
        operations["revision"] == semantic["revision"] == full["revision"],
        "Revision drifted across views after the bl_* exclude change",
    )

    for label, snapshot in (("operations", operations), ("semantic", semantic), ("all", full)):
        leaked = sorted(_property_keys(snapshot) & _DISPLAY_METADATA)
        assert_true(
            not leaked,
            f"{label} view leaked static bl_* display metadata: {leaked}",
        )

    # Operation-defining enums must survive (this is the patch-relevant state).
    for label, snapshot in (("operations", operations), ("semantic", semantic), ("all", full)):
        math_props = snapshot["tree"]["nodes"][math_name]["properties"]
        assert_true(
            math_props.get("operation") == "MULTIPLY",
            f"{label} view lost the Math node operation enum",
        )

    # Genuine non-default per-instance state must survive in semantic/all.
    for label, snapshot in (("semantic", semantic), ("all", full)):
        transform_props = snapshot["tree"]["nodes"][transform_name]["properties"]
        assert_true(
            "bl_idname" not in transform_props,
            f"{label} view leaked bl_idname (already excluded)",
        )

    # Operations view must remain meaningfully smaller than semantic.
    assert_true(
        operations["stats"]["json_bytes"] < semantic["stats"]["json_bytes"],
        "Operations view is not smaller than semantic after the exclude change",
    )

    slim = server.export_geometry_node_tree(tree.name, "slim")
    assert_true(
        slim["revision"] == operations["revision"],
        "Slim view drifted from the shared source revision",
    )
    assert_true(
        slim["stats"]["json_bytes"] < operations["stats"]["json_bytes"],
        "Slim view is not smaller than operations",
    )

    # Slim keeps what the graph computes.
    slim_math = slim["tree"]["nodes"][math_name]
    assert_true(
        slim_math["properties"].get("operation") == "MULTIPLY",
        "Slim view lost the Math node operation enum",
    )
    assert_true(
        "warning_propagation" not in slim_math.get("properties", {}),
        "Slim view leaked the warning_propagation UI enum",
    )

    # Frames are omitted, and that omission is reported rather than hidden.
    frame = tree.nodes.new("NodeFrame")
    try:
        with_frame = server.export_geometry_node_tree(tree.name, "slim")
        assert_true(
            frame.name not in with_frame["tree"]["nodes"],
            "Slim view emitted a NodeFrame",
        )
        assert_true(
            with_frame["stats"]["total_node_count"] == len(tree.nodes),
            "Slim total_node_count must count the whole tree, not just emitted nodes",
        )
        assert_true(
            with_frame["stats"].get("omitted_node_count") == 1
            and "NodeFrame" in (with_frame.get("view_omits") or []),
            "Slim view did not report which nodes it omitted",
        )
    finally:
        tree.nodes.remove(frame)

    # Slim links are strings, and every endpoint must resolve to an emitted node.
    slim_names = set(slim["tree"]["nodes"])
    for link in slim["tree"]["links"]:
        assert_true(isinstance(link, str), "Slim link is not a compact string")
        endpoints = link.split("#", 1)[0].split(" >> ")
        assert_true(len(endpoints) == 2, f"Slim link is malformed: {link}")
        for endpoint in endpoints:
            node_name = endpoint.rsplit("|", 1)[0]
            assert_true(
                node_name in slim_names,
                f"Slim link references a node the view omitted: {link}",
            )

    # A slim subgraph export must survive the string-link filter path.
    slim_subgraph = server.export_geometry_node_tree(tree.name, "slim", [join_name], 1)
    assert_true(
        slim_subgraph["scope"]["kind"] == "subgraph",
        "Slim subgraph export lost its scope",
    )
    assert_true(
        all(isinstance(link, str) for link in slim_subgraph["tree"]["links"]),
        "Slim subgraph links are not compact strings",
    )
    assert_true(
        slim_subgraph["revision"] == slim["revision"],
        "Slim subgraph lost the full-tree revision",
    )

    # Semantic sockets must not restate what the socket id already encodes,
    # while keeping the human-facing name that genuinely differs from it.
    semantic_sockets = [
        socket
        for node in semantic["tree"]["nodes"].values()
        for socket in node.get("inputs", []) + node.get("outputs", [])
    ]
    assert_true(semantic_sockets, "Semantic view exported no sockets")
    for socket in semantic_sockets:
        for derived in ("index", "direction", "identifier"):
            assert_true(
                derived not in socket,
                f"Semantic socket restates {derived}, which socket id already encodes",
            )
        direction, index, _identifier = socket["id"].split(":", 2)
        assert_true(
            direction in {"input", "output"} and index.isdigit(),
            f"Semantic socket id is not recoverable: {socket['id']}",
        )
        assert_true(
            socket.get("enabled") is not True and socket.get("multi_input") is not False,
            f"Semantic socket restates a default flag: {socket}",
        )

    return {
        "blender_version": list(bpy.app.version[:3]),
        "slim_bytes": slim["stats"]["json_bytes"],
        "operations_bytes": operations["stats"]["json_bytes"],
        "semantic_bytes": semantic["stats"]["json_bytes"],
        "all_bytes": full["stats"]["json_bytes"],
        "revision": operations["revision"],
    }


try:
    result = run_test()
    print(RESULT_PREFIX + json.dumps({"ok": True, **result}, sort_keys=True))
except Exception as exc:
    traceback.print_exc()
    print(
        RESULT_PREFIX
        + json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True)
    )
    sys.exit(1)
finally:
    remove_fixtures()
