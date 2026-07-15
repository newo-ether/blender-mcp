"""Read-only node-graph diagnostics."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic


def _node_json_pointer_token(value):
    return str(value).replace("~", "~0").replace("/", "~1")

def _node_named_socket(sockets, name):
    socket = sockets.get(name)
    if socket is not None:
        return socket
    folded = name.casefold()
    for candidate in sockets:
        if (
            str(getattr(candidate, "name", "")).casefold() == folded
            or str(getattr(candidate, "identifier", "")).casefold() == folded
        ):
            return candidate
    return None

def _node_reaches_group_output(tree, source_node):
    outputs = {
        node.name for node in tree.nodes if node.bl_idname == "NodeGroupOutput"
    }
    if not outputs:
        return False
    adjacency = {}
    for link in tree.links:
        if not bool(getattr(link, "is_valid", True)):
            continue
        adjacency.setdefault(link.from_node.name, set()).add(link.to_node.name)
    pending = list(adjacency.get(source_node.name, ()))
    visited = {source_node.name}
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name in outputs:
            return True
        visited.add(name)
        pending.extend(adjacency.get(name, ()))
    return False

def _node_graph_diagnostics(tree):
    diagnostics = []
    if tree.bl_idname != "GeometryNodeTree":
        return diagnostics
    for node in sorted(tree.nodes, key=lambda item: item.name):
        if node.bl_idname != "GeometryNodeObjectInfo":
            continue
        object_socket = _node_named_socket(node.inputs, "Object")
        instance_socket = _node_named_socket(node.inputs, "As Instance")
        if object_socket is None or instance_socket is None:
            continue
        source = getattr(object_socket, "default_value", None)
        if not isinstance(source, bpy.types.Object):
            continue
        if getattr(source, "library", None) is not None:
            continue
        if object_socket.is_linked or instance_socket.is_linked:
            continue
        if not bool(getattr(instance_socket, "default_value", False)):
            continue
        if not bool(getattr(source, "hide_render", False)):
            continue
        if not _node_reaches_group_output(tree, node):
            continue
        path = f"/tree/nodes/{_node_json_pointer_token(node.name)}"
        diagnostics.append(_gn_patch_diagnostic(
            "warning",
            "hidden_object_info_instance_source",
            path,
            f"Object Info node {node.name!r} instances local object "
            f"{source.name!r}, which is hidden from render; generated instances "
            "may also be invisible. Keep the prototype render-visible outside "
            "the camera, disable As Instance, or realize/author the geometry "
            "inside the node tree.",
        ))
    return diagnostics
