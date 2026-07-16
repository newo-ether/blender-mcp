from __future__ import annotations

import traceback

import bpy

from ..automation import (
    _blender_app_text,
    _blender_version_context,
    _runtime_automation_context,
)
from ..errors import BlenderMCPAddonError
from ..nodes.common import _gn_patch_diagnostic
from ..nodes.compositor import _node_ensure_scene_compositor_tree
from ..nodes.constants import (
    _GN_ASSET_SCOPES,
    _GN_NODE_PROPERTY_EXCLUDES,
    BLENDER_NODE_ASSET_CATALOG_SCHEMA,
    GEOMETRY_NODE_TYPE_CATALOG_SCHEMA,
    GEOMETRY_NODE_TYPE_SCHEMA_DETAILS,
    GEOMETRY_NODES_PATCH_SCHEMA,
    GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA,
    GEOMETRY_NODES_SNAPSHOT_SCHEMA,
    NODE_TREE_MAX_RESPONSE_BYTES,
    NODE_TREE_OWNER_KINDS,
    NODE_TREE_SNAPSHOT_SCHEMA,
    NODE_TREE_TYPES,
)
from ..nodes.geometry_transactions import _gn_apply_patch_transaction
from ..nodes.geometry_validation import _gn_validate_patch_runtime
from ..nodes.editor_context import _node_editor_context
from ..nodes.node_transactions import _node_apply_patch_transaction
from ..nodes.node_validation import _node_validate_patch_runtime
from ..nodes.query import _node_query_graph, _node_soft_limit_response, _node_tree_index
from ..nodes.schema import (
    _gn_dynamic_collection_schema,
    _gn_export_node_asset,
    _gn_geometry_node_type_catalog,
    _gn_import_node_asset,
    _gn_node_asset_summary,
    _gn_node_owned_properties,
    _gn_official_node_asset_catalog,
    _gn_property_schema,
    _gn_socket_type_schema,
    _gn_user_node_asset_catalog,
    _node_type_schema,
)
from ..nodes.serialization import _gn_export_tree, _gn_socket_record
from ..nodes.targets import (
    _gn_geometry_trees,
    _node_export_target,
    _node_id_editable,
    _node_id_library,
    _node_iter_targets,
    _node_resolve_tree_ref,
    _node_target_capabilities,
)
from ..nodes.workflow import _blendermcp_check_workflow_assertions


class NodeCommandsMixin:
    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # Reduced from 20 to 10
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def get_blender_version_context(self):
        """Return exact version/build metadata for documentation resolution."""
        return _blender_version_context()

    def get_runtime_automation_context(self):
        """Return live render, Action, compositor, and instance compatibility."""
        return _runtime_automation_context()

    def get_node_editor_context(
        self,
        expected_file_session_id="",
        expected_context_revision="",
        max_editors=32,
    ):
        """Return deterministic live Node Editor UI context without mutation."""
        from .. import state

        return _node_editor_context(
            state.file_session_id,
            expected_file_session_id,
            expected_context_revision,
            max_editors,
        )

    def ensure_scene_compositor_tree(
        self, scene_name, create_if_missing=False,
    ):
        """Inspect or transactionally initialize one local Scene compositor."""
        return _node_ensure_scene_compositor_tree(
            scene_name, bool(create_if_missing),
        )

    def list_node_trees(self, tree_types=None, owner_kinds=None):
        """List owner-addressed Geometry, Shader, and Compositor trees."""
        tree_types = list(tree_types or ())
        owner_kinds = [str(item).strip().upper() for item in (owner_kinds or ())]
        invalid_tree_types = sorted(set(tree_types) - NODE_TREE_TYPES)
        invalid_owner_kinds = sorted(set(owner_kinds) - NODE_TREE_OWNER_KINDS)
        if invalid_tree_types:
            raise ValueError(
                "Unsupported tree_types: " + ", ".join(invalid_tree_types)
            )
        if invalid_owner_kinds:
            raise ValueError(
                "Unsupported owner_kinds: " + ", ".join(invalid_owner_kinds)
            )
        records = []
        for target in _node_iter_targets():
            if tree_types and target["tree_type"] not in tree_types:
                continue
            if owner_kinds and target["owner_kind"] not in owner_kinds:
                continue
            snapshot = _node_export_target(target, "semantic")
            records.append({
                "domain": target["domain"],
                "tree_ref": target["tree_ref"],
                "owner": target["owner"],
                "tree": {
                    "name": target["tree"].name,
                    "bl_idname": target["tree"].bl_idname,
                    "library": _node_id_library(target["tree"]),
                    "editable": _node_id_editable(target["tree"]),
                },
                "capabilities": _node_target_capabilities(target),
                "revision": snapshot["revision"],
                "node_count": snapshot["stats"]["node_count"],
                "link_count": snapshot["stats"]["link_count"],
                "interface_item_count": snapshot["stats"]["interface_item_count"],
                "users": snapshot["users"],
                "diagnostics": snapshot["diagnostics"],
            })
        return {
            "schema": NODE_TREE_SNAPSHOT_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "tree_types": tree_types,
            "owner_kinds": owner_kinds,
            "tree_count": len(records),
            "trees": records,
        }

    def export_node_tree(
        self, tree_ref, view="auto", node_names=None, neighbor_depth=0,
        allow_large_response=False,
    ):
        """Export an owner-addressed NodeTree as deterministic flat JSON."""
        target = _node_resolve_tree_ref(tree_ref)
        snapshot = _node_export_target(
            target, view, node_names or [], neighbor_depth,
        )
        if not node_names and snapshot["stats"]["json_bytes"] > NODE_TREE_MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Full node-tree response is {snapshot['stats']['json_bytes']} bytes; "
                f"the limit is {NODE_TREE_MAX_RESPONSE_BYTES}. Use get_node_tree_index "
                "and export_node_tree with node_names."
            )
        return snapshot if allow_large_response else _node_soft_limit_response(snapshot)

    def get_node_tree_index(self, tree_ref, query="", offset=0, limit=100):
        """Return a compact index for one owner-addressed NodeTree."""
        return _node_tree_index(
            _node_resolve_tree_ref(tree_ref), query, offset, limit,
        )

    def query_node_graph(
        self,
        tree_ref,
        query_type,
        node_names=None,
        from_node="",
        to_node="",
        attribute_name="",
        socket_id="",
        direction="downstream",
        fields=None,
        limit=200,
    ):
        """Run a bounded field, link, attribute, path, or graph-slice query."""
        return _node_query_graph(
            _node_resolve_tree_ref(tree_ref),
            query_type,
            node_names=node_names or [],
            from_node=from_node,
            to_node=to_node,
            attribute_name=attribute_name,
            socket_id=socket_id,
            direction=direction,
            fields=fields or [],
            limit=limit,
        )

    def get_node_type_schema(
        self, tree_type, node_type, owner_kind="NODE_GROUP", detail="compact",
    ):
        """Inspect a node type in an exact tree and owner context."""
        return _node_type_schema(tree_type, owner_kind, node_type, detail)

    def validate_node_tree_patch(self, patch):
        """Dry-run a generic node-tree patch on an owner-aware disposable copy."""
        try:
            target = _node_resolve_tree_ref(patch.get("tree_ref"))
        except (AttributeError, TypeError, ValueError) as exc:
            return {
                "schema": "blender-node-tree-patch-validation/1",
                "valid": False,
                "stage": "runtime",
                "will_mutate": False,
                "tree_ref": patch.get("tree_ref"),
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "target_resolution_failed", "/tree_ref", str(exc),
                )],
                "plan": [],
                "semantic_diff": {},
            }
        return _node_validate_patch_runtime(target, patch)

    def apply_node_tree_patch(self, patch, keep_backup=True):
        """Apply a validated generic patch through its owner transaction."""
        try:
            target = _node_resolve_tree_ref(patch.get("tree_ref"))
        except (AttributeError, TypeError, ValueError) as exc:
            return {
                "schema": "blender-node-tree-patch-application/1",
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "tree_ref": patch.get("tree_ref") if isinstance(patch, dict) else None,
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "target_resolution_failed", "/tree_ref", str(exc),
                )],
                "plan": [],
            }
        return _node_apply_patch_transaction(
            target, patch, bool(keep_backup),
        )

    def list_geometry_node_trees(self):
        """List Geometry Node groups with revisions and user summaries."""
        trees = []
        for tree in _gn_geometry_trees():
            snapshot = _gn_export_tree(tree, "semantic")
            trees.append({
                "name": tree.name,
                "editable": snapshot["tree"]["editable"],
                "library": snapshot["tree"]["library"],
                "revision": snapshot["revision"],
                "node_count": snapshot["stats"]["node_count"],
                "link_count": snapshot["stats"]["link_count"],
                "interface_item_count": snapshot["stats"]["interface_item_count"],
                "users": snapshot["users"],
            })
        return {
            "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "tree_count": len(trees),
            "trees": trees,
        }

    def export_geometry_node_tree(
        self, tree_name, view="auto", node_names=None, neighbor_depth=0,
        allow_large_response=False,
    ):
        """Export one Geometry Node group as normalized graph JSON."""
        tree = bpy.data.node_groups.get(tree_name)
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            raise ValueError(f"Geometry Node tree not found: {tree_name}")
        snapshot = _gn_export_tree(tree, view, node_names, neighbor_depth)
        return snapshot if allow_large_response else _node_soft_limit_response(snapshot)

    def get_geometry_node_type_schema(self, node_type, detail="compact"):
        """Inspect a node type from this running Blender build."""
        if not isinstance(node_type, str) or not node_type.strip():
            raise ValueError("node_type must be a non-empty Blender node bl_idname")
        detail = str(detail or "compact").strip().lower()
        if detail not in GEOMETRY_NODE_TYPE_SCHEMA_DETAILS:
            raise ValueError("detail must be 'compact' or 'full'")

        tree = bpy.data.node_groups.new(".BlenderMCP_TypeSchema", "GeometryNodeTree")
        try:
            try:
                node = tree.nodes.new(type=node_type.strip())
            except RuntimeError as exc:
                raise ValueError(
                    f"Unsupported Geometry Node type in Blender {bpy.app.version_string}: {node_type}"
                ) from exc

            property_source = (
                _gn_node_owned_properties(node)
                if detail == "compact"
                else node.bl_rna.properties
            )
            properties = []
            dynamic_items = []
            for prop in property_source:
                if prop.identifier in _GN_NODE_PROPERTY_EXCLUDES or prop.identifier == "rna_type":
                    continue
                if getattr(prop, "is_hidden", False):
                    continue
                if prop.type == "COLLECTION":
                    if detail == "compact":
                        dynamic_items.append(_gn_dynamic_collection_schema(node, prop))
                    continue
                properties.append(_gn_property_schema(node, prop))

            result = {
                "schema": GEOMETRY_NODES_SNAPSHOT_SCHEMA,
                "blender_version": list(bpy.app.version[:3]),
                "blender_version_string": bpy.app.version_string,
                "build_hash": _blender_app_text("build_hash"),
                "detail": detail,
                "node_type": node.bl_idname,
                "label": node.bl_label,
                "description": node.bl_description,
                "properties": properties,
                "inputs": [
                    (_gn_socket_type_schema if detail == "compact" else _gn_socket_record)(
                        socket, "INPUT", index
                    )
                    for index, socket in enumerate(node.inputs)
                ],
                "outputs": [
                    (_gn_socket_type_schema if detail == "compact" else _gn_socket_record)(
                        socket, "OUTPUT", index
                    )
                    for index, socket in enumerate(node.outputs)
                ],
            }
            if detail == "compact":
                result["dynamic_items"] = dynamic_items
            return result
        finally:
            bpy.data.node_groups.remove(tree)

    def search_geometry_node_types(self, query="", offset=0, limit=100):
        """Search registered node types constructible in Geometry Nodes."""
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be from 1 to 500")
        query_value = "" if query is None else str(query)
        query_text = query_value.strip().casefold()
        catalog = _gn_geometry_node_type_catalog()
        matches = [
            item for item in catalog
            if not query_text or query_text in " ".join((
                item["bl_idname"], item["label"], item["description"], item["category"],
            )).casefold()
        ]
        page = matches[offset:offset + limit]
        next_offset = offset + len(page)
        return {
            "schema": GEOMETRY_NODE_TYPE_CATALOG_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "query": query_value,
            "offset": offset,
            "limit": limit,
            "total_types": len(catalog),
            "total_matches": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None,
            "node_types": page,
        }

    def search_blender_node_assets(
        self, query="", library="", tree_type="", detail="summary", scope="ESSENTIALS",
        offset=0, limit=20,
    ):
        """Search bundled or configured node assets without retaining inspected IDs."""
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        detail = str(detail or "summary").strip().lower()
        if detail not in {"summary", "full"}:
            raise ValueError("detail must be 'summary' or 'full'")
        if detail == "full" and limit > 20:
            raise ValueError("full detail limit must not exceed 20")
        scope_value = str(scope or "ESSENTIALS").strip().upper()
        if scope_value not in _GN_ASSET_SCOPES:
            raise ValueError("scope must be 'ESSENTIALS', 'USER', or 'ALL'")
        query_value = "" if query is None else str(query)
        library_value = "" if library is None else str(library)
        tree_type_value = "" if tree_type is None else str(tree_type)
        query_text = query_value.strip().casefold()
        library_text = library_value.strip().casefold()
        tree_type_text = tree_type_value.strip().casefold()
        catalogs = []
        if scope_value in {"ESSENTIALS", "ALL"}:
            catalogs.append(_gn_official_node_asset_catalog())
        if scope_value in {"USER", "ALL"}:
            catalogs.append(_gn_user_node_asset_catalog(library_value))
        catalog = {
            "records": [item for part in catalogs for item in part["records"]],
            "errors": [item for part in catalogs for item in part["errors"]],
            "library_paths": [item for part in catalogs for item in part["library_paths"]],
            "configured_libraries": [
                item
                for part in catalogs
                for item in part.get("configured_libraries", ())
            ],
        }
        matches = []
        for item in catalog["records"]:
            library_haystack = " ".join((
                item["source_library"],
                item["source_path"],
                item.get("configured_library", {}).get("name", ""),
                item.get("configured_library", {}).get("path", ""),
            )).casefold()
            if library_text and library_text not in library_haystack:
                continue
            if tree_type_text and tree_type_text != item["tree_type"].casefold():
                continue
            haystack = " ".join((
                item["name"], item["description"], item["author"],
                item["source_scope"], item["source_library"],
                item.get("configured_library", {}).get("name", ""),
                item["tree_type"], " ".join(item["tags"]),
            )).casefold()
            if query_text and query_text not in haystack:
                continue
            matches.append(item)
        page = matches[offset:offset + limit]
        next_offset = offset + len(page)
        return {
            "schema": BLENDER_NODE_ASSET_CATALOG_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "query": query_value,
            "library": library_value,
            "tree_type": tree_type_value,
            "scope": scope_value,
            "detail": detail,
            "offset": offset,
            "limit": limit,
            "library_count": len(catalog["library_paths"]),
            "configured_library_count": len(catalog["configured_libraries"]),
            "total_assets": len(catalog["records"]),
            "total_matches": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None,
            "assets": [
                dict(item) if detail == "full" else _gn_node_asset_summary(item)
                for item in page
            ],
            "errors": catalog["errors"],
        }

    def import_blender_node_asset(
        self,
        source_path,
        asset_name,
        tree_type="",
        scope="USER",
        library="",
        conflict_policy="REJECT",
    ):
        """Append one exact searched node asset through a rollback-safe transaction."""
        return _gn_import_node_asset(
            source_path,
            asset_name,
            tree_type=tree_type,
            scope=scope,
            library=library,
            conflict_policy=conflict_policy,
        )

    def export_blender_node_asset(
        self,
        source_path,
        asset_name,
        tree_type="",
        scope="USER",
        library="",
        view="auto",
        node_names=None,
        neighbor_depth=0,
    ):
        """Read one exact node asset without retaining appended datablocks."""
        return _gn_export_node_asset(
            source_path,
            asset_name,
            tree_type=tree_type,
            scope=scope,
            library=library,
            view=view,
            node_names=node_names or [],
            neighbor_depth=neighbor_depth,
        )

    def get_geometry_node_tree_index(self, tree_name, query="", offset=0, limit=100):
        """Return a searchable, paginated node-name/type index for subgraph discovery."""
        tree = bpy.data.node_groups.get(tree_name)
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            raise ValueError(f"Geometry Node tree not found: {tree_name}")
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be from 1 to 500")

        query_value = "" if query is None else str(query)
        query_text = query_value.strip().casefold()
        matches = [
            node for node in sorted(tree.nodes, key=lambda item: item.name)
            if not query_text or query_text in " ".join(
                (node.name, node.label, node.bl_idname, node.bl_label)
            ).casefold()
        ]
        page = matches[offset:offset + limit]
        revision = _gn_export_tree(tree, "all")["revision"]
        next_offset = offset + len(page)
        return {
            "schema": "blender-geometry-nodes-index/1",
            "blender_version": list(bpy.app.version[:3]),
            "tree_name": tree.name,
            "revision": revision,
            "query": query_value,
            "offset": offset,
            "limit": limit,
            "total_nodes": len(tree.nodes),
            "total_matches": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None,
            "nodes": [
                {
                    "name": node.name,
                    "label": node.label,
                    "bl_idname": node.bl_idname,
                    "bl_label": node.bl_label,
                }
                for node in page
            ],
        }

    def validate_geometry_node_patch(self, patch):
        """Build a runtime-resolved patch plan without mutating live Blender data."""
        if not isinstance(patch, dict):
            raise ValueError("Geometry Nodes patch must be a JSON object")
        if patch.get("schema") != GEOMETRY_NODES_PATCH_SCHEMA:
            raise ValueError(f"Expected patch schema {GEOMETRY_NODES_PATCH_SCHEMA!r}")
        tree_name = patch.get("tree_name")
        tree = bpy.data.node_groups.get(tree_name) if isinstance(tree_name, str) else None
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            return {
                "schema": GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA,
                "valid": False,
                "stage": "runtime",
                "will_mutate": False,
                "tree_name": tree_name,
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "tree_not_found", "/tree_name",
                    f"Geometry Node tree not found: {tree_name}",
                )],
                "plan": [],
                "semantic_diff": {},
            }
        return _gn_validate_patch_runtime(tree, patch)

    def apply_geometry_node_patch(self, patch, keep_backup=True):
        """Apply a validated patch through a copy-on-write transaction."""
        if not isinstance(patch, dict):
            raise ValueError("Geometry Nodes patch must be a JSON object")
        if patch.get("schema") != GEOMETRY_NODES_PATCH_SCHEMA:
            raise ValueError(f"Expected patch schema {GEOMETRY_NODES_PATCH_SCHEMA!r}")
        tree_name = patch.get("tree_name")
        tree = bpy.data.node_groups.get(tree_name) if isinstance(tree_name, str) else None
        if tree is None or tree.bl_idname != "GeometryNodeTree":
            return {
                "schema": "blender-geometry-nodes-patch-application/1",
                "status": "rejected",
                "applied": False,
                "mutated": False,
                "tree_name": tree_name,
                "diagnostics": [_gn_patch_diagnostic(
                    "error", "tree_not_found", "/tree_name",
                    f"Geometry Node tree not found: {tree_name}",
                )],
                "plan": [],
            }
        return _gn_apply_patch_transaction(tree, patch, bool(keep_backup))

    def modify_verify_save(
        self,
        patch_kind,
        patch,
        assertions=None,
        keep_backup=True,
        save_policy="never",
    ):
        """Validate, assert, transact, read back, and optionally save one Patch."""
        if patch_kind not in {"node_tree", "geometry_nodes"}:
            raise BlenderMCPAddonError(
                "invalid_request", "patch_kind must be node_tree or geometry_nodes"
            )
        if save_policy not in {"never", "on_success", "required"}:
            raise BlenderMCPAddonError(
                "invalid_request", "save_policy must be never, on_success, or required"
            )
        if not isinstance(patch, dict):
            raise BlenderMCPAddonError("invalid_request", "patch must be a JSON object")
        if save_policy == "required" and not bpy.data.filepath:
            raise BlenderMCPAddonError(
                "file_permission_error",
                "save_policy=required needs an existing .blend filepath; save the Untitled file first",
            )

        if patch_kind == "node_tree":
            validation = self.validate_node_tree_patch(patch)
        else:
            validation = self.validate_geometry_node_patch(patch)
        if not validation.get("valid"):
            return {
                "schema": "blender-modify-verify-save/1",
                "status": "rejected",
                "mutated": False,
                "saved": False,
                "patch_kind": patch_kind,
                "validation": validation,
                "assertions": [],
            }

        assertion_results = _blendermcp_check_workflow_assertions(
            validation.get("candidate_stats") or {}, assertions
        )
        if not all(item["passed"] for item in assertion_results):
            return {
                "schema": "blender-modify-verify-save/1",
                "status": "assertion_failed",
                "mutated": False,
                "saved": False,
                "patch_kind": patch_kind,
                "validation": {
                    "current_revision": validation.get("current_revision"),
                    "candidate_revision": validation.get("candidate_revision"),
                    "candidate_stats": validation.get("candidate_stats"),
                },
                "assertions": assertion_results,
            }

        if patch_kind == "node_tree":
            application = self.apply_node_tree_patch(patch, keep_backup=keep_backup)
        else:
            application = self.apply_geometry_node_patch(patch, keep_backup=keep_backup)
        if not application.get("applied"):
            return {
                "schema": "blender-modify-verify-save/1",
                "status": application.get("status", "failed"),
                "mutated": bool(application.get("mutated")),
                "saved": False,
                "patch_kind": patch_kind,
                "validation": {
                    "current_revision": validation.get("current_revision"),
                    "candidate_revision": validation.get("candidate_revision"),
                    "candidate_stats": validation.get("candidate_stats"),
                },
                "assertions": assertion_results,
                "application": application,
            }

        if patch_kind == "node_tree":
            resolved = _node_resolve_tree_ref(patch["tree_ref"])
            committed = _node_export_target(resolved, "all")
        else:
            committed_tree = bpy.data.node_groups.get(application.get("tree_name"))
            if committed_tree is None:
                raise RuntimeError("Committed Geometry Node tree could not be read back")
            committed = _gn_export_tree(committed_tree, "all")
        if committed["revision"] != application.get("new_revision"):
            raise RuntimeError("Post-application verification revision changed unexpectedly")
        candidate_stats = validation.get("candidate_stats") or {}
        verified_stats = {
            key: committed["stats"].get(key)
            for key in ("node_count", "link_count", "interface_item_count")
        }
        if verified_stats != {
            key: candidate_stats.get(key)
            for key in ("node_count", "link_count", "interface_item_count")
        }:
            raise RuntimeError("Post-application verification stats differ from the dry-run candidate")

        saved = False
        save_result = "not_requested"
        if save_policy == "on_success" and not bpy.data.filepath:
            save_result = "skipped_untitled"
        elif save_policy in {"on_success", "required"}:
            operation_result = bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
            if 'FINISHED' not in operation_result:
                raise BlenderMCPAddonError(
                    "file_permission_error", "Blender did not complete the requested save"
                )
            saved = True
            save_result = "saved"

        return {
            "schema": "blender-modify-verify-save/1",
            "status": "completed",
            "mutated": True,
            "saved": saved,
            "save_result": save_result,
            "save_policy": save_policy,
            "patch_kind": patch_kind,
            "validation": {
                "current_revision": validation.get("current_revision"),
                "candidate_revision": validation.get("candidate_revision"),
                "candidate_stats": validation.get("candidate_stats"),
            },
            "assertions": assertion_results,
            "verification": {
                "revision": committed["revision"],
                "stats": verified_stats,
                "matches_candidate": True,
            },
            "application": application,
        }
