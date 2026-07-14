"""Live Blender acceptance for empty compositor setup and graph diagnostics."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import traceback

import bpy


PREFIX = "BlenderMCP_CompositorInit_"


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    return parser.parse_args(arguments)


def cleanup() -> None:
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
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
        "blender_mcp_compositor_initialization_acceptance",
        addon_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on module from {addon_path}")
    addon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(addon)
    return addon


def run_test() -> None:
    cleanup()
    addon = load_addon()
    server = object.__new__(addon.BlenderMCPServer)

    scene = bpy.data.scenes.new(PREFIX + "Scene")
    untouched_scene = bpy.data.scenes.new(PREFIX + "Untouched")
    rollback_scene = bpy.data.scenes.new(PREFIX + "Rollback")

    missing = server.ensure_scene_compositor_tree(
        scene.name, create_if_missing=False
    )
    assert missing["status"] == "missing", missing
    assert not missing["mutated"]
    assert addon._node_scene_tree(scene)[0] is None

    created = server.ensure_scene_compositor_tree(
        scene.name, create_if_missing=True
    )
    assert created["status"] == "created", created
    assert created["created"] and created["mutated"]
    tree = addon._node_scene_tree(scene)[0]
    assert tree is not None
    assert addon._node_scene_tree(untouched_scene)[0] is None
    if hasattr(scene, "compositing_node_group"):
        assert tree.bl_idname == "CompositorNodeTree"
        assert any(
            item.item_type == "SOCKET"
            and item.in_out == "OUTPUT"
            and item.name == "Image"
            for item in tree.interface.items_tree
        )
        assert any(node.bl_idname == "NodeGroupOutput" for node in tree.nodes)

    ready = server.ensure_scene_compositor_tree(
        scene.name, create_if_missing=True
    )
    assert ready["status"] == "ready", ready
    assert not ready["created"] and not ready["mutated"]
    assert addon._node_scene_tree(scene)[0] == tree

    reference = created["tree_ref"]
    before = server.export_node_tree(reference, "operations")
    patch = {
        "schema": "blender-node-tree-patch/1",
        "tree_ref": reference,
        "base_revision": before["revision"],
        "capabilities": ["graph"],
        "operations": [{
            "op": "add_node",
            "id": "render_layers",
            "node_type": "CompositorNodeRLayers",
            "name": "Render Layers Source",
        }],
    }
    validation = server.validate_node_tree_patch(patch)
    assert validation["valid"], validation
    application = server.apply_node_tree_patch(patch, keep_backup=False)
    assert application["status"] == "applied", application
    assert server.export_node_tree(reference, "operations")["tree"]["nodes"].get(
        "Render Layers Source"
    )

    def reject_after_assignment(stage, _scene, _tree):
        if stage == "after_scene_tree_enabled":
            raise RuntimeError("injected compositor initialization failure")

    rolled_back = addon._node_ensure_scene_compositor_tree(
        rollback_scene.name,
        True,
        _commit_guard=reject_after_assignment,
    )
    assert rolled_back["status"] == "rolled_back", rolled_back
    assert not rolled_back["mutated"]
    assert addon._node_scene_tree(rollback_scene)[0] is None

    mesh = bpy.data.meshes.new(PREFIX + "PrototypeMesh")
    prototype = bpy.data.objects.new(PREFIX + "Prototype", mesh)
    prototype.hide_render = True
    geometry = bpy.data.node_groups.new(PREFIX + "Geometry", "GeometryNodeTree")
    geometry.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    object_info = geometry.nodes.new("GeometryNodeObjectInfo")
    object_info.name = "Hidden Prototype"
    object_info.inputs["Object"].default_value = prototype
    object_info.inputs["As Instance"].default_value = True
    group_output = geometry.nodes.new("NodeGroupOutput")
    geometry.links.new(object_info.outputs["Geometry"], group_output.inputs["Geometry"])
    geometry_ref = {
        "tree_type": "GeometryNodeTree",
        "owner": {"kind": "NODE_GROUP", "name": geometry.name},
    }
    exported = server.export_node_tree(geometry_ref, "operations")
    warning_codes = {item["code"] for item in exported["diagnostics"]}
    assert "hidden_object_info_instance_source" in warning_codes, exported["diagnostics"]

    warning_patch = {
        "schema": "blender-node-tree-patch/1",
        "tree_ref": geometry_ref,
        "base_revision": exported["revision"],
        "capabilities": ["layout"],
        "operations": [{
            "op": "set_node_layout",
            "node": object_info.name,
            "location": [40.0, 20.0],
        }],
    }
    warning_validation = server.validate_node_tree_patch(warning_patch)
    assert warning_validation["valid"], warning_validation
    assert "hidden_object_info_instance_source" in {
        item["code"] for item in warning_validation["diagnostics"]
    }

    result = {
        "version": list(bpy.app.version[:3]),
        "adapter": created["adapter"],
        "created_revision": created["revision"],
        "patch_status": application["status"],
        "rollback_status": rolled_back["status"],
        "warning_codes": sorted(warning_codes),
        "untouched_scene_preserved": addon._node_scene_tree(untouched_scene)[0] is None,
    }
    print("BLENDER_COMPOSITOR_INITIALIZATION=" + json.dumps(result, sort_keys=True))
    cleanup()


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
