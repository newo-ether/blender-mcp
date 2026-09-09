"""Live layout measurements kept separate from canonical graph revisions."""

from __future__ import annotations

import math
import json

import bpy

from .editor_context import _pointer_id
from .layout_core import MAX_LAYOUT_NODES, audit_layout, plan_layout
from .serialization import _gn_export_tree, _gn_link_record
from .targets import _node_export_target, _node_resolve_tree_ref, _node_target_capabilities


def inspect_layout(tree_ref, refresh=False, context_id="", size_hints=None):
    target = _node_resolve_tree_ref(tree_ref)
    tree = target["tree"]
    if len(tree.nodes) > MAX_LAYOUT_NODES:
        raise ValueError(f"Layout inspection supports at most {MAX_LAYOUT_NODES} nodes")
    matches = []
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "NODE_EDITOR" or area.spaces.active.edit_tree != tree:
                continue
            identity = f"{_pointer_id('window', window)}/{_pointer_id('area', area)}"
            matches.append((identity, window, area))
    if context_id:
        matches = [match for match in matches if match[0] == context_id]
        if not matches:
            raise ValueError("stale_layout_context: context_id does not show the requested tree")
    refreshed = False
    refresh_error = None
    if refresh and len(matches) > 1:
        raise ValueError("multiple_layout_editors: supply one exact context_id")
    if refresh and len(matches) == 1 and not bpy.app.background:
        _, window, area = matches[0]
        try:
            area.tag_redraw()
            with bpy.context.temp_override(window=window, area=area):
                result = bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            refreshed = "FINISHED" in result
        except (RuntimeError, TypeError) as exc:
            refresh_error = str(exc)
    # Canonical revision is obtained AFTER redraw, because auto-shrink may
    # update authored Frame locations. Measurements themselves are not hashed.
    snapshot = _gn_export_tree(tree, "layout") if tree.bl_idname == "GeometryNodeTree" else _node_export_target(target, "layout")
    snapshot["tree_ref"] = target["tree_ref"]
    snapshot["capabilities"] = _node_target_capabilities(target)
    snapshot["tree"]["links"] = [_gn_link_record(link) for link in tree.links]
    snapshot["stats"]["link_count"] = len(tree.links)
    snapshot["schema"] = "blender-node-layout/1"
    snapshot["scope"].pop("content_revision", None)
    scale = float(bpy.context.preferences.system.ui_scale)
    hints = size_hints or {}
    if not isinstance(hints, dict) or set(hints) - set(snapshot["tree"]["nodes"]):
        raise ValueError("size_hints must map exact existing node names to [width, height] in canvas units")
    for name, hint in hints.items():
        if not isinstance(hint, (list, tuple)) or len(hint) != 2 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in hint):
            raise ValueError(f"Invalid size hint for {name!r}")

    def absolute(node):
        if hasattr(node, "location_absolute"):
            return [float(v) for v in node.location_absolute]
        value = [float(v) for v in node.location]
        parent = node.parent
        while parent is not None:
            value = [value[0] + parent.location.x, value[1] + parent.location.y]
            parent = parent.parent
        return value

    for node in tree.nodes:
        row = snapshot["tree"]["nodes"][node.name]
        location = absolute(node)
        raw = [float(v) for v in node.dimensions]
        size, status = None, "unavailable"
        if scale > 0 and all(math.isfinite(v) and v > 0 for v in raw):
            candidate = [v / scale for v in raw]
            # Expanded widths validate UI units. RNA does not expose the drawn
            # origin offset for collapsed nodes/reroutes: keep those provisional
            # even after redraw rather than claiming exact rectangles.
            if node.hide or node.bl_idname == "NodeReroute" or abs(candidate[0] - node.width) <= max(2, node.width * .03):
                size = candidate
                status = "estimated" if node.hide or node.bl_idname == "NodeReroute" else ("refreshed" if refreshed else "cached")
        if node.name in hints:
            size, status = list(hints[node.name]), "estimated"
        row["layout"].update(
            absolute_location=location, dimensions=raw,
            bounds=([location[0], location[1], location[0] + size[0], location[1] - size[1]] if size else None),
            bounds_status=status, hide=bool(node.hide),
        )
    snapshot["measurement"] = {
        "units": "canvas", "ui_scale": scale, "refreshed": refreshed,
        "matching_context_ids": [match[0] for match in matches], "refresh_error": refresh_error,
        "bounds_order": ["left", "top", "right", "bottom"],
        "source_revision_excludes_measurements": True,
        "socket_positions_available": False,
    }
    for _ in range(3):
        size = len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if snapshot["stats"].get("json_bytes") == size:
            break
        snapshot["stats"]["json_bytes"] = size
    return snapshot


def audit_node_layout(tree_ref, refresh=False, context_id="", min_children=4, max_children=19, min_gap=20, diagnostic_limit=200):
    return audit_layout(inspect_layout(tree_ref, refresh, context_id), min_children, max_children, min_gap, diagnostic_limit)


def plan_node_layout(tree_ref, expected_revision, main_path=None, refresh=False, context_id="", size_hints=None, horizontal_gap=80, vertical_gap=40, stage_gap=140):
    snapshot = inspect_layout(tree_ref, refresh, context_id, size_hints)
    if snapshot["revision"] != expected_revision:
        return {"schema": "blender-node-layout-plan/1", "status": "stale_revision", "will_mutate": False,
                "revision": snapshot["revision"], "patch": None, "reason": "Inspect the latest layout before planning"}
    if not snapshot["capabilities"]["editable"]:
        return {"schema": "blender-node-layout-plan/1", "status": "read_only", "will_mutate": False, "patch": None}
    return plan_layout(snapshot, main_path, horizontal_gap, vertical_gap, stage_gap=stage_gap)
