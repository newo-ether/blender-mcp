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
    "version": (1, 7, 0),
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
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        return  # already running
    bpy.types.blendermcp_server = BlenderMCPServer(port=addon_prefs.preferences.port)
    bpy.types.blendermcp_server.start()
    bpy.context.scene.blendermcp_server_running = True

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

_GN_NODE_PROPERTY_EXCLUDES = {
    "rna_type", "name", "label", "location", "width", "width_hidden",
    "height", "dimensions", "parent", "select", "show_options",
    "show_preview", "show_texture", "use_custom_color", "color",
    "inputs", "outputs", "internal_links", "type", "bl_idname",
}


def _gn_normalize_view(view):
    normalized = str(view).strip().lower()
    if normalized not in GEOMETRY_NODES_VIEWS:
        choices = ", ".join(sorted(GEOMETRY_NODES_VIEWS))
        raise ValueError(f"Unsupported Geometry Nodes view {view!r}; expected: {choices}")
    return normalized


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


def _gn_export_tree(tree, view="semantic", node_names=None, neighbor_depth=0):
    view = _gn_normalize_view(view)
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
        node.name: _gn_node_record(node, view)
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
        "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
        "blender_version": list(bpy.app.version[:3]),
        "view": view,
        "tree": {
            **tree_identity,
            "interface": view_interface,
            "nodes": nodes,
            "links": links,
        },
        "scope": scope,
        "users": _gn_tree_users(tree),
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
        else {node.name: _gn_node_record(node, "all") for node in ordered_nodes}
    )
    revision_snapshot = {
        "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
        "view": "all",
        "tree": {
            **tree_identity,
            "interface": full_interface,
            "nodes": revision_nodes,
            "links": graph_links,
        },
    }
    snapshot["revision"] = _gn_snapshot_revision(revision_snapshot)
    scope["content_revision"] = _gn_snapshot_revision(snapshot)
    for _iteration in range(3):
        size = len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if snapshot["stats"]["json_bytes"] == size:
            break
        snapshot["stats"]["json_bytes"] = size
    return snapshot


def _gn_geometry_trees():
    return sorted(
        (tree for tree in bpy.data.node_groups if tree.bl_idname == "GeometryNodeTree"),
        key=lambda item: item.name,
    )


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
        "Image": bpy.data.images,
        "Texture": bpy.data.textures,
        "GeometryNodeTree": bpy.data.node_groups,
        "ShaderNodeTree": bpy.data.node_groups,
        "NodeTree": bpy.data.node_groups,
    }
    collection = collections.get(id_type)
    return collection.get(name) if collection is not None and isinstance(name, str) else None


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
        try:
            identifiers = {item.identifier for item in prop.enum_items}
            valid = isinstance(value, str) and value in identifiers
            if not valid:
                diagnostics.append(_gn_patch_diagnostic(
                    "error", "invalid_enum_value", path,
                    f"Expected one of: {', '.join(sorted(identifiers))}",
                ))
                return False
        except (AttributeError, RuntimeError, TypeError):
            valid = isinstance(value, str)
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
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "unsupported_node_type", f"{path}/node_type", str(exc),
                        ))
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
            state[identifier] = _gn_modifier_input_value(modifier, identifier)
        except (AttributeError, KeyError, TypeError, RuntimeError):
            continue
    return state


def _gn_restore_modifier_state(modifier, state):
    errors = []
    for identifier, value in state.items():
        try:
            _gn_set_modifier_input_value(modifier, identifier, value)
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
            "list_geometry_node_trees": self.list_geometry_node_trees,
            "export_geometry_node_tree": self.export_geometry_node_tree,
            "get_geometry_node_type_schema": self.get_geometry_node_type_schema,
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

    def get_geometry_node_type_schema(self, node_type):
        """Inspect a node type from this running Blender build."""
        if not isinstance(node_type, str) or not node_type.strip():
            raise ValueError("node_type must be a non-empty Blender node bl_idname")

        tree = bpy.data.node_groups.new(".BlenderMCP_TypeSchema", "GeometryNodeTree")
        try:
            try:
                node = tree.nodes.new(type=node_type.strip())
            except RuntimeError as exc:
                raise ValueError(
                    f"Unsupported Geometry Node type in Blender {bpy.app.version_string}: {node_type}"
                ) from exc

            properties = []
            for prop in node.bl_rna.properties:
                if prop.identifier in _GN_NODE_PROPERTY_EXCLUDES or prop.identifier == "rna_type":
                    continue
                if prop.type == "COLLECTION" or getattr(prop, "is_hidden", False):
                    continue
                properties.append(_gn_property_schema(node, prop))

            return {
                "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
                "blender_version": list(bpy.app.version[:3]),
                "node_type": node.bl_idname,
                "label": node.bl_label,
                "properties": properties,
                "inputs": [
                    _gn_socket_record(socket, "INPUT", index)
                    for index, socket in enumerate(node.inputs)
                ],
                "outputs": [
                    _gn_socket_record(socket, "OUTPUT", index)
                    for index, socket in enumerate(node.outputs)
                ],
            }
        finally:
            bpy.data.node_groups.remove(tree)

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
        default=False
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
