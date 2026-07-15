"""Transactional Scene compositor initialization."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic
from .constants import SCENE_COMPOSITOR_TREE_SCHEMA
from .targets import (
    _node_export_target,
    _node_id_editable,
    _node_owner_record,
    _node_resolve_tree_ref,
    _node_scene_tree,
    _node_tree_ref,
)


def _node_ensure_scene_compositor_tree(
    scene_name, create_if_missing=False, _commit_guard=None,
):
    if not isinstance(scene_name, str) or not scene_name.strip():
        raise ValueError("scene_name must be a non-empty string")
    scene_name = scene_name.strip()
    if len(scene_name) > 1024:
        raise ValueError("scene_name must not exceed 1024 characters")
    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        raise ValueError(f"Scene not found: {scene_name}")

    tree_ref = _node_tree_ref("CompositorNodeTree", "SCENE", scene.name)
    original_tree, adapter = _node_scene_tree(scene)
    if original_tree is not None:
        target = _node_resolve_tree_ref(tree_ref)
        snapshot = _node_export_target(target, "operations")
        return {
            "schema": SCENE_COMPOSITOR_TREE_SCHEMA,
            "status": "ready",
            "created": False,
            "mutated": False,
            "scene": target["owner"],
            "tree_ref": tree_ref,
            "adapter": adapter,
            "revision": snapshot["revision"],
            "diagnostics": snapshot["diagnostics"],
        }
    if not bool(create_if_missing):
        return {
            "schema": SCENE_COMPOSITOR_TREE_SCHEMA,
            "status": "missing",
            "created": False,
            "mutated": False,
            "scene": _node_owner_record("SCENE", scene),
            "tree_ref": tree_ref,
            "adapter": adapter,
            "revision": None,
            "diagnostics": [_gn_patch_diagnostic(
                "warning",
                "compositor_tree_missing",
                "/create_if_missing",
                "The Scene has no enabled compositor tree. Set create_if_missing "
                "to true to create one transactionally.",
            )],
        }

    editable = _node_id_editable(scene)
    is_override = getattr(scene, "override_library", None) is not None
    if not editable or is_override:
        reason = (
            "library overrides are not supported"
            if is_override else "the Scene is linked or read-only"
        )
        return {
            "schema": SCENE_COMPOSITOR_TREE_SCHEMA,
            "status": "rejected",
            "created": False,
            "mutated": False,
            "scene": _node_owner_record("SCENE", scene),
            "tree_ref": tree_ref,
            "adapter": adapter,
            "revision": None,
            "diagnostics": [_gn_patch_diagnostic(
                "error", "scene_not_editable", "/scene_name", reason,
            )],
        }

    original_use_nodes = bool(getattr(scene, "use_nodes", False))
    created_tree = None
    pointer_assigned = False
    committed = False

    def guard(stage):
        if _commit_guard is not None:
            _commit_guard(stage, scene, created_tree)

    try:
        if hasattr(scene, "compositing_node_group"):
            created_tree = bpy.data.node_groups.new(
                f"{scene.name} Compositor", "CompositorNodeTree"
            )
            created_tree.interface.new_socket(
                name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
            )
            output = created_tree.nodes.new("NodeGroupOutput")
            output.name = "Final Render Result"
            guard("after_tree_created")
            if scene.compositing_node_group is not None:
                raise RuntimeError("The Scene compositor pointer changed before commit")
            scene.compositing_node_group = created_tree
            pointer_assigned = True
        else:
            guard("before_use_nodes_enabled")
            scene.use_nodes = True
            pointer_assigned = True
        guard("after_scene_tree_enabled")

        target = _node_resolve_tree_ref(tree_ref)
        if target["owner_id"] != scene:
            raise RuntimeError("The compositor tree resolved to another Scene")
        if created_tree is not None and target["tree"] != created_tree:
            raise RuntimeError("The Scene compositor pointer did not retain the created tree")
        snapshot = _node_export_target(target, "operations")
        guard("after_tree_verified")
        committed = True
        return {
            "schema": SCENE_COMPOSITOR_TREE_SCHEMA,
            "status": "created",
            "created": True,
            "mutated": True,
            "scene": target["owner"],
            "tree_ref": tree_ref,
            "adapter": target["adapter"],
            "revision": snapshot["revision"],
            "tree": {
                "name": target["tree"].name,
                "node_count": snapshot["stats"]["node_count"],
                "interface_item_count": snapshot["stats"]["interface_item_count"],
            },
            "diagnostics": snapshot["diagnostics"],
        }
    except Exception as exc:
        rollback_errors = []
        if pointer_assigned:
            try:
                if hasattr(scene, "compositing_node_group"):
                    scene.compositing_node_group = original_tree
                else:
                    scene.use_nodes = original_use_nodes
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore Scene compositor state: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )
        if created_tree is not None:
            try:
                if created_tree.name in bpy.data.node_groups:
                    bpy.data.node_groups.remove(created_tree, do_unlink=True)
                created_tree = None
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"remove created compositor tree: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )
        restored_tree, _restored_adapter = _node_scene_tree(scene)
        if restored_tree != original_tree:
            rollback_errors.append("the Scene compositor state was not restored")
        diagnostics = [_gn_patch_diagnostic(
            "error",
            "compositor_creation_rolled_back",
            "",
            f"{type(exc).__name__}: {exc}",
        )]
        diagnostics.extend(
            _gn_patch_diagnostic("error", "rollback_incomplete", "", message)
            for message in rollback_errors
        )
        return {
            "schema": SCENE_COMPOSITOR_TREE_SCHEMA,
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "created": False,
            "mutated": bool(rollback_errors),
            "scene": _node_owner_record("SCENE", scene),
            "tree_ref": tree_ref,
            "adapter": adapter,
            "revision": None,
            "diagnostics": diagnostics,
        }
    finally:
        if not committed and created_tree is not None:
            try:
                if created_tree.name in bpy.data.node_groups:
                    bpy.data.node_groups.remove(created_tree, do_unlink=True)
            except (AttributeError, RuntimeError):
                pass
