"""Prove linked Shader/Compositor owners and groups fail closed as read-only."""

from __future__ import annotations

import json
import runpy
import tempfile
import traceback
from pathlib import Path

import bpy


PREFIX = "__BLENDER_MCP_SC_LINKED__"
RESULT_PREFIX = "BLENDER_MCP_SC_LINKED_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def cleanup():
    for collection_name in (
        "materials", "worlds", "lights", "node_groups", "scenes", "objects", "meshes"
    ):
        collection = getattr(bpy.data, collection_name)
        for value in list(collection):
            if value.name.startswith(PREFIX):
                collection.remove(value, do_unlink=True)


def record(value, tree=None):
    library = getattr(value, "library", None)
    tree_library = getattr(tree, "library", None) if tree else None
    return {
        "name": value.name,
        "library": library.filepath if library else None,
        "editable": bool(getattr(value, "is_editable", library is None)),
        "override": getattr(value, "override_library", None) is not None,
        "tree_library": tree_library.filepath if tree_library else None,
        "tree_editable": bool(getattr(tree, "is_editable", tree_library is None)) if tree else None,
    }


def main():
    cleanup()
    active_scene = bpy.context.scene
    with tempfile.TemporaryDirectory(prefix="blender-mcp-n0-linked-") as temp_dir:
        library_path = Path(temp_dir) / "node_owners.blend"
        material = bpy.data.materials.new(PREFIX + "Material")
        material.use_nodes = True
        world = bpy.data.worlds.new(PREFIX + "World")
        world.use_nodes = True
        light = bpy.data.lights.new(PREFIX + "Light", "POINT")
        light.use_nodes = True
        shader_group = bpy.data.node_groups.new(PREFIX + "ShaderGroup", "ShaderNodeTree")
        shader_group.nodes.new("ShaderNodeValue")
        compositor_group = bpy.data.node_groups.new(PREFIX + "CompositorGroup", "CompositorNodeTree")
        compositor_group.nodes.new("NodeFrame")
        names = {
            "material": material.name,
            "world": world.name,
            "light": light.name,
            "shader_group": shader_group.name,
            "compositor_group": compositor_group.name,
        }
        bpy.data.libraries.write(
            str(library_path),
            {material, world, light, shader_group, compositor_group},
        )
        cleanup()

        with bpy.data.libraries.load(str(library_path), link=True) as (source, target):
            target.materials = [names["material"]]
            target.worlds = [names["world"]]
            target.lights = [names["light"]]
            target.node_groups = [names["shader_group"], names["compositor_group"]]

        linked_material = bpy.data.materials[names["material"]]
        linked_world = bpy.data.worlds[names["world"]]
        linked_light = bpy.data.lights[names["light"]]
        linked_shader = bpy.data.node_groups[names["shader_group"]]
        linked_compositor = bpy.data.node_groups[names["compositor_group"]]
        namespace = runpy.run_path(
            str(REPO_ROOT / "addon.py"),
            run_name="blender_mcp_node_linked_test",
        )
        server = namespace["BlenderMCPServer"]()
        generic_listing = server.list_node_trees(
            tree_types=["ShaderNodeTree", "CompositorNodeTree"]
        )
        material_export = server.export_node_tree({
            "tree_type": "ShaderNodeTree",
            "owner": {"kind": "MATERIAL", "name": linked_material.name},
        })
        shader_export = server.export_node_tree({
            "tree_type": "ShaderNodeTree",
            "owner": {"kind": "NODE_GROUP", "name": linked_shader.name},
        })
        result = {
            "version": list(bpy.app.version[:3]),
            "owners": {
                "material": record(linked_material, linked_material.node_tree),
                "world": record(linked_world, linked_world.node_tree),
                "light": record(linked_light, linked_light.node_tree),
            },
            "groups": {
                "shader": record(linked_shader, linked_shader),
                "compositor": record(linked_compositor, linked_compositor),
            },
            "active_scene_unchanged": bpy.context.scene == active_scene,
            "generic": {
                "listed": [
                    item["tree_ref"] for item in generic_listing["trees"]
                    if item["owner"]["name"].startswith(PREFIX)
                ],
                "material_capabilities": material_export["capabilities"],
                "shader_group_capabilities": shader_export["capabilities"],
            },
        }
        for item in list(result["owners"].values()) + list(result["groups"].values()):
            if item["editable"] or item["tree_editable"]:
                raise AssertionError(f"linked target unexpectedly editable: {item}")
        if material_export["capabilities"]["editable"] or material_export["capabilities"]["apply"]:
            raise AssertionError("generic Material capabilities did not fail closed")
        if shader_export["capabilities"]["editable"] or shader_export["capabilities"]["apply"]:
            raise AssertionError("generic Shader group capabilities did not fail closed")
        cleanup()
        result["leaks"] = {
            collection_name: [
                value.name for value in getattr(bpy.data, collection_name)
                if value.name.startswith(PREFIX)
            ]
            for collection_name in ("materials", "worlds", "lights", "node_groups")
        }
        if any(result["leaks"].values()):
            raise AssertionError(f"linked datablock leak: {result['leaks']}")
        print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    main()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
