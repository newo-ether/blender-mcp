# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025

import re
import bpy
import mathutils
import json
import threading
import socket
import time
import requests
import tempfile
import traceback
import os
import shutil
import zipfile
from bpy.props import IntProperty, BoolProperty, EnumProperty, StringProperty, FloatProperty
import io
from datetime import datetime
import hashlib, hmac, base64
import os.path as osp
from contextlib import redirect_stdout, suppress

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (1, 8, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

# Blender Extensions are imported below ``bl_ext.<repository>.<extension>``.
# ``__package__`` is therefore the stable identifier for extension preferences,
# while legacy single-file installs need to fall back to ``__name__``.
ADDON_MODULE_ID = __package__ or __name__

RODIN_FREE_TRIAL_KEY = "vibecoding"

# Add User-Agent as required by Poly Haven API
REQ_HEADERS = requests.utils.default_headers()
REQ_HEADERS.update({"User-Agent": "blender-mcp"})

def get_blendermcp_addon_preferences(context=None):
    """Get add-on preferences object if available."""
    if context is None:
        context = bpy.context
    addon = context.preferences.addons.get(ADDON_MODULE_ID)
    return addon.preferences if addon else None

# ── Persistent preference sync ──
# Scene properties mirror AddonPreferences. On change, they sync to prefs.
# On Blender start / file load, prefs are copied back to scene.

_PREF_PROPERTY_NAMES = [
    'port', 'use_polyhaven',
    'use_hyper3d', 'hyper3d_mode', 'hyper3d_api_key',
    'use_sketchfab', 'sketchfab_api_key',
    'use_hunyuan3d', 'hunyuan3d_mode',
    'hunyuan3d_secret_id', 'hunyuan3d_secret_key',
    'hunyuan3d_api_url', 'hunyuan3d_octree_resolution',
    'hunyuan3d_num_inference_steps', 'hunyuan3d_guidance_scale',
    'hunyuan3d_texture',
]

def _make_scene_update(prop_name):
    """Factory: returns an update callback that syncs the scene value to AddonPreferences."""
    def update(self, context):
        addon_prefs = context.preferences.addons.get(ADDON_MODULE_ID)
        if addon_prefs and hasattr(addon_prefs.preferences, prop_name):
            setattr(addon_prefs.preferences, prop_name, getattr(self, f'blendermcp_{prop_name}'))
    return update

def sync_prefs_to_scene():
    """Copy all persistent AddonPreferences values to the current scene."""
    addon_prefs = bpy.context.preferences.addons.get(ADDON_MODULE_ID)
    if not addon_prefs:
        return
    prefs = addon_prefs.preferences
    scene = bpy.context.scene
    for name in _PREF_PROPERTY_NAMES:
        pref_val = getattr(prefs, name, None)
        scene_attr = f'blendermcp_{name}'
        if hasattr(scene, scene_attr):
            try:
                setattr(scene, scene_attr, pref_val)
            except (AttributeError, TypeError):
                pass

def _auto_connect_if_enabled():
    """Start MCP server if auto_connect is enabled and not already running."""
    addon_prefs = bpy.context.preferences.addons.get(ADDON_MODULE_ID)
    if not addon_prefs:
        return
    if not addon_prefs.preferences.auto_connect:
        return
    existing_server = getattr(bpy.types, "blendermcp_server", None)
    if existing_server and existing_server.running:
        bpy.context.scene.blendermcp_server_running = True
        return

    server = BlenderMCPServer(port=addon_prefs.preferences.port)
    bpy.types.blendermcp_server = server
    server.start()
    bpy.context.scene.blendermcp_server_running = server.running

@bpy.app.handlers.persistent
def _load_post_handler(_dummy):
    """On .blend file load: sync prefs → scene, auto-connect if enabled."""
    sync_prefs_to_scene()
    _auto_connect_if_enabled()


# ---------------------------------------------------------------------------
# Geometry Nodes read-only protocol (schema: blender-geometry-nodes/1)
# ---------------------------------------------------------------------------

GEOMETRY_NODES_SNAPSHOT_SCHEMA = "blender-geometry-nodes/1"
GEOMETRY_NODES_PATCH_SCHEMA = "blender-geometry-nodes-patch/1"
GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA = "blender-geometry-nodes-patch-validation/1"
GEOMETRY_NODES_VIEWS = {"semantic", "layout", "all"}
GEOMETRY_NODE_TYPE_SCHEMA_DETAILS = {"compact", "full"}
GEOMETRY_NODE_TYPE_CATALOG_SCHEMA = "blender-geometry-node-type-catalog/1"
BLENDER_NODE_ASSET_CATALOG_SCHEMA = "blender-node-asset-catalog/1"
NODE_TREE_SNAPSHOT_SCHEMA = "blender-node-tree/1"
NODE_TREE_INDEX_SCHEMA = "blender-node-tree-index/1"
NODE_TYPE_SCHEMA = "blender-node-type-schema/1"
NODE_TREE_TYPES = {"GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree"}
NODE_TREE_OWNER_KINDS = {"MATERIAL", "WORLD", "LIGHT", "SCENE", "NODE_GROUP"}
NODE_TREE_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
NODE_TREE_MAX_MUTATION_NODES = 10000
NODE_TREE_MAX_VALIDATION_SECONDS = 30.0

_GN_NODE_TYPE_CATALOG_CACHE = {}
_GN_ESSENTIALS_CATALOG_CACHE = {}


class _GNAssetCleanupError(RuntimeError):
    """Raised when disposable official-asset inspection cannot cleanly unwind."""

BLENDER_VERSION_CONTEXT_SCHEMA = "blender-version-context/1"


def _blender_app_text(attribute):
    """Read bpy.app build strings consistently across Blender releases."""
    value = getattr(bpy.app, attribute, None)
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = str(value).strip()
    return value or None


def _blender_version_context():
    """Return exact connected-build metadata without reading scene data."""
    version = [int(part) for part in bpy.app.version[:3]]
    version_string = str(bpy.app.version_string)
    version_cycle = str(getattr(bpy.app, "version_cycle", "unknown")).lower()
    commit_timestamp = getattr(bpy.app, "build_commit_timestamp", None)
    if not isinstance(commit_timestamp, int):
        commit_timestamp = None
    return {
        "schema": BLENDER_VERSION_CONTEXT_SCHEMA,
        "version": version,
        "version_string": version_string,
        "version_cycle": version_cycle,
        "is_prerelease": version_cycle not in {"release", "stable", "final"},
        "is_lts": "LTS" in version_string.upper(),
        "build": {
            "branch": _blender_app_text("build_branch"),
            "hash": _blender_app_text("build_hash"),
            "date": _blender_app_text("build_commit_date") or _blender_app_text("build_date"),
            "time": _blender_app_text("build_commit_time") or _blender_app_text("build_time"),
            "platform": _blender_app_text("build_platform"),
            "type": _blender_app_text("build_type"),
            "commit_timestamp": commit_timestamp,
        },
    }

_GN_NODE_PROPERTY_EXCLUDES = {
    "rna_type", "name", "label", "location", "width", "width_hidden",
    "height", "dimensions", "parent", "select", "show_options",
    "show_preview", "show_texture", "use_custom_color", "color",
    "inputs", "outputs", "internal_links", "type", "bl_idname",
}


def _node_normalize_view(view, label="Node tree"):
    normalized = str(view).strip().lower()
    if normalized not in GEOMETRY_NODES_VIEWS:
        choices = ", ".join(sorted(GEOMETRY_NODES_VIEWS))
        raise ValueError(f"Unsupported {label} view {view!r}; expected: {choices}")
    return normalized


def _gn_normalize_view(view):
    return _node_normalize_view(view, "Geometry Nodes")


def _gn_json_value(value):
    """Convert Blender RNA values without leaking pointer-based repr strings."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value:
            return {"$type": "float", "value": "nan"}
        if value == float("inf"):
            return {"$type": "float", "value": "infinity"}
        if value == float("-inf"):
            return {"$type": "float", "value": "-infinity"}
        return value
    if isinstance(value, bpy.types.Node):
        return {"$type": "NodeRef", "name": value.name}
    if isinstance(value, bpy.types.NodeSocket):
        return {
            "$type": "SocketRef",
            "node": value.node.name if value.node else None,
            "identifier": getattr(value, "identifier", "") or value.name,
        }
    if isinstance(value, bpy.types.ID):
        library = getattr(value, "library", None)
        return {
            "$type": "ID",
            "id_type": value.bl_rna.identifier,
            "name": value.name,
            "library": library.filepath if library else None,
        }
    if isinstance(value, dict):
        return {str(key): _gn_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        items = [_gn_json_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True)) if isinstance(value, set) else items
    if hasattr(value, "to_list"):
        return _gn_json_value(value.to_list())
    if hasattr(value, "__iter__") and not isinstance(value, bpy.types.bpy_struct):
        return [_gn_json_value(item) for item in value]
    raise TypeError(f"Unsupported RNA value type: {type(value).__name__}")


def _gn_socket_id(socket, direction, index):
    identifier = getattr(socket, "identifier", "") or socket.name
    return f"{direction.lower()}:{index}:{identifier}"


def _gn_socket_record(socket, direction, index, include_default=True):
    record = {
        "id": _gn_socket_id(socket, direction, index),
        "index": index,
        "name": socket.name,
        "identifier": getattr(socket, "identifier", "") or "",
        "direction": direction,
        "bl_idname": socket.bl_idname,
        "enabled": bool(socket.enabled),
        "linked": bool(socket.is_linked),
        "multi_input": bool(getattr(socket, "is_multi_input", False)),
    }
    if include_default and hasattr(socket, "default_value"):
        try:
            record["default"] = _gn_json_value(socket.default_value)
        except (AttributeError, TypeError, ValueError):
            pass
    return record


def _gn_rna_properties(value, excludes=None, include_readonly=None):
    excludes = set(excludes or ())
    include_readonly = set(include_readonly or ())
    result = {}
    for prop in value.bl_rna.properties:
        identifier = prop.identifier
        if identifier in excludes or identifier == "rna_type":
            continue
        if getattr(prop, "type", None) == "COLLECTION":
            continue
        if getattr(prop, "is_hidden", False) or getattr(prop, "is_skip_save", False):
            continue
        if getattr(prop, "is_readonly", False) and identifier not in include_readonly:
            continue
        try:
            result[identifier] = _gn_json_value(getattr(value, identifier))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue
    return result


def _gn_node_record(node, view):
    include_semantic = view in {"semantic", "all"}
    include_layout = view in {"layout", "all"}
    record = {
        "id": node.name,
        "name": node.name,
        "label": node.label,
        "bl_idname": node.bl_idname,
        "properties": {},
        "inputs": [],
        "outputs": [],
    }
    if include_semantic:
        record["properties"] = _gn_rna_properties(
            node,
            excludes=_GN_NODE_PROPERTY_EXCLUDES,
            include_readonly={"paired_input", "paired_output"},
        )
        record["inputs"] = [
            _gn_socket_record(socket, "INPUT", index)
            for index, socket in enumerate(node.inputs)
        ]
        record["outputs"] = [
            _gn_socket_record(socket, "OUTPUT", index)
            for index, socket in enumerate(node.outputs)
        ]
        annotation = node.get("blender_mcp_note")
        if isinstance(annotation, str) and annotation:
            limit = 16384
            record["annotation"] = {
                "text": annotation[:limit],
                "truncated": len(annotation) > limit,
            }
    if include_layout:
        record["layout"] = {
            "location": [float(node.location.x), float(node.location.y)],
            "width": float(node.width),
            "height": float(node.height),
            "parent": node.parent.name if node.parent else None,
        }
    return record


def _gn_interface_record(item):
    identifier = getattr(item, "identifier", "") or item.name
    parent = getattr(item, "parent", None)
    record = {
        "item_type": item.item_type,
        "identifier": identifier,
        "name": item.name,
        "parent": (getattr(parent, "identifier", "") or parent.name) if parent else None,
    }
    if item.item_type == "SOCKET":
        record.update({
            "in_out": item.in_out,
            "socket_type": item.socket_type,
            "description": getattr(item, "description", ""),
            "hide_value": bool(getattr(item, "hide_value", False)),
        })
        for property_name in (
            "default_value", "default_attribute_name", "attribute_domain",
            "default_input", "structure_type", "force_non_field",
        ):
            if hasattr(item, property_name):
                try:
                    output_name = "default" if property_name == "default_value" else property_name
                    record[output_name] = _gn_json_value(getattr(item, property_name))
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass
    else:
        record["description"] = getattr(item, "description", "")
        record["default_closed"] = bool(getattr(item, "default_closed", False))
    return record


def _gn_tree_interface(tree, include_semantic=True):
    if not include_semantic:
        return []
    interface = getattr(tree, "interface", None)
    if interface is None or not hasattr(interface, "items_tree"):
        raise RuntimeError(
            "This Blender version does not expose NodeTree.interface.items_tree; "
            "Geometry Nodes snapshots require Blender 4.2 or newer"
        )
    return [_gn_interface_record(item) for item in interface.items_tree]


def _gn_find_socket_index(node, socket, direction):
    sockets = node.outputs if direction == "OUTPUT" else node.inputs
    for index, candidate in enumerate(sockets):
        if candidate == socket:
            return index
    raise RuntimeError(f"Socket {socket.name!r} is not owned by node {node.name!r}")


def _gn_link_record(link):
    from_index = _gn_find_socket_index(link.from_node, link.from_socket, "OUTPUT")
    to_index = _gn_find_socket_index(link.to_node, link.to_socket, "INPUT")
    record = {
        "from_node": link.from_node.name,
        "from_socket": _gn_socket_id(link.from_socket, "OUTPUT", from_index),
        "to_node": link.to_node.name,
        "to_socket": _gn_socket_id(link.to_socket, "INPUT", to_index),
    }
    sort_id = getattr(link, "multi_input_sort_id", None)
    if sort_id is not None and sort_id >= 0:
        record["multi_input_sort_id"] = int(sort_id)
    return record


def _gn_tree_users(tree):
    users = []
    for obj in sorted(bpy.data.objects, key=lambda item: item.name):
        for modifier in obj.modifiers:
            if modifier.type == "NODES" and modifier.node_group == tree:
                users.append({
                    "kind": "MODIFIER",
                    "name": f"{obj.name}/{modifier.name}",
                    "object": obj.name,
                    "modifier": modifier.name,
                })
    for owner_tree in sorted(bpy.data.node_groups, key=lambda item: item.name):
        for node in sorted(owner_tree.nodes, key=lambda item: item.name):
            if getattr(node, "node_tree", None) == tree:
                users.append({
                    "kind": "GROUP_NODE",
                    "name": f"{owner_tree.name}/{node.name}",
                    "tree": owner_tree.name,
                    "node": node.name,
                })
    return users


def _gn_canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _gn_snapshot_revision(snapshot):
    tree = snapshot["tree"]
    revision_input = {
        "schema": snapshot["schema"],
        "view": snapshot["view"],
        "tree": {
            key: tree[key]
            for key in ("bl_idname", "interface", "nodes", "links")
        },
    }
    digest = hashlib.sha256(_gn_canonical_json(revision_input).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _node_export_tree(
    tree,
    view="semantic",
    node_names=None,
    neighbor_depth=0,
    *,
    schema=NODE_TREE_SNAPSHOT_SCHEMA,
    users=None,
    tree_ref=None,
    owner=None,
    capabilities=None,
    normalizer=_node_normalize_view,
    record_factory=None,
):
    """Serialize any supported NodeTree through the shared flat graph core."""
    view = normalizer(view)
    record_factory = record_factory or _node_graph_record
    include_semantic = view in {"semantic", "all"}
    library = getattr(tree, "library", None)
    try:
        neighbor_depth = int(neighbor_depth)
    except (TypeError, ValueError) as exc:
        raise ValueError("neighbor_depth must be an integer from 0 to 5") from exc
    if not 0 <= neighbor_depth <= 5:
        raise ValueError("neighbor_depth must be an integer from 0 to 5")

    ordered_nodes = sorted(tree.nodes, key=lambda item: item.name)
    view_nodes = {
        node.name: record_factory(node, view)
        for node in ordered_nodes
    }
    graph_links = sorted(
        (_gn_link_record(link) for link in tree.links),
        key=lambda link: (
            link["from_node"], link["from_socket"],
            link["to_node"], link["to_socket"],
            link.get("multi_input_sort_id", -1),
        ),
    )
    view_links = graph_links if include_semantic else []
    full_interface = _gn_tree_interface(tree, True)
    view_interface = full_interface if include_semantic else []

    requested_nodes = sorted(set(node_names or ()))
    missing_nodes = [name for name in requested_nodes if name not in view_nodes]
    if missing_nodes:
        raise ValueError(f"Nodes not found in {tree.name!r}: {', '.join(missing_nodes)}")

    if requested_nodes:
        included_nodes = set(requested_nodes)
        for _iteration in range(neighbor_depth):
            expanded = set(included_nodes)
            for link in graph_links:
                if link["from_node"] in included_nodes or link["to_node"] in included_nodes:
                    expanded.add(link["from_node"])
                    expanded.add(link["to_node"])
            included_nodes = expanded
        nodes = {
            name: view_nodes[name]
            for name in sorted(included_nodes)
        }
        links = [
            link for link in view_links
            if link["from_node"] in included_nodes and link["to_node"] in included_nodes
        ]
        scope = {
            "kind": "subgraph",
            "requested_nodes": requested_nodes,
            "neighbor_depth": neighbor_depth,
            "included_nodes": sorted(included_nodes),
        }
    else:
        nodes = view_nodes
        links = view_links
        scope = {
            "kind": "full",
            "requested_nodes": [],
            "neighbor_depth": 0,
            "included_nodes": sorted(view_nodes),
        }

    editable = bool(getattr(tree, "is_editable", library is None))
    tree_identity = {
        "name": tree.name,
        "bl_idname": tree.bl_idname,
        "editable": editable,
        "library": library.filepath if library else None,
    }
    snapshot = {
        "schema": schema,
        "blender_version": list(bpy.app.version[:3]),
        "view": view,
        "tree": {
            **tree_identity,
            "interface": view_interface,
            "nodes": nodes,
            "links": links,
        },
        "scope": scope,
        "users": list(users or ()),
        "stats": {
            "node_count": len(nodes),
            "link_count": len(links),
            "interface_item_count": len(view_interface),
            "total_node_count": len(view_nodes),
            "total_link_count": len(graph_links),
            "json_bytes": 0,
        },
    }
    revision_nodes = (
        view_nodes
        if view == "all"
        else {node.name: record_factory(node, "all") for node in ordered_nodes}
    )
    revision_snapshot = {
        "schema": schema,
        "view": "all",
        "tree": {
            **tree_identity,
            "interface": full_interface,
            "nodes": revision_nodes,
            "links": graph_links,
        },
    }
    if tree_ref is not None:
        snapshot["tree_ref"] = tree_ref
    if owner is not None:
        snapshot["owner"] = owner
    if capabilities is not None:
        snapshot["capabilities"] = capabilities
    snapshot["revision"] = _gn_snapshot_revision(revision_snapshot)
    scope["content_revision"] = _gn_snapshot_revision(snapshot)
    for _iteration in range(3):
        size = len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if snapshot["stats"]["json_bytes"] == size:
            break
        snapshot["stats"]["json_bytes"] = size
    return snapshot


def _gn_export_tree(tree, view="semantic", node_names=None, neighbor_depth=0):
    """Compatibility facade preserving the Geometry Nodes v1 envelope."""
    return _node_export_tree(
        tree,
        view,
        node_names,
        neighbor_depth,
        schema=GEOMETRY_NODES_SNAPSHOT_SCHEMA,
        users=_gn_tree_users(tree),
        normalizer=_gn_normalize_view,
        record_factory=_gn_node_record,
    )


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
        apply_supported = False
    elif is_override:
        mutation_reason = "library_override_apply_not_supported"
        apply_supported = False
    elif target["domain"] in {"shader", "compositor"}:
        mutation_reason = "available"
        apply_supported = True
    else:
        mutation_reason = "geometry_uses_v1_apply_tool"
        apply_supported = False
    return {
        "read": True,
        "index": True,
        "export": True,
        "schema": True,
        "validate": editable,
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
    return _node_export_tree(
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


def _node_tree_index(target, query="", offset=0, limit=100):
    try:
        offset = int(offset)
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset and limit must be integers") from exc
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be from 1 to 500")
    query_value = "" if query is None else str(query)
    query_text = query_value.strip().casefold()
    tree = target["tree"]
    matches = [
        node for node in sorted(tree.nodes, key=lambda item: item.name)
        if not query_text or query_text in " ".join(
            (node.name, node.label, node.bl_idname, node.bl_label)
        ).casefold()
    ]
    page = matches[offset:offset + limit]
    next_offset = offset + len(page)
    snapshot = _node_export_target(target, "all")
    return {
        "schema": NODE_TREE_INDEX_SCHEMA,
        "blender_version": list(bpy.app.version[:3]),
        "tree_ref": target["tree_ref"],
        "owner": target["owner"],
        "capabilities": _node_target_capabilities(target),
        "revision": snapshot["revision"],
        "query": query_value,
        "offset": offset,
        "limit": limit,
        "total_nodes": len(tree.nodes),
        "total_matches": len(matches),
        "next_offset": next_offset if next_offset < len(matches) else None,
        "nodes": [
            {
                "name": node.name,
                "label": node.label,
                "bl_idname": node.bl_idname,
                "bl_label": node.bl_label,
                "parent": node.parent.name if node.parent else None,
                "input_count": len(node.inputs),
                "output_count": len(node.outputs),
            }
            for node in page
        ],
    }


def _gn_actual_snapshot_diff(before, after):
    before_nodes = before["tree"]["nodes"]
    after_nodes = after["tree"]["nodes"]
    before_node_names = set(before_nodes)
    after_node_names = set(after_nodes)
    shared_node_names = before_node_names & after_node_names

    before_links = {_gn_canonical_json(link): link for link in before["tree"]["links"]}
    after_links = {_gn_canonical_json(link): link for link in after["tree"]["links"]}
    before_interface = {
        item["identifier"]: item for item in before["tree"]["interface"]
    }
    after_interface = {
        item["identifier"]: item for item in after["tree"]["interface"]
    }
    shared_interface = set(before_interface) & set(after_interface)

    result = {
        "nodes_added": sorted(after_node_names - before_node_names),
        "nodes_removed": sorted(before_node_names - after_node_names),
        "nodes_changed": sorted(
            name for name in shared_node_names
            if before_nodes[name] != after_nodes[name]
        ),
        "links_added": [after_links[key] for key in sorted(set(after_links) - set(before_links))],
        "links_removed": [before_links[key] for key in sorted(set(before_links) - set(after_links))],
        "interface_added": sorted(set(after_interface) - set(before_interface)),
        "interface_removed": sorted(set(before_interface) - set(after_interface)),
        "interface_changed": sorted(
            identifier for identifier in shared_interface
            if before_interface[identifier] != after_interface[identifier]
        ),
    }
    result["summary"] = {
        key: len(value) for key, value in result.items()
    }
    return result


def _gn_property_schema(owner, prop):
    record = {
        "identifier": prop.identifier,
        "type": prop.type,
        "readonly": bool(getattr(prop, "is_readonly", False)),
        "array_length": int(getattr(prop, "array_length", 0)),
    }
    for source, destination in (
        ("name", "name"), ("description", "description"),
        ("hard_min", "min"), ("hard_max", "max"),
    ):
        if hasattr(prop, source):
            try:
                record[destination] = _gn_json_value(getattr(prop, source))
            except (TypeError, ValueError):
                pass
    try:
        record["value"] = _gn_json_value(getattr(owner, prop.identifier))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    if prop.type == "ENUM":
        try:
            record["enum_items"] = [
                {"identifier": item.identifier, "name": item.name, "description": item.description}
                for item in prop.enum_items
            ]
        except (AttributeError, TypeError, RuntimeError):
            pass
    return record


def _gn_node_owned_properties(node):
    """Return RNA properties declared by the concrete node type only."""
    base = getattr(node.bl_rna, "base", None)
    base_identifiers = {
        prop.identifier for prop in base.properties
    } if base is not None else set()
    return [
        prop for prop in node.bl_rna.properties
        if prop.identifier not in base_identifiers and prop.identifier != "rna_type"
    ]


def _gn_dynamic_collection_schema(owner, prop, limit=50):
    """Describe dynamic node-owned item collections without inherited RNA."""
    record = {
        "identifier": prop.identifier,
        "type": "COLLECTION",
        "readonly": bool(getattr(prop, "is_readonly", False)),
        "item_rna_type": getattr(getattr(prop, "fixed_type", None), "identifier", None),
        "count": 0,
        "items": [],
        "truncated": False,
    }
    try:
        collection = getattr(owner, prop.identifier)
        record["count"] = len(collection)
        for item in list(collection)[:limit]:
            values = {}
            for item_prop in item.bl_rna.properties:
                identifier = item_prop.identifier
                if identifier == "rna_type" or item_prop.type == "COLLECTION":
                    continue
                if getattr(item_prop, "is_hidden", False):
                    continue
                try:
                    values[identifier] = _gn_json_value(getattr(item, identifier))
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    continue
            record["items"].append({
                "rna_type": item.bl_rna.identifier,
                "values": values,
            })
        record["truncated"] = record["count"] > limit
    except (AttributeError, TypeError, ValueError, RuntimeError):
        record["unavailable"] = True
    return record


def _gn_socket_type_schema(socket, direction, index):
    record = _gn_socket_record(socket, direction, index)
    for source, destination in (
        ("description", "description"),
        ("hide_value", "hide_value"),
        ("is_unavailable", "unavailable"),
        ("default_attribute_name", "default_attribute_name"),
    ):
        if hasattr(socket, source):
            try:
                record[destination] = _gn_json_value(getattr(socket, source))
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
    default_prop = _gn_rna_property(socket, "default_value")
    if default_prop is not None:
        for source, destination in (("hard_min", "min"), ("hard_max", "max")):
            if hasattr(default_prop, source):
                try:
                    record[destination] = _gn_json_value(getattr(default_prop, source))
                except (TypeError, ValueError):
                    pass
    return record


def _node_special_structure_schema(node):
    structures = []
    if hasattr(node, "color_ramp"):
        ramp = node.color_ramp
        structures.append({
            "identifier": "color_ramp",
            "type": "COLOR_RAMP",
            "interpolation": ramp.interpolation,
            "color_mode": ramp.color_mode,
            "hue_interpolation": ramp.hue_interpolation,
            "elements": [
                {
                    "index": index,
                    "position": float(element.position),
                    "color": _gn_json_value(element.color),
                }
                for index, element in enumerate(ramp.elements)
            ],
        })
    if hasattr(node, "mapping"):
        mapping = node.mapping
        structures.append({
            "identifier": "mapping",
            "type": "CURVE_MAPPING",
            "use_clip": bool(getattr(mapping, "use_clip", False)),
            "curves": [
                {
                    "index": curve_index,
                    "points": [
                        {
                            "index": point_index,
                            "location": [float(value) for value in point.location],
                            "handle_type": point.handle_type,
                        }
                        for point_index, point in enumerate(curve.points)
                    ],
                }
                for curve_index, curve in enumerate(mapping.curves)
            ],
        })
    for identifier in ("file_slots", "layer_slots"):
        if not hasattr(node, identifier):
            continue
        collection = getattr(node, identifier)
        items = []
        for index, item in enumerate(collection):
            values = {}
            for property_name in ("name", "path", "use_node_format"):
                if hasattr(item, property_name):
                    try:
                        values[property_name] = _gn_json_value(
                            getattr(item, property_name)
                        )
                    except (AttributeError, TypeError, ValueError, RuntimeError):
                        pass
            items.append({"index": index, "values": values})
        structures.append({
            "identifier": identifier,
            "type": "COLLECTION",
            "item_rna_type": getattr(
                getattr(collection, "bl_rna", None), "identifier", None
            ),
            "items": items,
        })
    return structures


def _node_graph_record(node, view):
    """Extend the compatibility node record with generic dynamic structures."""
    record = _gn_node_record(node, view)
    if view in {"semantic", "all"}:
        structures = _node_special_structure_schema(node)
        if structures:
            record["special_structures"] = structures
    return record


def _node_create_schema_probe(tree_type, owner_kind):
    canonical = _node_normalize_tree_ref({
        "tree_type": tree_type,
        "owner": {"kind": owner_kind, "name": ".BlenderMCP_TypeSchema"},
    })
    owner_kind = canonical["owner"]["kind"]
    temporary_owner = None
    standalone_tree = None
    if owner_kind == "MATERIAL":
        temporary_owner = bpy.data.materials.new(".BlenderMCP_TypeSchema")
        temporary_owner.use_nodes = True
        tree = temporary_owner.node_tree
    elif owner_kind == "WORLD":
        temporary_owner = bpy.data.worlds.new(".BlenderMCP_TypeSchema")
        temporary_owner.use_nodes = True
        tree = temporary_owner.node_tree
    elif owner_kind == "LIGHT":
        temporary_owner = bpy.data.lights.new(".BlenderMCP_TypeSchema", "POINT")
        temporary_owner.use_nodes = True
        tree = temporary_owner.node_tree
    elif owner_kind == "SCENE":
        temporary_owner = bpy.data.scenes.new(".BlenderMCP_TypeSchema")
        if hasattr(temporary_owner, "compositing_node_group"):
            standalone_tree = bpy.data.node_groups.new(
                ".BlenderMCP_TypeSchema", "CompositorNodeTree"
            )
            temporary_owner.compositing_node_group = standalone_tree
            tree = standalone_tree
        else:
            temporary_owner.use_nodes = True
            tree = temporary_owner.node_tree
    else:
        standalone_tree = bpy.data.node_groups.new(
            ".BlenderMCP_TypeSchema", tree_type
        )
        tree = standalone_tree
    return tree, temporary_owner, standalone_tree, owner_kind


def _node_remove_schema_probe(temporary_owner, standalone_tree):
    if temporary_owner is not None:
        if isinstance(temporary_owner, bpy.types.Material):
            bpy.data.materials.remove(temporary_owner, do_unlink=True)
        elif isinstance(temporary_owner, bpy.types.World):
            bpy.data.worlds.remove(temporary_owner, do_unlink=True)
        elif isinstance(temporary_owner, bpy.types.Light):
            bpy.data.lights.remove(temporary_owner, do_unlink=True)
        elif isinstance(temporary_owner, bpy.types.Scene):
            bpy.data.scenes.remove(temporary_owner, do_unlink=True)
    if (
        standalone_tree is not None
        and standalone_tree.name in bpy.data.node_groups
    ):
        bpy.data.node_groups.remove(standalone_tree, do_unlink=True)


def _node_type_schema(tree_type, owner_kind, node_type, detail="compact"):
    if not isinstance(node_type, str) or not node_type.strip():
        raise ValueError("node_type must be a non-empty Blender node bl_idname")
    detail = str(detail or "compact").strip().lower()
    if detail not in GEOMETRY_NODE_TYPE_SCHEMA_DETAILS:
        raise ValueError("detail must be 'compact' or 'full'")
    tree, temporary_owner, standalone_tree, owner_kind = _node_create_schema_probe(
        tree_type, owner_kind
    )
    try:
        try:
            node = tree.nodes.new(type=node_type.strip())
        except RuntimeError as exc:
            raise ValueError(
                f"Unsupported {node_type} in {owner_kind} {tree_type} "
                f"on Blender {bpy.app.version_string}"
            ) from exc
        property_source = (
            _gn_node_owned_properties(node)
            if detail == "compact"
            else node.bl_rna.properties
        )
        properties = []
        dynamic_items = []
        for prop in property_source:
            if prop.identifier in _GN_NODE_PROPERTY_EXCLUDES or prop.identifier == "rna_type":
                continue
            if getattr(prop, "is_hidden", False):
                continue
            if prop.type == "COLLECTION":
                if detail == "compact":
                    dynamic_items.append(_gn_dynamic_collection_schema(node, prop))
                continue
            properties.append(_gn_property_schema(node, prop))
        result = {
            "schema": NODE_TYPE_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "tree_type": tree_type,
            "owner_kind": owner_kind,
            "detail": detail,
            "node_type": node.bl_idname,
            "label": node.bl_label,
            "description": node.bl_description,
            "properties": properties,
            "inputs": [
                (_gn_socket_type_schema if detail == "compact" else _gn_socket_record)(
                    socket, "INPUT", index
                )
                for index, socket in enumerate(node.inputs)
            ],
            "outputs": [
                (_gn_socket_type_schema if detail == "compact" else _gn_socket_record)(
                    socket, "OUTPUT", index
                )
                for index, socket in enumerate(node.outputs)
            ],
        }
        if detail == "compact":
            result["dynamic_items"] = dynamic_items
            result["special_structures"] = _node_special_structure_schema(node)
        return result
    finally:
        _node_remove_schema_probe(temporary_owner, standalone_tree)


def _gn_node_catalog_cache_key():
    return (
        tuple(bpy.app.version[:3]),
        _blender_app_text("build_hash"),
    )


def _gn_geometry_node_type_catalog():
    """Probe all registered node types that can be created in Geometry Nodes."""
    key = _gn_node_catalog_cache_key()
    cached = _GN_NODE_TYPE_CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    tree = bpy.data.node_groups.new(".BlenderMCP_NodeTypeCatalog", "GeometryNodeTree")
    records = []
    try:
        for type_name in sorted(name for name in dir(bpy.types) if "Node" in name):
            cls = getattr(bpy.types, type_name, None)
            if cls is None or not hasattr(cls, "is_registered_node_type"):
                continue
            try:
                if not cls.is_registered_node_type():
                    continue
                node = tree.nodes.new(type=type_name)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
            try:
                if type_name.startswith("GeometryNode"):
                    category = "geometry"
                elif type_name.startswith("ShaderNode"):
                    category = "shader_utility"
                elif type_name.startswith("FunctionNode"):
                    category = "function"
                else:
                    category = "layout_or_group"
                records.append({
                    "bl_idname": node.bl_idname,
                    "label": node.bl_label,
                    "description": node.bl_description,
                    "category": category,
                    "input_count": len(node.inputs),
                    "output_count": len(node.outputs),
                })
            finally:
                tree.nodes.remove(node)
    finally:
        bpy.data.node_groups.remove(tree)

    records.sort(key=lambda item: item["bl_idname"])
    _GN_NODE_TYPE_CATALOG_CACHE.clear()
    _GN_NODE_TYPE_CATALOG_CACHE[key] = records
    return records


def _gn_essentials_library_paths():
    """Find bundled official node asset libraries below Blender DATAFILES."""
    root = bpy.utils.system_resource("DATAFILES")
    if not root:
        return []
    # Blender 4.2 stores Geometry Nodes Essentials below ``geometry_nodes``;
    # newer builds consolidate node assets below ``nodes``. Both locations are
    # fixed children of Blender's own DATAFILES resource, never user paths.
    paths = []
    for directory_name in ("nodes", "geometry_nodes"):
        node_assets = os.path.join(root, "assets", directory_name)
        if not os.path.isdir(node_assets):
            continue
        paths.extend(
            os.path.join(node_assets, name)
            for name in sorted(os.listdir(node_assets))
            if name.lower().endswith(".blend")
            and os.path.isfile(os.path.join(node_assets, name))
        )
    return paths


def _gn_blend_data_ids():
    """Snapshot every currently loaded Blender ID by pointer."""
    result = {}
    for prop in bpy.data.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.type != "COLLECTION":
            continue
        try:
            collection = getattr(bpy.data, prop.identifier)
            for item in collection:
                if isinstance(item, bpy.types.ID):
                    result[item.as_pointer()] = item
        except (AttributeError, TypeError, RuntimeError):
            continue
    return result


def _gn_node_group_dependencies(tree):
    dependencies = set()
    pending = [tree]
    visited = {tree.as_pointer()}
    while pending:
        current = pending.pop()
        for node in current.nodes:
            nested = getattr(node, "node_tree", None)
            if nested is None or not isinstance(nested, bpy.types.NodeTree):
                continue
            pointer = nested.as_pointer()
            if pointer in visited:
                continue
            visited.add(pointer)
            dependencies.add((nested.name, nested.bl_idname))
            pending.append(nested)
    return [
        {"name": name, "tree_type": tree_type}
        for name, tree_type in sorted(dependencies)
    ]


def _gn_node_asset_record(tree, library_path):
    metadata = tree.asset_data
    interface = _gn_tree_interface(tree, True)
    return {
        "name": tree.name,
        "description": getattr(metadata, "description", "") or "",
        "author": getattr(metadata, "author", "") or "",
        "catalog_id": str(getattr(metadata, "catalog_id", "") or ""),
        "tags": sorted(tag.name for tag in getattr(metadata, "tags", [])),
        "tree_type": tree.bl_idname,
        "source_library": os.path.basename(library_path),
        "source_path": os.path.normpath(library_path),
        "interface": interface,
        "node_count": len(tree.nodes),
        "link_count": len(tree.links),
        "interface_item_count": len(interface),
        "dependencies": _gn_node_group_dependencies(tree),
    }


def _gn_load_official_node_asset_library(library_path):
    """Inspect one library and remove every ID appended during inspection."""
    before = _gn_blend_data_ids()
    records = []
    cleanup_error = None
    try:
        try:
            loader = bpy.data.libraries.load(library_path, link=False, assets_only=True)
        except TypeError:
            loader = bpy.data.libraries.load(library_path, link=False)
        with loader as (data_from, data_to):
            data_to.node_groups = list(data_from.node_groups)
        for tree in data_to.node_groups:
            if tree is None or tree.asset_data is None:
                continue
            records.append(_gn_node_asset_record(tree, library_path))
    finally:
        after = _gn_blend_data_ids()
        appended = [item for pointer, item in after.items() if pointer not in before]
        if appended:
            try:
                bpy.data.batch_remove(ids=appended)
            except Exception as exc:
                cleanup_error = exc
        remaining = [
            item for pointer, item in _gn_blend_data_ids().items()
            if pointer not in before
        ]
        if remaining:
            names = ", ".join(
                f"{item.bl_rna.identifier}/{item.name}" for item in remaining[:10]
            )
            raise _GNAssetCleanupError(
                f"Official asset inspection leaked {len(remaining)} datablocks: {names}"
            ) from cleanup_error
        if cleanup_error is not None:
            raise _GNAssetCleanupError(
                f"Official asset inspection cleanup failed: {cleanup_error}"
            ) from cleanup_error
    return records


def _gn_official_node_asset_catalog():
    paths = _gn_essentials_library_paths()
    key = (
        _gn_node_catalog_cache_key(),
        tuple(
            (path, os.path.getsize(path), int(os.path.getmtime(path)))
            for path in paths
        ),
    )
    cached = _GN_ESSENTIALS_CATALOG_CACHE.get(key)
    if cached is not None:
        return cached
    records = []
    errors = []
    for path in paths:
        try:
            records.extend(_gn_load_official_node_asset_library(path))
        except _GNAssetCleanupError:
            raise
        except Exception as exc:
            errors.append({
                "library": os.path.basename(path),
                "path": os.path.normpath(path),
                "error": f"{type(exc).__name__}: {exc}",
            })
    records.sort(key=lambda item: (item["name"].casefold(), item["source_library"]))
    result = {"records": records, "errors": errors, "library_paths": paths}
    _GN_ESSENTIALS_CATALOG_CACHE.clear()
    _GN_ESSENTIALS_CATALOG_CACHE[key] = result
    return result


def _gn_node_asset_summary(record):
    inputs = []
    outputs = []
    panels = []
    for item in record["interface"]:
        if item["item_type"] == "PANEL":
            panels.append(item["name"])
        elif item.get("in_out") == "INPUT":
            inputs.append(item["name"])
        elif item.get("in_out") == "OUTPUT":
            outputs.append(item["name"])
    return {
        key: record[key]
        for key in (
            "name", "description", "author", "catalog_id", "tags",
            "tree_type", "source_library", "source_path", "node_count",
            "link_count", "interface_item_count", "dependencies",
        )
    } | {
        "interface_summary": {
            "inputs": inputs,
            "outputs": outputs,
            "panels": panels,
        },
    }


def _gn_patch_diagnostic(severity, code, path, message):
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
    }


def _gn_rna_property(owner, identifier):
    try:
        return owner.bl_rna.properties.get(identifier)
    except (AttributeError, KeyError, TypeError):
        return None


def _gn_resolve_id_reference(value):
    if not isinstance(value, dict) or value.get("$type") != "ID":
        return None
    id_type = value.get("id_type")
    name = value.get("name")
    collections = {
        "Object": bpy.data.objects,
        "Collection": bpy.data.collections,
        "Material": bpy.data.materials,
        "World": bpy.data.worlds,
        "Light": bpy.data.lights,
        "Image": bpy.data.images,
        "MovieClip": bpy.data.movieclips,
        "Mask": bpy.data.masks,
        "Scene": bpy.data.scenes,
        "Texture": bpy.data.textures,
        "GeometryNodeTree": bpy.data.node_groups,
        "ShaderNodeTree": bpy.data.node_groups,
        "CompositorNodeTree": bpy.data.node_groups,
        "NodeTree": bpy.data.node_groups,
    }
    collection = collections.get(id_type)
    resolved = collection.get(name) if collection is not None and isinstance(name, str) else None
    expected_library = value.get("library")
    if resolved is not None and expected_library is not None:
        actual_library = _node_id_library(resolved)
        if actual_library != expected_library:
            return None
    return resolved


def _gn_decode_patch_value(value, node_refs=None):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_gn_decode_patch_value(item, node_refs) for item in value]
    if isinstance(value, dict):
        value_type = value.get("$type")
        if value_type == "ID":
            resolved = _gn_resolve_id_reference(value)
            if resolved is None:
                raise ValueError(f"Blender ID not found: {value.get('id_type')}/{value.get('name')}")
            return resolved
        if value_type == "ViewLayer":
            scene_name = value.get("scene")
            layer_name = value.get("name")
            scene = bpy.data.scenes.get(scene_name) if isinstance(scene_name, str) else None
            layer = scene.view_layers.get(layer_name) if scene and isinstance(layer_name, str) else None
            if layer is None:
                raise ValueError(
                    f"View Layer not found: {scene_name}/{layer_name}"
                )
            return layer.name
        if value_type == "NodeRef" and node_refs is not None:
            reference = node_refs.get(value.get("name"))
            if reference and not reference["removed"]:
                return reference["node"]
            raise ValueError(f"Node reference not found: {value.get('name')}")
    raise ValueError(f"Unsupported typed patch value: {value!r}")


def _gn_validate_value(
    owner, property_name, value, path, diagnostics, node_refs=None, property_path=None,
):
    property_path = property_path or path
    prop = _gn_rna_property(owner, property_name)
    if prop is None:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "unknown_rna_property", property_path,
            f"RNA property {property_name!r} does not exist on {owner.bl_rna.identifier}",
        ))
        return False
    if getattr(prop, "is_readonly", False):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "readonly_rna_property", property_path,
            f"RNA property {property_name!r} is read-only",
        ))
        return False
    if prop.type == "COLLECTION":
        diagnostics.append(_gn_patch_diagnostic(
            "error", "unsupported_rna_collection", property_path,
            f"RNA collection property {property_name!r} is not patchable",
        ))
        return False

    array_length = int(getattr(prop, "array_length", 0))
    if array_length:
        if not isinstance(value, list) or len(value) != array_length:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "invalid_array_value", path,
                f"Expected an array with {array_length} values",
            ))
            return False
        scalar_type = "FLOAT" if prop.type == "FLOAT" else "INT"
        valid = all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            if scalar_type == "FLOAT"
            else isinstance(item, int) and not isinstance(item, bool)
            for item in value
        )
        if not valid:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "invalid_array_item", path,
                f"Expected {scalar_type.lower()} array values",
            ))
            return False
        hard_min = getattr(prop, "hard_min", None)
        hard_max = getattr(prop, "hard_max", None)
        if hard_min is not None and hard_max is not None and any(
            item < hard_min or item > hard_max for item in value
        ):
            diagnostics.append(_gn_patch_diagnostic(
                "error", "rna_value_out_of_range", path,
                f"Array values must be between {hard_min} and {hard_max}",
            ))
            return False
        return True

    valid = True
    if prop.type == "BOOLEAN":
        valid = isinstance(value, bool)
    elif prop.type == "INT":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif prop.type == "FLOAT":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif prop.type == "STRING":
        valid = isinstance(value, str)
    elif prop.type == "ENUM":
        enum_value = value
        is_view_layer_reference = (
            isinstance(value, dict) and value.get("$type") == "ViewLayer"
        )
        if is_view_layer_reference:
            try:
                enum_value = _gn_decode_patch_value(value, node_refs)
            except ValueError as exc:
                diagnostics.append(_gn_patch_diagnostic(
                    "error", "invalid_view_layer_reference", path, str(exc),
                ))
                return False
            valid = True
        else:
            try:
                identifiers = {item.identifier for item in prop.enum_items}
                valid = isinstance(enum_value, str) and enum_value in identifiers
                if not valid:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "invalid_enum_value", path,
                        f"Expected one of: {', '.join(sorted(identifiers))}",
                    ))
                    return False
            except (AttributeError, RuntimeError, TypeError):
                valid = isinstance(enum_value, str)
    elif prop.type == "POINTER":
        try:
            _gn_decode_patch_value(value, node_refs)
        except ValueError as exc:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "invalid_pointer_value", path, str(exc),
            ))
            return False

    if valid and prop.type in {"INT", "FLOAT"}:
        hard_min = getattr(prop, "hard_min", None)
        hard_max = getattr(prop, "hard_max", None)
        if hard_min is not None and value < hard_min:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "rna_value_out_of_range", path,
                f"Value must be at least {hard_min}",
            ))
            return False
        if hard_max is not None and value > hard_max:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "rna_value_out_of_range", path,
                f"Value must be at most {hard_max}",
            ))
            return False

    if not valid:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "invalid_rna_value", path,
            f"Value is incompatible with RNA type {prop.type}",
        ))
    return valid


def _gn_resolve_patch_node(node_refs, reference, path, diagnostics):
    item = node_refs.get(reference)
    if item is None:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "node_not_found", path, f"Node reference not found: {reference}",
        ))
        return None
    if item["removed"]:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "node_already_removed", path, f"Node was already removed: {reference}",
        ))
        return None
    return item


def _gn_resolve_patch_socket(node, socket_id, expected_direction, path, diagnostics):
    try:
        direction, index_text, identifier = socket_id.split(":", 2)
        index = int(index_text)
    except (AttributeError, TypeError, ValueError):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "invalid_socket_id", path, f"Invalid socket id: {socket_id!r}",
        ))
        return None
    if direction != expected_direction:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "wrong_socket_direction", path,
            f"Expected a {expected_direction} socket, got {direction}",
        ))
        return None
    sockets = node.outputs if direction == "output" else node.inputs
    if not 0 <= index < len(sockets):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "socket_index_out_of_range", path,
            f"Socket index {index} is out of range for node {node.name!r}",
        ))
        return None
    socket = sockets[index]
    actual_identifier = getattr(socket, "identifier", "") or socket.name
    if identifier != actual_identifier:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "stale_socket_id", path,
            f"Socket index {index} now identifies {actual_identifier!r}, not {identifier!r}",
        ))
        return None
    return socket


def _gn_validate_patch_runtime(tree, patch):
    diagnostics = []
    plan = []
    diff = {
        "nodes_added": 0,
        "nodes_removed": 0,
        "nodes_renamed": 0,
        "node_properties_changed": 0,
        "socket_defaults_changed": 0,
        "links_added": 0,
        "links_removed": 0,
        "layouts_changed": 0,
        "interface_sockets_added": 0,
        "interface_sockets_removed": 0,
        "modifier_inputs_changed": 0,
    }
    current_revision = _gn_export_tree(tree, "semantic")["revision"]
    if patch.get("base_revision") != current_revision:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "stale_revision", "/base_revision",
            f"Patch revision {patch.get('base_revision')!r} does not match current {current_revision!r}",
        ))
    if not bool(getattr(tree, "is_editable", tree.library is None)):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_not_editable", "/tree_name",
            f"Geometry Node tree {tree.name!r} is linked or otherwise read-only",
        ))

    users = _gn_tree_users(tree)
    policy = patch.get("shared_tree_policy", "reject")
    if len(users) > 1 and policy == "reject":
        diagnostics.append(_gn_patch_diagnostic(
            "error", "shared_tree_rejected", "/shared_tree_policy",
            f"Tree has {len(users)} users; select single_user_copy or mutate_shared explicitly",
        ))
    elif len(users) > 1 and policy == "mutate_shared":
        diagnostics.append(_gn_patch_diagnostic(
            "warning", "shared_tree_mutation", "/shared_tree_policy",
            f"Patch would affect all {len(users)} users of this tree",
        ))
    if policy == "single_user_copy":
        target_user = patch.get("target_user")
        if not isinstance(target_user, dict) or not any(
            all(user.get(key) == value for key, value in target_user.items())
            for user in users
        ):
            diagnostics.append(_gn_patch_diagnostic(
                "error", "target_user_not_found", "/target_user",
                "target_user does not identify a current modifier or group-node user of this tree",
            ))

    node_refs = {
        node.name: {
            "node": node,
            "existing": True,
            "removed": False,
            "projected_name": node.name,
        }
        for node in tree.nodes
    }
    projected_names = {node.name for node in tree.nodes}
    projected_links = {
        (
            link.from_node.name,
            _gn_socket_id(link.from_socket, "OUTPUT", _gn_find_socket_index(link.from_node, link.from_socket, "OUTPUT")),
            link.to_node.name,
            _gn_socket_id(link.to_socket, "INPUT", _gn_find_socket_index(link.to_node, link.to_socket, "INPUT")),
        )
        for link in tree.links
    }
    interface_items = {
        (getattr(item, "identifier", "") or item.name): {
            "item": item,
            "removed": False,
            "in_out": getattr(item, "in_out", None),
            "item_type": item.item_type,
        }
        for item in tree.interface.items_tree
    }

    temp_tree = bpy.data.node_groups.new(".BlenderMCP_PatchValidation", "GeometryNodeTree")
    try:
        for index, operation in enumerate(patch.get("operations", ())):
            path = f"/operations/{index}"
            errors_before = sum(item["severity"] == "error" for item in diagnostics)
            op = operation["op"]
            summary = op

            if op == "add_node":
                reference = operation["id"]
                if reference in node_refs:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "duplicate_node_reference", f"{path}/id",
                        f"Node reference already exists: {reference}",
                    ))
                else:
                    try:
                        node = temp_tree.nodes.new(operation["node_type"])
                    except RuntimeError as exc:
                        version = ".".join(str(item) for item in bpy.app.version[:3])
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "unsupported_node_type", f"{path}/node_type", str(exc),
                        ))
                        diagnostics[-1]["message"] = (
                            f"{operation['node_type']} is unavailable for "
                            f"{tree.bl_idname}/NODE_GROUP in Blender "
                            f"{version}: {exc}"
                        )
                        node = None
                    if node is not None:
                        projected_name = operation.get("name") or node.name
                        if projected_name in projected_names:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "duplicate_node_name", f"{path}/name",
                                f"Projected node name already exists: {projected_name}",
                            ))
                        node_refs[reference] = {
                            "node": node,
                            "existing": False,
                            "removed": False,
                            "projected_name": projected_name,
                        }
                        projected_names.add(projected_name)
                        layout = operation.get("layout", {})
                        if "parent" in layout and layout["parent"] is not None:
                            parent = _gn_resolve_patch_node(
                                node_refs, layout["parent"], f"{path}/layout/parent", diagnostics,
                            )
                            if parent and parent["node"].bl_idname != "NodeFrame":
                                diagnostics.append(_gn_patch_diagnostic(
                                    "error", "layout_parent_not_frame", f"{path}/layout/parent",
                                    "Node parent must reference a Frame node",
                                ))
                        for layout_field in ("location", "width", "height"):
                            if layout_field in layout:
                                try:
                                    setattr(node, layout_field, layout[layout_field])
                                except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                    diagnostics.append(_gn_patch_diagnostic(
                                        "error", "layout_assignment_rejected",
                                        f"{path}/layout/{layout_field}", str(exc),
                                    ))
                        for property_name, value in operation.get("properties", {}).items():
                            value_path = f"{path}/properties/{property_name}"
                            if _gn_validate_value(node, property_name, value, value_path, diagnostics, node_refs):
                                try:
                                    setattr(node, property_name, _gn_decode_patch_value(value, node_refs))
                                except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                    diagnostics.append(_gn_patch_diagnostic(
                                        "error", "rna_assignment_rejected", value_path, str(exc),
                                    ))
                        diff["nodes_added"] += 1
                        summary = f"Add {operation['node_type']} as {reference}"

            elif op == "remove_node":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item:
                    item["removed"] = True
                    projected_names.discard(item["projected_name"])
                    projected_links = {
                        link for link in projected_links
                        if link[0] != operation["node"] and link[2] != operation["node"]
                    }
                    diff["nodes_removed"] += 1
                    summary = f"Remove node {operation['node']}"

            elif op == "rename_node":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                new_name = operation["name"]
                if item:
                    if new_name != item["projected_name"] and new_name in projected_names:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "duplicate_node_name", f"{path}/name",
                            f"Projected node name already exists: {new_name}",
                        ))
                    else:
                        projected_names.discard(item["projected_name"])
                        projected_names.add(new_name)
                        item["projected_name"] = new_name
                        if not item["existing"]:
                            item["node"].name = new_name
                        diff["nodes_renamed"] += 1
                        summary = f"Rename {operation['node']} to {new_name}"

            elif op == "set_node_property":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item and _gn_validate_value(
                    item["node"], operation["property"], operation["value"],
                    f"{path}/value", diagnostics, node_refs,
                    property_path=f"{path}/property",
                ):
                    if not item["existing"]:
                        try:
                            setattr(
                                item["node"], operation["property"],
                                _gn_decode_patch_value(operation["value"], node_refs),
                            )
                        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "rna_assignment_rejected", f"{path}/value", str(exc),
                            ))
                    diff["node_properties_changed"] += 1
                    summary = f"Set {operation['node']}.{operation['property']}"

            elif op == "set_socket_default":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item:
                    socket = _gn_resolve_patch_socket(
                        item["node"], operation["socket"], "input", f"{path}/socket", diagnostics,
                    )
                    if socket and _gn_validate_value(
                        socket, "default_value", operation["value"], f"{path}/value", diagnostics, node_refs,
                    ):
                        if socket.is_linked and item["existing"]:
                            diagnostics.append(_gn_patch_diagnostic(
                                "warning", "linked_socket_default", f"{path}/socket",
                                "Default is stored but has no effect while the socket is linked",
                            ))
                        if not item["existing"]:
                            try:
                                socket.default_value = _gn_decode_patch_value(operation["value"], node_refs)
                            except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "error", "rna_assignment_rejected", f"{path}/value", str(exc),
                                ))
                        diff["socket_defaults_changed"] += 1
                        summary = f"Set default {operation['node']}:{operation['socket']}"

            elif op in {"add_link", "remove_link"}:
                from_item = _gn_resolve_patch_node(
                    node_refs, operation["from_node"], f"{path}/from_node", diagnostics,
                )
                to_item = _gn_resolve_patch_node(
                    node_refs, operation["to_node"], f"{path}/to_node", diagnostics,
                )
                from_socket = _gn_resolve_patch_socket(
                    from_item["node"], operation["from_socket"], "output", f"{path}/from_socket", diagnostics,
                ) if from_item else None
                to_socket = _gn_resolve_patch_socket(
                    to_item["node"], operation["to_socket"], "input", f"{path}/to_socket", diagnostics,
                ) if to_item else None
                link_key = (
                    operation["from_node"], operation["from_socket"],
                    operation["to_node"], operation["to_socket"],
                )
                if from_socket and to_socket:
                    if op == "add_link":
                        if link_key in projected_links:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "duplicate_link", path, "Link already exists",
                            ))
                        elif not getattr(to_socket, "is_multi_input", False) and any(
                            link[2:] == link_key[2:] for link in projected_links
                        ):
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "input_socket_occupied", f"{path}/to_socket",
                                "Remove the existing link before linking a non-multi-input socket",
                            ))
                        else:
                            projected_links.add(link_key)
                            if from_socket.type != to_socket.type:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "warning", "implicit_socket_conversion", path,
                                    f"Blender must convert {from_socket.type} to {to_socket.type}",
                                ))
                            diff["links_added"] += 1
                            summary = f"Link {operation['from_node']} to {operation['to_node']}"
                    elif link_key not in projected_links:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "link_not_found", path, "Link does not exist in projected graph",
                        ))
                    else:
                        projected_links.remove(link_key)
                        diff["links_removed"] += 1
                        summary = f"Unlink {operation['from_node']} from {operation['to_node']}"

            elif op == "set_node_layout":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                parent_ref = operation.get("parent")
                if item and parent_ref is not None:
                    parent = _gn_resolve_patch_node(
                        node_refs, parent_ref, f"{path}/parent", diagnostics,
                    )
                    if parent and parent["node"].bl_idname != "NodeFrame":
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "layout_parent_not_frame", f"{path}/parent",
                            "Node parent must reference a Frame node",
                        ))
                    if parent is item:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "layout_parent_cycle", f"{path}/parent",
                            "A node cannot be its own parent",
                        ))
                if item:
                    for layout_field in ("width", "height"):
                        if layout_field in operation:
                            _gn_validate_value(
                                item["node"], layout_field, operation[layout_field],
                                f"{path}/{layout_field}", diagnostics, node_refs,
                            )
                    diff["layouts_changed"] += 1
                    summary = f"Update layout for {operation['node']}"

            elif op == "add_interface_socket":
                reference = operation["id"]
                if reference in interface_items and not interface_items[reference]["removed"]:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "duplicate_interface_reference", f"{path}/id",
                        f"Interface reference already exists: {reference}",
                    ))
                try:
                    probe_socket = temp_tree.interface.new_socket(
                        name=operation["name"],
                        in_out=operation["in_out"],
                        socket_type=operation["socket_type"],
                    )
                    socket_type_valid = True
                except (TypeError, ValueError, RuntimeError):
                    socket_type_valid = False
                if not socket_type_valid:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "unsupported_interface_socket", f"{path}/socket_type",
                        f"Socket type is not valid for Geometry Nodes: {operation['socket_type']}",
                    ))
                elif "default" in operation and hasattr(probe_socket, "default_value"):
                    if _gn_validate_value(
                        probe_socket, "default_value", operation["default"],
                        f"{path}/default", diagnostics, node_refs,
                    ):
                        try:
                            probe_socket.default_value = _gn_decode_patch_value(
                                operation["default"], node_refs,
                            )
                        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "rna_assignment_rejected", f"{path}/default", str(exc),
                            ))
                parent_ref = operation.get("parent")
                if parent_ref:
                    parent = interface_items.get(parent_ref)
                    if not parent or parent["removed"] or parent["item_type"] != "PANEL":
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "interface_parent_not_found", f"{path}/parent",
                            f"Interface panel not found: {parent_ref}",
                        ))
                interface_items[reference] = {
                    "item": probe_socket if socket_type_valid else None, "removed": False,
                    "in_out": operation["in_out"], "item_type": "SOCKET",
                }
                diff["interface_sockets_added"] += 1
                summary = f"Add {operation['in_out']} interface socket {reference}"

            elif op == "remove_interface_socket":
                reference = operation["identifier"]
                item = interface_items.get(reference)
                if not item or item["removed"] or item["item_type"] != "SOCKET":
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "interface_socket_not_found", f"{path}/identifier",
                        f"Interface socket not found: {reference}",
                    ))
                else:
                    item["removed"] = True
                    diff["interface_sockets_removed"] += 1
                    summary = f"Remove interface socket {reference}"

            elif op == "set_modifier_input":
                obj = bpy.data.objects.get(operation["object"])
                modifier = obj.modifiers.get(operation["modifier"]) if obj else None
                interface = interface_items.get(operation["socket"])
                if obj is None:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "object_not_found", f"{path}/object",
                        f"Object not found: {operation['object']}",
                    ))
                elif modifier is None or modifier.type != "NODES":
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "modifier_not_found", f"{path}/modifier",
                        f"Geometry Nodes modifier not found: {operation['modifier']}",
                    ))
                elif modifier.node_group != tree:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "modifier_tree_mismatch", f"{path}/modifier",
                        "Modifier does not use the patched node tree",
                    ))
                if not interface or interface["removed"] or interface["in_out"] != "INPUT":
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "modifier_input_not_found", f"{path}/socket",
                        f"Input interface socket not found: {operation['socket']}",
                    ))
                elif interface["item"] is not None and hasattr(interface["item"], "default_value"):
                    _gn_validate_value(
                        interface["item"], "default_value", operation["value"],
                        f"{path}/value", diagnostics, node_refs,
                    )
                if policy == "single_user_copy":
                    target_user = patch.get("target_user", {})
                    if (
                        target_user.get("kind") != "MODIFIER"
                        or target_user.get("object") != operation["object"]
                        or target_user.get("modifier") != operation["modifier"]
                    ):
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "modifier_input_outside_copy_target", path,
                            "single_user_copy may only change inputs on its target modifier",
                        ))
                diff["modifier_inputs_changed"] += 1
                summary = f"Set modifier input {operation['object']}/{operation['modifier']}"

            errors_after = sum(item["severity"] == "error" for item in diagnostics)
            plan.append({
                "index": index,
                "op": op,
                "status": "ready" if errors_after == errors_before else "invalid",
                "summary": summary,
            })
    finally:
        bpy.data.node_groups.remove(temp_tree)

    candidate_revision = None
    candidate_stats = None
    if not any(item["severity"] == "error" for item in diagnostics):
        execution_probe = tree.copy()
        execution_probe.name = f".{tree.name}.MCP Dry Run"
        try:
            _gn_apply_operations_to_working(execution_probe, patch)
            invalid_links = [link for link in execution_probe.links if not link.is_valid]
            if invalid_links:
                raise RuntimeError(
                    f"Projected tree contains {len(invalid_links)} invalid links"
                )
            projected_snapshot = _gn_export_tree(execution_probe, "all")
            candidate_revision = projected_snapshot["revision"]
            candidate_stats = projected_snapshot["stats"]
        except Exception as exc:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "dry_run_execution_rejected", "",
                f"{type(exc).__name__}: {exc}",
            ))
        finally:
            if execution_probe.users == 0:
                bpy.data.node_groups.remove(execution_probe)

    result = {
        "schema": GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA,
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "stage": "runtime",
        "will_mutate": False,
        "tree_name": tree.name,
        "base_revision": patch.get("base_revision"),
        "current_revision": current_revision,
        "shared_tree_policy": policy,
        "users": users,
        "diagnostics": diagnostics,
        "plan": plan,
        "semantic_diff": diff,
    }
    if candidate_revision is not None:
        result["candidate_revision"] = candidate_revision
        result["candidate_stats"] = candidate_stats
    return result


def _gn_modifier_input_value(modifier, identifier):
    """Read one modifier input through the active Blender-version adapter."""
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            socket_value = getattr(properties.inputs, identifier)
            if hasattr(socket_value, "value"):
                return socket_value.value
        except (AttributeError, KeyError, TypeError, RuntimeError):
            pass
    try:
        if identifier in modifier.keys():
            return modifier[identifier]
    except (AttributeError, KeyError, TypeError, RuntimeError):
        pass
    raise KeyError(f"Modifier input has no restorable value: {identifier}")


def _gn_modifier_input_record(modifier, identifier):
    """Capture value/attribute state and animation paths for one input."""
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            socket_value = getattr(properties.inputs, identifier)
            fields = {}
            data_paths = {}
            for field in ("type", "attribute_name", "value"):
                if not hasattr(socket_value, field):
                    continue
                try:
                    fields[field] = getattr(socket_value, field)
                except (AttributeError, KeyError, TypeError, RuntimeError):
                    continue
                try:
                    data_paths[field] = socket_value.path_from_id(field)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass
            return {
                "adapter": "geometry_nodes_modifier_interface",
                "fields": fields,
                "data_paths": data_paths,
            }
        except (AttributeError, KeyError, TypeError, RuntimeError):
            pass

    fields = {}
    data_paths = {}
    candidate_keys = (
        identifier,
        f"{identifier}_use_attribute",
        f"{identifier}_attribute_name",
    )
    try:
        keys = set(modifier.keys())
    except (AttributeError, TypeError, RuntimeError):
        keys = set()
    for key in candidate_keys:
        if key not in keys:
            continue
        fields[key] = modifier[key]
        data_paths[key] = f'["{key}"]'
    if identifier not in fields:
        raise KeyError(f"Modifier input has no restorable value: {identifier}")
    return {
        "adapter": "legacy_id_property",
        "fields": fields,
        "data_paths": data_paths,
    }


def _gn_restore_modifier_input_record(modifier, identifier, record):
    adapter = record.get("adapter") if isinstance(record, dict) else None
    fields = record.get("fields", {}) if isinstance(record, dict) else {}
    if adapter == "geometry_nodes_modifier_interface":
        properties = getattr(modifier, "properties", None)
        if properties is None:
            raise RuntimeError("Geometry Nodes modifier interface is unavailable")
        socket_value = getattr(properties.inputs, identifier)
        for field in ("type", "attribute_name", "value"):
            if field not in fields or not hasattr(socket_value, field):
                continue
            prop = _gn_rna_property(socket_value, field)
            if prop is not None and getattr(prop, "is_readonly", False):
                continue
            setattr(socket_value, field, fields[field])
        return adapter
    if adapter == "legacy_id_property":
        for key, value in fields.items():
            modifier[key] = value
        return adapter
    # Compatibility for pre-adapter in-memory state records.
    _gn_set_modifier_input_value(modifier, identifier, record)
    return "legacy_state_value"


def _gn_set_modifier_input_value(modifier, identifier, value):
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            socket_value = getattr(properties.inputs, identifier)
            socket_value.value = value
            return "geometry_nodes_modifier_interface"
        except (AttributeError, KeyError, TypeError, RuntimeError):
            pass
    modifier[identifier] = value
    return "legacy_id_property"


def _gn_modifier_state(modifier, tree):
    state = {}
    for item in tree.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT":
            continue
        identifier = getattr(item, "identifier", "") or item.name
        try:
            state[identifier] = _gn_modifier_input_record(modifier, identifier)
        except (AttributeError, KeyError, TypeError, RuntimeError):
            continue
    return state


def _gn_restore_modifier_state(modifier, state):
    errors = []
    for identifier, value in state.items():
        try:
            _gn_restore_modifier_input_record(modifier, identifier, value)
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            errors.append(f"{identifier}: {type(exc).__name__}: {exc}")
    return errors


def _gn_user_handle(user):
    if user["kind"] == "MODIFIER":
        obj = bpy.data.objects.get(user["object"])
        modifier = obj.modifiers.get(user["modifier"]) if obj else None
        return modifier
    owner_tree = bpy.data.node_groups.get(user["tree"])
    return owner_tree.nodes.get(user["node"]) if owner_tree else None


_NODE_EFFECT_SENSITIVE_TYPES = {
    "ShaderNodeScript",
    "CompositorNodeOutputFile",
}
_NODE_EFFECT_SENSITIVE_PROPERTIES = {
    "base_path", "bytecode", "filepath", "file_slots", "layer_slots", "script",
}


def _node_validation_copy(target):
    owner_kind = target["owner_kind"]
    original_owner = target["owner_id"]
    standalone_tree = None
    if owner_kind == "NODE_GROUP":
        working_owner = original_owner.copy()
        working_tree = working_owner
        standalone_tree = working_tree
    elif owner_kind in {"MATERIAL", "WORLD", "LIGHT"}:
        working_owner = original_owner.copy()
        working_tree = working_owner.node_tree
    elif target["adapter"] == "scene_embedded_node_tree":
        working_owner = original_owner.copy()
        working_tree = working_owner.node_tree
    else:
        working_owner = original_owner.copy()
        working_tree = target["tree"].copy()
        working_owner.compositing_node_group = working_tree
        standalone_tree = working_tree
    working_owner.name = ".BlenderMCP Node Patch Validation"
    if standalone_tree is not None:
        standalone_tree.name = ".BlenderMCP Node Patch Validation"
    working_target = dict(target)
    working_target.update({
        "owner_id": working_owner,
        "tree": working_tree,
    })
    return working_target, working_owner, standalone_tree


def _node_remove_validation_copy(target, working_owner, standalone_tree):
    owner_kind = target["owner_kind"]
    if owner_kind == "NODE_GROUP":
        if working_owner.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(working_owner, do_unlink=True)
        return
    if owner_kind == "MATERIAL" and working_owner.name in bpy.data.materials:
        bpy.data.materials.remove(working_owner, do_unlink=True)
    elif owner_kind == "WORLD" and working_owner.name in bpy.data.worlds:
        bpy.data.worlds.remove(working_owner, do_unlink=True)
    elif owner_kind == "LIGHT" and working_owner.name in bpy.data.lights:
        bpy.data.lights.remove(working_owner, do_unlink=True)
    elif owner_kind == "SCENE" and working_owner.name in bpy.data.scenes:
        bpy.data.scenes.remove(working_owner, do_unlink=True)
    if (
        standalone_tree is not None
        and standalone_tree.name in bpy.data.node_groups
    ):
        bpy.data.node_groups.remove(standalone_tree, do_unlink=True)


def _node_interface_mutable(target):
    return (
        target["owner_kind"] == "NODE_GROUP"
        or target["adapter"] == "scene_compositing_node_group"
    )


def _node_mutation_allowed(node, operation, path, diagnostics, property_name=None):
    if getattr(node.__class__, "__module__", None) != "bpy.types":
        diagnostics.append(_gn_patch_diagnostic(
            "error", "custom_node_read_only", path,
            f"{node.bl_idname} is provided by Python or an add-on and is read-only "
            "until an explicit mutation capability is implemented",
        ))
        return False
    if node.bl_idname in _NODE_EFFECT_SENSITIVE_TYPES:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "effect_sensitive_node_read_only", path,
            f"{node.bl_idname} is read-only because mutation may configure external effects",
        ))
        return False
    if property_name in _NODE_EFFECT_SENSITIVE_PROPERTIES:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "effect_sensitive_property_read_only", path,
            f"Property {property_name!r} is read-only in the generic patch protocol",
        ))
        return False
    return True


def _node_apply_color_ramp(node, operation):
    ramp = getattr(node, "color_ramp", None)
    if ramp is None:
        raise ValueError(f"Node {node.name!r} has no Color Ramp")
    if "interpolation" in operation:
        ramp.interpolation = operation["interpolation"]
    requested = operation["elements"]
    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])
    for index, element in enumerate(requested):
        target = (
            ramp.elements[index]
            if index < 2
            else ramp.elements.new(float(element["position"]))
        )
        target.position = float(element["position"])
        target.color = element["color"]


def _node_apply_curve_mapping(node, operation):
    mapping = getattr(node, "mapping", None)
    if mapping is None:
        raise ValueError(f"Node {node.name!r} has no Curve Mapping")
    curves = operation["curves"]
    if len(curves) != len(mapping.curves):
        raise ValueError(
            f"Expected {len(mapping.curves)} curves, received {len(curves)}"
        )
    if "use_clip" in operation:
        mapping.use_clip = bool(operation["use_clip"])
    for curve, curve_patch in zip(mapping.curves, curves):
        requested = curve_patch["points"]
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-1])
        for index, point_patch in enumerate(requested):
            location = point_patch["location"]
            point = (
                curve.points[index]
                if index < 2
                else curve.points.new(float(location[0]), float(location[1]))
            )
            point.location = location
            if "handle_type" in point_patch:
                point.handle_type = point_patch["handle_type"]
    mapping.update()


def _node_execute_patch_operations(target, patch):
    tree = target["tree"]
    diagnostics = []
    plan = []
    diff = {
        "nodes_added": 0,
        "nodes_removed": 0,
        "nodes_renamed": 0,
        "node_properties_changed": 0,
        "socket_defaults_changed": 0,
        "links_added": 0,
        "links_removed": 0,
        "layouts_changed": 0,
        "annotations_changed": 0,
        "interface_sockets_added": 0,
        "interface_sockets_removed": 0,
        "dynamic_structures_changed": 0,
    }
    node_refs = {
        node.name: {"node": node, "removed": False, "existing": True}
        for node in tree.nodes
    }
    interface_refs = {
        (getattr(item, "identifier", "") or item.name): {
            "item": item,
            "removed": False,
        }
        for item in tree.interface.items_tree
    }
    created_nodes = {}
    created_interface = {}

    def resolve_node(reference, path):
        item = node_refs.get(reference)
        if item is None:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "node_not_found", path,
                f"Node reference not found: {reference}",
            ))
            return None
        if item["removed"]:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "node_already_removed", path,
                f"Node was already removed: {reference}",
            ))
            return None
        return item["node"]

    for index, operation in enumerate(patch["operations"]):
        path = f"/operations/{index}"
        errors_before = sum(item["severity"] == "error" for item in diagnostics)
        op = operation["op"]
        summary = op
        try:
            if op == "add_node":
                reference = operation["id"]
                if reference in node_refs:
                    raise ValueError(f"Node reference already exists: {reference}")
                if operation["node_type"] in _NODE_EFFECT_SENSITIVE_TYPES:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "effect_sensitive_node_read_only", f"{path}/node_type",
                        f"{operation['node_type']} cannot be added by the patch protocol",
                    ))
                    node = None
                else:
                    node = tree.nodes.new(operation["node_type"])
                    if not _node_mutation_allowed(
                        node, op, f"{path}/node_type", diagnostics
                    ):
                        tree.nodes.remove(node)
                        node = None
                if node is not None:
                    requested_name = operation.get("name")
                    if (
                        requested_name
                        and tree.nodes.get(requested_name) not in {None, node}
                    ):
                        tree.nodes.remove(node)
                        raise ValueError(f"Node name already exists: {requested_name}")
                    if requested_name:
                        node.name = requested_name
                    node_refs[reference] = {
                        "node": node, "removed": False, "existing": False,
                    }
                    created_nodes[reference] = node.name
                    for property_name, value in operation.get("properties", {}).items():
                        property_path = f"{path}/properties/{property_name}"
                        if not _node_mutation_allowed(
                            node, op, property_path, diagnostics, property_name
                        ):
                            continue
                        if _gn_validate_value(
                            node, property_name, value, property_path,
                            diagnostics, node_refs,
                        ):
                            setattr(
                                node, property_name,
                                _gn_decode_patch_value(value, node_refs),
                            )
                    layout = operation.get("layout", {})
                    for field in ("location", "width", "height"):
                        if field in layout:
                            setattr(node, field, layout[field])
                    if layout.get("parent") is not None:
                        parent = resolve_node(
                            layout["parent"], f"{path}/layout/parent"
                        )
                        if parent is not None:
                            if parent.bl_idname != "NodeFrame":
                                raise ValueError("Node parent must be a Frame")
                            node.parent = parent
                    diff["nodes_added"] += 1
                    summary = f"Add {node.bl_idname} as {reference}"

            elif op == "remove_node":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    tree.nodes.remove(node)
                    node_refs[operation["node"]]["removed"] = True
                    created_nodes.pop(operation["node"], None)
                    diff["nodes_removed"] += 1
                    summary = f"Remove {operation['node']}"

            elif op == "rename_node":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    if operation["name"] != node.name and operation["name"] in tree.nodes:
                        raise ValueError(f"Node name already exists: {operation['name']}")
                    node.name = operation["name"]
                    if operation["node"] in created_nodes:
                        created_nodes[operation["node"]] = node.name
                    diff["nodes_renamed"] += 1
                    summary = f"Rename {operation['node']} to {node.name}"

            elif op == "set_node_property":
                node = resolve_node(operation["node"], f"{path}/node")
                property_name = operation["property"]
                if node is not None and _node_mutation_allowed(
                    node, op, f"{path}/property", diagnostics, property_name
                ) and _gn_validate_value(
                    node, property_name, operation["value"], f"{path}/value",
                    diagnostics, node_refs, property_path=f"{path}/property",
                ):
                    setattr(
                        node, property_name,
                        _gn_decode_patch_value(operation["value"], node_refs),
                    )
                    diff["node_properties_changed"] += 1
                    summary = f"Set {operation['node']}.{property_name}"

            elif op == "set_socket_default":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    socket = _gn_resolve_patch_socket(
                        node, operation["socket"], "input", f"{path}/socket", diagnostics
                    )
                    if socket is not None and _gn_validate_value(
                        socket, "default_value", operation["value"], f"{path}/value",
                        diagnostics, node_refs,
                    ):
                        if socket.is_linked:
                            diagnostics.append(_gn_patch_diagnostic(
                                "warning", "linked_socket_default", f"{path}/socket",
                                "The default is stored but has no effect while the socket is linked",
                            ))
                        socket.default_value = _gn_decode_patch_value(
                            operation["value"], node_refs
                        )
                        diff["socket_defaults_changed"] += 1
                        summary = f"Set default for {operation['node']}"

            elif op in {"add_link", "remove_link"}:
                from_node = resolve_node(operation["from_node"], f"{path}/from_node")
                to_node = resolve_node(operation["to_node"], f"{path}/to_node")
                if from_node is not None and to_node is not None:
                    if not _node_mutation_allowed(from_node, op, path, diagnostics):
                        from_node = None
                    if not _node_mutation_allowed(to_node, op, path, diagnostics):
                        to_node = None
                if from_node is not None and to_node is not None:
                    from_socket = _gn_resolve_patch_socket(
                        from_node, operation["from_socket"], "output",
                        f"{path}/from_socket", diagnostics,
                    )
                    to_socket = _gn_resolve_patch_socket(
                        to_node, operation["to_socket"], "input",
                        f"{path}/to_socket", diagnostics,
                    )
                    if from_socket is not None and to_socket is not None:
                        existing = next((
                            link for link in tree.links
                            if link.from_socket == from_socket and link.to_socket == to_socket
                        ), None)
                        if op == "add_link":
                            if existing is not None:
                                raise ValueError("Link already exists")
                            if not getattr(to_socket, "is_multi_input", False) and to_socket.is_linked:
                                raise ValueError(
                                    "Remove the existing link before linking this input"
                                )
                            link = tree.links.new(from_socket, to_socket, verify_limits=True)
                            if not link.is_valid:
                                raise ValueError("Blender rejected the projected link")
                            if from_socket.type != to_socket.type:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "warning", "implicit_socket_conversion", path,
                                    f"Blender converts {from_socket.type} to {to_socket.type}",
                                ))
                            diff["links_added"] += 1
                        else:
                            if existing is None:
                                raise ValueError("Link does not exist")
                            tree.links.remove(existing)
                            diff["links_removed"] += 1
                        summary = f"{op} {operation['from_node']} -> {operation['to_node']}"

            elif op == "set_node_layout":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    for field in ("location", "width", "height"):
                        if field in operation:
                            setattr(node, field, operation[field])
                    if "parent" in operation:
                        if operation["parent"] is None:
                            node.parent = None
                        else:
                            parent = resolve_node(operation["parent"], f"{path}/parent")
                            if parent is not None:
                                if parent == node or parent.bl_idname != "NodeFrame":
                                    raise ValueError("Node parent must be a different Frame")
                                node.parent = parent
                    diff["layouts_changed"] += 1
                    summary = f"Update layout for {operation['node']}"

            elif op == "set_annotation":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    if node.bl_idname != "NodeFrame":
                        raise ValueError("Annotations may only be attached to Frame nodes")
                    if operation["text"]:
                        node["blender_mcp_note"] = operation["text"]
                    elif "blender_mcp_note" in node:
                        del node["blender_mcp_note"]
                    diff["annotations_changed"] += 1
                    summary = f"Set annotation for {operation['node']}"

            elif op == "add_interface_socket":
                if not _node_interface_mutable(target):
                    raise ValueError("This owner does not expose a mutable node interface")
                if operation["id"] in interface_refs:
                    raise ValueError(f"Interface reference already exists: {operation['id']}")
                parent = None
                if operation.get("parent"):
                    parent_record = interface_refs.get(operation["parent"])
                    if parent_record is None or parent_record["removed"]:
                        raise ValueError(f"Interface parent not found: {operation['parent']}")
                    parent = parent_record["item"]
                    if parent.item_type != "PANEL":
                        raise ValueError("Interface parent must be a panel")
                item = tree.interface.new_socket(
                    name=operation["name"],
                    in_out=operation["in_out"],
                    socket_type=operation["socket_type"],
                    parent=parent,
                )
                if "default" in operation and hasattr(item, "default_value"):
                    if _gn_validate_value(
                        item, "default_value", operation["default"], f"{path}/default",
                        diagnostics, node_refs,
                    ):
                        item.default_value = _gn_decode_patch_value(
                            operation["default"], node_refs
                        )
                interface_refs[operation["id"]] = {"item": item, "removed": False}
                created_interface[operation["id"]] = (
                    getattr(item, "identifier", "") or item.name
                )
                diff["interface_sockets_added"] += 1
                summary = f"Add interface socket {operation['id']}"

            elif op == "remove_interface_socket":
                if not _node_interface_mutable(target):
                    raise ValueError("This owner does not expose a mutable node interface")
                record = interface_refs.get(operation["identifier"])
                if record is None or record["removed"] or record["item"].item_type != "SOCKET":
                    raise ValueError(
                        f"Interface socket not found: {operation['identifier']}"
                    )
                tree.interface.remove(record["item"])
                record["removed"] = True
                created_interface.pop(operation["identifier"], None)
                diff["interface_sockets_removed"] += 1
                summary = f"Remove interface socket {operation['identifier']}"

            elif op == "set_color_ramp":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    _node_apply_color_ramp(node, operation)
                    diff["dynamic_structures_changed"] += 1
                    summary = f"Set Color Ramp on {operation['node']}"

            elif op == "set_curve_mapping":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    _node_apply_curve_mapping(node, operation)
                    diff["dynamic_structures_changed"] += 1
                    summary = f"Set Curve Mapping on {operation['node']}"
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            unavailable_node_type = (
                op == "add_node"
                and isinstance(exc, RuntimeError)
                and operation.get("id") not in node_refs
            )
            if unavailable_node_type:
                version = ".".join(str(item) for item in bpy.app.version[:3])
                code = "unsupported_node_type"
                diagnostic_path = f"{path}/node_type"
                message = (
                    f"{operation['node_type']} is unavailable for "
                    f"{target['tree_type']}/{target['owner_kind']} in Blender "
                    f"{version}: {exc}"
                )
            else:
                code = "operation_rejected"
                diagnostic_path = path
                message = f"{type(exc).__name__}: {exc}"
            diagnostics.append(_gn_patch_diagnostic(
                "error", code, diagnostic_path, message,
            ))
        errors_after = sum(item["severity"] == "error" for item in diagnostics)
        plan.append({
            "index": index,
            "op": op,
            "status": "ready" if errors_after == errors_before else "invalid",
            "summary": summary,
        })
    return {
        "diagnostics": diagnostics,
        "plan": plan,
        "semantic_diff": diff,
        "created_nodes": created_nodes,
        "created_interface": created_interface,
    }


def _node_validate_patch_runtime(target, patch):
    validation_started = time.perf_counter()
    current_snapshot = _node_export_target(target, "all")
    current_revision = current_snapshot["revision"]
    diagnostics = []
    try:
        patch_ref = _node_normalize_tree_ref(patch.get("tree_ref"))
    except ValueError as exc:
        patch_ref = patch.get("tree_ref")
        diagnostics.append(_gn_patch_diagnostic(
            "error", "invalid_tree_ref", "/tree_ref", str(exc),
        ))
    if patch_ref != target["tree_ref"]:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_ref_mismatch", "/tree_ref",
            "Patch tree_ref does not identify the resolved target",
        ))
    if patch.get("base_revision") != current_revision:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "stale_revision", "/base_revision",
            f"Patch revision {patch.get('base_revision')!r} does not match current {current_revision!r}",
        ))
    capabilities = _node_target_capabilities(target)
    if not capabilities["editable"]:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_not_editable", "/tree_ref",
            "The selected owner or NodeTree is linked or otherwise read-only",
        ))
    if len(target["tree"].nodes) > NODE_TREE_MAX_MUTATION_NODES:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_node_limit_exceeded", "/tree_ref",
            f"Mutation supports at most {NODE_TREE_MAX_MUTATION_NODES} nodes; "
            f"the selected tree has {len(target['tree'].nodes)}",
        ))

    execution = {
        "diagnostics": [],
        "plan": [],
        "semantic_diff": {},
    }
    candidate_snapshot = None
    if not any(item["severity"] == "error" for item in diagnostics):
        working_target = working_owner = standalone_tree = None
        try:
            working_target, working_owner, standalone_tree = _node_validation_copy(target)
            execution = _node_execute_patch_operations(working_target, patch)
            diagnostics.extend(execution["diagnostics"])
            projected_node_count = len(working_target["tree"].nodes)
            if projected_node_count > NODE_TREE_MAX_MUTATION_NODES:
                diagnostics.append(_gn_patch_diagnostic(
                    "error", "projected_tree_node_limit_exceeded", "/operations",
                    f"The patch would create {projected_node_count} nodes; the limit is "
                    f"{NODE_TREE_MAX_MUTATION_NODES}",
                ))
            if not any(item["severity"] == "error" for item in diagnostics):
                invalid_links = [link for link in working_target["tree"].links if not link.is_valid]
                if invalid_links:
                    raise RuntimeError(
                        f"Projected tree contains {len(invalid_links)} invalid links"
                    )
                candidate_snapshot = _node_export_tree(
                    working_target["tree"],
                    "all",
                    schema=NODE_TREE_SNAPSHOT_SCHEMA,
                    tree_ref=target["tree_ref"],
                    owner=target["owner"],
                    capabilities=capabilities,
                )
        except Exception as exc:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "dry_run_execution_rejected", "",
                f"{type(exc).__name__}: {exc}",
            ))
        finally:
            if working_owner is not None:
                _node_remove_validation_copy(target, working_owner, standalone_tree)

    elapsed_ms = (time.perf_counter() - validation_started) * 1000.0
    if elapsed_ms > NODE_TREE_MAX_VALIDATION_SECONDS * 1000.0:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "validation_time_limit_exceeded", "",
            f"Validation took {elapsed_ms:.3f} ms; the limit is "
            f"{NODE_TREE_MAX_VALIDATION_SECONDS * 1000.0:.0f} ms",
        ))
    result = {
        "schema": "blender-node-tree-patch-validation/1",
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "stage": "runtime",
        "will_mutate": False,
        "tree_ref": target["tree_ref"],
        "base_revision": patch.get("base_revision"),
        "current_revision": current_revision,
        "capabilities": capabilities,
        "users": current_snapshot["users"],
        "diagnostics": diagnostics,
        "plan": execution["plan"],
        "semantic_diff": execution["semantic_diff"],
        "timing_ms": round(elapsed_ms, 3),
    }
    if candidate_snapshot is not None:
        result["candidate_revision"] = candidate_snapshot["revision"]
        result["candidate_stats"] = candidate_snapshot["stats"]
    return result


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
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "applied": False,
            "mutated": bool(rollback_errors),
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": _node_export_target(
                _node_resolve_tree_ref(target["tree_ref"]), "all"
            )["revision"],
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
        return {
            "schema": "blender-node-tree-patch-application/1",
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "applied": False,
            "mutated": bool(rollback_errors),
            "tree_ref": target["tree_ref"],
            "base_revision": patch.get("base_revision"),
            "current_revision": _node_export_target(target, "all")["revision"],
            "diagnostics": diagnostics,
            "plan": validation["plan"],
        }
    finally:
        if not committed and working is not None:
            try:
                _node_remove_validation_copy(target, working, standalone_tree)
            except (AttributeError, RuntimeError):
                pass


def _gn_assign_user(handle, kind, tree):
    if kind == "MODIFIER":
        handle.node_group = tree
    else:
        handle.node_tree = tree


def _gn_apply_operations_to_working(working, patch):
    node_refs = {
        node.name: {"node": node, "existing": True, "removed": False}
        for node in working.nodes
    }
    interface_refs = {
        (getattr(item, "identifier", "") or item.name): {
            "item": item,
            "actual_identifier": getattr(item, "identifier", "") or item.name,
            "removed": False,
        }
        for item in working.interface.items_tree
    }
    created_nodes = {}
    created_interface = {}
    deferred_modifier_inputs = []
    operation_results = []

    for index, operation in enumerate(patch["operations"]):
        op = operation["op"]
        result = {"index": index, "op": op, "status": "applied"}

        if op == "add_node":
            node = working.nodes.new(operation["node_type"])
            reference = operation["id"]
            node_refs[reference] = {"node": node, "existing": False, "removed": False}
            if "name" in operation:
                node.name = operation["name"]
            for property_name, value in operation.get("properties", {}).items():
                setattr(node, property_name, _gn_decode_patch_value(value, node_refs))
            layout = operation.get("layout", {})
            for field in ("location", "width", "height"):
                if field in layout:
                    setattr(node, field, layout[field])
            if layout.get("parent") is not None:
                node.parent = node_refs[layout["parent"]]["node"]
            created_nodes[reference] = node.name
            result["node_name"] = node.name

        elif op == "remove_node":
            item = node_refs[operation["node"]]
            working.nodes.remove(item["node"])
            item["removed"] = True
            if not item["existing"]:
                created_nodes.pop(operation["node"], None)

        elif op == "rename_node":
            node = node_refs[operation["node"]]["node"]
            node.name = operation["name"]
            if operation["node"] in created_nodes:
                created_nodes[operation["node"]] = node.name
            result["node_name"] = node.name

        elif op == "set_node_property":
            node = node_refs[operation["node"]]["node"]
            setattr(
                node,
                operation["property"],
                _gn_decode_patch_value(operation["value"], node_refs),
            )

        elif op == "set_socket_default":
            node = node_refs[operation["node"]]["node"]
            socket = _gn_resolve_patch_socket(
                node, operation["socket"], "input", "", [],
            )
            if socket is None:
                raise RuntimeError(f"Validated input socket disappeared: {operation['socket']}")
            socket.default_value = _gn_decode_patch_value(operation["value"], node_refs)

        elif op in {"add_link", "remove_link"}:
            from_node = node_refs[operation["from_node"]]["node"]
            to_node = node_refs[operation["to_node"]]["node"]
            from_socket = _gn_resolve_patch_socket(
                from_node, operation["from_socket"], "output", "", [],
            )
            to_socket = _gn_resolve_patch_socket(
                to_node, operation["to_socket"], "input", "", [],
            )
            if from_socket is None or to_socket is None:
                raise RuntimeError("Validated link socket disappeared")
            if op == "add_link":
                working.links.new(from_socket, to_socket, verify_limits=True)
            else:
                match = next(
                    (
                        link for link in working.links
                        if link.from_socket == from_socket and link.to_socket == to_socket
                    ),
                    None,
                )
                if match is None:
                    raise RuntimeError("Validated link disappeared before removal")
                working.links.remove(match)

        elif op == "set_node_layout":
            node = node_refs[operation["node"]]["node"]
            for field in ("location", "width", "height"):
                if field in operation:
                    setattr(node, field, operation[field])
            if "parent" in operation:
                node.parent = (
                    node_refs[operation["parent"]]["node"]
                    if operation["parent"] is not None else None
                )

        elif op == "add_interface_socket":
            parent_ref = operation.get("parent")
            parent = interface_refs[parent_ref]["item"] if parent_ref else None
            item = working.interface.new_socket(
                name=operation["name"],
                in_out=operation["in_out"],
                socket_type=operation["socket_type"],
                parent=parent,
            )
            if "default" in operation and hasattr(item, "default_value"):
                item.default_value = _gn_decode_patch_value(operation["default"], node_refs)
            reference = operation["id"]
            actual_identifier = getattr(item, "identifier", "") or item.name
            interface_refs[reference] = {
                "item": item,
                "actual_identifier": actual_identifier,
                "removed": False,
            }
            created_interface[reference] = actual_identifier
            result["interface_identifier"] = actual_identifier

        elif op == "remove_interface_socket":
            item = interface_refs[operation["identifier"]]
            working.interface.remove(item["item"])
            item["removed"] = True
            created_interface.pop(operation["identifier"], None)

        elif op == "set_modifier_input":
            deferred_modifier_inputs.append({
                **operation,
                "actual_identifier": interface_refs[operation["socket"]]["actual_identifier"],
            })

        operation_results.append(result)

    return {
        "node_refs": node_refs,
        "interface_refs": interface_refs,
        "created_nodes": created_nodes,
        "created_interface": created_interface,
        "deferred_modifier_inputs": deferred_modifier_inputs,
        "operation_results": operation_results,
    }


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
        return {
            "schema": "blender-geometry-nodes-patch-application/1",
            "status": "rollback_failed" if rollback_errors else "rolled_back",
            "applied": False,
            "mutated": bool(rollback_errors),
            "tree_name": original_name,
            "base_revision": patch.get("base_revision"),
            "current_revision": _gn_export_tree(tree, "all")["revision"],
            "diagnostics": rollback_diagnostics,
            "plan": validation["plan"],
        }

class BlenderMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def _get_config_value(self, scene_attr, pref_attr=None, env_var=None):
        """Read config in order: addon preferences -> scene -> env var."""
        prefs = get_blendermcp_addon_preferences()
        if prefs and pref_attr:
            pref_value = getattr(prefs, pref_attr, "")
            if pref_value:
                return pref_value

        scene_value = getattr(bpy.context.scene, scene_attr, "")
        if scene_value:
            return scene_value

        if env_var:
            env_value = os.getenv(env_var, "")
            if env_value:
                return env_value
        return ""

    def _get_hyper3d_api_key(self):
        # Let the free-trial button temporarily override persistent keys
        # without overwriting user-saved private keys.
        scene_value = getattr(bpy.context.scene, "blendermcp_hyper3d_api_key", "")
        if scene_value == RODIN_FREE_TRIAL_KEY:
            return scene_value
        return self._get_config_value(
            "blendermcp_hyper3d_api_key",
            "hyper3d_api_key",
            "BLENDERMCP_HYPER3D_API_KEY",
        )

    def _get_sketchfab_api_key(self):
        return self._get_config_value(
            "blendermcp_sketchfab_api_key",
            "sketchfab_api_key",
            "BLENDERMCP_SKETCHFAB_API_KEY",
        )

    def _get_hunyuan3d_secret_id(self):
        return self._get_config_value(
            "blendermcp_hunyuan3d_secret_id",
            "hunyuan3d_secret_id",
            "BLENDERMCP_HUNYUAN3D_SECRET_ID",
        )

    def _get_hunyuan3d_secret_key(self):
        return self._get_config_value(
            "blendermcp_hunyuan3d_secret_key",
            "hunyuan3d_secret_key",
            "BLENDERMCP_HUNYUAN3D_SECRET_KEY",
        )

    def _get_hunyuan3d_api_url(self):
        return self._get_config_value(
            "blendermcp_hunyuan3d_api_url",
            "hunyuan3d_api_url",
            "BLENDERMCP_HUNYUAN3D_API_URL",
        ) or "http://localhost:8081"

    def start(self):
        if bpy.app.background:
            print("BlenderMCP: cannot start server in background mode (blender -b) - commands would never execute\n"
                  "BlenderMCP: run Blender with a GUI, or use a virtual display: xvfb-run -a blender")
            return

        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("BlenderMCP server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None

                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Add a handler for checking PolyHaven status
        if cmd_type == "get_polyhaven_status":
            return {"status": "success", "result": self.get_polyhaven_status()}

        # Base handlers that are always available
        handlers = {
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "get_blender_version_context": self.get_blender_version_context,
            "list_node_trees": self.list_node_trees,
            "export_node_tree": self.export_node_tree,
            "get_node_tree_index": self.get_node_tree_index,
            "get_node_type_schema": self.get_node_type_schema,
            "validate_node_tree_patch": self.validate_node_tree_patch,
            "apply_node_tree_patch": self.apply_node_tree_patch,
            "list_geometry_node_trees": self.list_geometry_node_trees,
            "export_geometry_node_tree": self.export_geometry_node_tree,
            "get_geometry_node_type_schema": self.get_geometry_node_type_schema,
            "search_geometry_node_types": self.search_geometry_node_types,
            "search_blender_node_assets": self.search_blender_node_assets,
            "get_geometry_node_tree_index": self.get_geometry_node_tree_index,
            "validate_geometry_node_patch": self.validate_geometry_node_patch,
            "apply_geometry_node_patch": self.apply_geometry_node_patch,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "get_telemetry_consent": self.get_telemetry_consent,
            "get_polyhaven_status": self.get_polyhaven_status,
            "get_hyper3d_status": self.get_hyper3d_status,
            "get_sketchfab_status": self.get_sketchfab_status,
            "get_hunyuan3d_status": self.get_hunyuan3d_status,
        }

        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.blendermcp_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
                "set_texture": self.set_texture,
            }
            handlers.update(polyhaven_handlers)

        # Add Hyper3d handlers only if enabled
        if bpy.context.scene.blendermcp_use_hyper3d:
            polyhaven_handlers = {
                "create_rodin_job": self.create_rodin_job,
                "poll_rodin_job_status": self.poll_rodin_job_status,
                "import_generated_asset": self.import_generated_asset,
            }
            handlers.update(polyhaven_handlers)

        # Add Sketchfab handlers only if enabled
        if bpy.context.scene.blendermcp_use_sketchfab:
            sketchfab_handlers = {
                "search_sketchfab_models": self.search_sketchfab_models,
                "get_sketchfab_model_preview": self.get_sketchfab_model_preview,
                "download_sketchfab_model": self.download_sketchfab_model,
            }
            handlers.update(sketchfab_handlers)
        
        # Add Hunyuan3d handlers only if enabled
        if bpy.context.scene.blendermcp_use_hunyuan3d:
            hunyuan_handlers = {
                "create_hunyuan_job": self.create_hunyuan_job,
                "poll_hunyuan_job_status": self.poll_hunyuan_job_status,
                "import_generated_asset_hunyuan": self.import_generated_asset_hunyuan
            }
            handlers.update(hunyuan_handlers)

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}



    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # Reduced from 20 to 10
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def get_blender_version_context(self):
        """Return exact version/build metadata for documentation resolution."""
        return _blender_version_context()

    def list_node_trees(self, tree_types=None, owner_kinds=None):
        """List owner-addressed Geometry, Shader, and Compositor trees."""
        tree_types = list(tree_types or ())
        owner_kinds = [str(item).strip().upper() for item in (owner_kinds or ())]
        invalid_tree_types = sorted(set(tree_types) - NODE_TREE_TYPES)
        invalid_owner_kinds = sorted(set(owner_kinds) - NODE_TREE_OWNER_KINDS)
        if invalid_tree_types:
            raise ValueError(
                "Unsupported tree_types: " + ", ".join(invalid_tree_types)
            )
        if invalid_owner_kinds:
            raise ValueError(
                "Unsupported owner_kinds: " + ", ".join(invalid_owner_kinds)
            )
        records = []
        for target in _node_iter_targets():
            if tree_types and target["tree_type"] not in tree_types:
                continue
            if owner_kinds and target["owner_kind"] not in owner_kinds:
                continue
            snapshot = _node_export_target(target, "semantic")
            records.append({
                "domain": target["domain"],
                "tree_ref": target["tree_ref"],
                "owner": target["owner"],
                "tree": {
                    "name": target["tree"].name,
                    "bl_idname": target["tree"].bl_idname,
                    "library": _node_id_library(target["tree"]),
                    "editable": _node_id_editable(target["tree"]),
                },
                "capabilities": _node_target_capabilities(target),
                "revision": snapshot["revision"],
                "node_count": snapshot["stats"]["node_count"],
                "link_count": snapshot["stats"]["link_count"],
                "interface_item_count": snapshot["stats"]["interface_item_count"],
                "users": snapshot["users"],
            })
        return {
            "schema": NODE_TREE_SNAPSHOT_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "tree_types": tree_types,
            "owner_kinds": owner_kinds,
            "tree_count": len(records),
            "trees": records,
        }

    def export_node_tree(
        self, tree_ref, view="semantic", node_names=None, neighbor_depth=0,
    ):
        """Export an owner-addressed NodeTree as deterministic flat JSON."""
        target = _node_resolve_tree_ref(tree_ref)
        snapshot = _node_export_target(
            target, view, node_names or [], neighbor_depth,
        )
        if not node_names and snapshot["stats"]["json_bytes"] > NODE_TREE_MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Full node-tree response is {snapshot['stats']['json_bytes']} bytes; "
                f"the limit is {NODE_TREE_MAX_RESPONSE_BYTES}. Use get_node_tree_index "
                "and export_node_tree with node_names."
            )
        return snapshot

    def get_node_tree_index(self, tree_ref, query="", offset=0, limit=100):
        """Return a compact index for one owner-addressed NodeTree."""
        return _node_tree_index(
            _node_resolve_tree_ref(tree_ref), query, offset, limit,
        )

    def get_node_type_schema(
        self, tree_type, node_type, owner_kind="NODE_GROUP", detail="compact",
    ):
        """Inspect a node type in an exact tree and owner context."""
        return _node_type_schema(tree_type, owner_kind, node_type, detail)

    def validate_node_tree_patch(self, patch):
        """Dry-run a generic node-tree patch on an owner-aware disposable copy."""
        try:
            target = _node_resolve_tree_ref(patch.get("tree_ref"))
        except (AttributeError, TypeError, ValueError) as exc:
            return {
                "schema": "blender-node-tree-patch-validation/1",
                "valid": False,
                "stage": "runtime",
                "will_mutate": False,
                "tree_ref": patch.get("tree_ref"),
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "target_resolution_failed", "/tree_ref", str(exc),
                )],
                "plan": [],
                "semantic_diff": {},
            }
        return _node_validate_patch_runtime(target, patch)

    def apply_node_tree_patch(self, patch, keep_backup=True):
        """Apply a validated generic patch through its owner transaction."""
        try:
            target = _node_resolve_tree_ref(patch.get("tree_ref"))
        except (AttributeError, TypeError, ValueError) as exc:
            return {
                "schema": "blender-node-tree-patch-application/1",
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "tree_ref": patch.get("tree_ref") if isinstance(patch, dict) else None,
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "target_resolution_failed", "/tree_ref", str(exc),
                )],
                "plan": [],
            }
        return _node_apply_patch_transaction(
            target, patch, bool(keep_backup),
        )

    def list_geometry_node_trees(self):
        """List Geometry Node groups with revisions and user summaries."""
        trees = []
        for tree in _gn_geometry_trees():
            snapshot = _gn_export_tree(tree, "semantic")
            trees.append({
                "name": tree.name,
                "editable": snapshot["tree"]["editable"],
                "library": snapshot["tree"]["library"],
                "revision": snapshot["revision"],
                "node_count": snapshot["stats"]["node_count"],
                "link_count": snapshot["stats"]["link_count"],
                "interface_item_count": snapshot["stats"]["interface_item_count"],
                "users": snapshot["users"],
            })
        return {
            "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "tree_count": len(trees),
            "trees": trees,
        }

    def export_geometry_node_tree(self, tree_name, view="semantic", node_names=None, neighbor_depth=0):
        """Export one Geometry Node group as normalized graph JSON."""
        tree = bpy.data.node_groups.get(tree_name)
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            raise ValueError(f"Geometry Node tree not found: {tree_name}")
        return _gn_export_tree(tree, view, node_names, neighbor_depth)

    def get_geometry_node_type_schema(self, node_type, detail="compact"):
        """Inspect a node type from this running Blender build."""
        if not isinstance(node_type, str) or not node_type.strip():
            raise ValueError("node_type must be a non-empty Blender node bl_idname")
        detail = str(detail or "compact").strip().lower()
        if detail not in GEOMETRY_NODE_TYPE_SCHEMA_DETAILS:
            raise ValueError("detail must be 'compact' or 'full'")

        tree = bpy.data.node_groups.new(".BlenderMCP_TypeSchema", "GeometryNodeTree")
        try:
            try:
                node = tree.nodes.new(type=node_type.strip())
            except RuntimeError as exc:
                raise ValueError(
                    f"Unsupported Geometry Node type in Blender {bpy.app.version_string}: {node_type}"
                ) from exc

            property_source = (
                _gn_node_owned_properties(node)
                if detail == "compact"
                else node.bl_rna.properties
            )
            properties = []
            dynamic_items = []
            for prop in property_source:
                if prop.identifier in _GN_NODE_PROPERTY_EXCLUDES or prop.identifier == "rna_type":
                    continue
                if getattr(prop, "is_hidden", False):
                    continue
                if prop.type == "COLLECTION":
                    if detail == "compact":
                        dynamic_items.append(_gn_dynamic_collection_schema(node, prop))
                    continue
                properties.append(_gn_property_schema(node, prop))

            result = {
                "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
                "blender_version": list(bpy.app.version[:3]),
                "blender_version_string": bpy.app.version_string,
                "build_hash": _blender_app_text("build_hash"),
                "detail": detail,
                "node_type": node.bl_idname,
                "label": node.bl_label,
                "description": node.bl_description,
                "properties": properties,
                "inputs": [
                    (_gn_socket_type_schema if detail == "compact" else _gn_socket_record)(
                        socket, "INPUT", index
                    )
                    for index, socket in enumerate(node.inputs)
                ],
                "outputs": [
                    (_gn_socket_type_schema if detail == "compact" else _gn_socket_record)(
                        socket, "OUTPUT", index
                    )
                    for index, socket in enumerate(node.outputs)
                ],
            }
            if detail == "compact":
                result["dynamic_items"] = dynamic_items
            return result
        finally:
            bpy.data.node_groups.remove(tree)

    def search_geometry_node_types(self, query="", offset=0, limit=100):
        """Search registered node types constructible in Geometry Nodes."""
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be from 1 to 500")
        query_value = "" if query is None else str(query)
        query_text = query_value.strip().casefold()
        catalog = _gn_geometry_node_type_catalog()
        matches = [
            item for item in catalog
            if not query_text or query_text in " ".join((
                item["bl_idname"], item["label"], item["description"], item["category"],
            )).casefold()
        ]
        page = matches[offset:offset + limit]
        next_offset = offset + len(page)
        return {
            "schema": GEOMETRY_NODE_TYPE_CATALOG_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "query": query_value,
            "offset": offset,
            "limit": limit,
            "total_types": len(catalog),
            "total_matches": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None,
            "node_types": page,
        }

    def search_blender_node_assets(
        self, query="", library="", tree_type="", detail="summary", offset=0, limit=20,
    ):
        """Search installed official Essentials node assets without retaining IDs."""
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        detail = str(detail or "summary").strip().lower()
        if detail not in {"summary", "full"}:
            raise ValueError("detail must be 'summary' or 'full'")
        if detail == "full" and limit > 20:
            raise ValueError("full detail limit must not exceed 20")
        query_value = "" if query is None else str(query)
        library_value = "" if library is None else str(library)
        tree_type_value = "" if tree_type is None else str(tree_type)
        query_text = query_value.strip().casefold()
        library_text = library_value.strip().casefold()
        tree_type_text = tree_type_value.strip().casefold()
        catalog = _gn_official_node_asset_catalog()
        matches = []
        for item in catalog["records"]:
            if library_text and library_text not in item["source_library"].casefold():
                continue
            if tree_type_text and tree_type_text != item["tree_type"].casefold():
                continue
            haystack = " ".join((
                item["name"], item["description"], item["author"],
                item["source_library"], item["tree_type"], " ".join(item["tags"]),
            )).casefold()
            if query_text and query_text not in haystack:
                continue
            matches.append(item)
        page = matches[offset:offset + limit]
        next_offset = offset + len(page)
        return {
            "schema": BLENDER_NODE_ASSET_CATALOG_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "query": query_value,
            "library": library_value,
            "tree_type": tree_type_value,
            "detail": detail,
            "offset": offset,
            "limit": limit,
            "library_count": len(catalog["library_paths"]),
            "total_assets": len(catalog["records"]),
            "total_matches": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None,
            "assets": [
                dict(item) if detail == "full" else _gn_node_asset_summary(item)
                for item in page
            ],
            "errors": catalog["errors"],
        }

    def get_geometry_node_tree_index(self, tree_name, query="", offset=0, limit=100):
        """Return a searchable, paginated node-name/type index for subgraph discovery."""
        tree = bpy.data.node_groups.get(tree_name)
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            raise ValueError(f"Geometry Node tree not found: {tree_name}")
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be from 1 to 500")

        query_value = "" if query is None else str(query)
        query_text = query_value.strip().casefold()
        matches = [
            node for node in sorted(tree.nodes, key=lambda item: item.name)
            if not query_text or query_text in " ".join(
                (node.name, node.label, node.bl_idname, node.bl_label)
            ).casefold()
        ]
        page = matches[offset:offset + limit]
        revision = _gn_export_tree(tree, "all")["revision"]
        next_offset = offset + len(page)
        return {
            "schema": "blender-geometry-nodes-index/1",
            "blender_version": list(bpy.app.version[:3]),
            "tree_name": tree.name,
            "revision": revision,
            "query": query_value,
            "offset": offset,
            "limit": limit,
            "total_nodes": len(tree.nodes),
            "total_matches": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None,
            "nodes": [
                {
                    "name": node.name,
                    "label": node.label,
                    "bl_idname": node.bl_idname,
                    "bl_label": node.bl_label,
                }
                for node in page
            ],
        }

    def validate_geometry_node_patch(self, patch):
        """Build a runtime-resolved patch plan without mutating live Blender data."""
        if not isinstance(patch, dict):
            raise ValueError("Geometry Nodes patch must be a JSON object")
        if patch.get("schema") != GEOMETRY_NODES_PATCH_SCHEMA:
            raise ValueError(f"Expected patch schema {GEOMETRY_NODES_PATCH_SCHEMA!r}")
        tree_name = patch.get("tree_name")
        tree = bpy.data.node_groups.get(tree_name) if isinstance(tree_name, str) else None
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            return {
                "schema": GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA,
                "valid": False,
                "stage": "runtime",
                "will_mutate": False,
                "tree_name": tree_name,
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "tree_not_found", "/tree_name",
                    f"Geometry Node tree not found: {tree_name}",
                )],
                "plan": [],
                "semantic_diff": {},
            }
        return _gn_validate_patch_runtime(tree, patch)

    def apply_geometry_node_patch(self, patch, keep_backup=True):
        """Apply a validated patch through a copy-on-write transaction."""
        if not isinstance(patch, dict):
            raise ValueError("Geometry Nodes patch must be a JSON object")
        if patch.get("schema") != GEOMETRY_NODES_PATCH_SCHEMA:
            raise ValueError(f"Expected patch schema {GEOMETRY_NODES_PATCH_SCHEMA!r}")
        tree_name = patch.get("tree_name")
        tree = bpy.data.node_groups.get(tree_name) if isinstance(tree_name, str) else None
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            return {
                "schema": "blender-geometry-nodes-patch-application/1",
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "tree_name": tree_name,
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "tree_not_found", "/tree_name",
                    f"Geometry Node tree not found: {tree_name}",
                )],
                "plan": [],
            }
        return _gn_apply_patch_transaction(tree, patch, bool(keep_backup))

    @staticmethod
    def _get_aabb(obj):
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]



    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """
        Capture a screenshot of the current 3D viewport and save it to the specified path.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Path where to save the screenshot file
        - format: Image format (png, jpg, etc.)

        Returns success/error status
        """
        try:
            if not filepath:
                return {"error": "No filepath provided"}

            # Find the active 3D viewport
            area = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    break

            if not area:
                return {"error": "No 3D viewport found"}

            # Take screenshot with proper context override
            with bpy.context.temp_override(area=area):
                bpy.ops.screen.screenshot_area(filepath=filepath)

            # Load and resize if needed
            img = bpy.data.images.load(filepath)
            width, height = img.size

            if max(width, height) > max_size:
                scale = max_size / max(width, height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                img.scale(new_width, new_height)

                # Set format and save
                img.file_format = format.upper()
                img.save()
                width, height = new_width, new_height

            # Cleanup Blender image data
            bpy.data.images.remove(img)

            return {
                "success": True,
                "width": width,
                "height": height,
                "filepath": filepath
            }

        except Exception as e:
            return {"error": str(e)}

    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}

            # Capture stdout during execution, and return it as result
            capture_buffer = io.StringIO()
            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            captured_output = capture_buffer.getvalue()
            return {"executed": True, "result": captured_output}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")



    def get_polyhaven_categories(self, asset_type):
        """Get categories for a specific asset type from Polyhaven"""
        try:
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}

            response = requests.get(f"https://api.polyhaven.com/categories/{asset_type}", headers=REQ_HEADERS)
            if response.status_code == 200:
                return {"categories": response.json()}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def search_polyhaven_assets(self, asset_type=None, categories=None):
        """Search for assets from Polyhaven with optional filtering"""
        try:
            url = "https://api.polyhaven.com/assets"
            params = {}

            if asset_type and asset_type != "all":
                if asset_type not in ["hdris", "textures", "models"]:
                    return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                params["type"] = asset_type

            if categories:
                params["categories"] = categories

            response = requests.get(url, params=params, headers=REQ_HEADERS)
            if response.status_code == 200:
                # Limit the response size to avoid overwhelming Blender
                assets = response.json()
                # Return only the first 20 assets to keep response size manageable
                limited_assets = {}
                for i, (key, value) in enumerate(assets.items()):
                    if i >= 20:  # Limit to 20 assets
                        break
                    limited_assets[key] = value

                return {"assets": limited_assets, "total_count": len(assets), "returned_count": len(limited_assets)}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def download_polyhaven_asset(self, asset_id, asset_type, resolution="1k", file_format=None):
        try:
            # First get the files information
            files_response = requests.get(f"https://api.polyhaven.com/files/{asset_id}", headers=REQ_HEADERS)
            if files_response.status_code != 200:
                return {"error": f"Failed to get asset files: {files_response.status_code}"}

            files_data = files_response.json()

            # Handle different asset types
            if asset_type == "hdris":
                # For HDRIs, download the .hdr or .exr file
                if not file_format:
                    file_format = "hdr"  # Default format for HDRIs

                if "hdri" in files_data and resolution in files_data["hdri"] and file_format in files_data["hdri"][resolution]:
                    file_info = files_data["hdri"][resolution][file_format]
                    file_url = file_info["url"]

                    # For HDRIs, we need to save to a temporary file first
                    # since Blender can't properly load HDR data directly from memory
                    with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                        # Download the file
                        response = requests.get(file_url, headers=REQ_HEADERS)
                        if response.status_code != 200:
                            return {"error": f"Failed to download HDRI: {response.status_code}"}

                        tmp_file.write(response.content)
                        tmp_path = tmp_file.name

                    try:
                        # Create a new world if none exists
                        if not bpy.data.worlds:
                            bpy.data.worlds.new("World")

                        world = bpy.data.worlds[0]
                        world.use_nodes = True
                        node_tree = world.node_tree

                        # Clear existing nodes
                        for node in node_tree.nodes:
                            node_tree.nodes.remove(node)

                        # Create nodes
                        tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
                        tex_coord.location = (-800, 0)

                        mapping = node_tree.nodes.new(type='ShaderNodeMapping')
                        mapping.location = (-600, 0)

                        # Load the image from the temporary file
                        env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
                        env_tex.location = (-400, 0)
                        env_tex.image = bpy.data.images.load(tmp_path)

                        # Use a color space that exists in all Blender versions
                        if file_format.lower() == 'exr':
                            # Try to use Linear color space for EXR files
                            try:
                                env_tex.image.colorspace_settings.name = 'Linear'
                            except:
                                # Fallback to Non-Color if Linear isn't available
                                env_tex.image.colorspace_settings.name = 'Non-Color'
                        else:  # hdr
                            # For HDR files, try these options in order
                            for color_space in ['Linear', 'Linear Rec.709', 'Non-Color']:
                                try:
                                    env_tex.image.colorspace_settings.name = color_space
                                    break  # Stop if we successfully set a color space
                                except:
                                    continue

                        background = node_tree.nodes.new(type='ShaderNodeBackground')
                        background.location = (-200, 0)

                        output = node_tree.nodes.new(type='ShaderNodeOutputWorld')
                        output.location = (0, 0)

                        # Connect nodes
                        node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
                        node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
                        node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
                        node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])

                        # Set as active world
                        bpy.context.scene.world = world

                        # Clean up temporary file
                        try:
                            tempfile._cleanup()  # This will clean up all temporary files
                        except:
                            pass

                        return {
                            "success": True,
                            "message": f"HDRI {asset_id} imported successfully",
                            "image_name": env_tex.image.name
                        }
                    except Exception as e:
                        return {"error": f"Failed to set up HDRI in Blender: {str(e)}"}
                else:
                    return {"error": f"Requested resolution or format not available for this HDRI"}

            elif asset_type == "textures":
                if not file_format:
                    file_format = "jpg"  # Default format for textures

                downloaded_maps = {}

                try:
                    for map_type in files_data:
                        if map_type not in ["blend", "gltf"]:  # Skip non-texture files
                            if resolution in files_data[map_type] and file_format in files_data[map_type][resolution]:
                                file_info = files_data[map_type][resolution][file_format]
                                file_url = file_info["url"]

                                # Use NamedTemporaryFile like we do for HDRIs
                                with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                                    # Download the file
                                    response = requests.get(file_url, headers=REQ_HEADERS)
                                    if response.status_code == 200:
                                        tmp_file.write(response.content)
                                        tmp_path = tmp_file.name

                                        # Load image from temporary file
                                        image = bpy.data.images.load(tmp_path)
                                        image.name = f"{asset_id}_{map_type}.{file_format}"

                                        # Pack the image into .blend file
                                        image.pack()

                                        # Set color space based on map type
                                        if map_type in ['color', 'diffuse', 'albedo']:
                                            try:
                                                image.colorspace_settings.name = 'sRGB'
                                            except:
                                                pass
                                        else:
                                            try:
                                                image.colorspace_settings.name = 'Non-Color'
                                            except:
                                                pass

                                        downloaded_maps[map_type] = image

                                        # Clean up temporary file
                                        try:
                                            os.unlink(tmp_path)
                                        except:
                                            pass

                    if not downloaded_maps:
                        return {"error": f"No texture maps found for the requested resolution and format"}

                    # Create a new material with the downloaded textures
                    mat = bpy.data.materials.new(name=asset_id)
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links

                    # Clear default nodes
                    for node in nodes:
                        nodes.remove(node)

                    # Create output node
                    output = nodes.new(type='ShaderNodeOutputMaterial')
                    output.location = (300, 0)

                    # Create principled BSDF node
                    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                    principled.location = (0, 0)
                    links.new(principled.outputs[0], output.inputs[0])

                    # Add texture nodes based on available maps
                    tex_coord = nodes.new(type='ShaderNodeTexCoord')
                    tex_coord.location = (-800, 0)

                    mapping = nodes.new(type='ShaderNodeMapping')
                    mapping.location = (-600, 0)
                    mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
                    links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

                    # Position offset for texture nodes
                    x_pos = -400
                    y_pos = 300

                    # Connect different texture maps
                    for map_type, image in downloaded_maps.items():
                        tex_node = nodes.new(type='ShaderNodeTexImage')
                        tex_node.location = (x_pos, y_pos)
                        tex_node.image = image

                        # Set color space based on map type
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            try:
                                tex_node.image.colorspace_settings.name = 'sRGB'
                            except:
                                pass  # Use default if sRGB not available
                        else:
                            try:
                                tex_node.image.colorspace_settings.name = 'Non-Color'
                            except:
                                pass  # Use default if Non-Color not available

                        links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])

                        # Connect to appropriate input on Principled BSDF
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                        elif map_type.lower() in ['roughness', 'rough']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                        elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                        elif map_type.lower() in ['normal', 'nor']:
                            # Add normal map node
                            normal_map = nodes.new(type='ShaderNodeNormalMap')
                            normal_map.location = (x_pos + 200, y_pos)
                            links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                        elif map_type in ['displacement', 'disp', 'height']:
                            # Add displacement node
                            disp_node = nodes.new(type='ShaderNodeDisplacement')
                            disp_node.location = (x_pos + 200, y_pos - 200)
                            links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                            links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])

                        y_pos -= 250

                    return {
                        "success": True,
                        "message": f"Texture {asset_id} imported as material",
                        "material": mat.name,
                        "maps": list(downloaded_maps.keys())
                    }

                except Exception as e:
                    return {"error": f"Failed to process textures: {str(e)}"}

            elif asset_type == "models":
                # For models, prefer glTF format if available
                if not file_format:
                    file_format = "gltf"  # Default format for models

                if file_format in files_data and resolution in files_data[file_format]:
                    file_info = files_data[file_format][resolution][file_format]
                    file_url = file_info["url"]

                    # Create a temporary directory to store the model and its dependencies
                    temp_dir = tempfile.mkdtemp()
                    main_file_path = ""

                    try:
                        # Download the main model file
                        main_file_name = file_url.split("/")[-1]
                        main_file_path = os.path.join(temp_dir, main_file_name)

                        response = requests.get(file_url, headers=REQ_HEADERS)
                        if response.status_code != 200:
                            return {"error": f"Failed to download model: {response.status_code}"}

                        with open(main_file_path, "wb") as f:
                            f.write(response.content)

                        # Check for included files and download them
                        if "include" in file_info and file_info["include"]:
                            for include_path, include_info in file_info["include"].items():
                                # Get the URL for the included file - this is the fix
                                include_url = include_info["url"]

                                # Create the directory structure for the included file
                                include_file_path = os.path.join(temp_dir, include_path)
                                os.makedirs(os.path.dirname(include_file_path), exist_ok=True)

                                # Download the included file
                                include_response = requests.get(include_url, headers=REQ_HEADERS)
                                if include_response.status_code == 200:
                                    with open(include_file_path, "wb") as f:
                                        f.write(include_response.content)
                                else:
                                    print(f"Failed to download included file: {include_path}")

                        # Import the model into Blender
                        if file_format == "gltf" or file_format == "glb":
                            bpy.ops.import_scene.gltf(filepath=main_file_path)
                        elif file_format == "fbx":
                            bpy.ops.import_scene.fbx(filepath=main_file_path)
                        elif file_format == "obj":
                            bpy.ops.import_scene.obj(filepath=main_file_path)
                        elif file_format == "blend":
                            # For blend files, we need to append or link
                            with bpy.data.libraries.load(main_file_path, link=False) as (data_from, data_to):
                                data_to.objects = data_from.objects

                            # Link the objects to the scene
                            for obj in data_to.objects:
                                if obj is not None:
                                    bpy.context.collection.objects.link(obj)
                        else:
                            return {"error": f"Unsupported model format: {file_format}"}

                        # Get the names of imported objects
                        imported_objects = [obj.name for obj in bpy.context.selected_objects]

                        return {
                            "success": True,
                            "message": f"Model {asset_id} imported successfully",
                            "imported_objects": imported_objects
                        }
                    except Exception as e:
                        return {"error": f"Failed to import model: {str(e)}"}
                    finally:
                        # Clean up temporary directory
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                else:
                    return {"error": f"Requested format or resolution not available for this model"}

            else:
                return {"error": f"Unsupported asset type: {asset_type}"}

        except Exception as e:
            return {"error": f"Failed to download asset: {str(e)}"}

    def set_texture(self, object_name, texture_id):
        """Apply a previously downloaded Polyhaven texture to an object by creating a new material"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                return {"error": f"Object not found: {object_name}"}

            # Make sure object can accept materials
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                return {"error": f"Object {object_name} cannot accept materials"}

            # Find all images related to this texture and ensure they're properly loaded
            texture_images = {}
            for img in bpy.data.images:
                if img.name.startswith(texture_id + "_"):
                    # Extract the map type from the image name
                    map_type = img.name.split('_')[-1].split('.')[0]

                    # Force a reload of the image
                    img.reload()

                    # Ensure proper color space
                    if map_type.lower() in ['color', 'diffuse', 'albedo']:
                        try:
                            img.colorspace_settings.name = 'sRGB'
                        except:
                            pass
                    else:
                        try:
                            img.colorspace_settings.name = 'Non-Color'
                        except:
                            pass

                    # Ensure the image is packed
                    if not img.packed_file:
                        img.pack()

                    texture_images[map_type] = img
                    print(f"Loaded texture map: {map_type} - {img.name}")

                    # Debug info
                    print(f"Image size: {img.size[0]}x{img.size[1]}")
                    print(f"Color space: {img.colorspace_settings.name}")
                    print(f"File format: {img.file_format}")
                    print(f"Is packed: {bool(img.packed_file)}")

            if not texture_images:
                return {"error": f"No texture images found for: {texture_id}. Please download the texture first."}

            # Create a new material
            new_mat_name = f"{texture_id}_material_{object_name}"

            # Remove any existing material with this name to avoid conflicts
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                bpy.data.materials.remove(existing_mat)

            new_mat = bpy.data.materials.new(name=new_mat_name)
            new_mat.use_nodes = True

            # Set up the material nodes
            nodes = new_mat.node_tree.nodes
            links = new_mat.node_tree.links

            # Clear default nodes
            nodes.clear()

            # Create output node
            output = nodes.new(type='ShaderNodeOutputMaterial')
            output.location = (600, 0)

            # Create principled BSDF node
            principled = nodes.new(type='ShaderNodeBsdfPrincipled')
            principled.location = (300, 0)
            links.new(principled.outputs[0], output.inputs[0])

            # Add texture nodes based on available maps
            tex_coord = nodes.new(type='ShaderNodeTexCoord')
            tex_coord.location = (-800, 0)

            mapping = nodes.new(type='ShaderNodeMapping')
            mapping.location = (-600, 0)
            mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
            links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

            # Position offset for texture nodes
            x_pos = -400
            y_pos = 300

            # Connect different texture maps
            for map_type, image in texture_images.items():
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.location = (x_pos, y_pos)
                tex_node.image = image

                # Set color space based on map type
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    try:
                        tex_node.image.colorspace_settings.name = 'sRGB'
                    except:
                        pass  # Use default if sRGB not available
                else:
                    try:
                        tex_node.image.colorspace_settings.name = 'Non-Color'
                    except:
                        pass  # Use default if Non-Color not available

                links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])

                # Connect to appropriate input on Principled BSDF
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                elif map_type.lower() in ['roughness', 'rough']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                elif map_type.lower() in ['normal', 'nor', 'dx', 'gl']:
                    # Add normal map node
                    normal_map = nodes.new(type='ShaderNodeNormalMap')
                    normal_map.location = (x_pos + 200, y_pos)
                    links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                    links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                elif map_type.lower() in ['displacement', 'disp', 'height']:
                    # Add displacement node
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (x_pos + 200, y_pos - 200)
                    disp_node.inputs['Scale'].default_value = 0.1  # Reduce displacement strength
                    links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])

                y_pos -= 250

            # Second pass: Connect nodes with proper handling for special cases
            texture_nodes = {}

            # First find all texture nodes and store them by map type
            for node in nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break

            # Now connect everything using the nodes instead of images
            # Handle base color (diffuse)
            for map_name in ['color', 'diffuse', 'albedo']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Base Color'])
                    print(f"Connected {map_name} to Base Color")
                    break

            # Handle roughness
            for map_name in ['roughness', 'rough']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Roughness'])
                    print(f"Connected {map_name} to Roughness")
                    break

            # Handle metallic
            for map_name in ['metallic', 'metalness', 'metal']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Metallic'])
                    print(f"Connected {map_name} to Metallic")
                    break

            # Handle normal maps
            for map_name in ['gl', 'dx', 'nor']:
                if map_name in texture_nodes:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (100, 100)
                    links.new(texture_nodes[map_name].outputs['Color'], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], principled.inputs['Normal'])
                    print(f"Connected {map_name} to Normal")
                    break

            # Handle displacement
            for map_name in ['displacement', 'disp', 'height']:
                if map_name in texture_nodes:
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (300, -200)
                    disp_node.inputs['Scale'].default_value = 0.1  # Reduce displacement strength
                    links.new(texture_nodes[map_name].outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                    print(f"Connected {map_name} to Displacement")
                    break

            # Handle ARM texture (Ambient Occlusion, Roughness, Metallic)
            if 'arm' in texture_nodes:
                separate_rgb = nodes.new(type='ShaderNodeSeparateRGB')
                separate_rgb.location = (-200, -100)
                links.new(texture_nodes['arm'].outputs['Color'], separate_rgb.inputs['Image'])

                # Connect Roughness (G) if no dedicated roughness map
                if not any(map_name in texture_nodes for map_name in ['roughness', 'rough']):
                    links.new(separate_rgb.outputs['G'], principled.inputs['Roughness'])
                    print("Connected ARM.G to Roughness")

                # Connect Metallic (B) if no dedicated metallic map
                if not any(map_name in texture_nodes for map_name in ['metallic', 'metalness', 'metal']):
                    links.new(separate_rgb.outputs['B'], principled.inputs['Metallic'])
                    print("Connected ARM.B to Metallic")

                # For AO (R channel), multiply with base color if we have one
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence

                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)

                    # Connect through the mix node
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(separate_rgb.outputs['R'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected ARM.R to AO mix with Base Color")

            # Handle AO (Ambient Occlusion) if separate
            if 'ao' in texture_nodes:
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence

                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)

                    # Connect through the mix node
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(texture_nodes['ao'].outputs['Color'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected AO to mix with Base Color")

            # CRITICAL: Make sure to clear all existing materials from the object
            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)

            # Assign the new material to the object
            obj.data.materials.append(new_mat)

            # CRITICAL: Make the object active and select it
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)

            # CRITICAL: Force Blender to update the material
            bpy.context.view_layer.update()

            # Get the list of texture maps
            texture_maps = list(texture_images.keys())

            # Get info about texture nodes for debugging
            material_info = {
                "name": new_mat.name,
                "has_nodes": new_mat.use_nodes,
                "node_count": len(new_mat.node_tree.nodes),
                "texture_nodes": []
            }

            for node in new_mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    connections = []
                    for output in node.outputs:
                        for link in output.links:
                            connections.append(f"{output.name} → {link.to_node.name}.{link.to_socket.name}")

                    material_info["texture_nodes"].append({
                        "name": node.name,
                        "image": node.image.name,
                        "colorspace": node.image.colorspace_settings.name,
                        "connections": connections
                    })

            return {
                "success": True,
                "message": f"Created new material and applied texture {texture_id} to {object_name}",
                "material": new_mat.name,
                "maps": texture_maps,
                "material_info": material_info
            }

        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}

    def get_telemetry_consent(self):
        """Get the current telemetry consent status"""
        try:
            # Get addon preferences - use the module name
            addon_prefs = bpy.context.preferences.addons.get(ADDON_MODULE_ID)
            if addon_prefs:
                consent = addon_prefs.preferences.telemetry_consent
            else:
                # Fallback to default if preferences not available
                consent = True
        except (AttributeError, KeyError):
            # Fallback to default if preferences not available
            consent = True
        return {"consent": consent}

    def get_polyhaven_status(self):
        """Get the current status of PolyHaven integration"""
        enabled = bpy.context.scene.blendermcp_use_polyhaven
        if enabled:
            return {"enabled": True, "message": "PolyHaven integration is enabled and ready to use."}
        else:
            return {
                "enabled": False,
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection to Claude"""
        }

    #region Hyper3D
    def get_hyper3d_status(self):
        """Get the current status of Hyper3D Rodin integration"""
        enabled = bpy.context.scene.blendermcp_use_hyper3d
        hyper3d_api_key = self._get_hyper3d_api_key()
        if enabled:
            if not hyper3d_api_key:
                return {
                    "enabled": False,
                    "message": """Hyper3D Rodin integration is currently enabled, but API key is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Hyper3D Rodin 3D model generation' checkbox checked
                                3. Choose the right plaform and fill in the API Key
                                4. Restart the connection to Claude"""
                }
            mode = bpy.context.scene.blendermcp_hyper3d_mode
            message = f"Hyper3D Rodin integration is enabled and ready to use. Mode: {mode}. " + \
                f"Key type: {'private' if hyper3d_api_key != RODIN_FREE_TRIAL_KEY else 'free_trial'}"
            return {
                "enabled": True,
                "message": message
            }
        else:
            return {
                "enabled": False,
                "message": """Hyper3D Rodin integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use Hyper3D Rodin 3D model generation' checkbox
                            3. Restart the connection to Claude"""
            }

    def create_rodin_job(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.create_rodin_job_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.create_rodin_job_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def create_rodin_job_main_site(
            self,
            text_prompt: str=None,
            images: list[tuple[str, str]]=None,
            bbox_condition=None
        ):
        try:
            api_key = self._get_hyper3d_api_key()
            if not api_key:
                return {"error": "Hyper3D API key is not given"}
            if images is None:
                images = []
            """Call Rodin API, get the job uuid and subscription key"""
            files = [
                *[("images", (f"{i:04d}{img_suffix}", base64.b64decode(img) if isinstance(img, str) else img)) for i, (img_suffix, img) in enumerate(images)],
                ("tier", (None, "Sketch")),
                ("mesh_mode", (None, "Raw")),
                ("texture_mode", (None, "high")),
            ]
            if text_prompt:
                files.append(("prompt", (None, text_prompt)))
            if bbox_condition:
                files.append(("bbox_condition", (None, json.dumps(bbox_condition))))
            response = requests.post(
                "https://hyperhuman.deemos.com/api/v2/rodin",
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                files=files
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}

    def create_rodin_job_fal_ai(
            self,
            text_prompt: str=None,
            images: list[tuple[str, str]]=None,
            bbox_condition=None
        ):
        try:
            api_key = self._get_hyper3d_api_key()
            if not api_key:
                return {"error": "Hyper3D API key is not given"}
            req_data = {
                "tier": "Sketch",
            }
            if images:
                req_data["input_image_urls"] = images
            if text_prompt:
                req_data["prompt"] = text_prompt
            if bbox_condition:
                req_data["bbox_condition"] = bbox_condition
            response = requests.post(
                "https://queue.fal.run/fal-ai/hyper3d/rodin",
                headers={
                    "Authorization": f"Key {api_key}",
                    "Content-Type": "application/json",
                },
                json=req_data
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}

    def poll_rodin_job_status(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.poll_rodin_job_status_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.poll_rodin_job_status_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def poll_rodin_job_status_main_site(self, subscription_key: str):
        """Call the job status API to get the job status"""
        api_key = self._get_hyper3d_api_key()
        if not api_key:
            return {"error": "Hyper3D API key is not given"}
        response = requests.post(
            "https://hyperhuman.deemos.com/api/v2/status",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "subscription_key": subscription_key,
            },
        )
        data = response.json()
        return {
            "status_list": [i["status"] for i in data["jobs"]]
        }

    def poll_rodin_job_status_fal_ai(self, request_id: str):
        """Call the job status API to get the job status"""
        api_key = self._get_hyper3d_api_key()
        if not api_key:
            return {"error": "Hyper3D API key is not given"}
        response = requests.get(
            f"https://queue.fal.run/fal-ai/hyper3d/requests/{request_id}/status",
            headers={
                "Authorization": f"KEY {api_key}",
            },
        )
        data = response.json()
        return data

    @staticmethod
    def _clean_imported_glb(filepath, mesh_name=None):
        # Get the set of existing objects before import
        existing_objects = set(bpy.data.objects)

        # Import the GLB file
        bpy.ops.import_scene.gltf(filepath=filepath)

        # Ensure the context is updated
        bpy.context.view_layer.update()

        # Get all imported objects
        imported_objects = list(set(bpy.data.objects) - existing_objects)
        # imported_objects = [obj for obj in bpy.context.view_layer.objects if obj.select_get()]

        if not imported_objects:
            print("Error: No objects were imported.")
            return

        # Identify the mesh object
        mesh_obj = None

        if len(imported_objects) == 1 and imported_objects[0].type == 'MESH':
            mesh_obj = imported_objects[0]
            print("Single mesh imported, no cleanup needed.")
        else:
            if len(imported_objects) == 2:
                empty_objs = [i for i in imported_objects if i.type == "EMPTY"]
                if len(empty_objs) != 1:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
                parent_obj = empty_objs.pop()
                if len(parent_obj.children) == 1:
                    potential_mesh = parent_obj.children[0]
                    if potential_mesh.type == 'MESH':
                        print("GLB structure confirmed: Empty node with one mesh child.")

                        # Unparent the mesh from the empty node
                        potential_mesh.parent = None

                        # Remove the empty node
                        bpy.data.objects.remove(parent_obj)
                        print("Removed empty node, keeping only the mesh.")

                        mesh_obj = potential_mesh
                    else:
                        print("Error: Child is not a mesh object.")
                        return
                else:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
            else:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return

        # Rename the mesh if needed
        try:
            if mesh_obj and mesh_obj.name is not None and mesh_name:
                mesh_obj.name = mesh_name
                if mesh_obj.data.name is not None:
                    mesh_obj.data.name = mesh_name
                print(f"Mesh renamed to: {mesh_name}")
        except Exception as e:
            print("Having issue with renaming, give up renaming.")

        return mesh_obj

    def import_generated_asset(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.import_generated_asset_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.import_generated_asset_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def import_generated_asset_main_site(self, task_uuid: str, name: str):
        """Fetch the generated asset, import into blender"""
        api_key = self._get_hyper3d_api_key()
        if not api_key:
            return {"succeed": False, "error": "Hyper3D API key is not given"}
        response = requests.post(
            "https://hyperhuman.deemos.com/api/v2/download",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            json={
                'task_uuid': task_uuid
            }
        )
        data_ = response.json()
        temp_file = None
        for i in data_["list"]:
            if i["name"].endswith(".glb"):
                temp_file = tempfile.NamedTemporaryFile(
                    delete=False,
                    prefix=task_uuid,
                    suffix=".glb",
                )

                try:
                    # Download the content
                    response = requests.get(i["url"], stream=True)
                    response.raise_for_status()  # Raise an exception for HTTP errors

                    # Write the content to the temporary file
                    for chunk in response.iter_content(chunk_size=8192):
                        temp_file.write(chunk)

                    # Close the file
                    temp_file.close()

                except Exception as e:
                    # Clean up the file if there's an error
                    temp_file.close()
                    os.unlink(temp_file.name)
                    return {"succeed": False, "error": str(e)}

                break
        else:
            return {"succeed": False, "error": "Generation failed. Please first make sure that all jobs of the task are done and then try again later."}

        try:
            obj = self._clean_imported_glb(
                filepath=temp_file.name,
                mesh_name=name
            )
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return {
                "succeed": True, **result
            }
        except Exception as e:
            return {"succeed": False, "error": str(e)}

    def import_generated_asset_fal_ai(self, request_id: str, name: str):
        """Fetch the generated asset, import into blender"""
        api_key = self._get_hyper3d_api_key()
        if not api_key:
            return {"succeed": False, "error": "Hyper3D API key is not given"}
        response = requests.get(
            f"https://queue.fal.run/fal-ai/hyper3d/requests/{request_id}",
            headers={
                "Authorization": f"Key {api_key}",
            }
        )
        data_ = response.json()
        temp_file = None

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            prefix=request_id,
            suffix=".glb",
        )

        try:
            # Download the content
            response = requests.get(data_["model_mesh"]["url"], stream=True)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Write the content to the temporary file
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)

            # Close the file
            temp_file.close()

        except Exception as e:
            # Clean up the file if there's an error
            temp_file.close()
            os.unlink(temp_file.name)
            return {"succeed": False, "error": str(e)}

        try:
            obj = self._clean_imported_glb(
                filepath=temp_file.name,
                mesh_name=name
            )
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return {
                "succeed": True, **result
            }
        except Exception as e:
            return {"succeed": False, "error": str(e)}
    #endregion
 
    #region Sketchfab API
    def get_sketchfab_status(self):
        """Get the current status of Sketchfab integration"""
        enabled = bpy.context.scene.blendermcp_use_sketchfab
        api_key = self._get_sketchfab_api_key()

        # Test the API key if present
        if api_key:
            try:
                headers = {
                    "Authorization": f"Token {api_key}"
                }

                response = requests.get(
                    "https://api.sketchfab.com/v3/me",
                    headers=headers,
                    timeout=30  # Add timeout of 30 seconds
                )

                if response.status_code == 200:
                    user_data = response.json()
                    username = user_data.get("username", "Unknown user")
                    return {
                        "enabled": True,
                        "message": f"Sketchfab integration is enabled and ready to use. Logged in as: {username}"
                    }
                else:
                    return {
                        "enabled": False,
                        "message": f"Sketchfab API key seems invalid. Status code: {response.status_code}"
                    }
            except requests.exceptions.Timeout:
                return {
                    "enabled": False,
                    "message": "Timeout connecting to Sketchfab API. Check your internet connection."
                }
            except Exception as e:
                return {
                    "enabled": False,
                    "message": f"Error testing Sketchfab API key: {str(e)}"
                }

        if enabled and api_key:
            return {"enabled": True, "message": "Sketchfab integration is enabled and ready to use."}
        elif enabled and not api_key:
            return {
                "enabled": False,
                "message": """Sketchfab integration is currently enabled, but API key is not given. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Keep the 'Use Sketchfab' checkbox checked
                            3. Enter your Sketchfab API Key
                            4. Restart the connection to Claude"""
            }
        else:
            return {
                "enabled": False,
                "message": """Sketchfab integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Sketchfab' checkbox
                            3. Enter your Sketchfab API Key
                            4. Restart the connection to Claude"""
            }

    def search_sketchfab_models(self, query, categories=None, count=20, downloadable=True):
        """Search for models on Sketchfab based on query and optional filters"""
        try:
            api_key = self._get_sketchfab_api_key()
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            # Build search parameters with exact fields from Sketchfab API docs
            params = {
                "type": "models",
                "q": query,
                "count": count,
                "downloadable": downloadable,
                "archives_flavours": False
            }

            if categories:
                params["categories"] = categories

            # Make API request to Sketchfab search endpoint
            # The proper format according to Sketchfab API docs for API key auth
            headers = {
                "Authorization": f"Token {api_key}"
            }


            # Use the search endpoint as specified in the API documentation
            response = requests.get(
                "https://api.sketchfab.com/v3/search",
                headers=headers,
                params=params,
                timeout=30  # Add timeout of 30 seconds
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code != 200:
                return {"error": f"API request failed with status code {response.status_code}"}

            response_data = response.json()

            # Safety check on the response structure
            if response_data is None:
                return {"error": "Received empty response from Sketchfab API"}

            # Handle 'results' potentially missing from response
            results = response_data.get("results", [])
            if not isinstance(results, list):
                return {"error": f"Unexpected response format from Sketchfab API: {response_data}"}

            return response_data

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    def get_sketchfab_model_preview(self, uid):
        """Get thumbnail preview image of a Sketchfab model by its UID"""
        try:
            import base64
            
            api_key = self._get_sketchfab_api_key()
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            headers = {"Authorization": f"Token {api_key}"}
            
            # Get model info which includes thumbnails
            response = requests.get(
                f"https://api.sketchfab.com/v3/models/{uid}",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}
            
            if response.status_code == 404:
                return {"error": f"Model not found: {uid}"}
            
            if response.status_code != 200:
                return {"error": f"Failed to get model info: {response.status_code}"}
            
            data = response.json()
            thumbnails = data.get("thumbnails", {}).get("images", [])
            
            if not thumbnails:
                return {"error": "No thumbnail available for this model"}
            
            # Find a suitable thumbnail (prefer medium size ~640px)
            selected_thumbnail = None
            for thumb in thumbnails:
                width = thumb.get("width", 0)
                if 400 <= width <= 800:
                    selected_thumbnail = thumb
                    break
            
            # Fallback to the first available thumbnail
            if not selected_thumbnail:
                selected_thumbnail = thumbnails[0]
            
            thumbnail_url = selected_thumbnail.get("url")
            if not thumbnail_url:
                return {"error": "Thumbnail URL not found"}
            
            # Download the thumbnail image
            img_response = requests.get(thumbnail_url, timeout=30)
            if img_response.status_code != 200:
                return {"error": f"Failed to download thumbnail: {img_response.status_code}"}
            
            # Encode image as base64
            image_data = base64.b64encode(img_response.content).decode('ascii')
            
            # Determine format from content type or URL
            content_type = img_response.headers.get("Content-Type", "")
            if "png" in content_type or thumbnail_url.endswith(".png"):
                img_format = "png"
            else:
                img_format = "jpeg"
            
            # Get additional model info for context
            model_name = data.get("name", "Unknown")
            author = data.get("user", {}).get("username", "Unknown")
            
            return {
                "success": True,
                "image_data": image_data,
                "format": img_format,
                "model_name": model_name,
                "author": author,
                "uid": uid,
                "thumbnail_width": selected_thumbnail.get("width"),
                "thumbnail_height": selected_thumbnail.get("height")
            }
            
        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Failed to get model preview: {str(e)}"}

    def download_sketchfab_model(self, uid, normalize_size=False, target_size=1.0):
        """Download a model from Sketchfab by its UID
        
        Parameters:
        - uid: The unique identifier of the Sketchfab model
        - normalize_size: If True, scale the model so its largest dimension equals target_size
        - target_size: The target size in Blender units (meters) for the largest dimension
        """
        try:
            api_key = self._get_sketchfab_api_key()
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            # Use proper authorization header for API key auth
            headers = {
                "Authorization": f"Token {api_key}"
            }

            # Request download URL using the exact endpoint from the documentation
            download_endpoint = f"https://api.sketchfab.com/v3/models/{uid}/download"

            response = requests.get(
                download_endpoint,
                headers=headers,
                timeout=30  # Add timeout of 30 seconds
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code != 200:
                return {"error": f"Download request failed with status code {response.status_code}"}

            data = response.json()

            # Safety check for None data
            if data is None:
                return {"error": "Received empty response from Sketchfab API for download request"}

            # Extract download URL with safety checks
            gltf_data = data.get("gltf")
            if not gltf_data:
                return {"error": "No gltf download URL available for this model. Response: " + str(data)}

            download_url = gltf_data.get("url")
            if not download_url:
                return {"error": "No download URL available for this model. Make sure the model is downloadable and you have access."}

            # Download the model (already has timeout)
            model_response = requests.get(download_url, timeout=60)  # 60 second timeout

            if model_response.status_code != 200:
                return {"error": f"Model download failed with status code {model_response.status_code}"}

            # Save to temporary file
            temp_dir = tempfile.mkdtemp()
            zip_file_path = os.path.join(temp_dir, f"{uid}.zip")

            with open(zip_file_path, "wb") as f:
                f.write(model_response.content)

            # Extract the zip file with enhanced security
            with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                # More secure zip slip prevention
                for file_info in zip_ref.infolist():
                    # Get the path of the file
                    file_path = file_info.filename

                    # Convert directory separators to the current OS style
                    # This handles both / and \ in zip entries
                    target_path = os.path.join(temp_dir, os.path.normpath(file_path))

                    # Get absolute paths for comparison
                    abs_temp_dir = os.path.abspath(temp_dir)
                    abs_target_path = os.path.abspath(target_path)

                    # Ensure the normalized path doesn't escape the target directory
                    if not abs_target_path.startswith(abs_temp_dir):
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                        return {"error": "Security issue: Zip contains files with path traversal attempt"}

                    # Additional explicit check for directory traversal
                    if ".." in file_path:
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                        return {"error": "Security issue: Zip contains files with directory traversal sequence"}

                # If all files passed security checks, extract them
                zip_ref.extractall(temp_dir)

            # Find the main glTF file
            gltf_files = [f for f in os.listdir(temp_dir) if f.endswith('.gltf') or f.endswith('.glb')]

            if not gltf_files:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
                return {"error": "No glTF file found in the downloaded model"}

            main_file = os.path.join(temp_dir, gltf_files[0])

            # Import the model
            bpy.ops.import_scene.gltf(filepath=main_file)

            # Get the imported objects
            imported_objects = list(bpy.context.selected_objects)
            imported_object_names = [obj.name for obj in imported_objects]

            # Clean up temporary files
            with suppress(Exception):
                shutil.rmtree(temp_dir)

            # Find root objects (objects without parents in the imported set)
            root_objects = [obj for obj in imported_objects if obj.parent is None]

            # Helper function to recursively get all mesh children
            def get_all_mesh_children(obj):
                """Recursively collect all mesh objects in the hierarchy"""
                meshes = []
                if obj.type == 'MESH':
                    meshes.append(obj)
                for child in obj.children:
                    meshes.extend(get_all_mesh_children(child))
                return meshes

            # Collect ALL meshes from the entire hierarchy (starting from roots)
            all_meshes = []
            for obj in root_objects:
                all_meshes.extend(get_all_mesh_children(obj))
            
            if all_meshes:
                # Calculate combined world bounding box for all meshes
                all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))
                
                for mesh_obj in all_meshes:
                    # Get world-space bounding box corners
                    for corner in mesh_obj.bound_box:
                        world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                        all_min.x = min(all_min.x, world_corner.x)
                        all_min.y = min(all_min.y, world_corner.y)
                        all_min.z = min(all_min.z, world_corner.z)
                        all_max.x = max(all_max.x, world_corner.x)
                        all_max.y = max(all_max.y, world_corner.y)
                        all_max.z = max(all_max.z, world_corner.z)
                
                # Calculate dimensions
                dimensions = [
                    all_max.x - all_min.x,
                    all_max.y - all_min.y,
                    all_max.z - all_min.z
                ]
                max_dimension = max(dimensions)
                
                # Apply normalization if requested
                scale_applied = 1.0
                if normalize_size and max_dimension > 0:
                    scale_factor = target_size / max_dimension
                    scale_applied = scale_factor
                    
                    # ✅ Only apply scale to ROOT objects (not children!)
                    # Child objects inherit parent's scale through matrix_world
                    for root in root_objects:
                        root.scale = (
                            root.scale.x * scale_factor,
                            root.scale.y * scale_factor,
                            root.scale.z * scale_factor
                        )
                    
                    # Update the scene to recalculate matrix_world for all objects
                    bpy.context.view_layer.update()
                    
                    # Recalculate bounding box after scaling
                    all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                    all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))
                    
                    for mesh_obj in all_meshes:
                        for corner in mesh_obj.bound_box:
                            world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                            all_min.x = min(all_min.x, world_corner.x)
                            all_min.y = min(all_min.y, world_corner.y)
                            all_min.z = min(all_min.z, world_corner.z)
                            all_max.x = max(all_max.x, world_corner.x)
                            all_max.y = max(all_max.y, world_corner.y)
                            all_max.z = max(all_max.z, world_corner.z)
                    
                    dimensions = [
                        all_max.x - all_min.x,
                        all_max.y - all_min.y,
                        all_max.z - all_min.z
                    ]
                
                world_bounding_box = [[all_min.x, all_min.y, all_min.z], [all_max.x, all_max.y, all_max.z]]
            else:
                world_bounding_box = None
                dimensions = None
                scale_applied = 1.0

            result = {
                "success": True,
                "message": "Model imported successfully",
                "imported_objects": imported_object_names
            }
            
            if world_bounding_box:
                result["world_bounding_box"] = world_bounding_box
            if dimensions:
                result["dimensions"] = [round(d, 4) for d in dimensions]
            if normalize_size:
                result["scale_applied"] = round(scale_applied, 6)
                result["normalized"] = True
            
            return result

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection and try again with a simpler model."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Failed to download model: {str(e)}"}
    #endregion

    #region Hunyuan3D
    def get_hunyuan3d_status(self):
        """Get the current status of Hunyuan3D integration"""
        enabled = bpy.context.scene.blendermcp_use_hunyuan3d
        hunyuan3d_mode = bpy.context.scene.blendermcp_hunyuan3d_mode
        secret_id = self._get_hunyuan3d_secret_id()
        secret_key = self._get_hunyuan3d_secret_key()
        api_url = self._get_hunyuan3d_api_url()
        if enabled:
            match hunyuan3d_mode:
                case "OFFICIAL_API":
                    if not secret_id or not secret_key:
                        return {
                            "enabled": False, 
                            "mode": hunyuan3d_mode, 
                            "message": """Hunyuan3D integration is currently enabled, but SecretId or SecretKey is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the SecretId and SecretKey
                                4. Restart the connection to Claude"""
                        }
                case "LOCAL_API":
                    if not api_url:
                        return {
                            "enabled": False, 
                            "mode": hunyuan3d_mode, 
                            "message": """Hunyuan3D integration is currently enabled, but API URL  is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the API URL
                                4. Restart the connection to Claude"""
                        }
                case _:
                    return {
                        "enabled": False, 
                        "message": "Hunyuan3D integration is enabled and mode is not supported."
                    }
            return {
                "enabled": True, 
                "mode": hunyuan3d_mode,
                "message": "Hunyuan3D integration is enabled and ready to use."
            }
        return {
            "enabled": False, 
            "message": """Hunyuan3D integration is currently disabled. To enable it:
                        1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                        2. Check the 'Use Tencent Hunyuan 3D model generation' checkbox
                        3. Restart the connection to Claude"""
        }
    
    @staticmethod
    def get_tencent_cloud_sign_headers(
        method: str,
        path: str,
        headParams: dict,
        data: dict,
        service: str,
        region: str,
        secret_id: str,
        secret_key: str,
        host: str = None
    ):
        """Generate the signature header required for Tencent Cloud API requests headers"""
        # Generate timestamp
        timestamp = int(time.time())
        date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
        
        # If host is not provided, it is generated based on service and region.
        if not host:
            host = f"{service}.tencentcloudapi.com"
        
        endpoint = f"https://{host}"
        
        # Constructing the request body
        payload_str = json.dumps(data)
        
        # ************* Step 1: Concatenate the canonical request string *************
        canonical_uri = path
        canonical_querystring = ""
        ct = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{headParams.get('Action', '').lower()}\n"
        signed_headers = "content-type;host;x-tc-action"
        hashed_request_payload = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        
        canonical_request = (method + "\n" +
                            canonical_uri + "\n" +
                            canonical_querystring + "\n" +
                            canonical_headers + "\n" +
                            signed_headers + "\n" +
                            hashed_request_payload)

        # ************* Step 2: Construct the reception signature string *************
        credential_scope = f"{date}/{service}/tc3_request"
        hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = ("TC3-HMAC-SHA256" + "\n" +
                        str(timestamp) + "\n" +
                        credential_scope + "\n" +
                        hashed_canonical_request)

        # ************* Step 3: Calculate the signature *************
        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
        secret_service = sign(secret_date, service)
        secret_signing = sign(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, 
            string_to_sign.encode("utf-8"), 
            hashlib.sha256
        ).hexdigest()

        # ************* Step 4: Connect Authorization *************
        authorization = ("TC3-HMAC-SHA256" + " " +
                        "Credential=" + secret_id + "/" + credential_scope + ", " +
                        "SignedHeaders=" + signed_headers + ", " +
                        "Signature=" + signature)

        # Constructing request headers
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": headParams.get("Action", ""),
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": headParams.get("Version", ""),
            "X-TC-Region": region
        }

        return headers, endpoint

    def create_hunyuan_job(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hunyuan3d_mode:
            case "OFFICIAL_API":
                return self.create_hunyuan_job_main_site(*args, **kwargs)
            case "LOCAL_API":
                return self.create_hunyuan_job_local_site(*args, **kwargs)
            case _:
                return f"Error: Unknown Hunyuan3D mode!"

    def create_hunyuan_job_main_site(
        self,
        text_prompt: str = None,
        image: str = None
    ):
        try:
            secret_id = self._get_hunyuan3d_secret_id()
            secret_key = self._get_hunyuan3d_secret_key()

            if not secret_id or not secret_key:
                return {"error": "SecretId or SecretKey is not given"}

            # Parameter verification
            if not text_prompt and not image:
                return {"error": "Prompt or Image is required"}
            if text_prompt and image:
                return {"error": "Prompt and Image cannot be provided simultaneously"}
            # Fixed parameter configuration
            service = "hunyuan"
            action = "SubmitHunyuanTo3DJob"
            version = "2023-09-01"
            region = "ap-guangzhou"

            headParams={
                "Action": action,
                "Version": version,
                "Region": region,
            }

            # Constructing request parameters
            data = {
                "Num": 1  # The current API limit is only 1
            }

            # Handling text prompts
            if text_prompt:
                if len(text_prompt) > 200:
                    return {"error": "Prompt exceeds 200 characters limit"}
                data["Prompt"] = text_prompt

            # Handling image
            if image:
                if re.match(r'^https?://', image, re.IGNORECASE) is not None:
                    data["ImageUrl"] = image
                else:
                    try:
                        # Convert to Base64 format
                        with open(image, "rb") as f:
                            image_base64 = base64.b64encode(f.read()).decode("ascii")
                        data["ImageBase64"] = image_base64
                    except Exception as e:
                        return {"error": f"Image encoding failed: {str(e)}"}
            
            # Get signed headers
            headers, endpoint = self.get_tencent_cloud_sign_headers("POST", "/", headParams, data, service, region, secret_id, secret_key)

            response = requests.post(
                endpoint,
                headers = headers,
                data = json.dumps(data)
            )

            if response.status_code == 200:
                return response.json()
            return {
                "error": f"API request failed with status {response.status_code}: {response}"
            }
        except Exception as e:
            return {"error": str(e)}

    def create_hunyuan_job_local_site(
        self,
        text_prompt: str = None,
        image: str = None,
        paint_model: str = "2.0"):
        try:
            base_url = self._get_hunyuan3d_api_url().rstrip('/')
            octree_resolution = bpy.context.scene.blendermcp_hunyuan3d_octree_resolution
            num_inference_steps = bpy.context.scene.blendermcp_hunyuan3d_num_inference_steps
            guidance_scale = bpy.context.scene.blendermcp_hunyuan3d_guidance_scale
            texture = bpy.context.scene.blendermcp_hunyuan3d_texture

            if not base_url:
                return {"error": "API URL is not given"}
            if not text_prompt and not image:
                return {"error": "Prompt or Image is required"}

            data = {
                "octree_resolution": octree_resolution,
                "num_inference_steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "texture": texture,
                "paint_model": paint_model,
            }

            if text_prompt:
                data["text"] = text_prompt

            if image:
                if re.match(r'^https?://', image, re.IGNORECASE) is not None:
                    try:
                        resImg = requests.get(image)
                        resImg.raise_for_status()
                        data["image"] = base64.b64encode(resImg.content).decode("ascii")
                    except Exception as e:
                        return {"error": f"Failed to download or encode image: {str(e)}"}
                else:
                    try:
                        with open(image, "rb") as f:
                            data["image"] = base64.b64encode(f.read()).decode("ascii")
                    except Exception as e:
                        return {"error": f"Image encoding failed: {str(e)}"}

            response = requests.post(
                f"{base_url}/generate",
                json=data,
            )

            if response.status_code != 200:
                return {"error": f"Generation failed: {response.text}"}

            # API returns binary GLB — save and import
            with tempfile.NamedTemporaryFile(delete=False, suffix=".glb") as tmp:
                tmp.write(response.content)
                glb_path = tmp.name

            def import_handler():
                bpy.ops.import_scene.gltf(filepath=glb_path)
                if os.path.exists(glb_path):
                    os.unlink(glb_path)
                return None

            bpy.app.timers.register(import_handler)

            return {
                "status": "DONE",
                "message": f"Generated with paint_model={paint_model} and imported"
            }
        except Exception as e:
            print(f"An error occurred: {e}")
            return {"error": str(e)}
        
    
    def poll_hunyuan_job_status(self, *args, **kwargs):
        return self.poll_hunyuan_job_status_ai(*args, **kwargs)
    
    def poll_hunyuan_job_status_ai(self, job_id: str):
        """Call the job status API to get the job status"""
        print(job_id)
        try:
            secret_id = self._get_hunyuan3d_secret_id()
            secret_key = self._get_hunyuan3d_secret_key()

            if not secret_id or not secret_key:
                return {"error": "SecretId or SecretKey is not given"}
            if not job_id:
                return {"error": "JobId is required"}
            
            service = "hunyuan"
            action = "QueryHunyuanTo3DJob"
            version = "2023-09-01"
            region = "ap-guangzhou"

            headParams={
                "Action": action,
                "Version": version,
                "Region": region,
            }

            clean_job_id = job_id.removeprefix("job_")
            data = {
                "JobId": clean_job_id
            }

            headers, endpoint = self.get_tencent_cloud_sign_headers("POST", "/", headParams, data, service, region, secret_id, secret_key)

            response = requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(data)
            )

            if response.status_code == 200:
                return response.json()
            return {
                "error": f"API request failed with status {response.status_code}: {response}"
            }
        except Exception as e:
            return {"error": str(e)}

    def import_generated_asset_hunyuan(self, *args, **kwargs):
        return self.import_generated_asset_hunyuan_ai(*args, **kwargs)
            
    def import_generated_asset_hunyuan_ai(self, name: str , zip_file_url: str):
        if not zip_file_url:
            return {"error": "Zip file not found"}
        
        # Validate URL
        if not re.match(r'^https?://', zip_file_url, re.IGNORECASE):
            return {"error": "Invalid URL format. Must start with http:// or https://"}
        
        # Create a temporary directory
        temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
        zip_file_path = osp.join(temp_dir, "model.zip")
        obj_file_path = osp.join(temp_dir, "model.obj")
        mtl_file_path = osp.join(temp_dir, "model.mtl")

        try:
            # Download ZIP file
            zip_response = requests.get(zip_file_url, stream=True)
            zip_response.raise_for_status()
            with open(zip_file_path, "wb") as f:
                for chunk in zip_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Unzip the ZIP
            with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
                zip_ref.extractall(temp_dir)

            # Find the .obj file (there may be multiple, assuming the main file is model.obj)
            for file in os.listdir(temp_dir):
                if file.endswith(".obj"):
                    obj_file_path = osp.join(temp_dir, file)

            if not osp.exists(obj_file_path):
                return {"succeed": False, "error": "OBJ file not found after extraction"}

            # Import obj file
            if bpy.app.version>=(4, 0, 0):
                bpy.ops.wm.obj_import(filepath=obj_file_path)
            else:
                bpy.ops.import_scene.obj(filepath=obj_file_path)

            imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
            if not imported_objs:
                return {"succeed": False, "error": "No mesh objects imported"}

            obj = imported_objs[0]
            if name:
                obj.name = name

            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return {"succeed": True, **result}
        except Exception as e:
            return {"succeed": False, "error": str(e)}
        finally:
            #  Clean up temporary zip and obj, save texture and mtl
            try:
                if os.path.exists(zip_file_path):
                    os.remove(zip_file_path) 
                if os.path.exists(obj_file_path):
                    os.remove(obj_file_path)
            except Exception as e:
                print(f"Failed to clean up temporary directory {temp_dir}: {e}")
    #endregion

# ── Blender Addon Preferences (persistent across sessions) ──
class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_MODULE_ID

    # ── General ──
    telemetry_consent: BoolProperty(
        name="Allow Telemetry",
        description="Allow collection of prompts, code, and screenshots",
        default=True
    )
    auto_connect: BoolProperty(
        name="Auto-connect on startup",
        description="Automatically start MCP server when Blender opens or loads a file",
        default=True
    )
    port: IntProperty(
        name="Default Port",
        description="Port for the BlenderMCP server",
        default=9876, min=1024, max=65535
    )
    # ── Poly Haven ──
    use_polyhaven: BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )
    # ── Hyper3D Rodin ──
    use_hyper3d: BoolProperty(name="Use Hyper3D Rodin", default=False)
    hyper3d_mode: EnumProperty(name="Rodin Mode", items=[
        ("MAIN_SITE", "hyper3d.ai", ""), ("FAL_AI", "fal.ai", ""),
    ], default="MAIN_SITE")
    hyper3d_api_key: StringProperty(name="API Key", subtype="PASSWORD", default="")
    # ── Sketchfab ──
    use_sketchfab: BoolProperty(name="Use Sketchfab", default=False)
    sketchfab_api_key: StringProperty(name="API Key", subtype="PASSWORD", default="")
    # ── Hunyuan3D ──
    use_hunyuan3d: BoolProperty(name="Use Hunyuan3D", default=False)
    hunyuan3d_mode: EnumProperty(name="Mode", items=[
        ("LOCAL_API", "Local API", ""), ("OFFICIAL_API", "Official API", ""),
    ], default="LOCAL_API")
    hunyuan3d_secret_id: StringProperty(name="SecretId", default="")
    hunyuan3d_secret_key: StringProperty(name="SecretKey", subtype="PASSWORD", default="")
    hunyuan3d_api_url: StringProperty(name="API URL", default="http://localhost:8081")
    hunyuan3d_octree_resolution: IntProperty(name="Octree Resolution", default=256, min=128, max=512)
    hunyuan3d_num_inference_steps: IntProperty(name="Inference Steps", default=30, min=20, max=50)
    hunyuan3d_guidance_scale: FloatProperty(name="Guidance Scale", default=5.5, min=1.0, max=10.0)
    hunyuan3d_texture: BoolProperty(name="Generate Texture", default=True)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "telemetry_consent")
        layout.prop(self, "auto_connect")
        layout.separator()
        layout.label(text="Persistent defaults (applied on startup):", icon='SETTINGS')
        layout.prop(self, "port")
        layout.separator()
        layout.prop(self, "use_polyhaven")
        layout.prop(self, "use_hyper3d")
        if self.use_hyper3d:
            box = layout.box()
            box.prop(self, "hyper3d_mode")
            box.prop(self, "hyper3d_api_key")
        layout.prop(self, "use_sketchfab")
        if self.use_sketchfab:
            layout.prop(self, "sketchfab_api_key")
        layout.separator()
        layout.prop(self, "use_hunyuan3d")
        if self.use_hunyuan3d:
            box = layout.box()
            box.prop(self, "hunyuan3d_mode")
            if self.hunyuan3d_mode == 'OFFICIAL_API':
                box.prop(self, "hunyuan3d_secret_id")
                box.prop(self, "hunyuan3d_secret_key")
            if self.hunyuan3d_mode == 'LOCAL_API':
                box.prop(self, "hunyuan3d_api_url")
                box.prop(self, "hunyuan3d_octree_resolution")
                box.prop(self, "hunyuan3d_num_inference_steps")
                box.prop(self, "hunyuan3d_guidance_scale")
                box.prop(self, "hunyuan3d_texture")
        layout.separator()
        layout.operator("blendermcp.open_terms", text="View Terms and Conditions", icon='TEXT')

        layout.separator()
        layout.label(text="Persistent API Credentials:", icon='LOCKED')
        cred_box = layout.box()
        cred_box.prop(self, "sketchfab_api_key", text="Sketchfab API Key")
        cred_box.prop(self, "hyper3d_api_key", text="Hyper3D API Key")
        cred_box.prop(self, "hunyuan3d_secret_id", text="Hunyuan3D SecretId")
        cred_box.prop(self, "hunyuan3d_secret_key", text="Hunyuan3D SecretKey")
        cred_box.prop(self, "hunyuan3d_api_url", text="Hunyuan3D API URL")

# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = get_blendermcp_addon_preferences(context)

        layout.prop(scene, "blendermcp_port")
        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")

        layout.prop(scene, "blendermcp_use_hyper3d", text="Use Hyper3D Rodin 3D model generation")
        if scene.blendermcp_use_hyper3d:
            layout.prop(scene, "blendermcp_hyper3d_mode", text="Rodin Mode")
            if prefs:
                layout.prop(prefs, "hyper3d_api_key", text="API Key")
            else:
                layout.prop(scene, "blendermcp_hyper3d_api_key", text="API Key")
            layout.operator("blendermcp.set_hyper3d_free_trial_api_key", text="Set Free Trial API Key")

        layout.prop(scene, "blendermcp_use_sketchfab", text="Use assets from Sketchfab")
        if scene.blendermcp_use_sketchfab:
            if prefs:
                layout.prop(prefs, "sketchfab_api_key", text="API Key")
            else:
                layout.prop(scene, "blendermcp_sketchfab_api_key", text="API Key")

        layout.prop(scene, "blendermcp_use_hunyuan3d", text="Use Tencent Hunyuan 3D model generation")
        if scene.blendermcp_use_hunyuan3d:
            layout.prop(scene, "blendermcp_hunyuan3d_mode", text="Hunyuan3D Mode")
            if scene.blendermcp_hunyuan3d_mode == 'OFFICIAL_API':
                if prefs:
                    layout.prop(prefs, "hunyuan3d_secret_id", text="SecretId")
                    layout.prop(prefs, "hunyuan3d_secret_key", text="SecretKey")
                else:
                    layout.prop(scene, "blendermcp_hunyuan3d_secret_id", text="SecretId")
                    layout.prop(scene, "blendermcp_hunyuan3d_secret_key", text="SecretKey")
            if scene.blendermcp_hunyuan3d_mode == 'LOCAL_API':
                if prefs:
                    layout.prop(prefs, "hunyuan3d_api_url", text="API URL")
                else:
                    layout.prop(scene, "blendermcp_hunyuan3d_api_url", text="API URL")
                layout.prop(scene, "blendermcp_hunyuan3d_octree_resolution", text="Octree Resolution")
                layout.prop(scene, "blendermcp_hunyuan3d_num_inference_steps", text="Number of Inference Steps")
                layout.prop(scene, "blendermcp_hunyuan3d_guidance_scale", text="Guidance Scale")
                layout.prop(scene, "blendermcp_hunyuan3d_texture", text="Generate Texture")
        
        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcp.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")

# Operator to set Hyper3D API Key
class BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"

    def execute(self, context):
        prefs = get_blendermcp_addon_preferences(context)
        if prefs:
            if not prefs.hyper3d_api_key or prefs.hyper3d_api_key == RODIN_FREE_TRIAL_KEY:
                prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
            else:
                self.report(
                    {'INFO'},
                    "Using free trial for this session only; saved private key was kept."
                )
        context.scene.blendermcp_hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        context.scene.blendermcp_hyper3d_mode = 'MAIN_SITE'
        self.report({'INFO'}, "API Key set successfully!")
        return {'FINISHED'}

# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)

        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = bpy.types.blendermcp_server.running

        return {'FINISHED'}

# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server

        scene.blendermcp_server_running = False

        return {'FINISHED'}

# Operator to open Terms and Conditions
class BLENDERMCP_OT_OpenTerms(bpy.types.Operator):
    bl_idname = "blendermcp.open_terms"
    bl_label = "View Terms and Conditions"
    bl_description = "Open the Terms and Conditions document"

    def execute(self, context):
        # Open the Terms and Conditions on GitHub
        terms_url = "https://github.com/ahujasid/blender-mcp/blob/main/TERMS_AND_CONDITIONS.md"
        try:
            import webbrowser
            webbrowser.open(terms_url)
            self.report({'INFO'}, "Terms and Conditions opened in browser")
        except Exception as e:
            self.report({'ERROR'}, f"Could not open Terms and Conditions: {str(e)}")
        
        return {'FINISHED'}

# Registration functions
def register():
    # Scene properties with update callbacks that sync to persistent AddonPreferences
    U = lambda name: _make_scene_update(name)  # shorthand

    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port", description="Port for the BlenderMCP server",
        default=9876, min=1024, max=65535, update=U('port')
    )

    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running", default=False
    )

    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven", description="Enable Poly Haven asset integration",
        default=False, update=U('use_polyhaven')
    )

    bpy.types.Scene.blendermcp_use_hyper3d = bpy.props.BoolProperty(
        name="Use Hyper3D Rodin", description="Enable Hyper3D Rodin generation integration",
        default=False, update=U('use_hyper3d')
    )

    bpy.types.Scene.blendermcp_hyper3d_mode = bpy.props.EnumProperty(
        name="Rodin Mode", description="Choose the platform used to call Rodin APIs",
        items=[("MAIN_SITE", "hyper3d.ai", "hyper3d.ai"), ("FAL_AI", "fal.ai", "fal.ai")],
        default="MAIN_SITE", update=U('hyper3d_mode')
    )

    bpy.types.Scene.blendermcp_hyper3d_api_key = bpy.props.StringProperty(
        name="Hyper3D API Key", subtype="PASSWORD",
        description="API Key provided by Hyper3D",
        default="", update=U('hyper3d_api_key')
    )

    bpy.types.Scene.blendermcp_use_hunyuan3d = bpy.props.BoolProperty(
        name="Use Hunyuan 3D", description="Enable Hunyuan asset integration",
        default=False, update=U('use_hunyuan3d')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_mode = bpy.props.EnumProperty(
        name="Hunyuan3D Mode", description="Choose local or official API",
        items=[("LOCAL_API", "local api", "local api"), ("OFFICIAL_API", "official api", "official api")],
        default="LOCAL_API", update=U('hunyuan3d_mode')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_secret_id = bpy.props.StringProperty(
        name="Hunyuan 3D SecretId", description="SecretId provided by Hunyuan 3D",
        default="", update=U('hunyuan3d_secret_id')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_secret_key = bpy.props.StringProperty(
        name="Hunyuan 3D SecretKey", subtype="PASSWORD",
        description="SecretKey provided by Hunyuan 3D",
        default="", update=U('hunyuan3d_secret_key')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_api_url = bpy.props.StringProperty(
        name="API URL", description="URL of the Hunyuan 3D API service",
        default="http://localhost:8081", update=U('hunyuan3d_api_url')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution = bpy.props.IntProperty(
        name="Octree Resolution", description="Octree resolution for 3D generation",
        default=256, min=128, max=512, update=U('hunyuan3d_octree_resolution')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps = bpy.props.IntProperty(
        name="Number of Inference Steps", description="Number of inference steps for 3D generation",
        default=30, min=20, max=50, update=U('hunyuan3d_num_inference_steps')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale = bpy.props.FloatProperty(
        name="Guidance Scale", description="Guidance scale for 3D generation",
        default=5.5, min=1.0, max=10.0, update=U('hunyuan3d_guidance_scale')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_texture = bpy.props.BoolProperty(
        name="Generate Texture", description="Whether to generate texture for the 3D model",
        default=True, update=U('hunyuan3d_texture')
    )

    bpy.types.Scene.blendermcp_use_sketchfab = bpy.props.BoolProperty(
        name="Use Sketchfab", description="Enable Sketchfab asset integration",
        default=False, update=U('use_sketchfab')
    )

    bpy.types.Scene.blendermcp_sketchfab_api_key = bpy.props.StringProperty(
        name="Sketchfab API Key", subtype="PASSWORD",
        description="API Key provided by Sketchfab",
        default="", update=U('sketchfab_api_key')
    )

    # Register all classes
    bpy.utils.register_class(BLENDERMCP_AddonPreferences)
    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)
    bpy.utils.register_class(BLENDERMCP_OT_OpenTerms)

    # Register load_post handler for persistence + auto-connect
    bpy.app.handlers.load_post.append(_load_post_handler)

    # One-shot timer: sync prefs→scene and auto-connect on initial Blender startup
    def _startup_sync():
        sync_prefs_to_scene()
        _auto_connect_if_enabled()
    bpy.app.timers.register(_startup_sync, first_interval=0.5)

    preferences = get_blendermcp_addon_preferences()
    auto_connect = bool(preferences and preferences.auto_connect)
    print(
        "BlenderMCP addon registered (auto-connect: "
        + ("on" if auto_connect else "off")
        + ")"
    )

def unregister():
    # Remove load_post handler
    if _load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_handler)

    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_OpenTerms)
    bpy.utils.unregister_class(BLENDERMCP_AddonPreferences)

    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_use_polyhaven
    del bpy.types.Scene.blendermcp_use_hyper3d
    del bpy.types.Scene.blendermcp_hyper3d_mode
    del bpy.types.Scene.blendermcp_hyper3d_api_key
    del bpy.types.Scene.blendermcp_use_sketchfab
    del bpy.types.Scene.blendermcp_sketchfab_api_key
    del bpy.types.Scene.blendermcp_use_hunyuan3d
    del bpy.types.Scene.blendermcp_hunyuan3d_mode
    del bpy.types.Scene.blendermcp_hunyuan3d_secret_id
    del bpy.types.Scene.blendermcp_hunyuan3d_secret_key
    del bpy.types.Scene.blendermcp_hunyuan3d_api_url
    del bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution
    del bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps
    del bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale
    del bpy.types.Scene.blendermcp_hunyuan3d_texture

    print("BlenderMCP addon unregistered")

if __name__ == "__main__":
    register()
