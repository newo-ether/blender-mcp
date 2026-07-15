"""Bounded node-graph queries and indexes."""

from __future__ import annotations

import bpy

from .constants import NODE_TREE_INDEX_SCHEMA, NODE_TREE_SOFT_RESPONSE_BYTES
from .serialization import _gn_link_record, _node_graph_record
from .targets import _node_export_target, _node_target_capabilities


def _node_soft_limit_response(snapshot):
    """Return bounded guidance instead of carrying an oversized full graph."""
    if snapshot["scope"]["kind"] != "full":
        return snapshot
    if snapshot["stats"]["json_bytes"] <= NODE_TREE_SOFT_RESPONSE_BYTES:
        return snapshot
    result = {
        "schema": snapshot["schema"],
        "status": "summary",
        "reason": "soft_response_limit",
        "view": snapshot["view"],
        "revision": snapshot["revision"],
        "scope": snapshot["scope"],
        "stats": snapshot["stats"],
        "tree": {
            key: snapshot["tree"][key]
            for key in ("name", "bl_idname", "editable", "library")
        },
        "next_action": (
            "Call get_node_tree_index, then export_node_tree with node_names and "
            "a bounded neighbor_depth. Use an explicit output_path only when the "
            "complete snapshot is required on disk."
        ),
        "soft_limit_bytes": NODE_TREE_SOFT_RESPONSE_BYTES,
    }
    for key in ("tree_ref", "owner", "capabilities", "view_selection"):
        if key in snapshot:
            result[key] = snapshot[key]
    return result

def _node_named_attribute_name(node):
    for socket in node.inputs:
        if socket.name == "Name" and hasattr(socket, "default_value"):
            value = socket.default_value
            return value if isinstance(value, str) else ""
    return ""

def _node_query_graph(
    target,
    query_type,
    *,
    node_names=None,
    from_node="",
    to_node="",
    attribute_name="",
    socket_id="",
    direction="downstream",
    fields=None,
    limit=200,
):
    """Run bounded deterministic graph queries without exporting a full graph."""
    tree = target["tree"]
    query_type = str(query_type or "").strip().lower()
    limit = int(limit)
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be from 1 to 1000")
    node_map = {node.name: node for node in tree.nodes}
    links = sorted(
        (_gn_link_record(link) for link in tree.links),
        key=lambda item: (item["from_node"], item["to_node"], item["from_socket"], item["to_socket"]),
    )
    requested = set(node_names or [])
    if requested - set(node_map):
        raise ValueError("One or more requested nodes do not exist")
    records = []
    if query_type == "socket_links":
        if socket_id and len(requested) != 1:
            raise ValueError("socket_id requires exactly one node_name")
        socket_node = next(iter(requested), "")
        records = [
            link for link in links
            if not requested or link["from_node"] in requested or link["to_node"] in requested
        ]
        if socket_id:
            records = [
                link for link in records
                if (link["from_node"] == socket_node and link["from_socket"] == socket_id)
                or (link["to_node"] == socket_node and link["to_socket"] == socket_id)
            ]
    elif query_type == "named_attributes":
        wanted = str(attribute_name or "").casefold()
        for node in sorted(tree.nodes, key=lambda item: item.name):
            name = _node_named_attribute_name(node)
            if wanted and name.casefold() != wanted:
                continue
            identifier = node.bl_idname
            if "StoreNamedAttribute" in identifier or "RemoveNamedAttribute" in identifier:
                access = "writer"
            elif "NamedAttribute" in identifier:
                access = "reader"
            else:
                continue
            records.append({
                "node": node.name,
                "node_type": identifier,
                "attribute": name,
                "access": access,
                "data_type": getattr(node, "data_type", None),
            })
    elif query_type == "shortest_path":
        if from_node not in node_map or to_node not in node_map:
            raise ValueError("from_node and to_node must name existing nodes")
        if direction not in {"upstream", "downstream", "both"}:
            raise ValueError("direction must be upstream, downstream, or both")
        adjacency = {}
        for link in links:
            if direction in {"downstream", "both"}:
                adjacency.setdefault(link["from_node"], []).append((link["to_node"], link))
            if direction in {"upstream", "both"}:
                adjacency.setdefault(link["to_node"], []).append((link["from_node"], link))
        queue = [from_node]
        previous = {from_node: (None, None)}
        for current in queue:
            if current == to_node:
                break
            for neighbor, link in adjacency.get(current, []):
                if neighbor not in previous:
                    previous[neighbor] = (current, link)
                    queue.append(neighbor)
        if to_node in previous:
            path_links = []
            current = to_node
            while previous[current][0] is not None:
                prior, link = previous[current]
                path_links.append(link)
                current = prior
            records = list(reversed(path_links))
    elif query_type in {"upstream", "downstream", "slice"}:
        if not requested:
            raise ValueError("node_names is required for graph slices")
        direction_value = direction if query_type == "slice" else query_type
        if direction_value not in {"upstream", "downstream", "both"}:
            raise ValueError("direction must be upstream, downstream, or both")
        included = set(requested)
        queue = list(sorted(requested))
        while queue and len(included) < limit:
            current = queue.pop(0)
            for link in links:
                neighbors = []
                if direction_value in {"downstream", "both"} and link["from_node"] == current:
                    neighbors.append(link["to_node"])
                if direction_value in {"upstream", "both"} and link["to_node"] == current:
                    neighbors.append(link["from_node"])
                for neighbor in neighbors:
                    if neighbor not in included:
                        included.add(neighbor)
                        queue.append(neighbor)
        records = [{"node": name, "node_type": node_map[name].bl_idname} for name in sorted(included)]
    elif query_type == "fields":
        allowed = set(fields or ["name", "label", "bl_idname"])
        for name in sorted(requested or set(node_map)):
            node = node_map[name]
            full = _node_graph_record(node, "operations")
            records.append({key: value for key, value in full.items() if key in allowed})
    else:
        raise ValueError(
            "query_type must be fields, socket_links, named_attributes, shortest_path, upstream, downstream, or slice"
        )
    total = len(records)
    records = records[:limit]
    snapshot = _node_export_target(target, "operations", list(requested), 0) if requested else None
    revision = snapshot["revision"] if snapshot else _node_export_target(target, "layout")["revision"]
    return {
        "schema": "blender-node-graph-query/1",
        "tree_ref": target["tree_ref"],
        "revision": revision,
        "query_type": query_type,
        "query": {
            "node_names": sorted(requested),
            "from_node": from_node,
            "to_node": to_node,
            "attribute_name": attribute_name,
            "socket_id": socket_id,
            "direction": direction,
            "fields": list(fields or []),
            "limit": limit,
        },
        "total_matches": total,
        "truncated": total > limit,
        "records": records,
    }

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
