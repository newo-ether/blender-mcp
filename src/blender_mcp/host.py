"""MCP host composition state and Blender connection lifecycle."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from mcp.server.fastmcp import FastMCP

from .observability.telemetry import record_startup
from .transport.connection import BlenderConnection
from .transport.constants import DEFAULT_BRIDGE_HOST, DEFAULT_BRIDGE_PORT
from .transport.instances import InstanceConnectionManager, discover_registry_records

logger = logging.getLogger("BlenderMCPServer")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
        logger.info("BlenderMCP server starting up")

        # Record startup event for telemetry
        try:
            record_startup()
        except Exception as e:
            logger.debug(f"Failed to record startup telemetry: {e}")

        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            blender = get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global blender_connection
        if instance_manager.active is not None:
            instance_manager.release(ignore_errors=True)
            blender_connection = None
        elif blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            blender_connection.disconnect()
            blender_connection = None
        logger.info("BlenderMCP server shut down")

BLENDER_MCP_INSTRUCTIONS = (
    "Use Blender MCP for tasks that depend on the live Blender file. "
    "Start with the smallest useful read-only inspection. Prefer dedicated "
    "structured tools; for node edits inspect an index or targeted export, "
    "check live schemas when version-sensitive, validate a patch, apply it "
    "transactionally, and read back the affected subgraph. Use arbitrary "
    "Blender Python only when structured tools cannot express the operation. "
    "Use screenshots only when appearance matters. Do not save or overwrite "
    "the .blend file unless the user asked. Stop and report a disconnected, "
    "read-only, or unsupported state instead of guessing."
)

mcp = FastMCP(
    "BlenderMCP",
    instructions=BLENDER_MCP_INSTRUCTIONS,
    lifespan=server_lifespan,
)

blender_connection = None

polyhaven_enabled = False  # Add this global variable

instance_manager = InstanceConnectionManager(
    connection_factory=lambda host, port: BlenderConnection(host=host, port=port),
    owner_label=os.getenv("BLENDER_MCP_CLIENT_LABEL", "MCP client"),
)

def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global blender_connection, polyhaven_enabled  # Add polyhaven_enabled to globals

    if instance_manager.active is not None:
        instance_manager.ensure_lease()
        blender_connection = instance_manager.active

    # If we have an existing connection, check if it's still valid
    if blender_connection is not None:
        try:
            # First check if PolyHaven is enabled by sending a ping command
            result = blender_connection.send_command("get_polyhaven_status")
            # Store the PolyHaven status globally
            polyhaven_enabled = result.get("enabled", False)
            return blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                blender_connection.disconnect()
            except:
                pass
            blender_connection = None
            if instance_manager.active is not None:
                instance_manager.invalidate()

    # Create a new connection if needed
    if blender_connection is None:
        registrations = discover_registry_records(directory=instance_manager.directory)
        if registrations:
            blender_connection = instance_manager.auto_select()
            blender_connection.params_enricher = instance_manager.prepare_params
            logger.info("Selected registered Blender instance %s", instance_manager.active_record["instance_id"])
            return blender_connection
        host = os.getenv("BLENDER_HOST", DEFAULT_BRIDGE_HOST)
        port = int(os.getenv("BLENDER_PORT", str(DEFAULT_BRIDGE_PORT)))
        blender_connection = BlenderConnection(host=host, port=port)
        if not blender_connection.connect():
            logger.error("Failed to connect to Blender")
            blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")

    return blender_connection
