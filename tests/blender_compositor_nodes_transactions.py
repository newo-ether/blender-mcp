"""Live transactional mutation acceptance for Compositor node owners."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import tempfile
import traceback

import bpy


PREFIX = "__BLENDER_MCP_COMPOSITOR_TX__"
RESULT_PREFIX = "BLENDER_MCP_COMPOSITOR_TX_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def cleanup():
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)
    for image in list(bpy.data.images):
        if image.name.startswith(PREFIX):
            bpy.data.images.remove(image, do_unlink=True)
    for mask in list(bpy.data.masks):
        if mask.name.startswith(PREFIX):
            bpy.data.masks.remove(mask, do_unlink=True)


def tree_ref(owner_kind, owner_name):
    return {
        "tree_type": "CompositorNodeTree",
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


def compositor_socket_ids():
    probe = bpy.data.node_groups.new(PREFIX + "SocketProbe", "CompositorNodeTree")
    result = {}
    try:
        for key, node_type in (
            ("curves", "CompositorNodeCurveRGB"),
            ("denoise", "CompositorNodeDenoise"),
            ("glare", "CompositorNodeGlare"),
        ):
            node = probe.nodes.new(node_type)
            image_input = node.inputs.get("Image") or node.inputs[0]
            image_output = node.outputs.get("Image") or node.outputs[0]
            result[key] = {
                "input": socket_id(node, image_input, "input"),
                "output": socket_id(node, image_output, "output"),
            }
    finally:
        bpy.data.node_groups.remove(probe, do_unlink=True)
    return result


def create_scene_tree(scene):
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(PREFIX + "SceneTree", "CompositorNodeTree")
        tree.interface.new_socket(
            name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
        )
        output = tree.nodes.new("NodeGroupOutput")
        output.name = "Final Render Result"
        scene.compositing_node_group = tree
        adapter = "scene_compositing_node_group"
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        output = next(
            node for node in tree.nodes if node.bl_idname == "CompositorNodeComposite"
        )
        output.name = "Final Render Result"
        adapter = "scene_embedded_node_tree"
    source = next(
        (node for node in tree.nodes if node.bl_idname == "CompositorNodeRLayers"),
        None,
    )
    if source is None:
        source = tree.nodes.new("CompositorNodeRLayers")
    source.name = "Render Layers Source"
    image_output = source.outputs.get("Image") or source.outputs[0]
    image_input = output.inputs.get("Image") or output.inputs[0]
    for link in list(tree.links):
        if link.to_node == output and link.to_socket == image_input:
            tree.links.remove(link)
    tree.links.new(image_output, image_input)
    return tree, source, output, adapter


def tutorial_operations(source, output, reference_scene):
    sockets = compositor_socket_ids()
    source_image = source.outputs.get("Image") or source.outputs[0]
    output_image = output.inputs.get("Image") or output.inputs[0]
    identity_curve = {
        "points": [
            {"location": [0.0, 0.0]},
            {"location": [1.0, 1.0]},
        ]
    }
    return [
        {
            "op": "set_node_property",
            "node": source.name,
            "property": "scene",
            "value": {
                "$type": "ID",
                "id_type": "Scene",
                "name": reference_scene.name,
            },
        },
        {
            "op": "set_node_property",
            "node": source.name,
            "property": "layer",
            "value": {
                "$type": "ViewLayer",
                "scene": reference_scene.name,
                "name": reference_scene.view_layers[0].name,
            },
        },
        {
            "op": "add_node",
            "id": "tutorial_frame",
            "node_type": "NodeFrame",
            "name": "Stage 1 - Image Treatment",
            "layout": {"location": [-200.0, 260.0], "width": 940.0},
        },
        {
            "op": "set_annotation",
            "node": "tutorial_frame",
            "text": (
                "Stage 1: shape contrast with RGB Curves, clean render noise with "
                "Denoise, then add a restrained Glare pass before the final output."
            ),
        },
        {
            "op": "add_node",
            "id": "curves",
            "node_type": "CompositorNodeCurveRGB",
            "name": "01 Color Correction",
            "layout": {"location": [-100.0, 100.0], "parent": "tutorial_frame"},
        },
        {
            "op": "set_curve_mapping",
            "node": "curves",
            "use_clip": True,
            "curves": [identity_curve, identity_curve, identity_curve, identity_curve],
        },
        {
            "op": "add_node",
            "id": "denoise",
            "node_type": "CompositorNodeDenoise",
            "name": "02 Denoise",
            "layout": {"location": [180.0, 100.0], "parent": "tutorial_frame"},
        },
        {
            "op": "add_node",
            "id": "glare",
            "node_type": "CompositorNodeGlare",
            "name": "03 Glare",
            "layout": {"location": [450.0, 100.0], "parent": "tutorial_frame"},
        },
        {
            "op": "add_node",
            "id": "cryptomatte",
            "node_type": "CompositorNodeCryptomatteV2",
            "name": "Optional Cryptomatte Reference",
            "properties": {
                "scene": {
                    "$type": "ID",
                    "id_type": "Scene",
                    "name": reference_scene.name,
                }
            },
            "layout": {"location": [-100.0, -220.0], "parent": "tutorial_frame"},
        },
        {
            "op": "remove_link",
            "from_node": source.name,
            "from_socket": socket_id(source, source_image, "output"),
            "to_node": output.name,
            "to_socket": socket_id(output, output_image, "input"),
        },
        {
            "op": "add_link",
            "from_node": source.name,
            "from_socket": socket_id(source, source_image, "output"),
            "to_node": "curves",
            "to_socket": sockets["curves"]["input"],
        },
        {
            "op": "add_link",
            "from_node": "curves",
            "from_socket": sockets["curves"]["output"],
            "to_node": "denoise",
            "to_socket": sockets["denoise"]["input"],
        },
        {
            "op": "add_link",
            "from_node": "denoise",
            "from_socket": sockets["denoise"]["output"],
            "to_node": "glare",
            "to_socket": sockets["glare"]["input"],
        },
        {
            "op": "add_link",
            "from_node": "glare",
            "from_socket": sockets["glare"]["output"],
            "to_node": output.name,
            "to_socket": socket_id(output, output_image, "input"),
        },
    ]


def run_test():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(
        str(REPO_ROOT / "addon.py"),
        run_name="blender_mcp_compositor_transactions_test",
    )
    server = namespace["BlenderMCPServer"]()
    transaction = namespace["_node_apply_patch_transaction"]

    reference_scene = bpy.data.scenes.new(PREFIX + "ReferenceScene")
    target_name = PREFIX + "TutorialScene"
    target_scene = bpy.data.scenes.new(target_name)
    tree, source, output, adapter = create_scene_tree(target_scene)
    target_ref = tree_ref("SCENE", target_name)

    shared_scene = None
    legacy_consumer = None
    legacy_reference = None
    if adapter == "scene_compositing_node_group":
        shared_scene = bpy.data.scenes.new(PREFIX + "SharedTreeScene")
        shared_scene.compositing_node_group = tree
    else:
        legacy_consumer = bpy.data.scenes.new(PREFIX + "LegacyConsumer")
        legacy_consumer.use_nodes = True
        legacy_reference = next(
            node for node in legacy_consumer.node_tree.nodes
            if node.bl_idname == "CompositorNodeRLayers"
        )
        legacy_reference.scene = target_scene

    target_before = server.export_node_tree(target_ref, "all")

    tutorial_patch = make_patch(
        target_ref,
        target_before["revision"],
        tutorial_operations(source, output, reference_scene),
        ["graph", "layout", "annotation", "dynamic", "id_reference"],
    )
    validation = server.validate_node_tree_patch(tutorial_patch)
    assert_true(validation["valid"], validation)
    assert_true(server.export_node_tree(target_ref, "all") == target_before, "validation mutated Scene")

    with tempfile.TemporaryDirectory(prefix="blender-mcp-compositor-no-output-") as output_dir:
        before_files = sorted(Path(output_dir).rglob("*"))
        application = server.apply_node_tree_patch(tutorial_patch, keep_backup=True)
        after_files = sorted(Path(output_dir).rglob("*"))
    assert_true(application["status"] == "applied", application)
    assert_true(before_files == after_files == [], "Compositor apply created an output artifact")
    committed_scene = bpy.data.scenes[target_name]
    committed_tree = (
        committed_scene.compositing_node_group
        if adapter == "scene_compositing_node_group"
        else committed_scene.node_tree
    )
    assert_true(committed_tree != tree, "Compositor transaction mutated the original tree")
    assert_true(
        server.export_node_tree(target_ref, "all")["revision"] == application["new_revision"],
        "Compositor application revision mismatch",
    )
    for node_name in (
        "Stage 1 - Image Treatment",
        "01 Color Correction",
        "02 Denoise",
        "03 Glare",
        "Optional Cryptomatte Reference",
        "Final Render Result",
    ):
        assert_true(committed_tree.nodes.get(node_name) is not None, f"missing tutorial node {node_name}")
    assert_true(len(committed_tree.links) >= 4, "tutorial image chain was not connected")
    if shared_scene is not None:
        assert_true(shared_scene.compositing_node_group == tree, "unselected shared Scene changed")
        assert_true(application["backup"]["retained_reason"] == "existing_shared_users", application)
    else:
        assert_true(legacy_reference.scene == committed_scene, "legacy Scene ID user was not remapped")

    no_backup_name = PREFIX + "NoBackupScene"
    no_backup_scene = bpy.data.scenes.new(no_backup_name)
    no_backup_tree, _no_backup_source, _no_backup_output, _no_backup_adapter = (
        create_scene_tree(no_backup_scene)
    )
    no_backup_tree_pointer = no_backup_tree.as_pointer()
    no_backup_scene_pointer = no_backup_scene.as_pointer()
    no_backup_ref = tree_ref("SCENE", no_backup_name)
    no_backup_before = server.export_node_tree(no_backup_ref, "all")
    no_backup_patch = make_patch(
        no_backup_ref,
        no_backup_before["revision"],
        [{"op": "add_node", "id": "note", "node_type": "NodeFrame"}],
        ["graph"],
    )
    no_backup_application = server.apply_node_tree_patch(
        no_backup_patch, keep_backup=False
    )
    assert_true(no_backup_application["status"] == "applied", no_backup_application)
    assert_true(not no_backup_application["backup"]["kept"], no_backup_application)
    if adapter == "scene_compositing_node_group":
        assert_true(
            no_backup_tree_pointer not in {
                item.as_pointer() for item in bpy.data.node_groups
            },
            "unshared modern compositor backup was retained",
        )
        assert_true(
            bpy.data.scenes[no_backup_name].as_pointer() == no_backup_scene_pointer,
            "modern Scene owner was replaced instead of its tree pointer",
        )
    else:
        assert_true(
            no_backup_scene_pointer not in {item.as_pointer() for item in bpy.data.scenes},
            "legacy Scene backup was retained",
        )

    group_name = PREFIX + "ReusableGroup"
    group = bpy.data.node_groups.new(group_name, "CompositorNodeTree")
    group["blender_mcp_test"] = "preserve"
    group.interface.new_socket(
        name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    group.nodes.new("NodeGroupOutput")
    group_user = committed_tree.nodes.new("CompositorNodeGroup")
    group_user.node_tree = group
    group_ref = tree_ref("NODE_GROUP", group_name)
    group_before = server.export_node_tree(group_ref, "all")
    group_patch = make_patch(
        group_ref,
        group_before["revision"],
        [{
            "op": "add_node",
            "id": "group_denoise",
            "node_type": "CompositorNodeDenoise",
            "name": "Reusable Denoise",
        }],
        ["graph"],
    )
    group_application = server.apply_node_tree_patch(group_patch, keep_backup=True)
    assert_true(group_application["status"] == "applied", group_application)
    committed_group = bpy.data.node_groups[group_name]
    assert_true(group_user.node_tree == committed_group and committed_group != group, "group user drifted")
    assert_true(committed_group.get("blender_mcp_test") == "preserve", "group metadata drifted")

    rollback_name = PREFIX + "RollbackScene"
    rollback_scene = bpy.data.scenes.new(rollback_name)
    rollback_tree, _rollback_source, _rollback_output, rollback_adapter = create_scene_tree(rollback_scene)
    rollback_ref = tree_ref("SCENE", rollback_name)
    rollback_consumer = None
    if rollback_adapter == "scene_embedded_node_tree":
        rollback_consumer_scene = bpy.data.scenes.new(PREFIX + "RollbackConsumer")
        rollback_consumer_scene.use_nodes = True
        rollback_consumer = next(
            node for node in rollback_consumer_scene.node_tree.nodes
            if node.bl_idname == "CompositorNodeRLayers"
        )
        rollback_consumer.scene = rollback_scene
    rollback_before = server.export_node_tree(rollback_ref, "all")
    rollback_patch = make_patch(
        rollback_ref,
        rollback_before["revision"],
        [{"op": "add_node", "id": "rollback_frame", "node_type": "NodeFrame"}],
        ["graph"],
    )
    rollback_stages = (
        [
            "after_working_verified",
            "before_scene_pointer_swap",
            "after_scene_pointer_swapped",
            "after_working_named",
            "after_post_commit_verified",
        ]
        if rollback_adapter == "scene_compositing_node_group"
        else [
            "after_working_verified",
            "after_original_renamed",
            "after_users_remapped",
            "after_working_named",
            "after_post_commit_verified",
        ]
    )
    for failure_stage in rollback_stages:
        scene_pointers_before = {item.as_pointer() for item in bpy.data.scenes}
        tree_pointers_before = {item.as_pointer() for item in bpy.data.node_groups}

        def commit_guard(stage, _original, _working, expected=failure_stage):
            if stage == expected:
                raise RuntimeError(f"injected failure at {stage}")

        rolled_back = transaction(
            namespace["_node_resolve_tree_ref"](rollback_ref),
            rollback_patch,
            True,
            _commit_guard=commit_guard,
        )
        assert_true(rolled_back["status"] == "rolled_back", rolled_back)
        assert_true(server.export_node_tree(rollback_ref, "all") == rollback_before, failure_stage)
        assert_true(
            {item.as_pointer() for item in bpy.data.scenes} == scene_pointers_before,
            f"working Scene leaked at {failure_stage}",
        )
        assert_true(
            {item.as_pointer() for item in bpy.data.node_groups} == tree_pointers_before,
            f"working compositor tree leaked at {failure_stage}",
        )
        if rollback_consumer is not None:
            assert_true(rollback_consumer.scene == rollback_scene, "legacy rollback user drifted")
        else:
            assert_true(rollback_scene.compositing_node_group == rollback_tree, "pointer rollback drifted")

    safety_before = server.export_node_tree(target_ref, "all")
    file_output_patch = make_patch(
        target_ref,
        safety_before["revision"],
        [{"op": "add_node", "id": "unsafe_output", "node_type": "CompositorNodeOutputFile"}],
        ["graph"],
    )
    with tempfile.TemporaryDirectory(prefix="blender-mcp-compositor-file-output-") as output_dir:
        rejected_output = server.apply_node_tree_patch(file_output_patch, keep_backup=True)
        output_files = sorted(Path(output_dir).rglob("*"))
    assert_true(rejected_output["status"] == "rejected", rejected_output)
    assert_true(
        "effect_sensitive_node_read_only" in {
            item["code"] for item in rejected_output["diagnostics"]
        },
        "File Output safety diagnostic missing",
    )
    assert_true(not output_files, "rejected File Output patch created an artifact")
    assert_true(server.export_node_tree(target_ref, "all") == safety_before, "safety rejection mutated graph")

    missing_layer_patch = make_patch(
        target_ref,
        safety_before["revision"],
        [{
            "op": "set_node_property",
            "node": "Render Layers Source",
            "property": "layer",
            "value": {
                "$type": "ViewLayer",
                "scene": reference_scene.name,
                "name": PREFIX + "MissingViewLayer",
            },
        }],
        ["graph", "id_reference"],
    )
    missing_layer = server.validate_node_tree_patch(missing_layer_patch)
    assert_true(not missing_layer["valid"], "missing View Layer validated")
    assert_true(
        "invalid_view_layer_reference" in {
            item["code"] for item in missing_layer["diagnostics"]
        },
        "View Layer existence diagnostic missing",
    )

    composite_patch = make_patch(
        target_ref,
        safety_before["revision"],
        [{"op": "add_node", "id": "legacy_output", "node_type": "CompositorNodeComposite"}],
        ["graph"],
    )
    composite_validation = server.validate_node_tree_patch(composite_patch)
    if adapter == "scene_compositing_node_group":
        assert_true(not composite_validation["valid"], "removed Composite node validated")
        diagnostic = next(
            item for item in composite_validation["diagnostics"]
            if item["code"] == "unsupported_node_type"
        )
        assert_true("Blender" in diagnostic["message"], "node diagnostic lacks version context")
    else:
        assert_true(composite_validation["valid"], composite_validation)

    result = {
        "version": list(bpy.app.version[:3]),
        "adapter": adapter,
        "scene_applied": application["applied"],
        "scene_applied_without_backup": (
            no_backup_application["applied"]
            and not no_backup_application["backup"]["kept"]
        ),
        "group_applied": group_application["applied"],
        "rollback_stages": rollback_stages,
        "shared_scene_preserved": shared_scene is None or shared_scene.compositing_node_group == tree,
        "file_output_rejected": not rejected_output["applied"],
        "missing_view_layer_rejected": not missing_layer["valid"],
        "final_output_contract": (
            "group_output" if adapter == "scene_compositing_node_group" else "composite_node"
        ),
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    result["leaks"] = {
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
        "images": [item.name for item in bpy.data.images if item.name.startswith(PREFIX)],
        "masks": [item.name for item in bpy.data.masks if item.name.startswith(PREFIX)],
    }
    assert_true(not any(result["leaks"].values()), f"fixture leak: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
