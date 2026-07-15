"""Live transactional mutation acceptance for Shader node owners."""

from __future__ import annotations

import json
import runpy
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_SHADER_TX__"
RESULT_PREFIX = "BLENDER_MCP_SHADER_TX_RESULT="
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


def tree_ref(owner_kind, owner_name):
    return {
        "tree_type": "ShaderNodeTree",
        "owner": {"kind": owner_kind, "name": owner_name},
    }


def socket_id(node, socket, direction="input"):
    sockets = node.inputs if direction == "input" else node.outputs
    index = next(index for index, candidate in enumerate(sockets) if candidate == socket)
    identifier = getattr(socket, "identifier", "") or socket.name
    return f"{direction}:{index}:{identifier}"


def make_patch(reference, revision, operations, capabilities):
    return {
        "schema": "blender-node-tree-patch/1",
        "tree_ref": reference,
        "base_revision": revision,
        "capabilities": capabilities,
        "operations": operations,
    }


def material_settings(material):
    return {
        "diffuse_color": [float(value) for value in material.diffuse_color],
        "custom": material.get("blender_mcp_test"),
        "use_nodes": bool(material.use_nodes),
    }


def run_test():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_shader_transactions_test",
    )
    server = namespace["BlenderMCPServer"]()

    material_name = PREFIX + "Material"
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    material.diffuse_color = (0.12, 0.24, 0.48, 1.0)
    material["blender_mcp_test"] = "preserve"
    material_ref = tree_ref("MATERIAL", material_name)
    material_users = []
    for suffix in ("A", "B"):
        mesh = bpy.data.meshes.new(PREFIX + "Mesh" + suffix)
        mesh.materials.append(material)
        obj = bpy.data.objects.new(PREFIX + "Object" + suffix, mesh)
        material_users.append((mesh, obj))
    unrelated = bpy.data.materials.new(PREFIX + "UnrelatedMaterial")
    unrelated_mesh = bpy.data.meshes.new(PREFIX + "UnrelatedMesh")
    unrelated_mesh.materials.append(unrelated)
    unrelated_object = bpy.data.objects.new(PREFIX + "UnrelatedObject", unrelated_mesh)

    principled = next(
        node for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    roughness = principled.inputs["Roughness"]
    roughness.default_value = 0.5
    roughness.keyframe_insert(data_path="default_value", frame=1)
    material_action = material.node_tree.animation_data.action
    assert_true(material_action is not None, "Material animation fixture was not created")
    material_before = server.export_node_tree(material_ref, "all")
    settings_before = material_settings(material)
    material_patch = make_patch(
        material_ref,
        material_before["revision"],
        [
            {
                "op": "set_socket_default",
                "node": principled.name,
                "socket": socket_id(principled, roughness),
                "value": 0.19,
            },
            {
                "op": "add_node",
                "id": "notes",
                "node_type": "NodeFrame",
                "name": "Material Tutorial",
            },
            {
                "op": "set_annotation",
                "node": "notes",
                "text": "Material transaction committed through an owner copy.",
            },
        ],
        ["graph", "annotation"],
    )
    material_result = server.apply_node_tree_patch(material_patch, keep_backup=True)
    assert_true(material_result["status"] == "applied", material_result)
    committed_material = bpy.data.materials[material_name]
    assert_true(committed_material != material, "Material owner was mutated in place")
    assert_true(
        all(mesh.materials[0] == committed_material for mesh, _obj in material_users),
        "not every Material user switched to the committed owner",
    )
    assert_true(unrelated_mesh.materials[0] == unrelated, "unrelated Material changed")
    assert_true(material_settings(committed_material) == settings_before, "Material settings drifted")
    assert_true(
        committed_material.node_tree.animation_data is not None
        and committed_material.node_tree.animation_data.action is not None,
        "Material node animation was not preserved",
    )
    backup_material = bpy.data.materials[material_result["backup"]["name"]]
    backup_ref = tree_ref("MATERIAL", backup_material.name)
    assert_true(backup_material == material, "backup is not the original Material")
    assert_true(backup_material.use_fake_user, "Material backup lacks fake user")
    assert_true(
        server.export_node_tree(backup_ref, "all")["revision"] == material_before["revision"],
        "Material backup graph changed",
    )
    committed_material_export = server.export_node_tree(material_ref, "all")
    assert_true(
        committed_material_export["revision"] == material_result["new_revision"],
        "Material application revision mismatch",
    )
    stale_material = server.apply_node_tree_patch(material_patch, keep_backup=True)
    assert_true(stale_material["status"] == "rejected", "stale Material patch applied")

    world_name = PREFIX + "World"
    world = bpy.data.worlds.new(world_name)
    world.use_nodes = True
    world["blender_mcp_test"] = "world"
    world_scene = bpy.data.scenes.new(PREFIX + "WorldScene")
    world_scene.world = world
    world_ref = tree_ref("WORLD", world_name)
    world_before = server.export_node_tree(world_ref, "all")
    background = next(
        node for node in world.node_tree.nodes
        if node.bl_idname == "ShaderNodeBackground"
    )
    strength = background.inputs["Strength"]
    world_patch = make_patch(
        world_ref,
        world_before["revision"],
        [{
            "op": "set_socket_default",
            "node": background.name,
            "socket": socket_id(background, strength),
            "value": 0.42,
        }],
        ["graph"],
    )
    world_result = server.apply_node_tree_patch(world_patch, keep_backup=False)
    assert_true(world_result["status"] == "applied", world_result)
    committed_world = bpy.data.worlds[world_name]
    assert_true(world_scene.world == committed_world and committed_world != world, "World user not switched")
    assert_true(not world_result["backup"]["kept"], "World backup unexpectedly kept")
    assert_true(committed_world.get("blender_mcp_test") == "world", "World settings drifted")

    light_name = PREFIX + "Light"
    light = bpy.data.lights.new(light_name, "POINT")
    light.use_nodes = True
    light.energy = 321.0
    light_objects = [
        bpy.data.objects.new(PREFIX + "LightObjectA", light),
        bpy.data.objects.new(PREFIX + "LightObjectB", light),
    ]
    unrelated_light = bpy.data.lights.new(PREFIX + "UnrelatedLight", "POINT")
    unrelated_light_object = bpy.data.objects.new(PREFIX + "UnrelatedLightObject", unrelated_light)
    light_ref = tree_ref("LIGHT", light_name)
    light_before = server.export_node_tree(light_ref, "all")
    emission = next(
        node for node in light.node_tree.nodes
        if node.bl_idname == "ShaderNodeEmission"
    )
    color = emission.inputs["Color"]
    light_patch = make_patch(
        light_ref,
        light_before["revision"],
        [{
            "op": "set_socket_default",
            "node": emission.name,
            "socket": socket_id(emission, color),
            "value": [0.8, 0.2, 0.1, 1.0],
        }],
        ["graph"],
    )
    light_result = server.apply_node_tree_patch(light_patch, keep_backup=True)
    assert_true(light_result["status"] == "applied", light_result)
    committed_light = bpy.data.lights[light_name]
    assert_true(
        all(obj.data == committed_light for obj in light_objects),
        "shared Light users did not switch together",
    )
    assert_true(unrelated_light_object.data == unrelated_light, "unrelated Light changed")
    assert_true(abs(committed_light.energy - 321.0) < 1e-6, "Light energy drifted")

    group_name = PREFIX + "ShaderGroup"
    group = bpy.data.node_groups.new(group_name, "ShaderNodeTree")
    group["blender_mcp_test"] = "group"
    group.interface.new_socket(
        name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat"
    )
    group.nodes.new("NodeGroupOutput")
    group_material = bpy.data.materials.new(PREFIX + "GroupMaterial")
    group_material.use_nodes = True
    group_material_node = group_material.node_tree.nodes.new("ShaderNodeGroup")
    group_material_node.node_tree = group
    group_world = bpy.data.worlds.new(PREFIX + "GroupWorld")
    group_world.use_nodes = True
    group_world_node = group_world.node_tree.nodes.new("ShaderNodeGroup")
    group_world_node.node_tree = group
    group_ref = tree_ref("NODE_GROUP", group_name)
    group_before = server.export_node_tree(group_ref, "all")
    group_patch = make_patch(
        group_ref,
        group_before["revision"],
        [{
            "op": "add_node",
            "id": "value",
            "node_type": "ShaderNodeValue",
            "name": "Control Value",
        }],
        ["graph"],
    )
    group_result = server.apply_node_tree_patch(group_patch, keep_backup=True)
    assert_true(group_result["status"] == "applied", group_result)
    committed_group = bpy.data.node_groups[group_name]
    assert_true(group_material_node.node_tree == committed_group, "Material group user not switched")
    assert_true(group_world_node.node_tree == committed_group, "World group user not switched")
    assert_true(committed_group != group, "Shader group mutated in place")
    assert_true(committed_group.get("blender_mcp_test") == "group", "group custom property drifted")

    rollback_name = PREFIX + "RollbackMaterial"
    rollback_material = bpy.data.materials.new(rollback_name)
    rollback_material.use_nodes = True
    rollback_mesh = bpy.data.meshes.new(PREFIX + "RollbackMesh")
    rollback_mesh.materials.append(rollback_material)
    rollback_ref = tree_ref("MATERIAL", rollback_name)
    rollback_before = server.export_node_tree(rollback_ref, "all")
    rollback_principled = next(
        node for node in rollback_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    rollback_socket = rollback_principled.inputs["Roughness"]
    rollback_patch = make_patch(
        rollback_ref,
        rollback_before["revision"],
        [{
            "op": "set_socket_default",
            "node": rollback_principled.name,
            "socket": socket_id(rollback_principled, rollback_socket),
            "value": 0.73,
        }],
        ["graph"],
    )
    target = namespace["_node_resolve_tree_ref"](rollback_ref)
    transaction = namespace["_node_apply_patch_transaction"]
    rollback_stages = [
        "after_working_verified",
        "after_original_renamed",
        "after_users_remapped",
        "after_working_named",
        "after_post_commit_verified",
    ]
    for failure_stage in rollback_stages:
        material_pointers_before = {item.as_pointer() for item in bpy.data.materials}

        def commit_guard(stage, _original, _working, expected=failure_stage):
            if stage == expected:
                raise RuntimeError(f"injected failure at {stage}")

        rolled_back = transaction(
            target, rollback_patch, True, _commit_guard=commit_guard
        )
        assert_true(rolled_back["status"] == "rolled_back", rolled_back)
        assert_true(rollback_mesh.materials[0] == rollback_material, "rollback user drifted")
        assert_true(
            server.export_node_tree(rollback_ref, "all") == rollback_before,
            f"rollback graph drifted at {failure_stage}",
        )
        assert_true(
            {item.as_pointer() for item in bpy.data.materials} == material_pointers_before,
            f"working Material leaked at {failure_stage}",
        )

    result = {
        "version": list(bpy.app.version[:3]),
        "material_applied": material_result["applied"],
        "world_applied_without_backup": world_result["applied"] and not world_result["backup"]["kept"],
        "light_shared_users": len(light_objects),
        "shader_group_applied": group_result["applied"],
        "rollback_stages": rollback_stages,
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    result["leaks"] = {
        "materials": [item.name for item in bpy.data.materials if item.name.startswith(PREFIX)],
        "worlds": [item.name for item in bpy.data.worlds if item.name.startswith(PREFIX)],
        "lights": [item.name for item in bpy.data.lights if item.name.startswith(PREFIX)],
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
        "objects": [item.name for item in bpy.data.objects if item.name.startswith(PREFIX)],
        "meshes": [item.name for item in bpy.data.meshes if item.name.startswith(PREFIX)],
    }
    assert_true(not any(result["leaks"].values()), f"fixture leak: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
