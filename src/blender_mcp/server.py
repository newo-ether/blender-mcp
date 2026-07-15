# blender_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
import tempfile
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List
import os
import sys
from pathlib import Path
import base64
from urllib.parse import urlparse

from .errors import BlenderMCPError, classify_exception
from .instance_registry import InstanceConnectionManager, discover_registry_records

from .blender_docs import (
    BlenderDocumentationContextError,
    resolve_documentation_context,
    version_requires_blender,
)
from .blender_docs_retrieval import (
    BlenderDocumentationClient,
    BlenderDocumentationRetrievalError,
)

from .geometry_nodes_schema import (
    GeometryNodesSchemaError,
    PATCH_APPLICATION_SCHEMA,
    PATCH_VALIDATION_SCHEMA,
    assert_valid_patch,
    read_patch_json,
    validate_patch_structure,
    write_snapshot_json,
)
from .node_tree_schema import (
    NodeTreeSchemaError,
    write_snapshot_json as write_node_tree_snapshot_json,
)
from .node_tree_patch import (
    NodeTreePatchError,
    PATCH_APPLICATION_SCHEMA as NODE_PATCH_APPLICATION_SCHEMA,
    PATCH_VALIDATION_SCHEMA as NODE_PATCH_VALIDATION_SCHEMA,
    assert_valid_patch as assert_valid_node_patch,
    read_patch_json as read_node_patch_json,
    validate_patch_structure as validate_node_patch_structure,
)

# Import telemetry
from .telemetry import record_startup, get_telemetry, EventType
from .telemetry_decorator import telemetry_tool, rich_telemetry_tool

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876

_LOG_REDACTED_KEYS = {
    "_claim_token", "claim_token", "code", "api_key", "secret_id",
    "secret_key", "password", "images", "input_image_urls",
}


def _redact_command_params(value: Any) -> Any:
    """Keep bridge logs useful without leaking claims, credentials, code, or media."""
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key).casefold() in _LOG_REDACTED_KEYS
            else _redact_command_params(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_command_params(item) for item in value[:20]]
    return value

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    params_enricher: Any = None
    
    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(180.0)  # Match the addon's timeout
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        
        prepared_params = dict(params or {})
        if self.params_enricher is not None:
            prepared_params = self.params_enricher(command_type, prepared_params)
        command = {
            "type": command_type,
            "params": prepared_params
        }
        
        try:
            # Log the command being sent
            logger.info(
                "Sending command: %s with params: %s",
                command_type,
                _redact_command_params(prepared_params),
            )
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(180.0)  # Match the addon's timeout
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                error = response.get("error") or {}
                raise BlenderMCPError(
                    error.get("code", "blender_python_error"),
                    response.get("message", "Unknown error from Blender"),
                    retryable=bool(error.get("retryable", False)),
                    details=error.get("details") or {},
                )
            
            return response.get("result", {})
        except BlenderMCPError:
            raise
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise BlenderMCPError(
                "blender_timeout",
                "Timeout waiting for Blender response; simplify the request and ensure Blender is running with a GUI",
                retryable=True,
            )
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise BlenderMCPError("mcp_transport_error", f"Connection to Blender lost: {str(e)}", retryable=True)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise BlenderMCPError("mcp_protocol_error", f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise classify_exception(e, operation=command_type)

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
        global _blender_connection
        if _instance_manager.active is not None:
            _instance_manager.release(ignore_errors=True)
            _blender_connection = None
        elif _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP server shut down")

# Keep this baseline short because clients may include it in every session.
# The optional prompt and Agent Skill provide expanded task guidance.
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

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCP",
    instructions=BLENDER_MCP_INSTRUCTIONS,
    lifespan=server_lifespan,
)

# Resource endpoints

# Global connection for resources (since resources can't access context)
_blender_connection = None
_polyhaven_enabled = False  # Add this global variable
_instance_manager = InstanceConnectionManager(
    connection_factory=lambda host, port: BlenderConnection(host=host, port=port),
    owner_label=os.getenv("BLENDER_MCP_CLIENT_LABEL", "MCP client"),
)

def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global _blender_connection, _polyhaven_enabled  # Add _polyhaven_enabled to globals

    if _instance_manager.active is not None:
        _instance_manager.ensure_lease()
        _blender_connection = _instance_manager.active
    
    # If we have an existing connection, check if it's still valid
    if _blender_connection is not None:
        try:
            # First check if PolyHaven is enabled by sending a ping command
            result = _blender_connection.send_command("get_polyhaven_status")
            # Store the PolyHaven status globally
            _polyhaven_enabled = result.get("enabled", False)
            return _blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None
            if _instance_manager.active is not None:
                _instance_manager.invalidate()
    
    # Create a new connection if needed
    if _blender_connection is None:
        registrations = discover_registry_records(directory=_instance_manager.directory)
        if registrations:
            _blender_connection = _instance_manager.auto_select()
            _blender_connection.params_enricher = _instance_manager.prepare_params
            logger.info("Selected registered Blender instance %s", _instance_manager.active_record["instance_id"])
            return _blender_connection
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
    
    return _blender_connection


@mcp.tool()
def list_blender_instances(ctx: Context, validate_live: bool = True) -> Dict[str, Any]:
    """Discover all registered local Blender instances without selecting one."""
    instances = _instance_manager.list_instances(validate_live=validate_live)
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
    lease_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Claim and select exactly one registered Blender instance."""
    global _blender_connection
    result = _instance_manager.claim(
        instance_id,
        expected_file_session_id=expected_file_session_id,
        lease_seconds=lease_seconds,
    )
    _blender_connection = _instance_manager.active
    _blender_connection.params_enricher = _instance_manager.prepare_params
    return result


@mcp.tool()
def get_active_blender_instance(ctx: Context) -> Dict[str, Any]:
    """Return the selected Blender identity and lease state."""
    return _instance_manager.active_summary()


@mcp.tool()
def release_blender_instance(ctx: Context) -> Dict[str, Any]:
    """Release only this MCP process's active Blender claim."""
    global _blender_connection
    result = _instance_manager.release()
    _blender_connection = None
    return result


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


@mcp.tool()
@telemetry_tool("get_blender_documentation_context")
def get_blender_documentation_context(
    ctx: Context,
    version: str = "auto",
    language: str = "en",
    sources: List[str] = None,
    user_prompt: str = "",
) -> str:
    """Resolve version-correct official Blender documentation sources.

    This tool performs no documentation network request. With version="auto"
    it reads exact build metadata from the connected Blender instance. Explicit
    major.minor, current, and dev requests work without a Blender connection.

    Parameters:
    - version: auto, current, dev, or major.minor[.patch]
    - language: Blender Manual language code, for example en or zh-hans
    - sources: manual, python_api, and/or release_notes
    - user_prompt: Original user prompt for telemetry
    """
    try:
        result = _resolve_blender_documentation_context(
            version=version,
            language=language,
            sources=sources,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except BlenderDocumentationContextError as e:
        logger.error(f"Invalid Blender documentation context request: {str(e)}")
        return f"Error resolving Blender documentation context: {str(e)}"
    except Exception as e:
        logger.error(f"Error resolving Blender documentation context: {str(e)}")
        return f"Error resolving Blender documentation context: {str(e)}"


def _resolve_blender_documentation_context(
    *,
    version: str,
    language: str,
    sources: List[str],
) -> Dict[str, Any]:
    """Resolve source context, consulting Blender only for version=auto."""
    detected = None
    if version_requires_blender(version):
        blender = get_blender_connection()
        detected = blender.send_command("get_blender_version_context")
    return resolve_documentation_context(
        version=version,
        language=language,
        sources=sources,
        detected_blender=detected,
    )


@mcp.tool()
@telemetry_tool("search_blender_docs")
def search_blender_docs(
    ctx: Context,
    query: str,
    version: str = "auto",
    sources: List[str] = None,
    language: str = "en",
    limit: int = 8,
    snippet_mode: str = "top",
    user_prompt: str = "",
) -> str:
    """Search version-correct official Blender documentation.

    Search is bounded to official Blender Manual, Python API, and Release Notes
    indexes. Results include source/version/fallback metadata and canonical URLs.

    Parameters:
    - query: Search text, up to 200 characters
    - version: auto, current, dev, or major.minor[.patch]
    - sources: manual, python_api, and/or release_notes; defaults to manual
    - language: Blender Manual language code, for example en or zh-hans
    - limit: Maximum results from 1 to 20
    - snippet_mode: none, top (default, first three), or all
    - user_prompt: Original user prompt for telemetry
    """
    try:
        context = _resolve_blender_documentation_context(
            version=version,
            language=language,
            sources=sources or ["manual"],
        )
        result = BlenderDocumentationClient().search(
            context,
            query=query,
            limit=limit,
            snippet_mode=snippet_mode,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (BlenderDocumentationContextError, BlenderDocumentationRetrievalError) as e:
        logger.error(f"Invalid Blender documentation search: {str(e)}")
        return f"Error searching Blender documentation: {str(e)}"
    except Exception as e:
        logger.error(f"Error searching Blender documentation: {str(e)}")
        return f"Error searching Blender documentation: {str(e)}"


@mcp.tool()
@telemetry_tool("get_blender_doc_page")
def get_blender_doc_page(
    ctx: Context,
    page: str,
    version: str = "auto",
    source: str = "manual",
    language: str = "en",
    heading: str = "",
    max_chars: int = 12000,
    user_prompt: str = "",
) -> str:
    """Fetch one bounded section from official Blender documentation.

    The page parameter is a source-relative identifier, never an arbitrary URL.
    Scripts, styles, navigation, and other page chrome are removed.

    Parameters:
    - page: Relative Manual/API/Release Notes page identifier
    - version: auto, current, dev, or major.minor[.patch]
    - source: manual, python_api, or release_notes
    - language: Blender Manual language code, for example en or zh-hans
    - heading: Optional exact heading whose section should be returned
    - max_chars: Output bound from 100 to 50000 characters
    - user_prompt: Original user prompt for telemetry
    """
    try:
        context = _resolve_blender_documentation_context(
            version=version,
            language=language,
            sources=[source],
        )
        canonical_source = context["sources"][0]["source"]
        result = BlenderDocumentationClient().get_page(
            context,
            page=page,
            source=canonical_source,
            heading=heading or None,
            max_chars=max_chars,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (BlenderDocumentationContextError, BlenderDocumentationRetrievalError) as e:
        logger.error(f"Invalid Blender documentation page request: {str(e)}")
        return f"Error getting Blender documentation page: {str(e)}"
    except Exception as e:
        logger.error(f"Error getting Blender documentation page: {str(e)}")
        return f"Error getting Blender documentation page: {str(e)}"


@mcp.tool()
@telemetry_tool("list_node_trees")
def list_node_trees(
    ctx: Context,
    tree_types: List[str] = None,
    owner_kinds: List[str] = None,
    user_prompt: str = "",
) -> str:
    """List owner-addressed Geometry, Shader, and Compositor node trees.

    Embedded Shader and Compositor trees are identified by owner rather than by
    their non-unique display names. Results disclose read/edit capabilities,
    revisions, graph size, libraries, and direct users.

    Parameters:
    - tree_types: Optional GeometryNodeTree/ShaderNodeTree/CompositorNodeTree filter
    - owner_kinds: Optional MATERIAL/WORLD/LIGHT/SCENE/NODE_GROUP filter
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "list_node_trees",
            {
                "tree_types": tree_types or [],
                "owner_kinds": owner_kinds or [],
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error listing node trees: {str(e)}")
        return f"Error listing node trees: {str(e)}"


@mcp.tool()
@telemetry_tool("ensure_scene_compositor_tree")
def ensure_scene_compositor_tree(
    ctx: Context,
    scene_name: str,
    create_if_missing: bool = False,
    user_prompt: str = "",
) -> str:
    """Inspect or explicitly create a local Scene compositor tree.

    The default is read-only and reports `missing` when the Scene has no active
    compositor tree. Set create_if_missing=true to opt into a transactional,
    version-aware initialization. Blender 5.1+ receives a standalone
    CompositorNodeTree with its required Image output interface; failures restore
    the Scene pointer and remove the created tree.

    Parameters:
    - scene_name: Exact local Blender Scene name
    - create_if_missing: Explicitly allow creation when no tree exists
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "ensure_scene_compositor_tree",
            {
                "scene_name": scene_name,
                "create_if_missing": create_if_missing,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error ensuring Scene compositor tree: {str(e)}")
        return f"Error ensuring Scene compositor tree: {str(e)}"


@mcp.tool()
@telemetry_tool("export_node_tree")
def export_node_tree(
    ctx: Context,
    tree_ref: Dict[str, Any],
    view: str = "auto",
    node_names: List[str] = None,
    neighbor_depth: int = 0,
    output_path: str = "",
    user_prompt: str = "",
) -> str:
    """Export an owner-addressed node tree as deterministic flat JSON.

    Use tree_ref from list_node_trees. With no output_path, JSON is returned;
    otherwise it is atomically written below BLENDER_MCP_WORKSPACE for compact,
    incremental editing with the client's normal file tools.

    Parameters:
    - tree_ref: Object containing tree_type and owner {kind, name}
    - view: auto (operations for full graphs, semantic for targeted), semantic, operations, layout, or all
    - node_names: Optional stable node names for a targeted subgraph
    - neighbor_depth: Include connected nodes up to 0-5 hops
    - output_path: Optional workspace-constrained .json output path
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        params = {
            "tree_ref": tree_ref,
            "view": view,
            "node_names": node_names or [],
            "neighbor_depth": neighbor_depth,
        }
        if output_path:
            params["allow_large_response"] = True
        result = blender.send_command("export_node_tree", params)
        if not output_path:
            return json.dumps(result, ensure_ascii=False, indent=2)
        destination = write_node_tree_snapshot_json(result, output_path)
        return json.dumps(
            {
                "status": "written",
                "path": str(destination),
                "tree_ref": result["tree_ref"],
                "view": result["view"],
                "revision": result["revision"],
                "stats": result["stats"],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error exporting node tree: {str(e)}")
        return f"Error exporting node tree: {str(e)}"


@mcp.tool()
@telemetry_tool("get_node_tree_index")
def get_node_tree_index(
    ctx: Context,
    tree_ref: Dict[str, Any],
    query: str = "",
    offset: int = 0,
    limit: int = 100,
    user_prompt: str = "",
) -> str:
    """Search and page a compact node index before targeted graph export.

    Parameters:
    - tree_ref: Object returned by list_node_trees
    - query: Optional text across node name, label, id, and type label
    - offset: Zero-based result offset
    - limit: Page size from 1 to 500
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "get_node_tree_index",
            {
                "tree_ref": tree_ref,
                "query": query,
                "offset": offset,
                "limit": limit,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error indexing node tree: {str(e)}")
        return f"Error indexing node tree: {str(e)}"


@mcp.tool()
@telemetry_tool("query_node_graph")
def query_node_graph(
    ctx: Context,
    tree_ref: Dict[str, Any],
    query_type: str,
    node_names: List[str] = None,
    from_node: str = "",
    to_node: str = "",
    attribute_name: str = "",
    socket_id: str = "",
    direction: str = "downstream",
    fields: List[str] = None,
    limit: int = 200,
    user_prompt: str = "",
) -> str:
    """Query fields, socket links, Named Attributes, paths, or bounded graph slices."""
    try:
        result = get_blender_connection().send_command(
            "query_node_graph",
            {
                "tree_ref": tree_ref,
                "query_type": query_type,
                "node_names": node_names or [],
                "from_node": from_node,
                "to_node": to_node,
                "attribute_name": attribute_name,
                "socket_id": socket_id,
                "direction": direction,
                "fields": fields or [],
                "limit": limit,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error querying node graph: {e}"


@mcp.tool()
@telemetry_tool("get_node_type_schema")
def get_node_type_schema(
    ctx: Context,
    tree_type: str,
    node_type: str,
    owner_kind: str = "NODE_GROUP",
    detail: str = "compact",
    user_prompt: str = "",
) -> str:
    """Inspect a live node type in an exact tree and owner context.

    Owner context matters for Shader output nodes, Render Layers, and versioned
    compositor contracts. Probing is disposable and leaves no datablocks.

    Parameters:
    - tree_type: GeometryNodeTree, ShaderNodeTree, or CompositorNodeTree
    - node_type: Blender node bl_idname
    - owner_kind: MATERIAL, WORLD, LIGHT, SCENE, or NODE_GROUP as valid for tree_type
    - detail: compact (default) or full inherited RNA detail
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "get_node_type_schema",
            {
                "tree_type": tree_type,
                "node_type": node_type,
                "owner_kind": owner_kind,
                "detail": detail,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error inspecting node type: {str(e)}")
        return f"Error inspecting node type: {str(e)}"


@mcp.tool()
@telemetry_tool("validate_node_tree_patch")
def validate_node_tree_patch(
    ctx: Context,
    patch: Dict[str, Any] = None,
    patch_path: str = "",
    user_prompt: str = "",
) -> str:
    """Dry-run an owner-addressed node-tree patch without changing live data.

    Provide exactly one inline patch or workspace-relative patch_path. Structural
    validation runs in the MCP process, then Blender repeats semantic validation
    on an owner-aware disposable copy. Script and File Output mutations fail
    closed, and this endpoint never commits a change.

    Parameters:
    - patch: Inline blender-node-tree-patch/1 object
    - patch_path: JSON file below BLENDER_MCP_WORKSPACE
    - user_prompt: Original user prompt for telemetry
    """
    try:
        has_inline_patch = patch is not None
        has_patch_path = bool(patch_path)
        if has_inline_patch == has_patch_path:
            return json.dumps(
                {
                    "schema": NODE_PATCH_VALIDATION_SCHEMA,
                    "valid": False,
                    "stage": "structure",
                    "will_mutate": False,
                    "diagnostics": [{
                        "severity": "error",
                        "code": "patch_source_count",
                        "path": "",
                        "message": "Provide exactly one of patch or patch_path",
                    }],
                    "plan": [],
                    "semantic_diff": {},
                },
                ensure_ascii=False,
                indent=2,
            )
        patch_document = read_node_patch_json(patch_path) if patch_path else patch
        diagnostics = validate_node_patch_structure(patch_document)
        if diagnostics:
            return json.dumps(
                {
                    "schema": NODE_PATCH_VALIDATION_SCHEMA,
                    "valid": False,
                    "stage": "structure",
                    "will_mutate": False,
                    "diagnostics": diagnostics,
                    "plan": [],
                    "semantic_diff": {},
                },
                ensure_ascii=False,
                indent=2,
            )
        patch_document = assert_valid_node_patch(patch_document)
        blender = get_blender_connection()
        result = blender.send_command(
            "validate_node_tree_patch", {"patch": patch_document}
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (NodeTreePatchError, NodeTreeSchemaError) as e:
        diagnostics = getattr(e, "diagnostics", None) or [{
            "severity": "error",
            "code": "patch_file_error",
            "path": "/patch_path",
            "message": str(e),
        }]
        return json.dumps(
            {
                "schema": NODE_PATCH_VALIDATION_SCHEMA,
                "valid": False,
                "stage": "structure",
                "will_mutate": False,
                "diagnostics": diagnostics,
                "plan": [],
                "semantic_diff": {},
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error validating node-tree patch: {str(e)}")
        return json.dumps(
            {
                "schema": NODE_PATCH_VALIDATION_SCHEMA,
                "valid": False,
                "stage": "transport",
                "will_mutate": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "validation_transport_error",
                    "path": "",
                    "message": str(e),
                }],
                "plan": [],
                "semantic_diff": {},
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
@telemetry_tool("apply_node_tree_patch")
def apply_node_tree_patch(
    ctx: Context,
    patch: Dict[str, Any] = None,
    patch_path: str = "",
    keep_backup: bool = True,
    user_prompt: str = "",
) -> str:
    """Apply an owner-addressed patch through a verified transaction.

    Supports local Material, World, Light, Scene, Shader node-group, and
    Compositor node-group owners. Validation is repeated immediately before the
    version-aware owner copy/remap or selected-Scene tree swap. On failure,
    owner users, names, fake-user state, and graph identity are restored.

    Parameters:
    - patch: Inline blender-node-tree-patch/1 object
    - patch_path: JSON file below BLENDER_MCP_WORKSPACE
    - keep_backup: Preserve the pre-commit owner/tree as a fake-user backup
    - user_prompt: Original user prompt for telemetry
    """
    try:
        has_inline_patch = patch is not None
        has_patch_path = bool(patch_path)
        if has_inline_patch == has_patch_path:
            return json.dumps(
                {
                    "schema": NODE_PATCH_APPLICATION_SCHEMA,
                    "status": "rejected",
                    "applied": False,
                    "mutated": False,
                    "diagnostics": [{
                        "severity": "error",
                        "code": "patch_source_count",
                        "path": "",
                        "message": "Provide exactly one of patch or patch_path",
                    }],
                    "plan": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        patch_document = read_node_patch_json(patch_path) if patch_path else patch
        diagnostics = validate_node_patch_structure(patch_document)
        if diagnostics:
            return json.dumps(
                {
                    "schema": NODE_PATCH_APPLICATION_SCHEMA,
                    "status": "rejected",
                    "applied": False,
                    "mutated": False,
                    "diagnostics": diagnostics,
                    "plan": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        patch_document = assert_valid_node_patch(patch_document)
        blender = get_blender_connection()
        result = blender.send_command(
            "apply_node_tree_patch",
            {"patch": patch_document, "keep_backup": keep_backup},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (NodeTreePatchError, NodeTreeSchemaError) as e:
        diagnostics = getattr(e, "diagnostics", None) or [{
            "severity": "error",
            "code": "patch_file_error",
            "path": "/patch_path",
            "message": str(e),
        }]
        return json.dumps(
            {
                "schema": NODE_PATCH_APPLICATION_SCHEMA,
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "diagnostics": diagnostics,
                "plan": [],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error applying node-tree patch: {str(e)}")
        return json.dumps(
            {
                "schema": NODE_PATCH_APPLICATION_SCHEMA,
                "status": "failed",
                "applied": False,
                "mutated": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "application_transport_error",
                    "path": "",
                    "message": str(e),
                }],
                "plan": [],
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
@telemetry_tool("list_geometry_node_trees")
def list_geometry_node_trees(ctx: Context, user_prompt: str = "") -> str:
    """List Geometry Node trees available in the current Blender file.

    Returns each tree's revision, editability, graph size, and modifier/group users.

    Parameters:
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_geometry_node_trees")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error listing Geometry Node trees: {str(e)}")
        return f"Error listing Geometry Node trees: {str(e)}"


@mcp.tool()
@telemetry_tool("export_geometry_node_tree")
def export_geometry_node_tree(
    ctx: Context,
    tree_name: str,
    view: str = "auto",
    node_names: List[str] = None,
    neighbor_depth: int = 0,
    output_path: str = "",
    user_prompt: str = "",
) -> str:
    """Export one Geometry Node tree as normalized graph JSON.

    With no output_path, the JSON is returned directly for inspection or use with
    the client's file-edit tool. When output_path is provided, the MCP server
    atomically writes below BLENDER_MCP_WORKSPACE (or its current working
    directory) and returns a compact summary. Blender never reads that path.

    Parameters:
    - tree_name: Exact Geometry Node group name
    - view: semantic, compact operations, layout, or all
    - node_names: Optional node names for a targeted subgraph export
    - neighbor_depth: Include connected nodes up to 0-5 hops from node_names
    - output_path: Optional .json path constrained to the MCP workspace root
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        params = {
            "tree_name": tree_name,
            "view": view,
            "node_names": node_names or [],
            "neighbor_depth": neighbor_depth,
        }
        if output_path:
            params["allow_large_response"] = True
        result = blender.send_command("export_geometry_node_tree", params)
        if not output_path:
            return json.dumps(result, ensure_ascii=False, indent=2)

        destination = write_snapshot_json(result, output_path)
        return json.dumps(
            {
                "status": "written",
                "path": str(destination),
                "tree_name": result["tree"]["name"],
                "view": result["view"],
                "revision": result["revision"],
                "stats": result["stats"],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error exporting Geometry Node tree: {str(e)}")
        return f"Error exporting Geometry Node tree: {str(e)}"


@mcp.tool()
@telemetry_tool("get_geometry_node_type_schema")
def get_geometry_node_type_schema(
    ctx: Context,
    node_type: str,
    detail: str = "compact",
    user_prompt: str = "",
) -> str:
    """Inspect sockets and RNA properties for a node type in running Blender.

    Parameters:
    - node_type: Blender node bl_idname, for example GeometryNodeJoinGeometry
    - detail: compact (default) or full inherited RNA detail
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "get_geometry_node_type_schema",
            {"node_type": node_type, "detail": detail},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error inspecting Geometry Node type: {str(e)}")
        return f"Error inspecting Geometry Node type: {str(e)}"


@mcp.tool()
@telemetry_tool("search_geometry_node_types")
def search_geometry_node_types(
    ctx: Context,
    query: str = "",
    offset: int = 0,
    limit: int = 100,
    user_prompt: str = "",
) -> str:
    """Search node types constructible in Geometry Nodes in running Blender.

    Results are version/build-specific and include Geometry, Function, Shader
    utility, layout, and group node types accepted by a GeometryNodeTree.

    Parameters:
    - query: Optional case-insensitive text across id, label, description, category
    - offset: Zero-based result offset
    - limit: Page size from 1 to 500
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "search_geometry_node_types",
            {"query": query, "offset": offset, "limit": limit},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error searching Geometry Node types: {str(e)}")
        return f"Error searching Geometry Node types: {str(e)}"


@mcp.tool()
@telemetry_tool("search_blender_node_assets")
def search_blender_node_assets(
    ctx: Context,
    query: str = "",
    library: str = "",
    tree_type: str = "",
    detail: str = "summary",
    scope: str = "ESSENTIALS",
    offset: int = 0,
    limit: int = 20,
    user_prompt: str = "",
) -> str:
    """Search bundled Essentials and configured user node assets.

    Blender loads assets only into a disposable inspection scope and removes all
    appended datablocks before returning. Summary is compact; full includes the
    complete interface for up to 20 assets. User paths come only from Blender's
    configured asset libraries and scans are bounded.

    Parameters:
    - query: Text across asset name, description, author, tags, and library
    - library: Optional library filename substring
    - tree_type: Optional exact GeometryNodeTree/ShaderNodeTree/CompositorNodeTree
    - detail: summary or full
    - scope: ESSENTIALS (default), USER, or ALL
    - offset: Zero-based result offset
    - limit: 1-100 for summary, 1-20 for full
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "search_blender_node_assets",
            {
                "query": query,
                "library": library,
                "tree_type": tree_type,
                "detail": detail,
                "scope": scope,
                "offset": offset,
                "limit": limit,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error searching Blender node assets: {str(e)}")
        return f"Error searching Blender node assets: {str(e)}"


@mcp.tool()
@telemetry_tool("export_blender_node_asset")
def export_blender_node_asset(
    ctx: Context,
    source_path: str,
    asset_name: str,
    tree_type: str = "",
    scope: str = "USER",
    library: str = "",
    view: str = "auto",
    node_names: List[str] = None,
    neighbor_depth: int = 0,
    user_prompt: str = "",
) -> str:
    """Inspect one exact searched node asset without importing it into the file."""
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "export_blender_node_asset",
            {
                "source_path": source_path,
                "asset_name": asset_name,
                "tree_type": tree_type,
                "scope": scope,
                "library": library,
                "view": view,
                "node_names": node_names or [],
                "neighbor_depth": neighbor_depth,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error exporting Blender node asset: {str(e)}")
        return f"Error exporting Blender node asset: {str(e)}"


@mcp.tool()
@telemetry_tool("import_blender_node_asset")
def import_blender_node_asset(
    ctx: Context,
    source_path: str,
    asset_name: str,
    tree_type: str = "",
    scope: str = "USER",
    library: str = "",
    conflict_policy: str = "REJECT",
    user_prompt: str = "",
) -> str:
    """Append one exact node asset returned by search into the current file.

    Source identity is revalidated against bundled Essentials or Blender's
    configured user libraries; arbitrary .blend paths are rejected. The import
    is a local append, never a link. On failure every newly appended datablock is
    removed. Existing names are rejected by default; use RENAME for a distinct
    Blender-suffixed copy.

    Parameters:
    - source_path: Exact source_path returned by search_blender_node_assets
    - asset_name: Exact asset name returned by search
    - tree_type: Optional exact node-tree type returned by search
    - scope: ESSENTIALS, USER (default), or ALL, matching the search
    - library: Optional configured/bundled library identity used in the search
    - conflict_policy: REJECT (default) or RENAME
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "import_blender_node_asset",
            {
                "source_path": source_path,
                "asset_name": asset_name,
                "tree_type": tree_type,
                "scope": scope,
                "library": library,
                "conflict_policy": conflict_policy,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error importing Blender node asset: {str(e)}")
        return f"Error importing Blender node asset: {str(e)}"


@mcp.tool()
@telemetry_tool("get_geometry_node_tree_index")
def get_geometry_node_tree_index(
    ctx: Context,
    tree_name: str,
    query: str = "",
    offset: int = 0,
    limit: int = 100,
    user_prompt: str = "",
) -> str:
    """Search and page a compact node-name/type index before subgraph export.

    Use this tool to discover stable node names without loading a full graph.
    Then pass selected names to export_geometry_node_tree(node_names=[...]).

    Parameters:
    - tree_name: Exact Geometry Node group name
    - query: Optional case-insensitive substring across name, label, and type
    - offset: Zero-based result offset
    - limit: Page size from 1 to 500
    - user_prompt: Original user prompt for telemetry
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command(
            "get_geometry_node_tree_index",
            {
                "tree_name": tree_name,
                "query": query,
                "offset": offset,
                "limit": limit,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error indexing Geometry Node tree: {str(e)}")
        return f"Error indexing Geometry Node tree: {str(e)}"


@mcp.tool()
@telemetry_tool("validate_geometry_node_patch")
def validate_geometry_node_patch(
    ctx: Context,
    patch: Dict[str, Any] = None,
    patch_path: str = "",
    user_prompt: str = "",
) -> str:
    """Dry-run a Geometry Nodes semantic patch without changing Blender data.

    Provide exactly one of patch or patch_path. patch_path must point to a JSON
    file below BLENDER_MCP_WORKSPACE (or the MCP server working directory), so a
    client can create and incrementally edit it using its existing file tools.
    Structural errors are returned before Blender runtime validation. A valid
    result is an executable plan only; this tool never applies the patch.

    Parameters:
    - patch: Inline blender-geometry-nodes-patch/1 object
    - patch_path: Workspace-relative path to a patch JSON file
    - user_prompt: Original user prompt for telemetry
    """
    try:
        has_inline_patch = patch is not None
        has_patch_path = bool(patch_path)
        if has_inline_patch == has_patch_path:
            return json.dumps(
                {
                    "schema": PATCH_VALIDATION_SCHEMA,
                    "valid": False,
                    "stage": "structure",
                    "will_mutate": False,
                    "diagnostics": [{
                        "severity": "error",
                        "code": "patch_source_count",
                        "path": "",
                        "message": "Provide exactly one of patch or patch_path",
                    }],
                },
                ensure_ascii=False,
                indent=2,
            )

        patch_document = read_patch_json(patch_path) if patch_path else patch
        diagnostics = validate_patch_structure(patch_document)
        if diagnostics:
            return json.dumps(
                {
                    "schema": PATCH_VALIDATION_SCHEMA,
                    "valid": False,
                    "stage": "structure",
                    "will_mutate": False,
                    "diagnostics": diagnostics,
                },
                ensure_ascii=False,
                indent=2,
            )
        patch_document = assert_valid_patch(patch_document)

        blender = get_blender_connection()
        result = blender.send_command(
            "validate_geometry_node_patch",
            {"patch": patch_document},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except GeometryNodesSchemaError as e:
        logger.error(f"Invalid Geometry Nodes patch file: {str(e)}")
        return json.dumps(
            {
                "schema": PATCH_VALIDATION_SCHEMA,
                "valid": False,
                "stage": "structure",
                "will_mutate": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "patch_file_error",
                    "path": "/patch_path",
                    "message": str(e),
                }],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error validating Geometry Nodes patch: {str(e)}")
        return json.dumps(
            {
                "schema": PATCH_VALIDATION_SCHEMA,
                "valid": False,
                "stage": "transport",
                "will_mutate": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "validation_transport_error",
                    "path": "",
                    "message": str(e),
                }],
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
@telemetry_tool("apply_geometry_node_patch")
def apply_geometry_node_patch(
    ctx: Context,
    patch: Dict[str, Any] = None,
    patch_path: str = "",
    keep_backup: bool = True,
    user_prompt: str = "",
) -> str:
    """Apply a Geometry Nodes patch through a copy-on-write transaction.

    Provide exactly one of patch or patch_path. The server validates structure,
    Blender repeats runtime dry-run validation, then operations are applied to a
    copied NodeTree. Live users are switched only after the copy re-exports
    successfully. On commit failure, users, modifier inputs, and names are
    restored. keep_backup preserves the original tree with a revisioned backup
    name for manual rollback (recommended and enabled by default).

    Parameters:
    - patch: Inline blender-geometry-nodes-patch/1 object
    - patch_path: Workspace-relative path to a patch JSON file
    - keep_backup: Keep the pre-commit NodeTree as a fake-user backup
    - user_prompt: Original user prompt for telemetry
    """
    try:
        has_inline_patch = patch is not None
        has_patch_path = bool(patch_path)
        if has_inline_patch == has_patch_path:
            return json.dumps(
                {
                    "schema": PATCH_APPLICATION_SCHEMA,
                    "status": "rejected",
                    "applied": False,
                    "mutated": False,
                    "diagnostics": [{
                        "severity": "error",
                        "code": "patch_source_count",
                        "path": "",
                        "message": "Provide exactly one of patch or patch_path",
                    }],
                    "plan": [],
                },
                ensure_ascii=False,
                indent=2,
            )

        patch_document = read_patch_json(patch_path) if patch_path else patch
        diagnostics = validate_patch_structure(patch_document)
        if diagnostics:
            return json.dumps(
                {
                    "schema": PATCH_APPLICATION_SCHEMA,
                    "status": "rejected",
                    "applied": False,
                    "mutated": False,
                    "diagnostics": diagnostics,
                    "plan": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        patch_document = assert_valid_patch(patch_document)

        blender = get_blender_connection()
        result = blender.send_command(
            "apply_geometry_node_patch",
            {"patch": patch_document, "keep_backup": keep_backup},
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except GeometryNodesSchemaError as e:
        logger.error(f"Invalid Geometry Nodes patch file: {str(e)}")
        return json.dumps(
            {
                "schema": PATCH_APPLICATION_SCHEMA,
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "patch_file_error",
                    "path": "/patch_path",
                    "message": str(e),
                }],
                "plan": [],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error applying Geometry Nodes patch: {str(e)}")
        return json.dumps(
            {
                "schema": PATCH_APPLICATION_SCHEMA,
                "status": "failed",
                "applied": False,
                "mutated": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "application_transport_error",
                    "path": "",
                    "message": str(e),
                }],
                "plan": [],
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
@telemetry_tool("modify_verify_save")
def modify_verify_save(
    ctx: Context,
    patch_kind: str,
    patch: Dict[str, Any],
    assertions: List[Dict[str, Any]] = None,
    keep_backup: bool = True,
    save_policy: str = "never",
    user_prompt: str = "",
) -> str:
    """Validate a Patch, check candidate assertions, commit, read back, and optionally save.

    This high-level workflow accepts only the reviewed node-tree Patch protocols.
    Assertions run against the disposable dry-run candidate before mutation.
    Blender's Patch transaction then verifies the committed revision. Saving is
    never implicit: use save_policy=on_success or required explicitly.
    """
    try:
        if patch_kind == "node_tree":
            patch_document = assert_valid_node_patch(patch)
        elif patch_kind == "geometry_nodes":
            patch_document = assert_valid_patch(patch)
        else:
            raise BlenderMCPError(
                "invalid_request", "patch_kind must be node_tree or geometry_nodes"
            )
        result = get_blender_connection().send_command(
            "modify_verify_save",
            {
                "patch_kind": patch_kind,
                "patch": patch_document,
                "assertions": assertions or [],
                "keep_backup": keep_backup,
                "save_policy": save_policy,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (NodeTreePatchError, NodeTreeSchemaError, GeometryNodesSchemaError) as error:
        raise BlenderMCPError(
            "node_validation_error",
            str(error),
            details={"diagnostics": getattr(error, "diagnostics", [])},
        ) from error

@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 1000, user_prompt: str = "") -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.

    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 800)
    - user_prompt: The original user prompt that led to this tool call (for telemetry)

    Returns the screenshot as an Image.
    """
    start_time = __import__('time').time()
    screenshot_url = None
    success = False
    error_msg = None
    
    try:
        blender = get_blender_connection()
        
        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")
        
        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })
        
        if "error" in result:
            raise Exception(result["error"])
        
        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")
        
        # Read the file
        with open(temp_path, 'rb') as f:
            image_bytes = f.read()
        
        # Delete the temp file
        os.remove(temp_path)
        
        # Upload to storage for telemetry
        try:
            telemetry = get_telemetry()
            if telemetry._check_user_consent():
                screenshot_url = telemetry.upload_screenshot(image_bytes, "screenshot")
        except Exception:
            pass  # Silently fail - don't break screenshot for telemetry issues
        
        success = True
        return Image(data=image_bytes, format="png")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")
    finally:
        # Record telemetry with screenshot URL in metadata
        try:
            telemetry = get_telemetry()
            duration_ms = (__import__('time').time() - start_time) * 1000
            
            metadata = None
            if screenshot_url:
                metadata = {"screenshot_url": screenshot_url}
                
            telemetry.record_event(
                event_type=EventType.TOOL_EXECUTION,
                tool_name="get_viewport_screenshot",
                prompt_text=user_prompt,
                success=success,
                duration_ms=duration_ms,
                error_message=error_msg,
                metadata=metadata,
            )
        except Exception:
            pass


@mcp.tool()
@rich_telemetry_tool("execute_blender_code", capture_code=True)
def execute_blender_code(
    ctx: Context,
    code: str,
    transaction: bool = False,
    rollback_on_error: bool = True,
    user_prompt: str = "",
) -> str:
    """
    Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.

    Parameters:
    - code: The Python code to execute
    - user_prompt: The original user prompt that led to this tool call (for telemetry)
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command(
            "execute_code",
            {
                "code": code,
                "transaction": transaction,
                "rollback_on_error": rollback_on_error,
            },
        )
        if transaction:
            return json.dumps(result, ensure_ascii=False, indent=2)
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"

@mcp.tool()
@telemetry_tool("get_polyhaven_categories")
def get_polyhaven_categories(ctx: Context, asset_type: str = "hdris", user_prompt: str = "") -> str:
    """
    Get a list of categories for a specific asset type on Polyhaven.

    Parameters:
    - asset_type: The type of asset to get categories for (hdris, textures, models, all)
    - user_prompt: The original user prompt that led to this tool call (for telemetry)
    """
    try:
        blender = get_blender_connection()
        if not _polyhaven_enabled:
            return "PolyHaven integration is disabled. Select it in the sidebar in BlenderMCP, then run it again."
        result = blender.send_command("get_polyhaven_categories", {"asset_type": asset_type})
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        # Format the categories in a more readable way
        categories = result["categories"]
        formatted_output = f"Categories for {asset_type}:\n\n"
        
        # Sort categories by count (descending)
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        
        for category, count in sorted_categories:
            formatted_output += f"- {category}: {count} assets\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error getting Polyhaven categories: {str(e)}")
        return f"Error getting Polyhaven categories: {str(e)}"

@mcp.tool()
@telemetry_tool("search_polyhaven_assets")
def search_polyhaven_assets(
    ctx: Context,
    asset_type: str = "all",
    categories: str = None,
    user_prompt: str = ""
) -> str:
    """
    Search for assets on Polyhaven with optional filtering.

    Parameters:
    - asset_type: Type of assets to search for (hdris, textures, models, all)
    - categories: Optional comma-separated list of categories to filter by
    - user_prompt: The original user prompt that led to this tool call (for telemetry)

    Returns a list of matching assets with basic information.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("search_polyhaven_assets", {
            "asset_type": asset_type,
            "categories": categories
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        # Format the assets in a more readable way
        assets = result["assets"]
        total_count = result["total_count"]
        returned_count = result["returned_count"]
        
        formatted_output = f"Found {total_count} assets"
        if categories:
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"
        
        # Sort assets by download count (popularity)
        sorted_assets = sorted(assets.items(), key=lambda x: x[1].get("download_count", 0), reverse=True)
        
        for asset_id, asset_data in sorted_assets:
            formatted_output += f"- {asset_data.get('name', asset_id)} (ID: {asset_id})\n"
            formatted_output += f"  Type: {['HDRI', 'Texture', 'Model'][asset_data.get('type', 0)]}\n"
            formatted_output += f"  Categories: {', '.join(asset_data.get('categories', []))}\n"
            formatted_output += f"  Downloads: {asset_data.get('download_count', 'Unknown')}\n\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error searching Polyhaven assets: {str(e)}")
        return f"Error searching Polyhaven assets: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("download_polyhaven_asset")
def download_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str = None,
    user_prompt: str = ""
) -> str:
    """
    Download and import a Polyhaven asset into Blender.

    Parameters:
    - asset_id: The ID of the asset to download
    - asset_type: The type of asset (hdris, textures, models)
    - resolution: The resolution to download (e.g., 1k, 2k, 4k)
    - file_format: Optional file format (e.g., hdr, exr for HDRIs; jpg, png for textures; gltf, fbx for models)
    - user_prompt: The original user prompt that led to this tool call (for telemetry)

    Returns a message indicating success or failure.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("download_polyhaven_asset", {
            "asset_id": asset_id,
            "asset_type": asset_type,
            "resolution": resolution,
            "file_format": file_format
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        if result.get("success"):
            message = result.get("message", "Asset downloaded and imported successfully")
            
            # Add additional information based on asset type
            if asset_type == "hdris":
                return f"{message}. The HDRI has been set as the world environment."
            elif asset_type == "textures":
                material_name = result.get("material", "")
                maps = ", ".join(result.get("maps", []))
                return f"{message}. Created material '{material_name}' with maps: {maps}."
            elif asset_type == "models":
                return f"{message}. The model has been imported into the current scene."
            else:
                return message
        else:
            return f"Failed to download asset: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error downloading Polyhaven asset: {str(e)}")
        return f"Error downloading Polyhaven asset: {str(e)}"

@mcp.tool()
@telemetry_tool("set_texture")
def set_texture(
    ctx: Context,
    object_name: str,
    texture_id: str, user_prompt: str = "") -> str:
    """
    Apply a previously downloaded Polyhaven texture to an object.
    
    Parameters:
    - object_name: Name of the object to apply the texture to
    - texture_id: ID of the Polyhaven texture to apply (must be downloaded first)
    
    Returns a message indicating success or failure.
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("set_texture", {
            "object_name": object_name,
            "texture_id": texture_id
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        if result.get("success"):
            material_name = result.get("material", "")
            maps = ", ".join(result.get("maps", []))
            
            # Add detailed material info
            material_info = result.get("material_info", {})
            node_count = material_info.get("node_count", 0)
            has_nodes = material_info.get("has_nodes", False)
            texture_nodes = material_info.get("texture_nodes", [])
            
            output = f"Successfully applied texture '{texture_id}' to {object_name}.\n"
            output += f"Using material '{material_name}' with maps: {maps}.\n\n"
            output += f"Material has nodes: {has_nodes}\n"
            output += f"Total node count: {node_count}\n\n"
            
            if texture_nodes:
                output += "Texture nodes:\n"
                for node in texture_nodes:
                    output += f"- {node['name']} using image: {node['image']}\n"
                    if node['connections']:
                        output += "  Connections:\n"
                        for conn in node['connections']:
                            output += f"    {conn}\n"
            else:
                output += "No texture nodes found in the material.\n"
            
            return output
        else:
            return f"Failed to apply texture: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error applying texture: {str(e)}")
        return f"Error applying texture: {str(e)}"

@mcp.tool()
@telemetry_tool("get_polyhaven_status")
def get_polyhaven_status(ctx: Context, user_prompt: str = "") -> str:
    """
    Check if PolyHaven integration is enabled in Blender.
    Returns a message indicating whether PolyHaven features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_polyhaven_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += "PolyHaven is good at Textures, and has a wider variety of textures than Sketchfab."
        return message
    except Exception as e:
        logger.error(f"Error checking PolyHaven status: {str(e)}")
        return f"Error checking PolyHaven status: {str(e)}"

@mcp.tool()
@telemetry_tool("get_hyper3d_status")
def get_hyper3d_status(ctx: Context, user_prompt: str = "") -> str:
    """
    Check if Hyper3D Rodin integration is enabled in Blender.
    Returns a message indicating whether Hyper3D Rodin features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_hyper3d_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += ""
        return message
    except Exception as e:
        logger.error(f"Error checking Hyper3D status: {str(e)}")
        return f"Error checking Hyper3D status: {str(e)}"

@mcp.tool()
@telemetry_tool("get_sketchfab_status")
def get_sketchfab_status(ctx: Context, user_prompt: str = "") -> str:
    """
    Check if Sketchfab integration is enabled in Blender.
    Returns a message indicating whether Sketchfab features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_sketchfab_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += "Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven."        
        return message
    except Exception as e:
        logger.error(f"Error checking Sketchfab status: {str(e)}")
        return f"Error checking Sketchfab status: {str(e)}"

@mcp.tool()
@telemetry_tool("search_sketchfab_models")
def search_sketchfab_models(
    ctx: Context,
    query: str,
    categories: str = None,
    count: int = 20,
    downloadable: bool = True, user_prompt: str = "") -> str:
    """
    Search for models on Sketchfab with optional filtering.

    Parameters:
    - query: Text to search for
    - categories: Optional comma-separated list of categories
    - count: Maximum number of results to return (default 20)
    - downloadable: Whether to include only downloadable models (default True)

    Returns a formatted list of matching models.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Searching Sketchfab models with query: {query}, categories: {categories}, count: {count}, downloadable: {downloadable}")
        result = blender.send_command("search_sketchfab_models", {
            "query": query,
            "categories": categories,
            "count": count,
            "downloadable": downloadable
        })
        
        if "error" in result:
            logger.error(f"Error from Sketchfab search: {result['error']}")
            return f"Error: {result['error']}"
        
        # Safely get results with fallbacks for None
        if result is None:
            logger.error("Received None result from Sketchfab search")
            return "Error: Received no response from Sketchfab search"
            
        # Format the results
        models = result.get("results", []) or []
        if not models:
            return f"No models found matching '{query}'"
            
        formatted_output = f"Found {len(models)} models matching '{query}':\n\n"
        
        for model in models:
            if model is None:
                continue
                
            model_name = model.get("name", "Unnamed model")
            model_uid = model.get("uid", "Unknown ID")
            formatted_output += f"- {model_name} (UID: {model_uid})\n"
            
            # Get user info with safety checks
            user = model.get("user") or {}
            username = user.get("username", "Unknown author") if isinstance(user, dict) else "Unknown author"
            formatted_output += f"  Author: {username}\n"
            
            # Get license info with safety checks
            license_data = model.get("license") or {}
            license_label = license_data.get("label", "Unknown") if isinstance(license_data, dict) else "Unknown"
            formatted_output += f"  License: {license_label}\n"
            
            # Add face count and downloadable status
            face_count = model.get("faceCount", "Unknown")
            is_downloadable = "Yes" if model.get("isDownloadable") else "No"
            formatted_output += f"  Face count: {face_count}\n"
            formatted_output += f"  Downloadable: {is_downloadable}\n\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error searching Sketchfab models: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error searching Sketchfab models: {str(e)}"

@mcp.tool()
@telemetry_tool("download_sketchfab_model")
def get_sketchfab_model_preview(
    ctx: Context,
    uid: str, user_prompt: str = "") -> Image:
    """
    Get a preview thumbnail of a Sketchfab model by its UID.
    Use this to visually confirm a model before downloading.
    
    Parameters:
    - uid: The unique identifier of the Sketchfab model (obtained from search_sketchfab_models)
    
    Returns the model's thumbnail as an Image for visual confirmation.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Getting Sketchfab model preview for UID: {uid}")
        
        result = blender.send_command("get_sketchfab_model_preview", {"uid": uid})
        
        if result is None:
            raise Exception("Received no response from Blender")
        
        if "error" in result:
            raise Exception(result["error"])
        
        # Decode base64 image data
        image_data = base64.b64decode(result["image_data"])
        img_format = result.get("format", "jpeg")
        
        # Log model info
        model_name = result.get("model_name", "Unknown")
        author = result.get("author", "Unknown")
        logger.info(f"Preview retrieved for '{model_name}' by {author}")
        
        return Image(data=image_data, format=img_format)
        
    except Exception as e:
        logger.error(f"Error getting Sketchfab preview: {str(e)}")
        raise Exception(f"Failed to get preview: {str(e)}")


@mcp.tool()
@rich_telemetry_tool("download_sketchfab_model")
def download_sketchfab_model(
    ctx: Context,
    uid: str,
    target_size: float, user_prompt: str = "") -> str:
    """
    Download and import a Sketchfab model by its UID.
    The model will be scaled so its largest dimension equals target_size.
    
    Parameters:
    - uid: The unique identifier of the Sketchfab model
    - target_size: REQUIRED. The target size in Blender units/meters for the largest dimension.
                  You must specify the desired size for the model.
                  Examples:
                  - Chair: target_size=1.0 (1 meter tall)
                  - Table: target_size=0.75 (75cm tall)
                  - Car: target_size=4.5 (4.5 meters long)
                  - Person: target_size=1.7 (1.7 meters tall)
                  - Small object (cup, phone): target_size=0.1 to 0.3
    
    Returns a message with import details including object names, dimensions, and bounding box.
    The model must be downloadable and you must have proper access rights.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Downloading Sketchfab model: {uid}, target_size={target_size}")
        
        result = blender.send_command("download_sketchfab_model", {
            "uid": uid,
            "normalize_size": True,  # Always normalize
            "target_size": target_size
        })
        
        if result is None:
            logger.error("Received None result from Sketchfab download")
            return "Error: Received no response from Sketchfab download request"
            
        if "error" in result:
            logger.error(f"Error from Sketchfab download: {result['error']}")
            return f"Error: {result['error']}"
        
        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            object_names = ", ".join(imported_objects) if imported_objects else "none"
            
            output = f"Successfully imported model.\n"
            output += f"Created objects: {object_names}\n"
            
            # Add dimension info if available
            if result.get("dimensions"):
                dims = result["dimensions"]
                output += f"Dimensions (X, Y, Z): {dims[0]:.3f} x {dims[1]:.3f} x {dims[2]:.3f} meters\n"
            
            # Add bounding box info if available
            if result.get("world_bounding_box"):
                bbox = result["world_bounding_box"]
                output += f"Bounding box: min={bbox[0]}, max={bbox[1]}\n"
            
            # Add normalization info if applied
            if result.get("normalized"):
                scale = result.get("scale_applied", 1.0)
                output += f"Size normalized: scale factor {scale:.6f} applied (target size: {target_size}m)\n"
            
            return output
        else:
            return f"Failed to download model: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error downloading Sketchfab model: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error downloading Sketchfab model: {str(e)}"

def _process_bbox(original_bbox: list[float] | list[int] | None) -> list[int] | None:
    if original_bbox is None:
        return None
    if all(isinstance(i, int) for i in original_bbox):
        return original_bbox
    if any(i<=0 for i in original_bbox):
        raise ValueError("Incorrect number range: bbox must be bigger than zero!")
    return [int(float(i) / max(original_bbox) * 100) for i in original_bbox] if original_bbox else None

@mcp.tool()
@rich_telemetry_tool("generate_hyper3d_model_via_text")
def generate_hyper3d_model_via_text(
    ctx: Context,
    text_prompt: str,
    bbox_condition: list[float]=None, user_prompt: str = "") -> str:
    """
    Generate 3D asset using Hyper3D by giving description of the desired asset, and import the asset into Blender.
    The 3D asset has built-in materials.
    The generated model has a normalized size, so re-scaling after generation can be useful.

    Parameters:
    - text_prompt: A short description of the desired model in **English**.
    - bbox_condition: Optional. If given, it has to be a list of floats of length 3. Controls the ratio between [Length, Width, Height] of the model.

    Returns a message indicating success or failure.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_rodin_job", {
            "text_prompt": text_prompt,
            "images": None,
            "bbox_condition": _process_bbox(bbox_condition),
        })
        succeed = result.get("submit_time", False)
        if succeed:
            return json.dumps({
                "task_uuid": result["uuid"],
                "subscription_key": result["jobs"]["subscription_key"],
            })
        else:
            return json.dumps(result)
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("generate_hyper3d_model_via_images")
def generate_hyper3d_model_via_images(
    ctx: Context,
    input_image_paths: list[str]=None,
    input_image_urls: list[str]=None,
    bbox_condition: list[float]=None, user_prompt: str = "") -> str:
    """
    Generate 3D asset using Hyper3D by giving images of the wanted asset, and import the generated asset into Blender.
    The 3D asset has built-in materials.
    The generated model has a normalized size, so re-scaling after generation can be useful.
    
    Parameters:
    - input_image_paths: The **absolute** paths of input images. Even if only one image is provided, wrap it into a list. Required if Hyper3D Rodin in MAIN_SITE mode.
    - input_image_urls: The URLs of input images. Even if only one image is provided, wrap it into a list. Required if Hyper3D Rodin in FAL_AI mode.
    - bbox_condition: Optional. If given, it has to be a list of ints of length 3. Controls the ratio between [Length, Width, Height] of the model.

    Only one of {input_image_paths, input_image_urls} should be given at a time, depending on the Hyper3D Rodin's current mode.
    Returns a message indicating success or failure.
    """
    if input_image_paths is not None and input_image_urls is not None:
        return f"Error: Conflict parameters given!"
    if input_image_paths is None and input_image_urls is None:
        return f"Error: No image given!"
    if input_image_paths is not None:
        if not all(os.path.exists(i) for i in input_image_paths):
            return "Error: not all image paths are valid!"
        images = []
        for path in input_image_paths:
            with open(path, "rb") as f:
                images.append(
                    (Path(path).suffix, base64.b64encode(f.read()).decode("ascii"))
                )
    elif input_image_urls is not None:
        if not all(urlparse(i) for i in input_image_paths):
            return "Error: not all image URLs are valid!"
        images = input_image_urls.copy()
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_rodin_job", {
            "text_prompt": None,
            "images": images,
            "bbox_condition": _process_bbox(bbox_condition),
        })
        succeed = result.get("submit_time", False)
        if succeed:
            return json.dumps({
                "task_uuid": result["uuid"],
                "subscription_key": result["jobs"]["subscription_key"],
            })
        else:
            return json.dumps(result)
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
@telemetry_tool("poll_rodin_job_status")
def poll_rodin_job_status(
    ctx: Context,
    subscription_key: str=None,
    request_id: str=None,
):
    """
    Check if the Hyper3D Rodin generation task is completed.

    For Hyper3D Rodin mode MAIN_SITE:
        Parameters:
        - subscription_key: The subscription_key given in the generate model step.

        Returns a list of status. The task is done if all status are "Done".
        If "Failed" showed up, the generating process failed.
        This is a polling API, so only proceed if the status are finally determined ("Done" or "Canceled").

    For Hyper3D Rodin mode FAL_AI:
        Parameters:
        - request_id: The request_id given in the generate model step.

        Returns the generation task status. The task is done if status is "COMPLETED".
        The task is in progress if status is "IN_PROGRESS".
        If status other than "COMPLETED", "IN_PROGRESS", "IN_QUEUE" showed up, the generating process might be failed.
        This is a polling API, so only proceed if the status are finally determined ("COMPLETED" or some failed state).
    """
    try:
        blender = get_blender_connection()
        kwargs = {}
        if subscription_key:
            kwargs = {
                "subscription_key": subscription_key,
            }
        elif request_id:
            kwargs = {
                "request_id": request_id,
            }
        result = blender.send_command("poll_rodin_job_status", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("import_generated_asset")
def import_generated_asset(
    ctx: Context,
    name: str,
    task_uuid: str=None,
    request_id: str=None,
):
    """
    Import the asset generated by Hyper3D Rodin after the generation task is completed.

    Parameters:
    - name: The name of the object in scene
    - task_uuid: For Hyper3D Rodin mode MAIN_SITE: The task_uuid given in the generate model step.
    - request_id: For Hyper3D Rodin mode FAL_AI: The request_id given in the generate model step.

    Only give one of {task_uuid, request_id} based on the Hyper3D Rodin Mode!
    Return if the asset has been imported successfully.
    """
    try:
        blender = get_blender_connection()
        kwargs = {
            "name": name
        }
        if task_uuid:
            kwargs["task_uuid"] = task_uuid
        elif request_id:
            kwargs["request_id"] = request_id
        result = blender.send_command("import_generated_asset", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
def get_hunyuan3d_status(ctx: Context, user_prompt: str = "") -> str:
    """
    Check if Hunyuan3D integration is enabled in Blender.
    Returns a message indicating whether Hunyuan3D features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_hunyuan3d_status")
        message = result.get("message", "")
        return message
    except Exception as e:
        logger.error(f"Error checking Hunyuan3D status: {str(e)}")
        return f"Error checking Hunyuan3D status: {str(e)}"
    
@mcp.tool()
@rich_telemetry_tool("generate_hunyuan3d_model")
def generate_hunyuan3d_model(
    ctx: Context,
    text_prompt: str = None,
    input_image_url: str = None, user_prompt: str = "") -> str:
    """
    Generate 3D asset using Hunyuan3D by providing either text description, image reference, 
    or both for the desired asset, and import the asset into Blender.
    The 3D asset has built-in materials.
    
    Parameters:
    - text_prompt: (Optional) A short description of the desired model in English/Chinese.
    - input_image_url: (Optional) The local or remote url of the input image. Accepts None if only using text prompt.

    Returns: 
    - When successful, returns a JSON with job_id (format: "job_xxx") indicating the task is in progress
    - When the job completes, the status will change to "DONE" indicating the model has been imported
    - Returns error message if the operation fails
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_hunyuan_job", {
            "text_prompt": text_prompt,
            "image": input_image_url,
        })
        if "JobId" in result.get("Response", {}):
            job_id = result["Response"]["JobId"]
            formatted_job_id = f"job_{job_id}"
            return json.dumps({
                "job_id": formatted_job_id,
            })
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {str(e)}")
        return f"Error generating Hunyuan3D task: {str(e)}"
    
@mcp.tool()
def poll_hunyuan_job_status(
    ctx: Context,
    job_id: str=None,
):
    """
    Check if the Hunyuan3D generation task is completed.

    For Hunyuan3D:
        Parameters:
        - job_id: The job_id given in the generate model step.

        Returns the generation task status. The task is done if status is "DONE".
        The task is in progress if status is "RUN".
        If status is "DONE", returns ResultFile3Ds, which is the generated ZIP model path
        When the status is "DONE", the response includes a field named ResultFile3Ds that contains the generated ZIP file path of the 3D model in OBJ format.
        This is a polling API, so only proceed if the status are finally determined ("DONE" or some failed state).
    """
    try:
        blender = get_blender_connection()
        kwargs = {
            "job_id": job_id,
        }
        result = blender.send_command("poll_hunyuan_job_status", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {str(e)}")
        return f"Error generating Hunyuan3D task: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("import_generated_asset_hunyuan")
def import_generated_asset_hunyuan(
    ctx: Context,
    name: str,
    zip_file_url: str,
):
    """
    Import the asset generated by Hunyuan3D after the generation task is completed.

    Parameters:
    - name: The name of the object in scene
    - zip_file_url: The zip_file_url given in the generate model step.

    Return if the asset has been imported successfully.
    """
    try:
        blender = get_blender_connection()
        kwargs = {
            "name": name
        }
        if zip_file_url:
            kwargs["zip_file_url"] = zip_file_url
        result = blender.send_command("import_generated_asset_hunyuan", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {str(e)}")
        return f"Error generating Hunyuan3D task: {str(e)}"


@mcp.prompt()
def asset_creation_strategy() -> str:
    """Return optional expanded guidance for asset-oriented Blender work."""
    return """Use an asset source only when it fits the requested outcome.

1. Inspect only the scene or target objects needed to place and verify the asset.
2. Check the status of the one relevant provider; do not probe every integration.
3. Search and compare metadata before any download, import, or generation job.
4. Treat downloads, provider jobs, and imports as mutations. Proceed when the
   user requested that result; otherwise confirm the external action first.
5. Prefer Blender node assets for reusable node groups, PolyHaven for HDRIs,
   textures, and generic models, Sketchfab for specific downloadable models,
   and Hyper3D or Hunyuan3D for a custom single item.
6. For Sketchfab, review the license and preview when visual selection matters,
   then provide an explicit real-world target size.
7. For generated assets, create one job, poll that job to completion, import
   once, and never silently switch providers after quota or service failure.
8. After import, verify returned object names, dimensions, world bounding box,
   orientation, and placement. Use a viewport screenshot only when appearance
   or spatial composition is part of acceptance.
9. Prefer structured tools. Use small execute_blender_code calls only for an
   operation the structured surface cannot express.
10. Do not delete unrelated data or save the .blend file unless the user asks.

Report the selected source, external action taken, imported datablocks,
verification evidence, and whether the Blender file remains unsaved.
"""

# Main execution

def main():
    """Run the MCP server"""
    # When run by hand (stdin is a TTY) the server appears to "hang" while it
    # silently waits for an MCP client; log a hint so that state is obvious.
    # Launched by a client, stdin is a pipe so this is skipped, and logging goes
    # to stderr, never to the stdio protocol on stdout.
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if interactive:
        logger.info(
            "BlenderMCP is an MCP server and is meant to be launched by your MCP "
            "client (Claude Desktop, Cursor, VS Code, ...), not run by hand. "
            "It will now wait silently for a client on stdin -- that is normal, "
            "not a hang. Press Ctrl-C to exit. "
            "Setup guide: https://github.com/ahujasid/blender-mcp#installation"
        )
    mcp.run()

if __name__ == "__main__":
    main()
