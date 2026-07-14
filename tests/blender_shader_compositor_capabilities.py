"""Inventory Shader/Compositor owner and runtime-node capabilities."""

from __future__ import annotations

import json
import traceback

import bpy


PREFIX = "__BLENDER_MCP_SC_N0__"
RESULT_PREFIX = "BLENDER_MCP_SC_N0_RESULT="


def attempt(callback):
    try:
        value = callback()
        return {"ok": True, "value": value}
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def id_names(collection):
    return sorted(item.name for item in collection if item.name.startswith(PREFIX))


def tree_summary(tree):
    return {
        "name": tree.name,
        "bl_idname": tree.bl_idname,
        "users": tree.users,
        "in_node_groups": any(item == tree for item in bpy.data.node_groups),
        "nodes": sorted(node.bl_idname for node in tree.nodes),
        "interface_items": len(getattr(getattr(tree, "interface", None), "items_tree", ())),
    }


def set_same(owner, attribute):
    value = getattr(owner, attribute)
    setattr(owner, attribute, value)
    return True


def create_compositor_tree(name):
    return bpy.data.node_groups.new(name, "CompositorNodeTree")


def cleanup():
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(PREFIX):
            bpy.data.meshes.remove(mesh, do_unlink=True)
    for light in list(bpy.data.lights):
        if light.name.startswith(PREFIX):
            bpy.data.lights.remove(light, do_unlink=True)
    for material in list(bpy.data.materials):
        if material.name.startswith(PREFIX):
            bpy.data.materials.remove(material, do_unlink=True)
    for world in list(bpy.data.worlds):
        if world.name.startswith(PREFIX):
            bpy.data.worlds.remove(world, do_unlink=True)
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def main():
    cleanup()
    before_scene = bpy.context.scene
    result = {
        "version": list(bpy.app.version[:3]),
        "version_string": bpy.app.version_string,
        "scene_api": {},
        "owners": {},
        "groups": {},
        "node_types": {},
    }

    material = bpy.data.materials.new(PREFIX + "Material")
    material.use_nodes = True
    material_copy = material.copy()
    result["owners"]["material"] = {
        "tree": tree_summary(material.node_tree),
        "same_pointer_assignment": attempt(lambda: set_same(material, "node_tree")),
        "copy_tree_distinct": material_copy.node_tree != material.node_tree,
        "copy_tree": tree_summary(material_copy.node_tree),
    }

    world = bpy.data.worlds.new(PREFIX + "World")
    world.use_nodes = True
    world_copy = world.copy()
    result["owners"]["world"] = {
        "tree": tree_summary(world.node_tree),
        "same_pointer_assignment": attempt(lambda: set_same(world, "node_tree")),
        "copy_tree_distinct": world_copy.node_tree != world.node_tree,
        "copy_tree": tree_summary(world_copy.node_tree),
    }

    light = bpy.data.lights.new(PREFIX + "Light", "POINT")
    light.use_nodes = True
    light_copy = light.copy()
    result["owners"]["light"] = {
        "tree": tree_summary(light.node_tree),
        "same_pointer_assignment": attempt(lambda: set_same(light, "node_tree")),
        "copy_tree_distinct": light_copy.node_tree != light.node_tree,
        "copy_tree": tree_summary(light_copy.node_tree),
    }

    shader_group = bpy.data.node_groups.new(PREFIX + "ShaderGroup", "ShaderNodeTree")
    shader_group.interface.new_socket(
        name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader"
    )
    shader_group.nodes.new("NodeGroupOutput")
    shader_group.nodes.new("ShaderNodeValue")
    shader_copy = shader_group.copy()
    result["groups"]["shader"] = {
        "tree": tree_summary(shader_group),
        "copy": tree_summary(shader_copy),
        "copy_distinct": shader_copy != shader_group,
    }

    compositor_group = create_compositor_tree(PREFIX + "CompositorGroup")
    compositor_group.interface.new_socket(
        name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    compositor_group.nodes.new("NodeGroupOutput")
    compositor_copy = compositor_group.copy()
    result["groups"]["compositor"] = {
        "tree": tree_summary(compositor_group),
        "copy": tree_summary(compositor_copy),
        "copy_distinct": compositor_copy != compositor_group,
    }

    scene = bpy.data.scenes.new(PREFIX + "Scene")
    result["scene_api"] = {
        "has_use_nodes": hasattr(scene, "use_nodes"),
        "has_node_tree": hasattr(scene, "node_tree"),
        "has_compositing_node_group": hasattr(scene, "compositing_node_group"),
    }
    if hasattr(scene, "compositing_node_group"):
        scene_tree = create_compositor_tree(PREFIX + "SceneTree")
        scene_tree.interface.new_socket(
            name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
        )
        scene_tree.nodes.new("NodeGroupOutput")
        scene.compositing_node_group = scene_tree
        scene_copy = scene_tree.copy()
        same_assignment = attempt(lambda: set_same(scene, "compositing_node_group"))
        swap_assignment = attempt(
            lambda: setattr(scene, "compositing_node_group", scene_copy) or True
        )
        result["owners"]["scene"] = {
            "adapter": "compositing_node_group",
            "tree": tree_summary(scene_tree),
            "copy": tree_summary(scene_copy),
            "same_pointer_assignment": same_assignment,
            "copy_pointer_assignment": swap_assignment,
            "assigned_copy": scene.compositing_node_group == scene_copy,
        }
        scene.compositing_node_group = scene_tree
    else:
        scene.use_nodes = True
        scene_tree = scene.node_tree
        scene_copy = scene_tree.copy()
        result["owners"]["scene"] = {
            "adapter": "node_tree",
            "tree": tree_summary(scene_tree),
            "copy": tree_summary(scene_copy),
            "same_pointer_assignment": attempt(lambda: set_same(scene, "node_tree")),
            "copy_pointer_assignment": attempt(lambda: setattr(scene, "node_tree", scene_copy)),
            "copy_type": scene_copy.bl_idname,
        }

    for tree_type, candidates in {
        "ShaderNodeTree": [
            "ShaderNodeOutputMaterial", "ShaderNodeOutputWorld", "ShaderNodeOutputLight",
            "ShaderNodeBsdfPrincipled", "ShaderNodeValToRGB", "ShaderNodeRGBCurve",
            "ShaderNodeGroup", "NodeFrame", "NodeReroute",
        ],
        "CompositorNodeTree": [
            "CompositorNodeComposite", "CompositorNodeViewer", "CompositorNodeRLayers",
            "CompositorNodeValToRGB", "CompositorNodeCurveRGB", "CompositorNodeOutputFile",
            "CompositorNodeGroup", "NodeGroupOutput", "NodeFrame", "NodeReroute",
        ],
    }.items():
        probe_tree = bpy.data.node_groups.new(PREFIX + "Probe" + tree_type, tree_type)
        records = {}
        try:
            for node_type in candidates:
                def create(node_type=node_type):
                    node = probe_tree.nodes.new(node_type)
                    record = {
                        "actual": node.bl_idname,
                        "inputs": [socket.name for socket in node.inputs],
                        "outputs": [socket.name for socket in node.outputs],
                    }
                    probe_tree.nodes.remove(node)
                    return record
                records[node_type] = attempt(create)
        finally:
            bpy.data.node_groups.remove(probe_tree)
        result["node_types"][tree_type] = records

    result["active_scene_unchanged"] = bpy.context.scene == before_scene
    assert result["owners"]["material"]["copy_tree_distinct"]
    assert result["owners"]["world"]["copy_tree_distinct"]
    assert result["owners"]["light"]["copy_tree_distinct"]
    for owner_kind in ("material", "world", "light"):
        assert not result["owners"][owner_kind]["same_pointer_assignment"]["ok"]
    if result["owners"]["scene"]["adapter"] == "node_tree":
        assert not result["owners"]["scene"]["copy_pointer_assignment"]["ok"]
        assert result["node_types"]["CompositorNodeTree"]["CompositorNodeComposite"]["ok"]
    else:
        assert result["owners"]["scene"]["copy_pointer_assignment"]["ok"]
        assert not result["node_types"]["CompositorNodeTree"]["CompositorNodeComposite"]["ok"]
    assert result["active_scene_unchanged"]
    cleanup()
    result["leaks"] = {
        "objects": id_names(bpy.data.objects),
        "meshes": id_names(bpy.data.meshes),
        "lights": id_names(bpy.data.lights),
        "materials": id_names(bpy.data.materials),
        "worlds": id_names(bpy.data.worlds),
        "scenes": id_names(bpy.data.scenes),
        "node_groups": id_names(bpy.data.node_groups),
    }
    assert not any(result["leaks"].values())
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    main()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
