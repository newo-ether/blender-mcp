"""Live non-mutating validation acceptance for generic node-tree patches."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import tempfile
import traceback

import bpy


PREFIX = "__BLENDER_MCP_NODE_VALIDATE__"
RESULT_PREFIX = "BLENDER_MCP_NODE_VALIDATE_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_SCHEMA = "blender-node-tree-patch/1"


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
    for material in list(bpy.data.materials):
        if material.name.startswith(PREFIX):
            bpy.data.materials.remove(material, do_unlink=True)
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)
    for image in list(bpy.data.images):
        if image.name.startswith(PREFIX):
            bpy.data.images.remove(image, do_unlink=True)


def all_test_ids():
    return {
        collection_name: {
            value.as_pointer()
            for value in getattr(bpy.data, collection_name)
        }
        for collection_name in (
            "materials", "worlds", "lights", "scenes", "node_groups", "images"
        )
    }


def tree_ref(tree_type, owner_kind, owner_name):
    return {
        "tree_type": tree_type,
        "owner": {"kind": owner_kind, "name": owner_name},
    }


def socket_id(node, socket, direction):
    sockets = node.outputs if direction == "output" else node.inputs
    index = next(index for index, candidate in enumerate(sockets) if candidate == socket)
    identifier = getattr(socket, "identifier", "") or socket.name
    return f"{direction}:{index}:{identifier}"


def patch(reference, revision, capabilities, operations):
    return {
        "schema": PATCH_SCHEMA,
        "tree_ref": reference,
        "base_revision": revision,
        "capabilities": capabilities,
        "operations": operations,
    }


def create_compositor_tree(scene):
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(PREFIX + "SceneTree", "CompositorNodeTree")
        tree.interface.new_socket(
            name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
        )
        tree.nodes.new("NodeGroupOutput")
        scene.compositing_node_group = tree
        return tree
    scene.use_nodes = True
    return scene.node_tree


def diagnostic_codes(result):
    return {item["code"] for item in result["diagnostics"]}


def run_test():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(
        str(REPO_ROOT / "addon.py"),
        run_name="blender_mcp_node_validation_test",
    )
    server = namespace["BlenderMCPServer"]()

    material = bpy.data.materials.new(PREFIX + "Material")
    material.use_nodes = True
    material_ref = tree_ref("ShaderNodeTree", "MATERIAL", material.name)
    principled = next(
        node for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    image_node.name = "Plate Image"
    image = bpy.data.images.new(PREFIX + "Image", width=8, height=8)
    roughness = principled.inputs["Roughness"]
    base_color = principled.inputs["Base Color"]
    color_output = image_node.outputs["Color"]
    material_before = server.export_node_tree(material_ref, "all")
    material_patch = patch(
        material_ref,
        material_before["revision"],
        ["graph", "layout", "annotation", "dynamic", "id_reference"],
        [
            {
                "op": "add_node",
                "id": "tutorial_frame",
                "node_type": "NodeFrame",
                "name": "Tutorial Stage",
                "layout": {"location": [-600.0, 300.0], "width": 420.0},
            },
            {
                "op": "set_annotation",
                "node": "tutorial_frame",
                "text": "Stage 1: image-driven base color and a shaped lookup ramp.",
            },
            {
                "op": "add_node",
                "id": "look_ramp",
                "node_type": "ShaderNodeValToRGB",
                "name": "Look Ramp",
                "layout": {"location": [-350.0, 100.0], "parent": "tutorial_frame"},
            },
            {
                "op": "set_color_ramp",
                "node": "look_ramp",
                "interpolation": "EASE",
                "elements": [
                    {"position": 0.0, "color": [0.01, 0.02, 0.04, 1.0]},
                    {"position": 0.4, "color": [0.2, 0.5, 0.8, 1.0]},
                    {"position": 1.0, "color": [1.0, 0.8, 0.3, 1.0]},
                ],
            },
            {
                "op": "set_node_property",
                "node": image_node.name,
                "property": "image",
                "value": {"$type": "ID", "id_type": "Image", "name": image.name},
            },
            {
                "op": "set_socket_default",
                "node": principled.name,
                "socket": socket_id(principled, roughness, "input"),
                "value": 0.28,
            },
            {
                "op": "add_link",
                "from_node": image_node.name,
                "from_socket": socket_id(image_node, color_output, "output"),
                "to_node": principled.name,
                "to_socket": socket_id(principled, base_color, "input"),
            },
        ],
    )
    ids_before_validation = all_test_ids()
    material_validation = server.validate_node_tree_patch(material_patch)
    assert_true(material_validation["valid"], material_validation["diagnostics"])
    assert_true(not material_validation["will_mutate"], "validation claims mutation")
    assert_true(
        material_validation["candidate_revision"] != material_before["revision"],
        "candidate revision did not change",
    )
    assert_true(
        server.export_node_tree(material_ref, "all") == material_before,
        "Material validation changed the live graph",
    )
    assert_true(all_test_ids() == ids_before_validation, "Material validation leaked IDs")

    stale = dict(material_patch)
    stale["base_revision"] = "sha256:" + "0" * 64
    stale_validation = server.validate_node_tree_patch(stale)
    assert_true(not stale_validation["valid"], "stale patch validated")
    assert_true("stale_revision" in diagnostic_codes(stale_validation), "stale error missing")

    script_patch = patch(
        material_ref,
        material_before["revision"],
        ["graph"],
        [{"op": "add_node", "id": "script", "node_type": "ShaderNodeScript"}],
    )
    script_validation = server.validate_node_tree_patch(script_patch)
    assert_true(not script_validation["valid"], "Script node mutation validated")
    assert_true(
        "effect_sensitive_node_read_only" in diagnostic_codes(script_validation),
        "Script safety diagnostic missing",
    )

    missing_image_patch = patch(
        material_ref,
        material_before["revision"],
        ["graph", "id_reference"],
        [{
            "op": "set_node_property",
            "node": image_node.name,
            "property": "image",
            "value": {"$type": "ID", "id_type": "Image", "name": PREFIX + "Missing"},
        }],
    )
    missing_image = server.validate_node_tree_patch(missing_image_patch)
    assert_true(not missing_image["valid"], "missing typed ID validated")
    assert_true("invalid_pointer_value" in diagnostic_codes(missing_image), "ID diagnostic missing")

    material_interface_patch = patch(
        material_ref,
        material_before["revision"],
        ["interface"],
        [{
            "op": "add_interface_socket",
            "id": "unsupported",
            "name": "Unsupported",
            "in_out": "INPUT",
            "socket_type": "NodeSocketFloat",
        }],
    )
    material_interface = server.validate_node_tree_patch(material_interface_patch)
    assert_true(not material_interface["valid"], "embedded Shader interface validated")

    shader_group = bpy.data.node_groups.new(PREFIX + "ShaderGroup", "ShaderNodeTree")
    shader_group.nodes.new("NodeGroupOutput")
    group_ref = tree_ref("ShaderNodeTree", "NODE_GROUP", shader_group.name)
    group_before = server.export_node_tree(group_ref, "all")
    group_patch = patch(
        group_ref,
        group_before["revision"],
        ["interface"],
        [{
            "op": "add_interface_socket",
            "id": "strength",
            "name": "Strength",
            "in_out": "INPUT",
            "socket_type": "NodeSocketFloat",
            "default": 0.75,
        }],
    )
    group_validation = server.validate_node_tree_patch(group_patch)
    assert_true(group_validation["valid"], group_validation["diagnostics"])
    assert_true(
        server.export_node_tree(group_ref, "all") == group_before,
        "group interface validation changed live data",
    )

    scene = bpy.data.scenes.new(PREFIX + "Scene")
    compositor = create_compositor_tree(scene)
    curve = compositor.nodes.new("CompositorNodeCurveRGB")
    curve.name = "Grade Curve"
    scene_ref = tree_ref("CompositorNodeTree", "SCENE", scene.name)
    scene_before = server.export_node_tree(scene_ref, "all")
    curve_patch = patch(
        scene_ref,
        scene_before["revision"],
        ["graph", "annotation", "dynamic"],
        [
            {"op": "add_node", "id": "notes", "node_type": "NodeFrame", "name": "Grade"},
            {"op": "set_annotation", "node": "notes", "text": "Stage 2: contrast shaping."},
            {
                "op": "set_curve_mapping",
                "node": curve.name,
                "use_clip": True,
                "curves": [
                    {"points": [
                        {"location": [0.0, 0.0], "handle_type": "AUTO"},
                        {"location": [1.0, 1.0], "handle_type": "AUTO"},
                    ]}
                    for _index in range(4)
                ],
            },
        ],
    )
    scene_validation = server.validate_node_tree_patch(curve_patch)
    assert_true(scene_validation["valid"], scene_validation["diagnostics"])
    assert_true(
        server.export_node_tree(scene_ref, "all") == scene_before,
        "Compositor validation changed live graph",
    )

    output_patch = patch(
        scene_ref,
        scene_before["revision"],
        ["graph"],
        [{"op": "add_node", "id": "output", "node_type": "CompositorNodeOutputFile"}],
    )
    cross_domain_patch = patch(
        scene_ref,
        scene_before["revision"],
        ["graph"],
        [{"op": "add_node", "id": "cube", "node_type": "GeometryNodeMeshCube"}],
    )
    with tempfile.TemporaryDirectory(prefix="blender-mcp-validate-no-output-") as temp_dir:
        before_files = list(Path(temp_dir).iterdir())
        output_validation = server.validate_node_tree_patch(output_patch)
        cross_domain_validation = server.validate_node_tree_patch(cross_domain_patch)
        after_files = list(Path(temp_dir).iterdir())
    assert_true(not output_validation["valid"], "File Output mutation validated")
    assert_true(
        "effect_sensitive_node_read_only" in diagnostic_codes(output_validation),
        "File Output safety diagnostic missing",
    )
    assert_true(not cross_domain_validation["valid"], "cross-domain node validated")
    assert_true("operation_rejected" in diagnostic_codes(cross_domain_validation), "domain error missing")
    assert_true(before_files == after_files, "validation created an external file")

    result = {
        "version": list(bpy.app.version[:3]),
        "material_operations": len(material_validation["plan"]),
        "material_candidate_revision": material_validation["candidate_revision"],
        "scene_candidate_revision": scene_validation["candidate_revision"],
        "group_interface_valid": group_validation["valid"],
        "stale_rejected": not stale_validation["valid"],
        "script_rejected": not script_validation["valid"],
        "file_output_rejected": not output_validation["valid"],
        "cross_domain_rejected": not cross_domain_validation["valid"],
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    result["leaks"] = {
        "materials": [item.name for item in bpy.data.materials if item.name.startswith(PREFIX)],
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
        "images": [item.name for item in bpy.data.images if item.name.startswith(PREFIX)],
    }
    assert_true(not any(result["leaks"].values()), f"fixture leak: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
