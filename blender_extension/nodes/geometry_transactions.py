"""Transactional Geometry Nodes patch application."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic
from .geometry_operations import _gn_apply_operations_to_working
from .geometry_validation import _gn_validate_patch_runtime
from .modifiers import (
    _gn_modifier_state,
    _gn_restore_modifier_state,
    _gn_set_modifier_input_value,
    _gn_user_handle,
)
from .patch_values import _gn_decode_patch_value
from .schema import _gn_actual_snapshot_diff
from .serialization import _gn_export_tree, _gn_tree_users


def _gn_assign_user(handle, kind, tree):
    if kind == "MODIFIER":
        handle.node_group = tree
    else:
        handle.node_tree = tree

def _gn_apply_patch_transaction(tree, patch, keep_backup=True, _commit_guard=None):
    validation = _gn_validate_patch_runtime(tree, patch)
    if not validation["valid"]:
        return {
            "schema": "blender-geometry-nodes-patch-application/1",
            "status": "rejected",
            "applied": False,
            "mutated": False,
            "tree_name": tree.name,
            "base_revision": patch.get("base_revision"),
            "current_revision": validation["current_revision"],
            "revision": validation["current_revision"],
            "diagnostics": validation["diagnostics"],
            "plan": validation["plan"],
        }

    original_snapshot = _gn_export_tree(tree, "all")
    original_name = tree.name
    original_fake_user = tree.use_fake_user
    working = tree.copy()
    working.name = f".{original_name}.MCP Working"
    candidate_snapshot = None
    applied = None
    selected_users = []
    modifier_states = {}
    renamed_original = False

    try:
        applied = _gn_apply_operations_to_working(working, patch)
        invalid_links = [link for link in working.links if not link.is_valid]
        if invalid_links:
            raise RuntimeError(f"Working tree contains {len(invalid_links)} invalid links")
        candidate_snapshot = _gn_export_tree(working, "all")

        all_users = _gn_tree_users(tree)
        policy = patch.get("shared_tree_policy", "reject")
        if policy == "single_user_copy":
            target = patch["target_user"]
            selected_records = [
                user for user in all_users
                if all(user.get(key) == value for key, value in target.items())
            ]
        else:
            selected_records = all_users

        for record in selected_records:
            handle = _gn_user_handle(record)
            if handle is None:
                raise RuntimeError(f"User disappeared before commit: {record['name']}")
            selected_users.append((record, handle))
            if record["kind"] == "MODIFIER":
                modifier_states[handle] = _gn_modifier_state(handle, tree)

        for operation in applied["deferred_modifier_inputs"]:
            obj = bpy.data.objects[operation["object"]]
            modifier = obj.modifiers[operation["modifier"]]
            if modifier not in modifier_states:
                modifier_states[modifier] = _gn_modifier_state(modifier, tree)

        if policy == "single_user_copy":
            working.name = f"{original_name}.MCP Copy"
        else:
            backup_suffix = validation["current_revision"].split(":", 1)[1][:8]
            tree.name = f"{original_name}.MCP Backup {backup_suffix}"
            renamed_original = True
            working.name = original_name

        for record, handle in selected_users:
            _gn_assign_user(handle, record["kind"], working)

        for operation in applied["deferred_modifier_inputs"]:
            obj = bpy.data.objects[operation["object"]]
            modifier = obj.modifiers[operation["modifier"]]
            if modifier.node_group != working:
                raise RuntimeError(
                    f"Modifier input target was not reassigned to working tree: "
                    f"{operation['object']}/{operation['modifier']}"
                )
            decoded = _gn_decode_patch_value(operation["value"], applied["node_refs"])
            operation["adapter"] = _gn_set_modifier_input_value(
                modifier, operation["actual_identifier"], decoded,
            )

        if _commit_guard is not None:
            _commit_guard()

        committed_snapshot = _gn_export_tree(working, "all")
        if committed_snapshot["revision"] != candidate_snapshot["revision"]:
            raise RuntimeError("Committed tree revision differs from verified working revision")
        if any(
            (_gn_user_handle(record) != handle)
            for record, handle in selected_users
        ):
            raise RuntimeError("A committed user could not be re-resolved")
        for record, handle in selected_users:
            assigned_tree = handle.node_group if record["kind"] == "MODIFIER" else handle.node_tree
            if assigned_tree != working:
                raise RuntimeError(f"User was not committed to working tree: {record['name']}")

        backup = None
        if not selected_users:
            working.use_fake_user = True
        if policy != "single_user_copy":
            if keep_backup:
                tree.use_fake_user = True
                backup = {"kept": True, "tree_name": tree.name}
            else:
                backup = {"kept": False, "tree_name": None}
                bpy.data.node_groups.remove(tree)

        warnings = [
            item for item in validation["diagnostics"] if item["severity"] == "warning"
        ]
        return {
            "schema": "blender-geometry-nodes-patch-application/1",
            "status": "applied",
            "applied": True,
            "mutated": True,
            "tree_name": working.name,
            "previous_tree_name": original_name,
            "base_revision": validation["current_revision"],
            "new_revision": committed_snapshot["revision"],
            # Stable alias: "the tree's revision as this response was produced",
            # whatever the status. Chaining patches otherwise means branching on
            # status to pick between new_revision and current_revision, and the
            # obvious result["revision"] silently reads None.
            "revision": committed_snapshot["revision"],
            "shared_tree_policy": policy,
            "users_reassigned": [record for record, _handle in selected_users],
            "backup": backup,
            "created_nodes": applied["created_nodes"],
            "created_interface_sockets": applied["created_interface"],
            "modifier_input_adapters": [
                {
                    "object": item["object"],
                    "modifier": item["modifier"],
                    "socket": item["actual_identifier"],
                    "adapter": item.get("adapter"),
                }
                for item in applied["deferred_modifier_inputs"]
            ],
            "operations": applied["operation_results"],
            "semantic_diff": validation["semantic_diff"],
            "actual_diff": _gn_actual_snapshot_diff(original_snapshot, committed_snapshot),
            "warnings": warnings,
            "verification": {
                "node_count": committed_snapshot["stats"]["node_count"],
                "link_count": committed_snapshot["stats"]["link_count"],
                "interface_item_count": committed_snapshot["stats"]["interface_item_count"],
            },
        }
    except Exception as exc:
        rollback_errors = []
        for record, handle in selected_users:
            try:
                _gn_assign_user(handle, record["kind"], tree)
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore user {record['name']}: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        if renamed_original:
            try:
                working.name = f".{original_name}.MCP Rollback"
                tree.name = original_name
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore tree names: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        tree.use_fake_user = original_fake_user
        for modifier, state in modifier_states.items():
            rollback_errors.extend(
                f"restore modifier {modifier.name}: {message}"
                for message in _gn_restore_modifier_state(modifier, state)
            )
        for record, handle in selected_users:
            assigned_tree = handle.node_group if record["kind"] == "MODIFIER" else handle.node_tree
            if assigned_tree != tree:
                rollback_errors.append(f"user still points to working tree: {record['name']}")
        if working.name in bpy.data.node_groups:
            if working.users == 0:
                bpy.data.node_groups.remove(working)
            else:
                rollback_errors.append(
                    f"working tree still has {working.users} users and could not be removed"
                )
        rollback_diagnostics = [_gn_patch_diagnostic(
            "error", "transaction_rolled_back", "", f"{type(exc).__name__}: {exc}",
        )]
        rollback_diagnostics.extend(
            _gn_patch_diagnostic("error", "rollback_incomplete", "", message)
            for message in rollback_errors
        )
        rolled_back_revision = _gn_export_tree(tree, "all")["revision"]
        return {
            "schema": "blender-geometry-nodes-patch-application/1",
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "applied": False,
            "mutated": bool(rollback_errors),
            "tree_name": original_name,
            "base_revision": patch.get("base_revision"),
            "current_revision": rolled_back_revision,
            "revision": rolled_back_revision,
            "diagnostics": rollback_diagnostics,
            "plan": validation["plan"],
        }
