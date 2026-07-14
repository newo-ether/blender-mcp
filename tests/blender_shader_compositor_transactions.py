"""Cross-version transaction spike for Shader and Compositor node owners."""

from __future__ import annotations

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy


PREFIX = "__BLENDER_MCP_SC_TX__"
RESULT_PREFIX = "BLENDER_MCP_SC_TX_RESULT="


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def node_signature(tree):
    return {
        "nodes": sorted((node.name, node.bl_idname) for node in tree.nodes),
        "links": sorted(
            (
                link.from_node.name,
                link.from_socket.name,
                link.to_node.name,
                link.to_socket.name,
            )
            for link in tree.links
        ),
    }


def remove_id(collection, value):
    if value is not None and value.name in collection:
        collection.remove(value, do_unlink=True)


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


def prove_owner_rollback(owner, collection, getters, node_type="ShaderNodeValue"):
    original_name = owner.name
    original_tree = owner.node_tree
    before = node_signature(original_tree)
    start = time.perf_counter()
    working = owner.copy()
    copy_ms = (time.perf_counter() - start) * 1000.0
    working.name = PREFIX + "Working" + original_name
    working.node_tree.nodes.new(node_type)
    assert_true(node_signature(original_tree) == before, "owner copy changed original tree")
    assert_true(all(getter() == owner for getter in getters), "unexpected original user")
    start = time.perf_counter()
    owner.user_remap(working)
    remap_ms = (time.perf_counter() - start) * 1000.0
    committed = all(getter() == working for getter in getters)
    assert_true(committed, "owner user_remap did not switch every explicit user")
    working.user_remap(owner)
    rolled_back = all(getter() == owner for getter in getters)
    assert_true(rolled_back, "owner rollback did not restore every explicit user")
    assert_true(node_signature(original_tree) == before, "rollback changed original tree")
    remove_id(collection, working)
    assert_true(owner.name == original_name, "rollback changed owner name")
    return {
        "copy_ms": round(copy_ms, 4),
        "remap_ms": round(remap_ms, 4),
        "committed": committed,
        "rolled_back": rolled_back,
        "original_users": owner.users,
    }


def prove_group_rollback(tree, getters):
    before = node_signature(tree)
    start = time.perf_counter()
    working = tree.copy()
    copy_ms = (time.perf_counter() - start) * 1000.0
    working.name = PREFIX + "Working" + tree.name
    if tree.bl_idname == "ShaderNodeTree":
        working.nodes.new("ShaderNodeValue")
    else:
        working.nodes.new("NodeFrame")
    assert_true(node_signature(tree) == before, "group copy changed original")
    tree.user_remap(working)
    committed = all(getter() == working for getter in getters)
    assert_true(committed, "group user_remap did not switch explicit users")
    working.user_remap(tree)
    rolled_back = all(getter() == tree for getter in getters)
    assert_true(rolled_back, "group rollback did not restore explicit users")
    assert_true(node_signature(tree) == before, "group rollback changed original")
    remove_id(bpy.data.node_groups, working)
    return {
        "copy_ms": round(copy_ms, 4),
        "committed": committed,
        "rolled_back": rolled_back,
        "original_users": tree.users,
    }


def compositor_tree_for(scene):
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(PREFIX + "CompositorTree", "CompositorNodeTree")
        tree.interface.new_socket(
            name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
        )
        tree.nodes.new("NodeGroupOutput")
        scene.compositing_node_group = tree
        return tree, "compositing_node_group"
    scene.use_nodes = True
    return scene.node_tree, "node_tree"


def prove_scene_rollback(scene):
    tree, adapter = compositor_tree_for(scene)
    before = node_signature(tree)
    if adapter == "compositing_node_group":
        working = tree.copy()
        working.name = PREFIX + "WorkingCompositor"
        working.nodes.new("NodeFrame")
        scene.compositing_node_group = working
        committed = scene.compositing_node_group == working
        scene.compositing_node_group = tree
        rolled_back = scene.compositing_node_group == tree
        assert_true(node_signature(tree) == before, "scene tree rollback changed original")
        remove_id(bpy.data.node_groups, working)
        return {
            "adapter": adapter,
            "committed": committed,
            "rolled_back": rolled_back,
            "original_users": tree.users,
        }

    consumer = bpy.data.scenes.new(PREFIX + "LegacyConsumer")
    consumer.use_nodes = True
    render_layers = next(
        node for node in consumer.node_tree.nodes
        if node.bl_idname == "CompositorNodeRLayers"
    )
    render_layers.scene = scene
    working = scene.copy()
    working.name = PREFIX + "WorkingScene"
    working.node_tree.nodes.new("NodeFrame")
    scene.user_remap(working)
    committed = render_layers.scene == working
    assert_true(committed, "legacy Scene user_remap did not switch consumer")
    working.user_remap(scene)
    rolled_back = render_layers.scene == scene
    assert_true(rolled_back, "legacy Scene rollback did not restore consumer")
    assert_true(node_signature(tree) == before, "legacy Scene rollback changed original")
    remove_id(bpy.data.scenes, working)
    remove_id(bpy.data.scenes, consumer)
    return {
        "adapter": adapter,
        "committed": committed,
        "rolled_back": rolled_back,
        "original_users": scene.users,
    }


def main():
    cleanup()
    active_scene = bpy.context.scene
    result = {
        "version": list(bpy.app.version[:3]),
        "owners": {},
        "groups": {},
    }

    mesh = bpy.data.meshes.new(PREFIX + "Mesh")
    obj = bpy.data.objects.new(PREFIX + "MaterialObject", mesh)
    material = bpy.data.materials.new(PREFIX + "Material")
    material.use_nodes = True
    mesh.materials.append(material)
    result["owners"]["material"] = prove_owner_rollback(
        material,
        bpy.data.materials,
        [lambda: mesh.materials[0]],
    )

    world_scene = bpy.data.scenes.new(PREFIX + "WorldScene")
    world = bpy.data.worlds.new(PREFIX + "World")
    world.use_nodes = True
    world_scene.world = world
    result["owners"]["world"] = prove_owner_rollback(
        world,
        bpy.data.worlds,
        [lambda: world_scene.world],
    )

    light = bpy.data.lights.new(PREFIX + "Light", "POINT")
    light.use_nodes = True
    light_obj_a = bpy.data.objects.new(PREFIX + "LightObjectA", light)
    light_obj_b = bpy.data.objects.new(PREFIX + "LightObjectB", light)
    result["owners"]["light"] = prove_owner_rollback(
        light,
        bpy.data.lights,
        [lambda: light_obj_a.data, lambda: light_obj_b.data],
    )

    shader_group = bpy.data.node_groups.new(PREFIX + "ShaderGroup", "ShaderNodeTree")
    shader_group.interface.new_socket(
        name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat"
    )
    shader_group.nodes.new("NodeGroupOutput")
    shader_material = bpy.data.materials.new(PREFIX + "GroupMaterial")
    shader_material.use_nodes = True
    shader_user_a = shader_material.node_tree.nodes.new("ShaderNodeGroup")
    shader_user_a.node_tree = shader_group
    shader_world = bpy.data.worlds.new(PREFIX + "GroupWorld")
    shader_world.use_nodes = True
    shader_user_b = shader_world.node_tree.nodes.new("ShaderNodeGroup")
    shader_user_b.node_tree = shader_group
    result["groups"]["shader"] = prove_group_rollback(
        shader_group,
        [lambda: shader_user_a.node_tree, lambda: shader_user_b.node_tree],
    )

    compositor_group = bpy.data.node_groups.new(
        PREFIX + "CompositorGroup", "CompositorNodeTree"
    )
    compositor_group.interface.new_socket(
        name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    compositor_group.nodes.new("NodeGroupOutput")
    compositor_scene = bpy.data.scenes.new(PREFIX + "CompositorUserScene")
    compositor_owner_tree, _adapter = compositor_tree_for(compositor_scene)
    compositor_user = compositor_owner_tree.nodes.new("CompositorNodeGroup")
    compositor_user.node_tree = compositor_group
    result["groups"]["compositor"] = prove_group_rollback(
        compositor_group,
        [lambda: compositor_user.node_tree],
    )

    target_scene = bpy.data.scenes.new(PREFIX + "TargetScene")
    with tempfile.TemporaryDirectory(prefix="blender-mcp-n0-no-output-") as temp_dir:
        output_dir = Path(temp_dir)
        before_files = list(output_dir.iterdir())
        result["owners"]["scene"] = prove_scene_rollback(target_scene)
        after_files = list(output_dir.iterdir())
        result["external_files_created"] = [item.name for item in after_files if item not in before_files]

    result["active_scene_unchanged"] = bpy.context.scene == active_scene
    cleanup()
    result["leaks"] = {
        "objects": [item.name for item in bpy.data.objects if item.name.startswith(PREFIX)],
        "meshes": [item.name for item in bpy.data.meshes if item.name.startswith(PREFIX)],
        "lights": [item.name for item in bpy.data.lights if item.name.startswith(PREFIX)],
        "materials": [item.name for item in bpy.data.materials if item.name.startswith(PREFIX)],
        "worlds": [item.name for item in bpy.data.worlds if item.name.startswith(PREFIX)],
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
    }
    assert_true(not any(result["leaks"].values()), f"temporary datablock leak: {result['leaks']}")
    assert_true(result["active_scene_unchanged"], "active scene changed")
    assert_true(not result["external_files_created"], "transaction created external files")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    main()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
