from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from mcp.server.fastmcp import Context

from ..host import get_blender_connection, mcp
from ..observability.decorators import telemetry_tool
from ..protocol.errors import unresolved_application_result
from ..protocol.node_patch import (
    PATCH_APPLICATION_SCHEMA as NODE_PATCH_APPLICATION_SCHEMA,
)
from ..protocol.node_patch import (
    PATCH_VALIDATION_SCHEMA as NODE_PATCH_VALIDATION_SCHEMA,
)
from ..protocol.node_patch import (
    NodeTreePatchError,
    RESPONSE_DETAIL_LEVELS,
    shape_application_response,
)
from ..protocol.node_patch import (
    assert_valid_patch as assert_valid_node_patch,
)
from ..protocol.node_patch import (
    read_patch_json as read_node_patch_json,
)
from ..protocol.node_patch import (
    validate_patch_structure as validate_node_patch_structure,
)
from ..protocol.node_tree import (
    NodeTreeSchemaError,
)
from ..protocol.node_tree import (
    write_snapshot_json as write_node_tree_snapshot_json,
)

logger = logging.getLogger("BlenderMCPServer")

@mcp.tool()
@telemetry_tool("create_node_group")
def create_node_group(
    ctx: Context,
    name: str,
    tree_type: str,
    geometry_is_modifier: bool = False,
    description: str = "",
    reuse_existing: bool = False,
    user_prompt: str = "",
) -> str:
    """Create an empty local node group and return its initial revision.

    Use the returned tree_ref/revision with structured node patch tools. Exact
    name collisions are rejected unless reuse_existing=true; a compatible
    existing group is then returned without mutation after verifying type,
    editability, and Geometry Nodes usage.

    Parameters:
    - name: Exact node-group datablock name
    - tree_type: GeometryNodeTree, ShaderNodeTree, or CompositorNodeTree
    - geometry_is_modifier: Mark a Geometry Node group for modifier use
    - description: Optional node-group description
    - reuse_existing: Return a compatible existing group instead of rejecting
    - user_prompt: Original user prompt for telemetry
    """
    try:
        result = get_blender_connection().send_command(
            "create_node_group",
            {
                "name": name,
                "tree_type": tree_type,
                "geometry_is_modifier": geometry_is_modifier,
                "description": description,
                "reuse_existing": reuse_existing,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error creating node group: {str(e)}")
        return f"Error creating node group: {str(e)}"

@mcp.tool()
@telemetry_tool("get_node_editor_context")
def get_node_editor_context(
    ctx: Context,
    expected_file_session_id: str = "",
    expected_context_revision: str = "",
    max_editors: int = 32,
    user_prompt: str = "",
) -> str:
    """Inspect the visible Node Editor context without changing Blender.

    Returns an explicit NO_EDITOR, UNIQUE_EDITOR, PINNED_EDITOR,
    MULTIPLE_EDITORS, or STALE_CONTEXT state. Multiple editors are never chosen
    by window order or focus. Use returned tree_ref values with graph tools.

    Parameters:
    - expected_file_session_id: Optional prior file session for stale detection
    - expected_context_revision: Optional prior context revision for stale detection
    - max_editors: Maximum editor records returned, from 1 to 32
    - user_prompt: Original user prompt for telemetry
    """
    try:
        result = get_blender_connection().send_command(
            "get_node_editor_context",
            {
                "expected_file_session_id": expected_file_session_id,
                "expected_context_revision": expected_context_revision,
                "max_editors": max_editors,
            },
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error inspecting Node Editor context: {str(e)}")
        return f"Error inspecting Node Editor context: {str(e)}"

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
    - view: auto (slim for full graphs, semantic for targeted), slim, operations,
      semantic, layout, or all. slim is the compact reading view: node types,
      operation enums, links, and only the socket defaults that are neither
      linked over nor at the type default. Escalate to operations for every
      socket record, semantic for full RNA detail, or all for layout on top —
      only for an identified missing fact.
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
    """Run one bounded, deterministic query against an owner-addressed graph.

    Query contracts:
    - fields: optional node_names; fields may contain id, name, label,
      bl_idname, properties, inputs, outputs, or special_structures. This is
      the cheapest way to read one node before patching it. inputs reports only
      the sockets whose value differs from the node type's own default, so an
      untouched node reports none; request outputs or an export when you need
      every socket regardless.
    - socket_links: optional node_names; socket_id requires exactly one node.
    - named_attributes: optional node_names and exact attribute_name filter.
    - shortest_path: requires from_node and to_node; direction is downstream,
      upstream, or both.
    - upstream/downstream: require node_names and follow that fixed direction.
    - slice: requires node_names; direction is downstream, upstream, or both.

    All query types accept limit from 1 to 1000 and return the same full-graph
    revision used by exports. Use fields/paths/links queries before exporting
    graph payload that is not needed.
    """
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

    Use this tool only for ShaderNodeTree and CompositorNodeTree targets.
    GeometryNodeTree mutations use validate_geometry_node_patch so modifier
    inputs, shared-tree policy, and the Geometry v1 contract remain explicit.

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
    response_detail: str = "full",
) -> str:
    """Apply an owner-addressed patch through a verified transaction.

    Supports local Material, World, Light, Scene, Shader node-group, and
    Compositor node-group owners. Validation is repeated immediately before the
    version-aware owner copy/remap or selected-Scene tree swap. On failure,
    owner users, names, fake-user state, and graph identity are restored.
    GeometryNodeTree targets must use apply_geometry_node_patch.

    Parameters:
    - patch: Inline blender-node-tree-patch/1 object
    - patch_path: JSON file below BLENDER_MCP_WORKSPACE
    - keep_backup: Preserve the pre-commit owner/tree as a fake-user backup
    - user_prompt: Original user prompt for telemetry
    - response_detail: Verbosity of a successful apply response. "full"
      (default) returns the per-operation status list and the per-link
      actual_diff; "operations" drops actual_diff; "summary" drops both and
      relies on semantic_diff/verification. Use a leaner level for large,
      uniform patches to save context.
    """
    if response_detail not in RESPONSE_DETAIL_LEVELS:
        return json.dumps(
            {
                "schema": NODE_PATCH_APPLICATION_SCHEMA,
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "diagnostics": [{
                    "severity": "error",
                    "code": "invalid_response_detail",
                    "path": "/response_detail",
                    "message": (
                        "response_detail must be one of "
                        f"{', '.join(RESPONSE_DETAIL_LEVELS)}"
                    ),
                }],
                "plan": [],
            },
            ensure_ascii=False,
            indent=2,
        )
    dispatched = False
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
        # Past this point the command reaches Blender and may commit before any
        # failure surfaces here, so a later exception cannot prove "not mutated".
        dispatched = True
        result = blender.send_command(
            "apply_node_tree_patch",
            {"patch": patch_document, "keep_backup": keep_backup},
        )
        result = shape_application_response(result, response_detail)
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
            unresolved_application_result(NODE_PATCH_APPLICATION_SCHEMA, dispatched, e),
            ensure_ascii=False,
            indent=2,
        )
