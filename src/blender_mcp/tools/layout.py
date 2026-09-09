"""Node presentation tools: inspect, audit and plan; apply via existing patches."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from mcp.server.fastmcp import Context

from ..host import get_blender_connection, mcp
from ..observability.decorators import telemetry_tool
from ..protocol.node_tree import resolve_workspace_json_path


def _call(command, params, output_path=""):
    result = get_blender_connection().send_command(command, params)
    if output_path:
        path = resolve_workspace_json_path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = handle.name
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()
        return json.dumps({"status": "written", "path": str(path), "revision": result.get("revision")}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
@telemetry_tool("inspect_node_layout")
def inspect_node_layout(ctx: Context, tree_ref: Dict[str, Any], refresh: bool = False,
                        context_id: str = "", output_path: str = "", user_prompt: str = "") -> str:
    """Read complete layout, frame parents, links and measured canvas bounds.

    Measurements are separate from source revision. refresh redraws an already
    open matching Node Editor without changing its tree/selection/view. Multiple
    matching editors require context_id from get_node_editor_context. Undrawn or
    stale dimensions remain explicitly unavailable/cached. Does not save.
    Supports up to 1500 nodes. output_path is a workspace-relative JSON file.
    """
    return _call("inspect_node_layout", {"tree_ref": tree_ref, "refresh": refresh, "context_id": context_id}, output_path)


@mcp.tool()
@telemetry_tool("audit_node_layout")
def audit_node_layout(ctx: Context, tree_ref: Dict[str, Any], refresh: bool = False,
                      context_id: str = "", min_children: int = 4, max_children: int = 19,
                      min_gap: float = 20, diagnostic_limit: int = 200, user_prompt: str = "") -> str:
    """Read-only frame membership, 4-19 child counts, bounds and wire-order audit.

    All functional nodes, including Group Output and Reroute, require a Frame.
    Nested Frames count as one direct child and are checked independently.
    Counts are exact; cached/unknown bounds are disclosed. Wire checks cover
    endpoint direction/order, not exact rendered Bezier intersections. valid
    means no detected hard errors, not visual acceptance; check bounds_verified
    and diagnostics as well. refresh/context_id follow inspect_node_layout.
    """
    return _call("audit_node_layout", {"tree_ref": tree_ref, "refresh": refresh, "context_id": context_id,
                 "min_children": min_children, "max_children": max_children, "min_gap": min_gap, "diagnostic_limit": diagnostic_limit})


@mcp.tool()
@telemetry_tool("plan_node_layout")
def plan_node_layout(ctx: Context, tree_ref: Dict[str, Any], expected_revision: str,
                     main_path: List[str] = None, refresh: bool = False, context_id: str = "",
                     size_hints: Dict[str, Any] = None, horizontal_gap: float = 80,
                     vertical_gap: float = 40, stage_gap: float = 140,
                     output_path: str = "", user_prompt: str = "") -> str:
    """Plan hierarchical left-to-right layout as a read-only candidate patch.

    Keep existing nodes, names, links, interfaces and semantic frame membership.
    Define module subgroups and 4-19-child stage Frames before calling. main_path
    contains exact node names to prioritize in the upper lane. Missing bounds
    block planning; explicit canvas [width,height] size_hints are provisional.
    Cyclic stage dependencies are reported, never silently rewired. At most 500
    nodes per plan. No automatic node/frame/subgroup creation or .blend save.
    Validate then apply the returned patch with the Geometry-specific tools for
    GeometryNodeTree, or generic tools for Shader/Compositor. Shared Geometry
    trees retain policy=reject until the caller explicitly chooses a policy.
    Reinspect/audit after application and redraw; predicted bounds are advisory.
    output_path writes the complete plan envelope, not an apply-ready patch file.
    """
    return _call("plan_node_layout", {"tree_ref": tree_ref, "expected_revision": expected_revision,
                 "main_path": main_path or [], "refresh": refresh, "context_id": context_id,
                 "size_hints": size_hints or {}, "horizontal_gap": horizontal_gap,
                 "vertical_gap": vertical_gap, "stage_gap": stage_gap}, output_path)
