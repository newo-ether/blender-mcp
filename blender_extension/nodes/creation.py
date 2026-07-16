"""Transactional bootstrap helpers for structured node automation."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic
from .constants import (
    GEOMETRY_NODES_MODIFIER_SCHEMA,
    NODE_GROUP_CREATION_SCHEMA,
    NODE_TREE_TYPES,
)
from .serialization import _gn_export_tree
from .targets import (
    _node_export_target,
    _node_id_editable,
    _node_id_library,
    _node_resolve_tree_ref,
    _node_tree_ref,
)


def _node_group_result(status, created, mutated, tree=None, diagnostics=None):
    tree_type = tree.bl_idname if tree is not None else None
    tree_ref = (
        _node_tree_ref(tree_type, "NODE_GROUP", tree.name)
        if tree is not None else None
    )
    result = {
        "schema": NODE_GROUP_CREATION_SCHEMA,
        "status": status,
        "created": bool(created),
        "mutated": bool(mutated),
        "tree_type": tree_type,
        "tree_name": tree.name if tree is not None else None,
        "tree_ref": tree_ref,
        "patch_kind": (
            "geometry_nodes" if tree_type == "GeometryNodeTree" else "node_tree"
        ) if tree_type else None,
        "revision": None,
        "stats": None,
        "diagnostics": list(diagnostics or ()),
    }
    if tree is None:
        return result
    if tree_type == "GeometryNodeTree":
        snapshot = _gn_export_tree(tree, "operations")
    else:
        snapshot = _node_export_target(
            _node_resolve_tree_ref(tree_ref), "operations"
        )
    result["revision"] = snapshot["revision"]
    result["stats"] = {
        key: snapshot["stats"][key]
        for key in ("node_count", "link_count", "interface_item_count")
    }
    return result


def _node_create_group(
    name,
    tree_type,
    geometry_is_modifier=False,
    description="",
    reuse_existing=False,
):
    """Create one empty local node group and return its first patch revision."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    name = name.strip()
    if len(name) > 1024:
        raise ValueError("name must not exceed 1024 characters")
    if tree_type not in NODE_TREE_TYPES:
        choices = ", ".join(sorted(NODE_TREE_TYPES))
        raise ValueError(f"tree_type must be one of: {choices}")
    if geometry_is_modifier and tree_type != "GeometryNodeTree":
        raise ValueError("geometry_is_modifier is only valid for GeometryNodeTree")
    if not isinstance(description, str):
        raise ValueError("description must be a string")

    existing = bpy.data.node_groups.get(name)
    if existing is not None:
        if not reuse_existing:
            return _node_group_result(
                "rejected", False, False,
                diagnostics=[_gn_patch_diagnostic(
                    "error", "node_group_exists", "/name",
                    f"Node group already exists: {name}",
                )],
            )
        if existing.bl_idname != tree_type:
            return _node_group_result(
                "rejected", False, False,
                diagnostics=[_gn_patch_diagnostic(
                    "error", "node_group_type_mismatch", "/tree_type",
                    f"Existing node group is {existing.bl_idname}, not {tree_type}",
                )],
            )
        if not _node_id_editable(existing) or _node_id_library(existing) is not None:
            return _node_group_result(
                "rejected", False, False,
                diagnostics=[_gn_patch_diagnostic(
                    "error", "node_group_not_local", "/name",
                    "Existing node group is linked or read-only",
                )],
            )
        if tree_type == "GeometryNodeTree" and bool(
            getattr(existing, "is_modifier", False)
        ) != bool(geometry_is_modifier):
            return _node_group_result(
                "rejected", False, False,
                diagnostics=[_gn_patch_diagnostic(
                    "error", "geometry_usage_mismatch", "/geometry_is_modifier",
                    "Existing Geometry Node group has a different modifier usage flag",
                )],
            )
        return _node_group_result("existing", False, False, tree=existing)

    created_tree = None
    try:
        created_tree = bpy.data.node_groups.new(name, tree_type)
        if created_tree.name != name:
            raise RuntimeError(
                f"Blender created {created_tree.name!r} instead of exact name {name!r}"
            )
        if description and hasattr(created_tree, "description"):
            created_tree.description = description
        if tree_type == "GeometryNodeTree" and hasattr(created_tree, "is_modifier"):
            created_tree.is_modifier = bool(geometry_is_modifier)
        return _node_group_result("created", True, True, tree=created_tree)
    except Exception:
        if created_tree is not None and created_tree.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(created_tree, do_unlink=True)
        raise


def _modifier_record(modifier):
    group = modifier.node_group
    return {
        "name": modifier.name,
        "type": modifier.type,
        "node_group": group.name if group is not None else None,
    }


def _modifier_result(
    status,
    *,
    created_object=False,
    created_modifier=False,
    mutated=False,
    obj=None,
    modifier=None,
    tree=None,
    diagnostics=None,
):
    return {
        "schema": GEOMETRY_NODES_MODIFIER_SCHEMA,
        "status": status,
        "created_object": bool(created_object),
        "created_modifier": bool(created_modifier),
        "mutated": bool(mutated),
        "object": ({
            "name": obj.name,
            "type": obj.type,
            "library": _node_id_library(obj),
            "editable": _node_id_editable(obj),
            "location": [float(value) for value in obj.location],
        } if obj is not None else None),
        "modifier": _modifier_record(modifier) if modifier is not None else None,
        "tree_name": tree.name if tree is not None else None,
        "revision": (
            _gn_export_tree(tree, "operations")["revision"]
            if tree is not None else None
        ),
        "diagnostics": list(diagnostics or ()),
    }


def _gn_ensure_modifier(
    object_name,
    node_group_name,
    modifier_name="GeometryNodes",
    create_object_if_missing=False,
    create_modifier_if_missing=False,
    assign_if_different=False,
    location=None,
):
    """Inspect or explicitly create a Geometry Nodes host and modifier."""
    for field_name, value in (
        ("object_name", object_name),
        ("node_group_name", node_group_name),
        ("modifier_name", modifier_name),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
    object_name = object_name.strip()
    node_group_name = node_group_name.strip()
    modifier_name = modifier_name.strip()
    if any(len(value) > 1024 for value in (
        object_name, node_group_name, modifier_name
    )):
        raise ValueError("names must not exceed 1024 characters")
    if create_object_if_missing and not create_modifier_if_missing:
        raise ValueError(
            "create_object_if_missing requires create_modifier_if_missing=true"
        )
    location = [0.0, 0.0, 0.0] if location is None else location
    if (
        not isinstance(location, (list, tuple))
        or len(location) != 3
        or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in location
        )
    ):
        raise ValueError("location must contain exactly three numeric values")

    tree = bpy.data.node_groups.get(node_group_name)
    if tree is None or tree.bl_idname != "GeometryNodeTree":
        return _modifier_result(
            "rejected",
            diagnostics=[_gn_patch_diagnostic(
                "error", "geometry_node_group_not_found", "/node_group_name",
                f"Geometry Node group not found: {node_group_name}",
            )],
        )

    obj = bpy.data.objects.get(object_name)
    modifier = obj.modifiers.get(modifier_name) if obj is not None else None
    if obj is None and not create_object_if_missing:
        return _modifier_result(
            "missing", tree=tree,
            diagnostics=[_gn_patch_diagnostic(
                "warning", "object_missing", "/object_name",
                f"Object not found: {object_name}",
            )],
        )
    if obj is not None and (
        not _node_id_editable(obj) or _node_id_library(obj) is not None
    ):
        return _modifier_result(
            "rejected", obj=obj, tree=tree,
            diagnostics=[_gn_patch_diagnostic(
                "error", "object_not_editable", "/object_name",
                "Object is linked or read-only",
            )],
        )
    if modifier is not None and modifier.type != "NODES":
        return _modifier_result(
            "rejected", obj=obj, modifier=modifier, tree=tree,
            diagnostics=[_gn_patch_diagnostic(
                "error", "modifier_type_mismatch", "/modifier_name",
                f"Modifier {modifier_name!r} exists but is not a Geometry Nodes modifier",
            )],
        )
    if obj is not None and modifier is None and not create_modifier_if_missing:
        return _modifier_result(
            "missing", obj=obj, tree=tree,
            diagnostics=[_gn_patch_diagnostic(
                "warning", "modifier_missing", "/modifier_name",
                f"Geometry Nodes modifier not found: {modifier_name}",
            )],
        )
    if (
        modifier is not None
        and modifier.node_group != tree
        and not assign_if_different
    ):
        current_group = (
            repr(modifier.node_group.name)
            if modifier.node_group is not None else "no node group"
        )
        return _modifier_result(
            "rejected", obj=obj, modifier=modifier, tree=tree,
            diagnostics=[_gn_patch_diagnostic(
                "error", "modifier_group_mismatch", "/node_group_name",
                f"Modifier currently uses {current_group}; set "
                "assign_if_different=true to replace it",
            )],
        )

    created_obj = False
    created_mesh = None
    created_modifier = False
    previous_tree = modifier.node_group if modifier is not None else None
    assigned = False
    try:
        if obj is None:
            created_mesh = bpy.data.meshes.new(f"{object_name} Mesh")
            obj = bpy.data.objects.new(object_name, created_mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj.location = location
            created_obj = True
        if modifier is None:
            modifier = obj.modifiers.new(modifier_name, "NODES")
            created_modifier = True
        if modifier.node_group != tree:
            modifier.node_group = tree
            assigned = True
        if modifier.node_group != tree:
            raise RuntimeError("Geometry Nodes modifier did not retain the requested group")

        status = "created" if (created_obj or created_modifier) else (
            "assigned" if previous_tree is None and assigned else (
                "reassigned" if assigned else "existing"
            )
        )
        return _modifier_result(
            status,
            created_object=created_obj,
            created_modifier=created_modifier,
            mutated=created_obj or created_modifier or assigned,
            obj=obj,
            modifier=modifier,
            tree=tree,
        )
    except Exception as exc:
        rollback_errors = []
        if modifier is not None:
            try:
                if created_modifier:
                    obj.modifiers.remove(modifier)
                    modifier = None
                elif assigned:
                    modifier.node_group = previous_tree
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"restore modifier: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        if created_obj and obj is not None:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                obj = None
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"remove object: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        if created_mesh is not None and created_mesh.name in bpy.data.meshes:
            try:
                bpy.data.meshes.remove(created_mesh)
            except (AttributeError, RuntimeError) as rollback_exc:
                rollback_errors.append(
                    f"remove mesh: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        diagnostics = [_gn_patch_diagnostic(
            "error", "geometry_nodes_modifier_rolled_back", "",
            f"{type(exc).__name__}: {exc}",
        )]
        diagnostics.extend(
            _gn_patch_diagnostic("error", "rollback_incomplete", "", message)
            for message in rollback_errors
        )
        return _modifier_result(
            "rollback_failed" if rollback_errors else "rolled_back",
            mutated=bool(rollback_errors),
            obj=obj,
            modifier=modifier,
            tree=tree,
            diagnostics=diagnostics,
        )
