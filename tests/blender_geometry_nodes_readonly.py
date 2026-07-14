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
    scale = None
    if include_scale:
        scale = tree.interface.new_socket(
            name="Scale", in_out="INPUT", socket_type="NodeSocketFloat"
        )
        scale.default_value = 1.25
    return scale


def build_fixture():
    nested = bpy.data.node_groups.new(PREFIX + "Nested", "GeometryNodeTree")
    geometry_interface(nested)
    nested_input = nested.nodes.new("NodeGroupInput")
    nested_output = nested.nodes.new("NodeGroupOutput")
    nested_transform = nested.nodes.new("GeometryNodeTransform")
    nested.links.new(nested_input.outputs["Geometry"], nested_transform.inputs["Geometry"])
    nested.links.new(nested_transform.outputs["Geometry"], nested_output.inputs["Geometry"])

    tree = bpy.data.node_groups.new(PREFIX + "Main", "GeometryNodeTree")
    scale_interface = geometry_interface(tree, include_scale=True)
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
    return (
        tree,
        nested,
        nested_node.name,
        join.name,
        cube.name,
        zones,
        obj.name,
        modifier.name,
        scale_interface.identifier,
    )


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
    (
        tree,
        nested,
        nested_node_name,
        join_name,
        cube_name,
        zones,
        object_name,
        modifier_name,
        scale_identifier,
    ) = build_fixture()

    first = server.export_geometry_node_tree(tree.name, "semantic")
    second = server.export_geometry_node_tree(tree.name, "semantic")
    layout = server.export_geometry_node_tree(tree.name, "layout")
    subgraph = server.export_geometry_node_tree(tree.name, "semantic", [join_name], 1)
    listing = server.list_geometry_node_trees()

    assert_true(first == second, "Repeated exports must be byte-equivalent as objects")
    assert_true(first["revision"] == second["revision"], "Revision must be deterministic")
    assert_true("layout" not in first["tree"]["nodes"][join_name], "Semantic view leaked layout")
    assert_true(layout["tree"]["links"] == [], "Layout view must omit semantic links")
    assert_true(layout["revision"] == first["revision"], "Source revision must be view-independent")
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
    join_node = tree.nodes[join_name]
    original_location = join_node.location.copy()
    join_node.location.x += 37.0
    layout_changed = server.export_geometry_node_tree(tree.name, "semantic")
    assert_true(
        layout_changed["revision"] != first["revision"],
        "Semantic export revision failed to detect a layout-only source change",
    )
    join_node.location = original_location
    assert_true(
        server.export_geometry_node_tree(tree.name, "semantic") == first,
        "Restoring layout did not restore the deterministic snapshot",
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

    patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "operations": [
            {
                "op": "add_node",
                "id": "new_transform",
                "node_type": "GeometryNodeTransform",
                "name": PREFIX + "PatchTransform",
                "layout": {"location": [600.0, 100.0]},
            },
            {
                "op": "set_node_property",
                "node": "new_transform",
                "property": "mute",
                "value": True,
            },
            {
                "op": "set_socket_default",
                "node": "new_transform",
                "socket": "input:2:Translation",
                "value": [1.0, 2.0, 3.0],
            },
            {
                "op": "remove_link",
                "from_node": cube_name,
                "from_socket": "output:0:Mesh",
                "to_node": join_name,
                "to_socket": "input:0:Geometry",
            },
            {
                "op": "add_link",
                "from_node": cube_name,
                "from_socket": "output:0:Mesh",
                "to_node": "new_transform",
                "to_socket": "input:0:Geometry",
            },
            {
                "op": "add_link",
                "from_node": "new_transform",
                "from_socket": "output:0:Geometry",
                "to_node": join_name,
                "to_socket": "input:0:Geometry",
            },
            {
                "op": "rename_node",
                "node": cube_name,
                "name": PREFIX + "RenamedCube",
            },
            {
                "op": "set_node_layout",
                "node": join_name,
                "location": [800.0, 0.0],
                "width": 180.0,
            },
            {
                "op": "add_interface_socket",
                "id": "new_density",
                "name": "Density",
                "in_out": "INPUT",
                "socket_type": "NodeSocketFloat",
                "default": 0.5,
            },
            {
                "op": "remove_interface_socket",
                "identifier": "new_density",
            },
            {
                "op": "set_modifier_input",
                "object": object_name,
                "modifier": modifier_name,
                "socket": scale_identifier,
                "value": 2.0,
            },
            {
                "op": "remove_node",
                "node": "new_transform",
            },
        ],
    }
    groups_before_dry_run = len(bpy.data.node_groups)
    dry_run = server.validate_geometry_node_patch(patch)
    assert_true(dry_run["valid"], f"Valid patch was rejected: {dry_run['diagnostics']}")
    assert_true(not dry_run["will_mutate"], "Dry-run claimed it would mutate")
    assert_true(all(item["status"] == "ready" for item in dry_run["plan"]), "Plan not ready")
    assert_true(len(dry_run["plan"]) == len(patch["operations"]), "Incomplete plan")
    assert_true(
        len(bpy.data.node_groups) == groups_before_dry_run,
        "Dry-run leaked a temporary validation tree",
    )
    after_dry_run = server.export_geometry_node_tree(tree.name, "semantic")
    assert_true(after_dry_run == first, "Dry-run mutated the live node tree")

    stale_patch = {**patch, "base_revision": "sha256:" + "0" * 64, "operations": patch["operations"][:1]}
    stale_result = server.validate_geometry_node_patch(stale_patch)
    assert_true(not stale_result["valid"], "Stale patch was accepted")
    assert_true(
        any(item["code"] == "stale_revision" for item in stale_result["diagnostics"]),
        "Stale revision diagnostic missing",
    )

    invalid_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "operations": [
            {
                "op": "set_node_property",
                "node": cube_name,
                "property": "definitely_missing",
                "value": 1,
            },
            {
                "op": "set_socket_default",
                "node": cube_name,
                "socket": "input:99:Size",
                "value": [1.0, 1.0, 1.0],
            },
        ],
    }
    invalid_result = server.validate_geometry_node_patch(invalid_patch)
    invalid_by_code = {item["code"]: item["path"] for item in invalid_result["diagnostics"]}
    assert_true(
        invalid_by_code.get("unknown_rna_property") == "/operations/0/property",
        "Unknown property diagnostic path is unstable",
    )
    assert_true(
        invalid_by_code.get("socket_index_out_of_range") == "/operations/1/socket",
        "Invalid socket diagnostic path is unstable",
    )

    second_mesh = bpy.data.meshes.new(PREFIX + "SharedMesh")
    second_object = bpy.data.objects.new(PREFIX + "SharedObject", second_mesh)
    bpy.context.scene.collection.objects.link(second_object)
    second_modifier = second_object.modifiers.new(PREFIX + "SharedModifier", "NODES")
    second_modifier.node_group = tree
    shared_operation = [{"op": "set_node_layout", "node": join_name, "width": 190.0}]
    shared_reject = server.validate_geometry_node_patch({
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "operations": shared_operation,
    })
    assert_true(
        any(item["code"] == "shared_tree_rejected" for item in shared_reject["diagnostics"]),
        "Shared-tree rejection diagnostic missing",
    )
    shared_copy = server.validate_geometry_node_patch({
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "shared_tree_policy": "single_user_copy",
        "target_user": {
            "kind": "MODIFIER",
            "object": object_name,
            "modifier": modifier_name,
        },
        "operations": shared_operation,
    })
    assert_true(shared_copy["valid"], f"Explicit shared copy was rejected: {shared_copy['diagnostics']}")

    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert_true(first["stats"]["json_bytes"] == len(encoded.encode("utf-8")), "json_bytes mismatch")
    json.loads(encoded)
    output_path = os.environ.get("BLENDER_MCP_TEST_OUTPUT")
    if output_path:
        Path(output_path).write_text(
            json.dumps(first, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    validation_output_path = os.environ.get("BLENDER_MCP_TEST_VALIDATION_OUTPUT")
    if validation_output_path:
        Path(validation_output_path).write_text(
            json.dumps(dry_run, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
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
        "dry_run_operations": len(dry_run["plan"]),
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
