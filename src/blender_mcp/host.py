"""MCP host composition state and Blender connection lifecycle."""

from __future__ import annotations

import logging
import os
import threading
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
    try:
        logger.info("BlenderMCP server starting up")
        try:
            record_startup()
        except Exception as e:
            logger.debug(f"Failed to record startup telemetry: {e}")
        # Starting an MCP process must not occupy Blender. The first live tool
        # selects a target; the AI releases it before handing control back.
        yield {}
    finally:
        release_blender_connection(ignore_errors=True)
        logger.info("BlenderMCP server shut down")

BLENDER_MCP_INSTRUCTIONS = (
    "Use Blender MCP for tasks that depend on the live Blender file. "
    "Start with the smallest useful read-only inspection. Prefer dedicated "
    "structured tools; for node edits inspect an index or targeted export, "
    "check live schemas when version-sensitive, validate a patch, apply it "
    "transactionally, and read back the affected subgraph. Use arbitrary "
    "Blender Python only when structured tools cannot express the operation. "
    "Use screenshots only when appearance matters. Do not save or overwrite "
    "the .blend file unless the user asked. Before handing control back after "
    "a live Blender task, call release_blender_instance even when the task "
    "failed or stopped early. Stop and report a disconnected, "
    "read-only, or unsupported state instead of guessing."
)

mcp = FastMCP(
    "BlenderMCP",
    instructions=BLENDER_MCP_INSTRUCTIONS,
    lifespan=server_lifespan,
)

blender_connection = None
_connection_lock = threading.RLock()

polyhaven_enabled = False  # Add this global variable

instance_manager = InstanceConnectionManager(
    connection_factory=lambda host, port: BlenderConnection(host=host, port=port),
    owner_label=os.getenv("BLENDER_MCP_CLIENT_LABEL", "MCP client"),
)


def _bind_active_connection(connection: BlenderConnection) -> BlenderConnection:
    global blender_connection
    connection.params_enricher = instance_manager.prepare_params
    blender_connection = connection
    return connection


def _refresh_connection_status(connection: BlenderConnection) -> None:
    global polyhaven_enabled
    result = connection.send_command("get_polyhaven_status")
    polyhaven_enabled = result.get("enabled", False)


def claim_blender_connection(
    instance_id: str,
    *,
    expected_file_session_id: str = "",
    lease_seconds: float,
) -> dict[str, Any]:
    """Claim one exact target and keep host/manager state synchronized."""
    with _connection_lock:
        result = instance_manager.claim(
            instance_id,
            expected_file_session_id=expected_file_session_id,
            lease_seconds=lease_seconds,
        )
        _bind_active_connection(instance_manager.active)
        return result


def release_blender_connection(*, ignore_errors: bool = False) -> dict[str, Any]:
    """Release any AI claim and always clear this process's local connection."""
    global blender_connection
    with _connection_lock:
        try:
            if instance_manager.active is not None:
                return instance_manager.release(ignore_errors=ignore_errors)
            if blender_connection is not None:
                blender_connection.disconnect()
            return {"released": False, "reason": "no_active_instance"}
        finally:
            blender_connection = None


def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global blender_connection

    with _connection_lock:
        if instance_manager.active is not None:
            blender_connection = instance_manager.active

        if blender_connection is not None:
            try:
                instance_manager.ensure_lease()
                _refresh_connection_status(blender_connection)
                return blender_connection
            except Exception as error:
                target = dict(instance_manager.active_record or {})
                logger.warning("Existing connection is no longer valid: %s", error)
                if target:
                    try:
                        instance_manager.claim(
                            target["instance_id"],
                            expected_file_session_id=target["file_session_id"],
                        )
                        connection = _bind_active_connection(instance_manager.active)
                        _refresh_connection_status(connection)
                        logger.info("Reconnected selected Blender instance %s", target["instance_id"])
                        return connection
                    except Exception:
                        instance_manager.invalidate()
                        blender_connection = None
                        raise
                blender_connection.disconnect()
                blender_connection = None

        registrations = discover_registry_records(directory=instance_manager.directory)
        if registrations:
            try:
                connection = _bind_active_connection(instance_manager.auto_select())
                _refresh_connection_status(connection)
            except Exception:
                instance_manager.invalidate()
                blender_connection = None
                raise
            logger.info("Selected registered Blender instance %s", instance_manager.active_record["instance_id"])
            return connection

        host = os.getenv("BLENDER_HOST", DEFAULT_BRIDGE_HOST)
        port = int(os.getenv("BLENDER_PORT", str(DEFAULT_BRIDGE_PORT)))
        connection = BlenderConnection(host=host, port=port)
        if not connection.connect():
            logger.error("Failed to connect to Blender")
            raise ConnectionError("Could not connect to Blender. Make sure the Blender addon is running.")
        blender_connection = connection
        logger.info("Created new persistent connection to Blender")
        return blender_connection
