"""Shared parent-local layout assignment for both transactional patch domains."""


def assign_node_layout(node, layout, resolve_parent):
    # Blender preserves canvas position when reparenting. Set the parent first
    # so an explicitly supplied location is relative to the final parent.
    if "parent" in layout:
        parent = resolve_parent(layout["parent"]) if layout["parent"] is not None else None
        if layout["parent"] is not None and parent is None:
            raise ValueError("Node layout parent was not found")
        if parent is not None:
            if parent.bl_idname != "NodeFrame":
                raise ValueError("Node parent must be a Frame")
            ancestor = parent
            visited = set()
            while ancestor is not None:
                if ancestor == node or ancestor in visited:
                    raise ValueError("Node layout parent cycle")
                visited.add(ancestor)
                ancestor = ancestor.parent
        node.parent = parent
    for field in ("location", "width", "height"):
        if field in layout:
            setattr(node, field, layout[field])
