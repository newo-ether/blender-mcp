"""Live read-only acceptance for generic owner-addressed node-tree tools."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import traceback

import bpy


PREFIX = "__BLENDER_MCP_NODE_READONLY__"
RESULT_PREFIX = "BLENDER_MCP_NODE_READONLY_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


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
    for image in list(bpy.data.images):
        if image.name.startswith(PREFIX):
            bpy.data.images.remove(image, do_unlink=True)


def tree_ref(tree_type, owner_kind, owner_name):
    return {
        "tree_type": tree_type,
        "owner": {"kind": owner_kind, "name": owner_name},
    }


def create_compositor_tree(scene):
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(PREFIX + "SceneTree", "CompositorNodeTree")
        tree.interface.new_socket(
            name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
        )
        tree.nodes.new("NodeGroupOutput")
        scene.compositing_node_group = tree
        return tree, "scene_compositing_node_group"
    scene.use_nodes = True
    return scene.node_tree, "scene_embedded_node_tree"


def run_test():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(
        str(REPO_ROOT / "addon.py"),
        run_name="blender_mcp_node_readonly_test",
    )
    server = namespace["BlenderMCPServer"]()

    material = bpy.data.materials.new(PREFIX + "Material")
    material.use_nodes = True
    frame = material.node_tree.nodes.new("NodeFrame")
    frame.name = "Stage Notes"
    frame.label = "Stage 1: base shader"
    frame["blender_mcp_note"] = "Detailed human-readable stage description."
    image = bpy.data.images.new(PREFIX + "Image", width=4, height=4)
    image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    image_node.name = "Reference Image"
    image_node.image = image
    mesh = bpy.data.meshes.new(PREFIX + "Mesh")
    mesh.materials.append(material)
    obj = bpy.data.objects.new(PREFIX + "Object", mesh)

    world = bpy.data.worlds.new(PREFIX + "World")
    world.use_nodes = True
    world_scene = bpy.data.scenes.new(PREFIX + "WorldScene")
    world_scene.world = world

    light = bpy.data.lights.new(PREFIX + "Light", "POINT")
    light.use_nodes = True
    light_obj = bpy.data.objects.new(PREFIX + "LightObject", light)

    shader_group = bpy.data.node_groups.new(PREFIX + "ShaderGroup", "ShaderNodeTree")
    shader_group.interface.new_socket(
        name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat"
    )
    shader_group.nodes.new("ShaderNodeValue")
    shader_group.nodes.new("NodeGroupOutput")
    shader_group_node = material.node_tree.nodes.new("ShaderNodeGroup")
    shader_group_node.node_tree = shader_group

    compositor_scene = bpy.data.scenes.new(PREFIX + "CompositorScene")
    compositor_scene_tree, expected_scene_adapter = create_compositor_tree(
        compositor_scene
    )
    compositor_scene_tree.nodes.new("CompositorNodeCurveRGB")

    compositor_group = bpy.data.node_groups.new(
        PREFIX + "CompositorGroup", "CompositorNodeTree"
    )
    compositor_group.interface.new_socket(
        name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    compositor_group.nodes.new("NodeGroupOutput")
    compositor_group_node = compositor_scene_tree.nodes.new("CompositorNodeGroup")
    compositor_group_node.node_tree = compositor_group

    geometry_group = bpy.data.node_groups.new(
        PREFIX + "GeometryGroup", "GeometryNodeTree"
    )
    geometry_group.nodes.new("GeometryNodeMeshCube")

    listing = server.list_node_trees()
    listed_refs = {
        json.dumps(item["tree_ref"], sort_keys=True): item
        for item in listing["trees"]
    }
    expected_refs = [
        tree_ref("ShaderNodeTree", "MATERIAL", material.name),
        tree_ref("ShaderNodeTree", "WORLD", world.name),
        tree_ref("ShaderNodeTree", "LIGHT", light.name),
        tree_ref("ShaderNodeTree", "NODE_GROUP", shader_group.name),
        tree_ref("CompositorNodeTree", "SCENE", compositor_scene.name),
        tree_ref("CompositorNodeTree", "NODE_GROUP", compositor_group.name),
        tree_ref("GeometryNodeTree", "NODE_GROUP", geometry_group.name),
    ]
    for expected in expected_refs:
        key = json.dumps(expected, sort_keys=True)
        assert_true(key in listed_refs, f"tree missing from generic list: {expected}")
        expected_apply = expected["tree_type"] in {
            "ShaderNodeTree", "CompositorNodeTree",
        }
        assert_true(
            listed_refs[key]["capabilities"]["apply"] == expected_apply,
            f"unexpected apply capability for {expected}",
        )

    shader_only = server.list_node_trees(
        tree_types=["ShaderNodeTree"], owner_kinds=["MATERIAL"]
    )
    assert_true(
        any(item["tree_ref"]["owner"]["name"] == material.name for item in shader_only["trees"]),
        "filtered material tree missing",
    )
    assert_true(
        all(item["tree_ref"]["owner"]["kind"] == "MATERIAL" for item in shader_only["trees"]),
        "owner-kind filter leaked another owner",
    )

    material_ref = tree_ref("ShaderNodeTree", "MATERIAL", material.name)
    first = server.export_node_tree(material_ref, "semantic")
    second = server.export_node_tree(material_ref, "semantic")
    assert_true(first == second, "unchanged material export is not deterministic")
    assert_true(first["schema"] == "blender-node-tree/1", "wrong generic schema")
    assert_true(first["tree_ref"] == material_ref, "tree_ref drifted")
    assert_true(
        first["tree"]["nodes"][frame.name]["annotation"]["text"].startswith("Detailed"),
        "Frame annotation missing",
    )
    image_property = first["tree"]["nodes"][image_node.name]["properties"]["image"]
    assert_true(
        image_property["$type"] == "ID" and image_property["name"] == image.name,
        "typed Image reference missing",
    )
    assert_true(
        any(user["id_type"] == "Mesh" for user in first["users"] if user["kind"] == "ID"),
        "Material user map missing Mesh",
    )

    index = server.get_node_tree_index(material_ref, query="Reference", limit=10)
    assert_true(index["revision"] == first["revision"], "index revision mismatch")
    assert_true(
        [node["name"] for node in index["nodes"]] == [image_node.name],
        "index query returned unexpected nodes",
    )
    targeted = server.export_node_tree(
        material_ref, "semantic", [image_node.name], 0
    )
    assert_true(
        list(targeted["tree"]["nodes"]) == [image_node.name],
        "targeted export included unrelated nodes",
    )
    assert_true(
        targeted["stats"]["json_bytes"] < first["stats"]["json_bytes"],
        "targeted export was not smaller",
    )

    scene_ref = tree_ref("CompositorNodeTree", "SCENE", compositor_scene.name)
    scene_export = server.export_node_tree(scene_ref, "all")
    assert_true(
        scene_export["capabilities"]["transaction_adapter"] == expected_scene_adapter,
        "wrong Scene compositor adapter",
    )
    assert_true(
        scene_export["owner"]["kind"] == "SCENE",
        "Scene owner metadata missing",
    )

    ramp_schema = server.get_node_type_schema(
        "ShaderNodeTree", "ShaderNodeValToRGB", "MATERIAL", "compact"
    )
    assert_true(ramp_schema["schema"] == "blender-node-type-schema/1", "schema id wrong")
    assert_true(
        any(item["type"] == "COLOR_RAMP" for item in ramp_schema["special_structures"]),
        "Color Ramp structure missing",
    )
    render_layers_schema = server.get_node_type_schema(
        "CompositorNodeTree", "CompositorNodeRLayers", "SCENE", "compact"
    )
    assert_true(
        render_layers_schema["owner_kind"] == "SCENE",
        "Render Layers owner context missing",
    )
    if bpy.app.version[:2] == (4, 2):
        composite_schema = server.get_node_type_schema(
            "CompositorNodeTree", "CompositorNodeComposite", "SCENE", "compact"
        )
        assert_true(composite_schema["node_type"] == "CompositorNodeComposite", "4.2 output missing")
    else:
        try:
            server.get_node_type_schema(
                "CompositorNodeTree", "CompositorNodeComposite", "SCENE", "compact"
            )
        except ValueError as exc:
            assert_true("Unsupported" in str(exc), "wrong removed-node diagnostic")
        else:
            raise AssertionError("removed Composite node unexpectedly resolved")

    try:
        server.export_node_tree(
            tree_ref("ShaderNodeTree", "SCENE", compositor_scene.name)
        )
    except ValueError as exc:
        assert_true("cannot own" in str(exc), "wrong domain mismatch diagnostic")
    else:
        raise AssertionError("invalid owner/domain reference was accepted")

    geometry_v1 = server.export_geometry_node_tree(geometry_group.name, "semantic")
    assert_true(
        geometry_v1["schema"] == "blender-geometry-nodes/1",
        "Geometry v1 schema changed",
    )
    assert_true("tree_ref" not in geometry_v1, "Geometry v1 envelope changed")

    result = {
        "version": list(bpy.app.version[:3]),
        "listed_expected_trees": len(expected_refs),
        "scene_adapter": expected_scene_adapter,
        "material_revision": first["revision"],
        "material_bytes": first["stats"]["json_bytes"],
        "targeted_bytes": targeted["stats"]["json_bytes"],
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    result["leaks"] = {
        "objects": [item.name for item in bpy.data.objects if item.name.startswith(PREFIX)],
        "meshes": [item.name for item in bpy.data.meshes if item.name.startswith(PREFIX)],
        "lights": [item.name for item in bpy.data.lights if item.name.startswith(PREFIX)],
        "materials": [item.name for item in bpy.data.materials if item.name.startswith(PREFIX)],
        "worlds": [item.name for item in bpy.data.worlds if item.name.startswith(PREFIX)],
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
        "images": [item.name for item in bpy.data.images if item.name.startswith(PREFIX)],
    }
    assert_true(not any(result["leaks"].values()), f"datablock leak: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
