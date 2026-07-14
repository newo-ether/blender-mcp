"""Headless Blender integration test for Geometry Nodes read-only handlers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import traceback

import bpy


PREFIX = "__BLENDER_MCP_GN_READONLY_TEST__"
RESULT_PREFIX = "BLENDER_MCP_GN_READONLY_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def remove_fixtures():
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(PREFIX):
            bpy.data.meshes.remove(mesh, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def geometry_interface(tree, include_scale=False):
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    if include_scale:
        scale = tree.interface.new_socket(
            name="Scale", in_out="INPUT", socket_type="NodeSocketFloat"
        )
        scale.default_value = 1.25


def build_fixture():
    nested = bpy.data.node_groups.new(PREFIX + "Nested", "GeometryNodeTree")
    geometry_interface(nested)
    nested_input = nested.nodes.new("NodeGroupInput")
    nested_output = nested.nodes.new("NodeGroupOutput")
    nested_transform = nested.nodes.new("GeometryNodeTransform")
    nested.links.new(nested_input.outputs["Geometry"], nested_transform.inputs["Geometry"])
    nested.links.new(nested_transform.outputs["Geometry"], nested_output.inputs["Geometry"])

    tree = bpy.data.node_groups.new(PREFIX + "Main", "GeometryNodeTree")
    geometry_interface(tree, include_scale=True)
    group_input = tree.nodes.new("NodeGroupInput")
    group_output = tree.nodes.new("NodeGroupOutput")
    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (1.0, 2.0, 3.0)
    nested_node = tree.nodes.new("GeometryNodeGroup")
    nested_node.node_tree = nested
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    transform = tree.nodes.new("GeometryNodeTransform")

    tree.links.new(group_input.outputs["Geometry"], nested_node.inputs["Geometry"])
    tree.links.new(cube.outputs["Mesh"], join.inputs["Geometry"])
    tree.links.new(nested_node.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], transform.inputs["Geometry"])
    tree.links.new(transform.outputs["Geometry"], group_output.inputs["Geometry"])

    zones = []
    for input_type, output_type in (
        ("GeometryNodeSimulationInput", "GeometryNodeSimulationOutput"),
        ("GeometryNodeRepeatInput", "GeometryNodeRepeatOutput"),
        (
            "GeometryNodeForeachGeometryElementInput",
            "GeometryNodeForeachGeometryElementOutput",
        ),
    ):
        output_node = tree.nodes.new(output_type)
        input_node = tree.nodes.new(input_type)
        input_node.pair_with_output(output_node)
        zones.append((input_node.name, output_node.name))

    mesh = bpy.data.meshes.new(PREFIX + "Mesh")
    obj = bpy.data.objects.new(PREFIX + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(PREFIX + "Modifier", "NODES")
    modifier.node_group = tree
    return tree, nested, nested_node.name, join.name, zones


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def run_test():
    remove_fixtures()
    namespace = runpy.run_path(
        str(REPO_ROOT / "addon.py"),
        run_name="blender_mcp_addon_readonly_test",
    )
    server = namespace["BlenderMCPServer"]()
    tree, nested, nested_node_name, join_name, zones = build_fixture()

    first = server.export_geometry_node_tree(tree.name, "semantic")
    second = server.export_geometry_node_tree(tree.name, "semantic")
    layout = server.export_geometry_node_tree(tree.name, "layout")
    subgraph = server.export_geometry_node_tree(tree.name, "semantic", [join_name], 1)
    listing = server.list_geometry_node_trees()

    assert_true(first == second, "Repeated exports must be byte-equivalent as objects")
    assert_true(first["revision"] == second["revision"], "Revision must be deterministic")
    assert_true("layout" not in first["tree"]["nodes"][join_name], "Semantic view leaked layout")
    assert_true(layout["tree"]["links"] == [], "Layout view must omit semantic links")
    assert_true(
        "layout" in layout["tree"]["nodes"][join_name],
        "Layout view omitted node position",
    )
    assert_true(subgraph["scope"]["kind"] == "subgraph", "Subgraph scope missing")
    assert_true(join_name in subgraph["tree"]["nodes"], "Requested node missing")
    assert_true(subgraph["revision"] == first["revision"], "Subgraph lost full-tree revision")
    assert_true(
        subgraph["stats"]["node_count"] < subgraph["stats"]["total_node_count"],
        "Subgraph export did not reduce graph size",
    )
    assert_true(
        first["tree"]["nodes"][nested_node_name]["properties"]["node_tree"]["name"]
        == nested.name,
        "Nested group ID reference was not encoded",
    )
    assert_true(
        sum("multi_input_sort_id" in link for link in first["tree"]["links"]) >= 2,
        "Multi-input link ordering was not exported",
    )
    for input_name, output_name in zones:
        pair = first["tree"]["nodes"][input_name]["properties"].get("paired_output")
        assert_true(pair and pair["name"] == output_name, f"Zone pair missing for {input_name}")

    main_summary = next(item for item in listing["trees"] if item["name"] == tree.name)
    assert_true(main_summary["revision"] == first["revision"], "List/export revisions differ")
    assert_true(
        any(user["kind"] == "MODIFIER" for user in first["users"]),
        "Modifier user missing",
    )
    assert_true(
        any(user["kind"] == "GROUP_NODE" for user in server.export_geometry_node_tree(nested.name)["users"]),
        "Nested group user missing",
    )

    before_groups = len(bpy.data.node_groups)
    type_schema = server.get_geometry_node_type_schema("GeometryNodeJoinGeometry")
    assert_true(type_schema["node_type"] == "GeometryNodeJoinGeometry", "Wrong node schema")
    assert_true(any(item["multi_input"] for item in type_schema["inputs"]), "Missing multi-input socket")
    assert_true(len(bpy.data.node_groups) == before_groups, "Type schema leaked temporary tree")

    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert_true(first["stats"]["json_bytes"] == len(encoded.encode("utf-8")), "json_bytes mismatch")
    json.loads(encoded)
    output_path = os.environ.get("BLENDER_MCP_TEST_OUTPUT")
    if output_path:
        Path(output_path).write_text(
            json.dumps(first, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "blender_version": list(bpy.app.version[:3]),
        "revision": first["revision"],
        "nodes": first["stats"]["node_count"],
        "links": first["stats"]["link_count"],
        "interface_items": first["stats"]["interface_item_count"],
        "json_bytes": first["stats"]["json_bytes"],
        "tree_count": listing["tree_count"],
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
    remove_fixtures()
