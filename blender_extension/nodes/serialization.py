"""Node-tree serialization and stable revision helpers."""

from __future__ import annotations

import hashlib
import json

import bpy

from .constants import (
    _GN_NODE_PROPERTY_EXCLUDES,
    GEOMETRY_NODES_SNAPSHOT_SCHEMA,
    GEOMETRY_NODES_VIEWS,
    NODE_TREE_SNAPSHOT_SCHEMA,
)
from .dynamic import (
    _node_dynamic_collection_names,
    _node_dynamic_collection_record,
)


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

def _gn_operation_socket_record(socket, direction, index):
    record = {"id": _gn_socket_id(socket, direction, index)}
    identifier = getattr(socket, "identifier", "") or socket.name
    if socket.name != identifier:
        record["name"] = socket.name
    if not bool(socket.enabled):
        record["enabled"] = False
    if bool(getattr(socket, "is_multi_input", False)):
        record["multi_input"] = True
    if hasattr(socket, "default_value"):
        try:
            record["default"] = _gn_json_value(socket.default_value)
        except (AttributeError, TypeError, ValueError):
            pass
    return record

def _gn_explicit_node_references(node):
    """Expose stable references that Blender may mark as read-only RNA."""
    result = {}
    node_tree = getattr(node, "node_tree", None)
    if isinstance(node_tree, bpy.types.NodeTree):
        result["node_tree"] = _gn_json_value(node_tree)
    for identifier in ("paired_input", "paired_output"):
        paired_node = getattr(node, identifier, None)
        if isinstance(paired_node, bpy.types.Node):
            result[identifier] = _gn_json_value(paired_node)
    return result

def _gn_operation_properties(node):
    """Keep operation-defining enums and non-default writable scalar values."""
    result = {}
    for prop in node.bl_rna.properties:
        identifier = prop.identifier
        if identifier in _GN_NODE_PROPERTY_EXCLUDES or identifier == "rna_type":
            continue
        if getattr(prop, "type", None) == "COLLECTION":
            continue
        if getattr(prop, "is_hidden", False) or getattr(prop, "is_skip_save", False):
            continue
        if getattr(prop, "is_readonly", False):
            continue
        try:
            value = _gn_json_value(getattr(node, identifier))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue
        include = getattr(prop, "type", None) == "ENUM"
        if not include:
            try:
                default = _gn_json_value(prop.default)
                include = value != default
            except (AttributeError, TypeError, ValueError, RuntimeError):
                include = False
        if include:
            result[identifier] = value
    for identifier, value in _gn_explicit_node_references(node).items():
        result.setdefault(identifier, value)
    return result

def _gn_operation_node_record(node):
    return {
        "id": node.name,
        "name": node.name,
        "label": node.label,
        "bl_idname": node.bl_idname,
        "properties": _gn_operation_properties(node),
        "inputs": [
            _gn_operation_socket_record(socket, "INPUT", index)
            for index, socket in enumerate(node.inputs)
            if socket.enabled or socket.is_linked
        ],
        "outputs": [
            _gn_operation_socket_record(socket, "OUTPUT", index)
            for index, socket in enumerate(node.outputs)
            if socket.enabled or socket.is_linked
        ],
    }

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
    if view == "operations":
        return _gn_operation_node_record(node)
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
        for identifier, value in _gn_explicit_node_references(node).items():
            record["properties"].setdefault(identifier, value)
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

def _gn_operation_interface_record(item):
    full = _gn_interface_record(item)
    keys = {
        "item_type", "identifier", "name", "parent", "in_out", "socket_type",
        "default", "default_input", "structure_type",
    }
    return {key: value for key, value in full.items() if key in keys}

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
    requested_view = str(view or "auto").strip().lower()
    if requested_view == "auto":
        view = "semantic" if node_names else "operations"
    else:
        view = normalizer(view)
    record_factory = record_factory or _node_graph_record
    include_semantic = view in {"semantic", "operations", "all"}
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
    if view == "operations":
        view_interface = [_gn_operation_interface_record(item) for item in tree.interface.items_tree]
    else:
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
    if requested_view == "auto":
        snapshot["view_selection"] = {
            "requested": "auto",
            "selected": view,
            "reason": "targeted_subgraph" if node_names else "full_graph_context_budget",
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
    for identifier in _node_dynamic_collection_names(node):
        structures.append(
            _node_dynamic_collection_record(node, identifier, _gn_json_value)
        )
    return structures

def _node_graph_record(node, view):
    """Extend the compatibility node record with generic dynamic structures."""
    record = _gn_node_record(node, view)
    if view in {"semantic", "operations", "all"}:
        structures = _node_special_structure_schema(node)
        if structures:
            record["special_structures"] = structures
    return record
