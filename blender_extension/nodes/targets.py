"""Node-tree owner resolution, targeting, and capability records."""

from __future__ import annotations

import json

import bpy

from .constants import (
    NODE_TREE_MAX_MUTATION_NODES,
    NODE_TREE_MAX_RESPONSE_BYTES,
    NODE_TREE_MAX_VALIDATION_SECONDS,
    NODE_TREE_OWNER_KINDS,
    NODE_TREE_SNAPSHOT_SCHEMA,
    NODE_TREE_TYPES,
)
from .diagnostics import _node_graph_diagnostics
from .serialization import _gn_canonical_json, _node_export_tree


def _gn_geometry_trees():
    return sorted(
        (tree for tree in bpy.data.node_groups if tree.bl_idname == "GeometryNodeTree"),
        key=lambda item: item.name,
    )

def _node_domain(tree_type):
    return {
        "GeometryNodeTree": "geometry",
        "ShaderNodeTree": "shader",
        "CompositorNodeTree": "compositor",
    }[tree_type]

def _node_id_library(value):
    library = getattr(value, "library", None)
    return library.filepath if library else None

def _node_id_editable(value):
    library = getattr(value, "library", None)
    return bool(getattr(value, "is_editable", library is None))

def _node_owner_record(kind, owner):
    return {
        "kind": kind,
        "name": owner.name,
        "id_type": owner.bl_rna.identifier,
        "library": _node_id_library(owner),
        "editable": _node_id_editable(owner),
        "user_count": int(owner.users),
    }

def _node_tree_ref(tree_type, owner_kind, owner_name):
    return {
        "tree_type": tree_type,
        "owner": {"kind": owner_kind, "name": owner_name},
    }

def _node_normalize_tree_ref(tree_ref):
    if not isinstance(tree_ref, dict):
        raise ValueError("tree_ref must be an object")
    tree_type = tree_ref.get("tree_type")
    if tree_type not in NODE_TREE_TYPES:
        choices = ", ".join(sorted(NODE_TREE_TYPES))
        raise ValueError(f"tree_ref.tree_type must be one of: {choices}")
    owner = tree_ref.get("owner")
    if not isinstance(owner, dict):
        raise ValueError("tree_ref.owner must be an object")
    owner_kind = str(owner.get("kind", "")).strip().upper()
    if owner_kind not in NODE_TREE_OWNER_KINDS:
        choices = ", ".join(sorted(NODE_TREE_OWNER_KINDS))
        raise ValueError(f"tree_ref.owner.kind must be one of: {choices}")
    owner_name = owner.get("name")
    if not isinstance(owner_name, str) or not owner_name.strip():
        raise ValueError("tree_ref.owner.name must be a non-empty string")
    owner_name = owner_name.strip()
    if len(owner_name) > 1024:
        raise ValueError("tree_ref.owner.name must not exceed 1024 characters")
    allowed = {
        "GeometryNodeTree": {"NODE_GROUP"},
        "ShaderNodeTree": {"MATERIAL", "WORLD", "LIGHT", "NODE_GROUP"},
        "CompositorNodeTree": {"SCENE", "NODE_GROUP"},
    }[tree_type]
    if owner_kind not in allowed:
        raise ValueError(
            f"{owner_kind} cannot own a {tree_type}; expected one of: "
            + ", ".join(sorted(allowed))
        )
    return _node_tree_ref(tree_type, owner_kind, owner_name)

def _node_scene_tree(scene):
    if hasattr(scene, "compositing_node_group"):
        return scene.compositing_node_group, "scene_compositing_node_group"
    if not bool(getattr(scene, "use_nodes", False)):
        return None, "scene_embedded_node_tree"
    return getattr(scene, "node_tree", None), "scene_embedded_node_tree"

def _node_target(tree_type, owner_kind, owner, tree, adapter):
    if tree is None:
        raise ValueError(
            f"{owner_kind} {owner.name!r} has no enabled {tree_type}"
        )
    if tree.bl_idname != tree_type:
        raise ValueError(
            f"{owner_kind} {owner.name!r} owns {tree.bl_idname}, not {tree_type}"
        )
    return {
        "tree_type": tree_type,
        "domain": _node_domain(tree_type),
        "tree_ref": _node_tree_ref(tree_type, owner_kind, owner.name),
        "owner_kind": owner_kind,
        "owner_id": owner,
        "owner": _node_owner_record(owner_kind, owner),
        "tree": tree,
        "adapter": adapter,
    }

def _node_resolve_tree_ref(tree_ref):
    canonical = _node_normalize_tree_ref(tree_ref)
    tree_type = canonical["tree_type"]
    owner_kind = canonical["owner"]["kind"]
    owner_name = canonical["owner"]["name"]
    if owner_kind == "MATERIAL":
        owner = bpy.data.materials.get(owner_name)
        tree = getattr(owner, "node_tree", None) if owner else None
        adapter = "embedded_shader_owner"
    elif owner_kind == "WORLD":
        owner = bpy.data.worlds.get(owner_name)
        tree = getattr(owner, "node_tree", None) if owner else None
        adapter = "embedded_shader_owner"
    elif owner_kind == "LIGHT":
        owner = bpy.data.lights.get(owner_name)
        tree = getattr(owner, "node_tree", None) if owner else None
        adapter = "embedded_shader_owner"
    elif owner_kind == "SCENE":
        owner = bpy.data.scenes.get(owner_name)
        tree, adapter = _node_scene_tree(owner) if owner else (None, None)
    else:
        owner = bpy.data.node_groups.get(owner_name)
        tree = owner
        adapter = "standalone_node_group"
    if owner is None:
        raise ValueError(f"{owner_kind} owner not found: {owner_name}")
    return _node_target(tree_type, owner_kind, owner, tree, adapter)

def _node_iter_targets():
    targets = []
    for material in sorted(bpy.data.materials, key=lambda item: item.name):
        if material.node_tree is not None:
            targets.append(_node_target(
                "ShaderNodeTree", "MATERIAL", material, material.node_tree,
                "embedded_shader_owner",
            ))
    for world in sorted(bpy.data.worlds, key=lambda item: item.name):
        if world.node_tree is not None:
            targets.append(_node_target(
                "ShaderNodeTree", "WORLD", world, world.node_tree,
                "embedded_shader_owner",
            ))
    for light in sorted(bpy.data.lights, key=lambda item: item.name):
        if light.node_tree is not None:
            targets.append(_node_target(
                "ShaderNodeTree", "LIGHT", light, light.node_tree,
                "embedded_shader_owner",
            ))
    for scene in sorted(bpy.data.scenes, key=lambda item: item.name):
        tree, adapter = _node_scene_tree(scene)
        if tree is not None:
            targets.append(_node_target(
                "CompositorNodeTree", "SCENE", scene, tree, adapter,
            ))
    for tree in sorted(bpy.data.node_groups, key=lambda item: item.name):
        if tree.bl_idname in NODE_TREE_TYPES:
            targets.append(_node_target(
                tree.bl_idname, "NODE_GROUP", tree, tree,
                "standalone_node_group",
            ))
    return sorted(
        targets,
        key=lambda item: (
            item["tree_type"], item["owner_kind"], item["owner_id"].name,
        ),
    )

def _node_id_users(value):
    try:
        user_map = bpy.data.user_map(subset={value})
        direct_users = user_map.get(value, set())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        direct_users = set()
    return [
        {
            "kind": "ID",
            "id_type": user.bl_rna.identifier,
            "name": user.name,
            "library": _node_id_library(user),
        }
        for user in sorted(
            direct_users,
            key=lambda item: (item.bl_rna.identifier, item.name),
        )
    ]

def _node_target_users(target):
    records = _node_id_users(target["owner_id"])
    if target["owner_kind"] == "NODE_GROUP":
        tree = target["tree"]
        for parent in _node_iter_targets():
            for node in sorted(parent["tree"].nodes, key=lambda item: item.name):
                if getattr(node, "node_tree", None) == tree:
                    records.append({
                        "kind": "GROUP_NODE",
                        "tree_ref": parent["tree_ref"],
                        "node": node.name,
                    })
        if target["tree_type"] == "GeometryNodeTree":
            for obj in sorted(bpy.data.objects, key=lambda item: item.name):
                for modifier in obj.modifiers:
                    if modifier.type == "NODES" and modifier.node_group == tree:
                        records.append({
                            "kind": "MODIFIER",
                            "object": obj.name,
                            "modifier": modifier.name,
                        })
    unique = {_gn_canonical_json(record): record for record in records}
    return [unique[key] for key in sorted(unique)]

def _node_target_capabilities(target):
    tree = target["tree"]
    editable = target["owner"]["editable"] and _node_id_editable(tree)
    is_override = (
        getattr(target["owner_id"], "override_library", None) is not None
        or getattr(tree, "override_library", None) is not None
    )
    if not editable:
        mutation_reason = "linked_or_read_only"
        validate_supported = False
        apply_supported = False
    elif is_override:
        mutation_reason = "library_override_apply_not_supported"
        validate_supported = True
        apply_supported = False
    elif target["domain"] in {"shader", "compositor"}:
        mutation_reason = "available"
        validate_supported = True
        apply_supported = True
    else:
        mutation_reason = "geometry_uses_v1_mutation_tools"
        validate_supported = False
        apply_supported = False
    return {
        "read": True,
        "index": True,
        "export": True,
        "schema": True,
        "validate": validate_supported,
        "apply": apply_supported,
        "editable": editable,
        "mutation_reason": mutation_reason,
        "transaction_adapter": target["adapter"],
        "interface": getattr(tree, "interface", None) is not None,
        "limits": {
            "max_full_response_bytes": NODE_TREE_MAX_RESPONSE_BYTES,
            "max_mutation_nodes": NODE_TREE_MAX_MUTATION_NODES,
            "max_validation_seconds": NODE_TREE_MAX_VALIDATION_SECONDS,
            "max_neighbor_depth": 5,
            "max_index_page": 500,
            "max_patch_operations": 500,
            "max_patch_bytes": 2 * 1024 * 1024,
        },
    }

def _node_export_target(target, view="semantic", node_names=None, neighbor_depth=0):
    snapshot = _node_export_tree(
        target["tree"],
        view,
        node_names,
        neighbor_depth,
        schema=NODE_TREE_SNAPSHOT_SCHEMA,
        users=_node_target_users(target),
        tree_ref=target["tree_ref"],
        owner=target["owner"],
        capabilities=_node_target_capabilities(target),
    )
    snapshot["diagnostics"] = _node_graph_diagnostics(target["tree"])
    for _iteration in range(3):
        size = len(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        if snapshot["stats"]["json_bytes"] == size:
            break
        snapshot["stats"]["json_bytes"] = size
    return snapshot
