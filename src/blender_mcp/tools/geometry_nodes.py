from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from mcp.server.fastmcp import Context

from ..host import get_blender_connection, mcp
from ..observability.decorators import telemetry_tool
from ..protocol.errors import BlenderMCPError
from ..protocol.geometry_nodes import (
    PATCH_APPLICATION_SCHEMA,
    PATCH_VALIDATION_SCHEMA,
    GeometryNodesSchemaError,
    assert_valid_patch,
    read_patch_json,
    validate_patch_structure,
    write_snapshot_json,
)
from ..protocol.node_patch import (
    NodeTreePatchError,
)
from ..protocol.node_patch import (
    assert_valid_patch as assert_valid_node_patch,
)
from ..protocol.node_tree import (
    NodeTreeSchemaError,
)

logger = logging.getLogger("BlenderMCPServer")

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
