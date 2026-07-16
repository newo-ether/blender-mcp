"""Live Blender acceptance for structured node-group and modifier bootstrap."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

import bpy

PREFIX = "BlenderMCP_NodeBootstrap_"
RESULT_PREFIX = "BLENDER_MCP_NODE_BOOTSTRAP_RESULT="


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    return parser.parse_args(arguments)


def cleanup() -> None:
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(PREFIX):
            bpy.data.meshes.remove(mesh, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def load_addon():
    addon_path = Path(parse_args().addon).resolve()
    spec = importlib.util.spec_from_file_location(
        "blender_mcp_node_bootstrap_acceptance",
        addon_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on module from {addon_path}")
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    try:
        spec.loader.exec_module(addon)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return addon


def run_test() -> dict:
    cleanup()
    addon = load_addon()
    server = object.__new__(addon.BlenderMCPServer)
    group_name = PREFIX + "Geometry"
    object_name = PREFIX + "Host"

    created = server.create_node_group(
        group_name,
        "GeometryNodeTree",
        geometry_is_modifier=True,
        description="Structured bootstrap acceptance",
    )
    assert created["status"] == "created", created
    assert created["created"] and created["mutated"], created
    assert created["revision"].startswith("sha256:"), created
    tree = bpy.data.node_groups[group_name]
    assert tree.bl_idname == "GeometryNodeTree"
    assert bool(getattr(tree, "is_modifier", False))

    collision = server.create_node_group(
        group_name,
        "GeometryNodeTree",
        geometry_is_modifier=True,
    )
    assert collision["status"] == "rejected", collision
    assert not collision["mutated"]

    existing = server.create_node_group(
        group_name,
        "GeometryNodeTree",
        geometry_is_modifier=True,
        reuse_existing=True,
    )
    assert existing["status"] == "existing", existing
    assert not existing["created"] and not existing["mutated"]

    missing = server.ensure_geometry_nodes_modifier(object_name, group_name)
    assert missing["status"] == "missing", missing
    assert not missing["mutated"]
    assert bpy.data.objects.get(object_name) is None

    host = server.ensure_geometry_nodes_modifier(
        object_name,
        group_name,
        modifier_name="PCB Traces",
        create_object_if_missing=True,
        create_modifier_if_missing=True,
        location=[1.0, 2.0, 3.0],
    )
    assert host["status"] == "created", host
    assert host["created_object"] and host["created_modifier"] and host["mutated"]
    obj = bpy.data.objects[object_name]
    modifier = obj.modifiers["PCB Traces"]
    assert obj.type == "MESH"
    assert list(obj.location) == [1.0, 2.0, 3.0]
    assert modifier.node_group == tree

    empty_mesh = bpy.data.meshes.new(PREFIX + "EmptyHost Mesh")
    empty_obj = bpy.data.objects.new(PREFIX + "EmptyHost", empty_mesh)
    bpy.context.scene.collection.objects.link(empty_obj)
    empty_modifier = empty_obj.modifiers.new("Unassigned", "NODES")
    rejected_assignment = server.ensure_geometry_nodes_modifier(
        empty_obj.name,
        group_name,
        modifier_name=empty_modifier.name,
    )
    assert rejected_assignment["status"] == "rejected", rejected_assignment
    assert not rejected_assignment["mutated"]
    assert empty_modifier.node_group is None
    explicit_assignment = server.ensure_geometry_nodes_modifier(
        empty_obj.name,
        group_name,
        modifier_name=empty_modifier.name,
        assign_if_different=True,
    )
    assert explicit_assignment["status"] == "assigned", explicit_assignment
    assert explicit_assignment["mutated"]
    assert empty_modifier.node_group == tree
    bpy.data.objects.remove(empty_obj, do_unlink=True)
    bpy.data.meshes.remove(empty_mesh)

    patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": group_name,
        "base_revision": host["revision"],
        "operations": [
            {
                "op": "add_interface_panel",
                "id": "routing",
                "name": "Routing",
                "description": "Shortest-path routing controls",
                "default_closed": True,
            },
            {
                "op": "add_interface_socket",
                "id": "trace_width",
                "name": "Trace Width",
                "in_out": "INPUT",
                "socket_type": "NodeSocketFloat",
                "parent": "routing",
                "default": 0.02,
            },
            {
                "op": "set_interface_item",
                "identifier": "trace_width",
                "property": "min_value",
                "value": 0.001,
            },
            {
                "op": "set_interface_item",
                "identifier": "trace_width",
                "property": "max_value",
                "value": 1.0,
            },
        ],
    }
    validation = server.validate_geometry_node_patch(patch)
    assert validation["valid"], validation
    assert validation["semantic_diff"]["interface_panels_added"] == 1
    assert validation["semantic_diff"]["interface_sockets_added"] == 1
    assert validation["semantic_diff"]["interface_items_changed"] == 2
    application = server.apply_geometry_node_patch(patch, keep_backup=False)
    assert application["status"] == "applied", application

    tree = bpy.data.node_groups[group_name]
    interface = list(tree.interface.items_tree)
    panel = next(item for item in interface if item.item_type == "PANEL")
    socket = next(item for item in interface if item.item_type == "SOCKET")
    assert panel.name == "Routing" and panel.default_closed
    assert socket.parent == panel
    assert abs(socket.default_value - 0.02) < 1e-8
    assert abs(socket.min_value - 0.001) < 1e-8
    assert abs(socket.max_value - 1.0) < 1e-8

    exported = server.export_geometry_node_tree(group_name, "operations")
    socket_record = next(
        item for item in exported["tree"]["interface"]
        if item["item_type"] == "SOCKET"
    )
    panel_identifier = getattr(panel, "identifier", "") or panel.name
    assert socket_record["parent"] == panel_identifier
    assert abs(socket_record["min_value"] - 0.001) < 1e-8
    assert abs(socket_record["max_value"] - 1.0) < 1e-8

    shader_name = PREFIX + "Shader"
    shader_created = server.create_node_group(shader_name, "ShaderNodeTree")
    assert shader_created["status"] == "created", shader_created
    shader_patch = {
        "schema": "blender-node-tree-patch/1",
        "tree_ref": shader_created["tree_ref"],
        "base_revision": shader_created["revision"],
        "capabilities": ["interface"],
        "operations": [
            {
                "op": "add_interface_panel",
                "id": "surface",
                "name": "Surface",
                "description": "Shader controls",
            },
            {
                "op": "add_interface_socket",
                "id": "roughness",
                "name": "Roughness",
                "in_out": "INPUT",
                "socket_type": "NodeSocketFloat",
                "parent": "surface",
                "default": 0.5,
            },
            {
                "op": "set_interface_item",
                "identifier": "roughness",
                "property": "min_value",
                "value": 0.0,
            },
        ],
    }
    shader_validation = server.validate_node_tree_patch(shader_patch)
    assert shader_validation["valid"], shader_validation
    shader_application = server.apply_node_tree_patch(
        shader_patch, keep_backup=False
    )
    assert shader_application["status"] == "applied", shader_application
    shader = bpy.data.node_groups[shader_name]
    shader_interface = list(shader.interface.items_tree)
    assert [item.item_type for item in shader_interface] == ["PANEL", "SOCKET"]
    assert abs(shader_interface[1].default_value - 0.5) < 1e-8

    compositor_created = server.create_node_group(
        PREFIX + "Compositor", "CompositorNodeTree"
    )
    assert compositor_created["status"] == "created", compositor_created
    assert compositor_created["revision"].startswith("sha256:")

    result = {
        "blender": list(bpy.app.version[:3]),
        "group_status": created["status"],
        "host_status": host["status"],
        "interface_items": len(interface),
        "application": application["status"],
        "shader_application": shader_application["status"],
        "compositor_status": compositor_created["status"],
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
