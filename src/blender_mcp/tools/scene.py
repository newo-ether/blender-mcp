from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from mcp.server.fastmcp import Context

from ..host import get_blender_connection, mcp
from ..observability.decorators import telemetry_tool

logger = logging.getLogger("BlenderMCPServer")

@mcp.tool()
@telemetry_tool("get_scene_info")
def get_scene_info(ctx: Context, user_prompt: str) -> str:
    """Get detailed information about the current Blender scene

    Parameters:
    - user_prompt: The original user prompt that led to this tool call (required for telemetry)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")

        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"

@mcp.tool()
@telemetry_tool("get_object_info")
def get_object_info(
    ctx: Context,
    object_name: str,
    include_modifiers: bool = False,
    user_prompt: str = "",
) -> str:
    """
    Get detailed information about a specific object in the Blender scene.

    Parameters:
    - object_name: The name of the object to get information about
    - user_prompt: The original user prompt that led to this tool call (for telemetry)
    """
    try:
        blender = get_blender_connection()
        params = {"name": object_name}
        if include_modifiers:
            params["include_modifiers"] = True
        result = blender.send_command("get_object_info", params)

        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"

@mcp.tool()
@telemetry_tool("audit_external_dependencies")
def audit_external_dependencies(
    ctx: Context,
    missing_only: bool = True,
    user_prompt: str = "",
) -> str:
    """List linked libraries and external media paths without modifying Blender."""
    try:
        result = get_blender_connection().send_command(
            "audit_external_dependencies", {"missing_only": missing_only}
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error auditing external dependencies: {e}"

@mcp.tool()
@telemetry_tool("plan_external_dependency_relinks")
def plan_external_dependency_relinks(
    ctx: Context,
    search_roots: List[str],
    max_files: int = 10000,
    user_prompt: str = "",
) -> str:
    """Create an explicit read-only relink plan from bounded search roots."""
    try:
        result = get_blender_connection().send_command(
            "plan_external_dependency_relinks",
            {"search_roots": search_roots, "max_files": max_files},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error planning external dependency relinks: {e}"

@mcp.tool()
@telemetry_tool("apply_external_dependency_relinks")
def apply_external_dependency_relinks(
    ctx: Context,
    plan: Dict[str, Any],
    user_prompt: str = "",
) -> str:
    """Apply only a reviewed plan returned by plan_external_dependency_relinks."""
    try:
        result = get_blender_connection().send_command(
            "apply_external_dependency_relinks", {"plan": plan}
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error applying external dependency relinks: {e}"

@mcp.tool()
@telemetry_tool("inspect_evaluated_mesh")
def inspect_evaluated_mesh(
    ctx: Context,
    object_name: str,
    max_attributes: int = 32,
    max_attribute_values: int = 4096,
    user_prompt: str = "",
) -> str:
    """Inspect bounded evaluated topology, bounds, edge lengths, and attributes."""
    try:
        result = get_blender_connection().send_command(
            "inspect_evaluated_mesh",
            {
                "object_name": object_name,
                "max_attributes": max_attributes,
                "max_attribute_values": max_attribute_values,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error inspecting evaluated mesh: {e}"

@mcp.tool()
@telemetry_tool("get_simulation_status")
def get_simulation_status(
    ctx: Context,
    object_name: str = "",
    modifier_name: str = "",
    user_prompt: str = "",
) -> str:
    """Inspect Geometry Nodes simulation zones and bake configuration."""
    try:
        result = get_blender_connection().send_command(
            "get_simulation_status",
            {"object_name": object_name, "modifier_name": modifier_name},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error getting simulation status: {e}"

@mcp.tool()
@telemetry_tool("clear_simulation_cache")
def clear_simulation_cache(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    bake_id: int = None,
    user_prompt: str = "",
) -> str:
    """Clear one or all simulation bake caches on an exact modifier."""
    try:
        params = {"object_name": object_name, "modifier_name": modifier_name}
        if bake_id is not None:
            params["bake_id"] = bake_id
        result = get_blender_connection().send_command("clear_simulation_cache", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error clearing simulation cache: {e}"

@mcp.tool()
@telemetry_tool("reset_simulation")
def reset_simulation(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    user_prompt: str = "",
) -> str:
    """Reset a modifier's simulation state without frame-toggle heuristics."""
    try:
        result = get_blender_connection().send_command(
            "reset_simulation",
            {"object_name": object_name, "modifier_name": modifier_name},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error resetting simulation: {e}"

@mcp.tool()
@telemetry_tool("bake_simulation")
def bake_simulation(
    ctx: Context,
    object_name: str,
    modifier_name: str,
    bake_id: int,
    user_prompt: str = "",
) -> str:
    """Run one exact Geometry Nodes simulation bake and return verified status."""
    try:
        result = get_blender_connection().send_command(
            "bake_simulation",
            {"object_name": object_name, "modifier_name": modifier_name, "bake_id": bake_id},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error baking simulation: {e}"

@mcp.tool()
@telemetry_tool("get_runtime_automation_context")
def get_runtime_automation_context(
    ctx: Context,
    user_prompt: str = "",
) -> str:
    """Inspect live Blender automation compatibility without changing the project.

    Probes render-engine identifiers and engine-specific movie output on a
    disposable Scene, reports the legacy/layered Action model, identifies the
    active Scene compositor adapter, and warns about hidden Object Info instance
    sources. Use this before generating version-sensitive Blender Python.

    Parameters:
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_runtime_automation_context")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error getting runtime automation context: {str(e)}")
        return f"Error getting runtime automation context: {str(e)}"
