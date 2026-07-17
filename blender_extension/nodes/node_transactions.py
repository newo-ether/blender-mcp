"""Transactional generic node-tree patch application."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic
from .constants import NODE_TREE_SNAPSHOT_SCHEMA
from .node_validation import _node_validate_patch_runtime
from .patch_operations import (
    _node_execute_patch_operations,
    _node_remove_validation_copy,
    _node_validation_copy,
)
from .schema import _gn_actual_snapshot_diff
from .serialization import _node_export_tree
from .targets import (
    _node_export_target,
    _node_resolve_tree_ref,
    _node_target_capabilities,
)


def _node_owner_collection(owner_kind):
    return {
        "MATERIAL": bpy.data.materials,
        "WORLD": bpy.data.worlds,
        "LIGHT": bpy.data.lights,
        "SCENE": bpy.data.scenes,
        "NODE_GROUP": bpy.data.node_groups,
    }[owner_kind]

def _node_direct_user_pointers(value):
    try:
        return {
            user.as_pointer()
            for user in bpy.data.user_map(subset={value}).get(value, set())
        }
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return set()

def _node_apply_scene_tree_transaction(
    target, patch, keep_backup=True, _commit_guard=None,
):
    """Atomically switch one modern Scene to a patched compositor tree copy."""
    validation = _node_validate_patch_runtime(target, patch)
    if not validation["valid"]:
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rejected",
            "applied": False,
            "mutated": False,
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": validation["current_revision"],
            "revision": validation["current_revision"],
            "diagnostics": validation["diagnostics"],
            "plan": validation["plan"],
        }
    capabilities = _node_target_capabilities(target)
    if not capabilities["apply"]:
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rejected",
            "applied": False,
            "mutated": False,
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": validation["current_revision"],
            "revision": validation["current_revision"],
            "diagnostics": [_gn_patch_diagnostic(
                "error", "apply_not_supported", "/tree_ref",
                capabilities["mutation_reason"],
            )],
            "plan": validation["plan"],
        }

    scene = target["owner_id"]
    original = target["tree"]
    original_name = original.name
    original_fake_user = bool(original.use_fake_user)
    original_snapshot = _node_export_target(target, "all")
    original_user_pointers = _node_direct_user_pointers(original)
    scene_pointer = scene.as_pointer()
    other_user_pointers = original_user_pointers - {scene_pointer}
    working = None
    pointer_switched = False
    committed = False
    execution = None

    def guard(stage):
        if _commit_guard is not None:
            _commit_guard(stage, original, working)

    try:
        working = original.copy()
        suffix = patch["base_revision"].split(":", 1)[-1][:8]
        working.name = f"{original_name}.MCP Applied {suffix}"
        working_target = dict(target)
        working_target["tree"] = working
        execution = _node_execute_patch_operations(working_target, patch)
        execution_errors = [
            item for item in execution["diagnostics"] if item["severity"] == "error"
        ]
        if execution_errors:
            raise RuntimeError(
                "Validated operations failed during commit: "
                + "; ".join(item["message"] for item in execution_errors)
            )
        candidate_snapshot = _node_export_tree(
            working,
            "all",
            schema=NODE_TREE_SNAPSHOT_SCHEMA,
            tree_ref=target["tree_ref"],
            owner=target["owner"],
            capabilities=capabilities,
        )
        if candidate_snapshot["revision"] != validation.get("candidate_revision"):
            raise RuntimeError(
                "Commit candidate revision differs from the validated dry-run"
            )
        guard("after_working_verified")

        if scene.compositing_node_group != original:
            raise RuntimeError("The Scene compositor pointer changed before commit")
        guard("before_scene_pointer_swap")
        scene.compositing_node_group = working
        pointer_switched = True
        if scene.compositing_node_group != working:
            raise RuntimeError("The Scene compositor pointer did not switch")
        if not other_user_pointers.issubset(_node_direct_user_pointers(original)):
            raise RuntimeError("Switching the selected Scene changed other tree users")
        guard("after_scene_pointer_swapped")

        resolved = _node_resolve_tree_ref(target["tree_ref"])
        if resolved["owner_id"] != scene or resolved["tree"] != working:
            raise RuntimeError("The canonical tree_ref did not resolve to the working tree")
        guard("after_working_named")
        committed_snapshot = _node_export_target(resolved, "all")
        if committed_snapshot["revision"] != candidate_snapshot["revision"]:
            raise RuntimeError("Post-commit graph differs from the validated candidate")
        guard("after_post_commit_verified")

        remaining_users = _node_direct_user_pointers(original)
        if keep_backup:
            if not remaining_users:
                original.use_fake_user = True
                retained_reason = "requested_fake_user"
            else:
                retained_reason = "existing_shared_users"
            backup = {
                "kept": True,
                "requested": True,
                "owner_kind": "NODE_GROUP",
                "name": original.name,
                "tree_revision": original_snapshot["revision"],
                "retained_reason": retained_reason,
            }
        elif remaining_users:
            backup = {
                "kept": True,
                "requested": False,
                "owner_kind": "NODE_GROUP",
                "name": original.name,
                "tree_revision": original_snapshot["revision"],
                "retained_reason": "existing_shared_users",
            }
        else:
            bpy.data.node_groups.remove(original, do_unlink=True)
            working.name = original_name
            backup = {
                "kept": False,
                "requested": False,
                "owner_kind": "NODE_GROUP",
                "name": None,
                "tree_revision": original_snapshot["revision"],
                "retained_reason": None,
            }
            resolved = _node_resolve_tree_ref(target["tree_ref"])
            committed_snapshot = _node_export_target(resolved, "all")
        committed = True
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "applied",
            "applied": True,
            "mutated": True,
            "tree_ref": target["tree_ref"],
            "previous_owner_name": scene.name,
            "base_revision": validation["current_revision"],
            "new_revision": committed_snapshot["revision"],
            # Stable alias: "the tree's revision as this response was produced",
            # whatever the status. Chaining patches otherwise means branching on
            # status to pick between new_revision and current_revision, and the
            # obvious result["revision"] silently reads None.
            "revision": committed_snapshot["revision"],
            "users_reassigned": [{
                "kind": "SCENE_COMPOSITOR_POINTER",
                "scene": scene.name,
                "from_tree": original_name,
                "to_tree": working.name,
            }],
            "backup": backup,
            "created_nodes": execution["created_nodes"],
            "created_interface_sockets": execution["created_interface"],
            "operations": execution["plan"],
            "semantic_diff": validation["semantic_diff"],
            "actual_diff": _gn_actual_snapshot_diff(
                original_snapshot, committed_snapshot
            ),
            "warnings": [
                item for item in validation["diagnostics"]
                if item["severity"] == "warning"
            ],
            "verification": {
                "node_count": committed_snapshot["stats"]["node_count"],
                "link_count": committed_snapshot["stats"]["link_count"],
                "interface_item_count": committed_snapshot["stats"]["interface_item_count"],
                "transaction_adapter": target["adapter"],
                "other_users_preserved": len(other_user_pointers),
            },
        }
    except Exception as exc:
        rollback_errors = []
        if pointer_switched:
            try:
                scene.compositing_node_group = original
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore Scene compositor pointer: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )
        try:
            original.use_fake_user = original_fake_user
        except (AttributeError, RuntimeError) as rollback_exc:
            rollback_errors.append(
                f"restore fake-user state: {type(rollback_exc).__name__}: {rollback_exc}"
            )
        if not original_user_pointers.issubset(_node_direct_user_pointers(original)):
            rollback_errors.append("not every original compositor-tree user was restored")
        if working is not None and _node_direct_user_pointers(working):
            rollback_errors.append("the working compositor tree still has direct users")
        if working is not None:
            try:
                if working.name in bpy.data.node_groups:
                    bpy.data.node_groups.remove(working, do_unlink=True)
                working = None
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"remove working compositor tree: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )
        try:
            restored = _node_resolve_tree_ref(target["tree_ref"])
            if restored["owner_id"] != scene or restored["tree"] != original:
                rollback_errors.append("canonical tree_ref did not resolve to original Scene tree")
            elif _node_export_target(restored, "all") != original_snapshot:
                rollback_errors.append("restored graph or owner metadata differs from original")
        except Exception as rollback_exc:
            rollback_errors.append(
                f"verify rollback: {type(rollback_exc).__name__}: {rollback_exc}"
            )
        diagnostics = [_gn_patch_diagnostic(
            "error", "transaction_rolled_back", "",
            f"{type(exc).__name__}: {exc}",
        )]
        diagnostics.extend(
            _gn_patch_diagnostic("error", "rollback_incomplete", "", message)
            for message in rollback_errors
        )
        rolled_back_revision = _node_export_target(
            _node_resolve_tree_ref(target["tree_ref"]), "all"
        )["revision"]
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "applied": False,
            "mutated": bool(rollback_errors),
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": rolled_back_revision,
            "revision": rolled_back_revision,
            "diagnostics": diagnostics,
            "plan": validation["plan"],
        }
    finally:
        if not committed and working is not None:
            try:
                if working.name in bpy.data.node_groups:
                    bpy.data.node_groups.remove(working, do_unlink=True)
            except (AttributeError, RuntimeError):
                pass

def _node_apply_patch_transaction(
    target, patch, keep_backup=True, _commit_guard=None,
):
    if target["adapter"] == "scene_compositing_node_group":
        return _node_apply_scene_tree_transaction(
            target, patch, keep_backup, _commit_guard,
        )
    validation = _node_validate_patch_runtime(target, patch)
    if not validation["valid"]:
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rejected",
            "applied": False,
            "mutated": False,
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": validation["current_revision"],
            "revision": validation["current_revision"],
            "diagnostics": validation["diagnostics"],
            "plan": validation["plan"],
        }
    capabilities = _node_target_capabilities(target)
    if not capabilities["apply"]:
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rejected",
            "applied": False,
            "mutated": False,
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": validation["current_revision"],
            "revision": validation["current_revision"],
            "diagnostics": [_gn_patch_diagnostic(
                "error", "apply_not_supported", "/tree_ref",
                capabilities["mutation_reason"],
            )],
            "plan": validation["plan"],
        }

    original = target["owner_id"]
    original_name = original.name
    original_fake_user = bool(original.use_fake_user)
    original_snapshot = _node_export_target(target, "all")
    original_user_pointers = _node_direct_user_pointers(original)
    working_target = working = standalone_tree = None
    original_renamed = False
    users_remapped = False
    committed = False
    execution = None

    def guard(stage):
        if _commit_guard is not None:
            _commit_guard(stage, original, working)

    try:
        working_target, working, standalone_tree = _node_validation_copy(target)
        execution = _node_execute_patch_operations(working_target, patch)
        execution_errors = [
            item for item in execution["diagnostics"] if item["severity"] == "error"
        ]
        if execution_errors:
            raise RuntimeError(
                "Validated operations failed during commit: "
                + "; ".join(item["message"] for item in execution_errors)
            )
        candidate_snapshot = _node_export_tree(
            working_target["tree"],
            "all",
            schema=NODE_TREE_SNAPSHOT_SCHEMA,
            tree_ref=target["tree_ref"],
            owner=target["owner"],
            capabilities=capabilities,
        )
        if candidate_snapshot["revision"] != validation.get("candidate_revision"):
            raise RuntimeError(
                "Commit candidate revision differs from the validated dry-run"
            )
        guard("after_working_verified")

        backup_suffix = patch["base_revision"].split(":", 1)[-1][:8]
        original.name = f"{original_name}.MCP Backup {backup_suffix}"
        original_renamed = True
        guard("after_original_renamed")

        original.user_remap(working)
        users_remapped = True
        if _node_direct_user_pointers(original):
            raise RuntimeError("Some original owner users were not remapped")
        if not original_user_pointers.issubset(_node_direct_user_pointers(working)):
            raise RuntimeError("The working owner did not receive every original user")
        guard("after_users_remapped")

        working.name = original_name
        guard("after_working_named")
        resolved = _node_resolve_tree_ref(target["tree_ref"])
        if resolved["owner_id"] != working:
            raise RuntimeError("The canonical tree_ref did not resolve to the working owner")
        committed_snapshot = _node_export_target(resolved, "all")
        if committed_snapshot["revision"] != candidate_snapshot["revision"]:
            raise RuntimeError("Post-commit graph differs from the validated candidate")
        guard("after_post_commit_verified")

        backup = None
        if keep_backup:
            original.use_fake_user = True
            backup = {
                "kept": True,
                "owner_kind": target["owner_kind"],
                "name": original.name,
                "tree_revision": original_snapshot["revision"],
            }
        else:
            _node_owner_collection(target["owner_kind"]).remove(
                original, do_unlink=True
            )
            backup = {
                "kept": False,
                "owner_kind": target["owner_kind"],
                "name": None,
                "tree_revision": original_snapshot["revision"],
            }
        committed = True
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "applied",
            "applied": True,
            "mutated": True,
            "tree_ref": target["tree_ref"],
            "previous_owner_name": original_name,
            "base_revision": validation["current_revision"],
            "new_revision": committed_snapshot["revision"],
            # Stable alias; see the compositor path above.
            "revision": committed_snapshot["revision"],
            "users_reassigned": original_snapshot["users"],
            "backup": backup,
            "created_nodes": execution["created_nodes"],
            "created_interface_sockets": execution["created_interface"],
            "operations": execution["plan"],
            "semantic_diff": validation["semantic_diff"],
            "actual_diff": _gn_actual_snapshot_diff(
                original_snapshot, committed_snapshot
            ),
            "warnings": [
                item for item in validation["diagnostics"]
                if item["severity"] == "warning"
            ],
            "verification": {
                "node_count": committed_snapshot["stats"]["node_count"],
                "link_count": committed_snapshot["stats"]["link_count"],
                "interface_item_count": committed_snapshot["stats"]["interface_item_count"],
            },
        }
    except Exception as exc:
        rollback_errors = []
        if users_remapped and working is not None:
            try:
                working.user_remap(original)
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore owner users: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        if original_renamed:
            try:
                if working is not None:
                    working.name = f".{original_name}.MCP Rollback"
                original.name = original_name
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore owner names: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        try:
            original.use_fake_user = original_fake_user
        except (AttributeError, RuntimeError) as rollback_exc:
            rollback_errors.append(
                f"restore fake-user state: {type(rollback_exc).__name__}: {rollback_exc}"
            )
        if not original_user_pointers.issubset(_node_direct_user_pointers(original)):
            rollback_errors.append("not every original direct user was restored")
        if working is not None and _node_direct_user_pointers(working):
            rollback_errors.append("the working owner still has direct users")
        if working is not None:
            try:
                _node_remove_validation_copy(target, working, standalone_tree)
                working = None
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"remove working owner: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        try:
            restored = _node_resolve_tree_ref(target["tree_ref"])
            if restored["owner_id"] != original:
                rollback_errors.append("canonical tree_ref did not resolve to original owner")
            elif _node_export_target(restored, "all") != original_snapshot:
                rollback_errors.append("restored graph or owner metadata differs from original")
        except Exception as rollback_exc:
            rollback_errors.append(
                f"verify rollback: {type(rollback_exc).__name__}: {rollback_exc}"
            )
        diagnostics = [_gn_patch_diagnostic(
            "error", "transaction_rolled_back", "",
            f"{type(exc).__name__}: {exc}",
        )]
        diagnostics.extend(
            _gn_patch_diagnostic("error", "rollback_incomplete", "", message)
            for message in rollback_errors
        )
        rolled_back_revision = _node_export_target(target, "all")["revision"]
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "applied": False,
            "mutated": bool(rollback_errors),
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": rolled_back_revision,
            "revision": rolled_back_revision,
            "diagnostics": diagnostics,
            "plan": validation["plan"],
        }
    finally:
        if not committed and working is not None:
            try:
                _node_remove_validation_copy(target, working, standalone_tree)
            except (AttributeError, RuntimeError):
                pass
