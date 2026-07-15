from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.server.fastmcp import Context

from ..host import mcp
from ..transport.instances import DEFAULT_LEASE_SECONDS

logger = logging.getLogger("BlenderMCPServer")

from .. import host


@mcp.tool()
def list_blender_instances(ctx: Context, validate_live: bool = True) -> Dict[str, Any]:
    """Discover all registered local Blender instances without selecting one."""
    instances = host.instance_manager.list_instances(validate_live=validate_live)
    claimable = [item for item in instances if item["status"] == "ready"]
    return {
        "schema": "blender-mcp-instance-list/1",
        "instances": instances,
        "count": len(instances),
        "claimable_count": len(claimable),
        "requires_selection": len(claimable) > 1,
    }

@mcp.tool()
def claim_blender_instance(
    ctx: Context,
    instance_id: str,
    expected_file_session_id: str = "",
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> Dict[str, Any]:
    """Claim and select exactly one registered Blender instance."""
    result = host.instance_manager.claim(
        instance_id,
        expected_file_session_id=expected_file_session_id,
        lease_seconds=lease_seconds,
    )
    host.blender_connection = host.instance_manager.active
    host.blender_connection.params_enricher = host.instance_manager.prepare_params
    return result

@mcp.tool()
def get_active_blender_instance(ctx: Context) -> Dict[str, Any]:
    """Return the selected Blender identity and lease state."""
    return host.instance_manager.active_summary()

@mcp.tool()
def release_blender_instance(ctx: Context) -> Dict[str, Any]:
    """Release only this MCP process's active Blender claim."""
    result = host.instance_manager.release()
    host.blender_connection = None
    return result
