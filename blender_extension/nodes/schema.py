from __future__ import annotations

import os

import bpy

from ..automation import _blender_app_text
from .common import _gn_patch_diagnostic, _gn_rna_property
from .constants import (
    _GN_ASSET_SCOPES,
    _GN_ESSENTIALS_CATALOG_CACHE,
    _GN_MAX_CONFIGURED_LIBRARY_BLEND_FILES,
    _GN_NODE_PROPERTY_EXCLUDES,
    _GN_NODE_TYPE_CATALOG_CACHE,
    _GN_USER_ASSET_CATALOG_CACHE,
    BLENDER_NODE_ASSET_EXPORT_SCHEMA,
    BLENDER_NODE_ASSET_IMPORT_SCHEMA,
    GEOMETRY_NODE_TYPE_SCHEMA_DETAILS,
    NODE_TREE_SNAPSHOT_SCHEMA,
    NODE_TREE_TYPES,
    NODE_TYPE_SCHEMA,
    _GNAssetCleanupError,
)
from .dynamic import _node_dynamic_collection_record
from .query import _node_soft_limit_response
from .serialization import (
    _gn_canonical_json,
    _gn_json_value,
    _gn_socket_record,
    _gn_tree_interface,
    _node_export_tree,
    _node_special_structure_schema,
)
from .targets import _node_id_editable, _node_id_library, _node_normalize_tree_ref


def _gn_actual_snapshot_diff(before, after):
    before_nodes = before["tree"]["nodes"]
    after_nodes = after["tree"]["nodes"]
    before_node_names = set(before_nodes)
    after_node_names = set(after_nodes)
    shared_node_names = before_node_names & after_node_names

    before_links = {_gn_canonical_json(link): link for link in before["tree"]["links"]}
    after_links = {_gn_canonical_json(link): link for link in after["tree"]["links"]}
    before_interface = {
        item["identifier"]: item for item in before["tree"]["interface"]
    }
    after_interface = {
        item["identifier"]: item for item in after["tree"]["interface"]
    }
    shared_interface = set(before_interface) & set(after_interface)

    result = {
        "nodes_added": sorted(after_node_names - before_node_names),
        "nodes_removed": sorted(before_node_names - after_node_names),
        "nodes_changed": sorted(
            name for name in shared_node_names
            if before_nodes[name] != after_nodes[name]
        ),
        "links_added": [after_links[key] for key in sorted(set(after_links) - set(before_links))],
        "links_removed": [before_links[key] for key in sorted(set(before_links) - set(after_links))],
        "interface_added": sorted(set(after_interface) - set(before_interface)),
        "interface_removed": sorted(set(before_interface) - set(after_interface)),
        "interface_changed": sorted(
            identifier for identifier in shared_interface
            if before_interface[identifier] != after_interface[identifier]
        ),
    }
    result["summary"] = {
        key: len(value) for key, value in result.items()
    }
    return result

def _gn_property_schema(owner, prop):
    record = {
        "identifier": prop.identifier,
        "type": prop.type,
        "readonly": bool(getattr(prop, "is_readonly", False)),
        "array_length": int(getattr(prop, "array_length", 0)),
    }
    for source, destination in (
        ("name", "name"), ("description", "description"),
        ("hard_min", "min"), ("hard_max", "max"),
    ):
        if hasattr(prop, source):
            try:
                record[destination] = _gn_json_value(getattr(prop, source))
            except (TypeError, ValueError):
                pass
    try:
        record["value"] = _gn_json_value(getattr(owner, prop.identifier))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    if prop.type == "ENUM":
        try:
            record["enum_items"] = [
                {"identifier": item.identifier, "name": item.name, "description": item.description}
                for item in prop.enum_items
            ]
        except (AttributeError, TypeError, RuntimeError):
            pass
    return record

def _gn_node_owned_properties(node):
    """Return RNA properties declared by the concrete node type only."""
    base = getattr(node.bl_rna, "base", None)
    base_identifiers = {
        prop.identifier for prop in base.properties
    } if base is not None else set()
    return [
        prop for prop in node.bl_rna.properties
        if prop.identifier not in base_identifiers and prop.identifier != "rna_type"
    ]

def _gn_dynamic_collection_schema(owner, prop, limit=50):
    """Describe dynamic node-owned item collections without inherited RNA."""
    return _node_dynamic_collection_record(owner, prop, _gn_json_value, limit)

def _gn_socket_type_schema(socket, direction, index):
    record = _gn_socket_record(socket, direction, index)
    for source, destination in (
        ("description", "description"),
        ("hide_value", "hide_value"),
        ("is_unavailable", "unavailable"),
        ("default_attribute_name", "default_attribute_name"),
    ):
        if hasattr(socket, source):
            try:
                record[destination] = _gn_json_value(getattr(socket, source))
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
    default_prop = _gn_rna_property(socket, "default_value")
    if default_prop is not None:
        for source, destination in (("hard_min", "min"), ("hard_max", "max")):
            if hasattr(default_prop, source):
                try:
                    record[destination] = _gn_json_value(getattr(default_prop, source))
                except (TypeError, ValueError):
                    pass
    return record

def _node_create_schema_probe(tree_type, owner_kind):
    canonical = _node_normalize_tree_ref({
        "tree_type": tree_type,
        "owner": {"kind": owner_kind, "name": ".BlenderMCP_TypeSchema"},
    })
    owner_kind = canonical["owner"]["kind"]
    temporary_owner = None
    standalone_tree = None
    if owner_kind == "MATERIAL":
        temporary_owner = bpy.data.materials.new(".BlenderMCP_TypeSchema")
        temporary_owner.use_nodes = True
        tree = temporary_owner.node_tree
    elif owner_kind == "WORLD":
        temporary_owner = bpy.data.worlds.new(".BlenderMCP_TypeSchema")
        temporary_owner.use_nodes = True
        tree = temporary_owner.node_tree
    elif owner_kind == "LIGHT":
        temporary_owner = bpy.data.lights.new(".BlenderMCP_TypeSchema", "POINT")
        temporary_owner.use_nodes = True
        tree = temporary_owner.node_tree
    elif owner_kind == "SCENE":
        temporary_owner = bpy.data.scenes.new(".BlenderMCP_TypeSchema")
        if hasattr(temporary_owner, "compositing_node_group"):
            standalone_tree = bpy.data.node_groups.new(
                ".BlenderMCP_TypeSchema", "CompositorNodeTree"
            )
            temporary_owner.compositing_node_group = standalone_tree
            tree = standalone_tree
        else:
            temporary_owner.use_nodes = True
            tree = temporary_owner.node_tree
    else:
        standalone_tree = bpy.data.node_groups.new(
            ".BlenderMCP_TypeSchema", tree_type
        )
        tree = standalone_tree
    return tree, temporary_owner, standalone_tree, owner_kind

def _node_remove_schema_probe(temporary_owner, standalone_tree):
    if temporary_owner is not None:
        if isinstance(temporary_owner, bpy.types.Material):
            bpy.data.materials.remove(temporary_owner, do_unlink=True)
        elif isinstance(temporary_owner, bpy.types.World):
            bpy.data.worlds.remove(temporary_owner, do_unlink=True)
        elif isinstance(temporary_owner, bpy.types.Light):
            bpy.data.lights.remove(temporary_owner, do_unlink=True)
        elif isinstance(temporary_owner, bpy.types.Scene):
            bpy.data.scenes.remove(temporary_owner, do_unlink=True)
    if (
        standalone_tree is not None
        and standalone_tree.name in bpy.data.node_groups
    ):
        bpy.data.node_groups.remove(standalone_tree, do_unlink=True)

def _node_type_schema(tree_type, owner_kind, node_type, detail="compact"):
    if not isinstance(node_type, str) or not node_type.strip():
        raise ValueError("node_type must be a non-empty Blender node bl_idname")
    detail = str(detail or "compact").strip().lower()
    if detail not in GEOMETRY_NODE_TYPE_SCHEMA_DETAILS:
        raise ValueError("detail must be 'compact' or 'full'")
    tree, temporary_owner, standalone_tree, owner_kind = _node_create_schema_probe(
        tree_type, owner_kind
    )
    try:
        try:
            node = tree.nodes.new(type=node_type.strip())
        except RuntimeError as exc:
            raise ValueError(
                f"Unsupported {node_type} in {owner_kind} {tree_type} "
                f"on Blender {bpy.app.version_string}"
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
            "schema": NODE_TYPE_SCHEMA,
            "blender_version": list(bpy.app.version[:3]),
            "blender_version_string": bpy.app.version_string,
            "build_hash": _blender_app_text("build_hash"),
            "tree_type": tree_type,
            "owner_kind": owner_kind,
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
            result["special_structures"] = _node_special_structure_schema(node)
        return result
    finally:
        _node_remove_schema_probe(temporary_owner, standalone_tree)

def _gn_node_catalog_cache_key():
    return (
        tuple(bpy.app.version[:3]),
        _blender_app_text("build_hash"),
    )

def _gn_geometry_node_type_catalog():
    """Probe all registered node types that can be created in Geometry Nodes."""
    key = _gn_node_catalog_cache_key()
    cached = _GN_NODE_TYPE_CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    tree = bpy.data.node_groups.new(".BlenderMCP_NodeTypeCatalog", "GeometryNodeTree")
    records = []
    try:
        for type_name in sorted(name for name in dir(bpy.types) if "Node" in name):
            cls = getattr(bpy.types, type_name, None)
            if cls is None or not hasattr(cls, "is_registered_node_type"):
                continue
            try:
                if not cls.is_registered_node_type():
                    continue
                node = tree.nodes.new(type=type_name)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
            try:
                if type_name.startswith("GeometryNode"):
                    category = "geometry"
                elif type_name.startswith("ShaderNode"):
                    category = "shader_utility"
                elif type_name.startswith("FunctionNode"):
                    category = "function"
                else:
                    category = "layout_or_group"
                records.append({
                    "bl_idname": node.bl_idname,
                    "label": node.bl_label,
                    "description": node.bl_description,
                    "category": category,
                    "input_count": len(node.inputs),
                    "output_count": len(node.outputs),
                })
            finally:
                tree.nodes.remove(node)
    finally:
        bpy.data.node_groups.remove(tree)

    records.sort(key=lambda item: item["bl_idname"])
    _GN_NODE_TYPE_CATALOG_CACHE.clear()
    _GN_NODE_TYPE_CATALOG_CACHE[key] = records
    return records

def _gn_essentials_library_paths():
    """Find bundled official node asset libraries below Blender DATAFILES."""
    root = bpy.utils.system_resource("DATAFILES")
    if not root:
        return []
    # Blender 4.2 stores Geometry Nodes Essentials below ``geometry_nodes``;
    # newer builds consolidate node assets below ``nodes``. Both locations are
    # fixed children of Blender's own DATAFILES resource, never user paths.
    paths = []
    for directory_name in ("nodes", "geometry_nodes"):
        node_assets = os.path.join(root, "assets", directory_name)
        if not os.path.isdir(node_assets):
            continue
        paths.extend(
            os.path.join(node_assets, name)
            for name in sorted(os.listdir(node_assets))
            if name.lower().endswith(".blend")
            and os.path.isfile(os.path.join(node_assets, name))
        )
    return paths

def _gn_blend_data_ids():
    """Snapshot every currently loaded Blender ID by pointer."""
    result = {}
    for prop in bpy.data.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.type != "COLLECTION":
            continue
        try:
            collection = getattr(bpy.data, prop.identifier)
            for item in collection:
                if isinstance(item, bpy.types.ID):
                    result[item.as_pointer()] = item
        except (AttributeError, TypeError, RuntimeError):
            continue
    return result

def _gn_node_group_dependencies(tree):
    dependencies = set()
    pending = [tree]
    visited = {tree.as_pointer()}
    while pending:
        current = pending.pop()
        for node in current.nodes:
            nested = getattr(node, "node_tree", None)
            if nested is None or not isinstance(nested, bpy.types.NodeTree):
                continue
            pointer = nested.as_pointer()
            if pointer in visited:
                continue
            visited.add(pointer)
            dependencies.add((nested.name, nested.bl_idname))
            pending.append(nested)
    return [
        {"name": name, "tree_type": tree_type}
        for name, tree_type in sorted(dependencies)
    ]

def _gn_node_asset_record(
    tree,
    library_path,
    *,
    source_scope="ESSENTIALS",
    configured_library=None,
):
    metadata = tree.asset_data
    interface = _gn_tree_interface(tree, True)
    configured_library = dict(configured_library or {})
    return {
        "name": tree.name,
        "description": getattr(metadata, "description", "") or "",
        "author": getattr(metadata, "author", "") or "",
        "catalog_id": str(getattr(metadata, "catalog_id", "") or ""),
        "tags": sorted(tag.name for tag in getattr(metadata, "tags", [])),
        "tree_type": tree.bl_idname,
        "source_scope": source_scope,
        "source_library": os.path.basename(library_path),
        "source_path": os.path.normpath(library_path),
        "configured_library": configured_library,
        "interface": interface,
        "node_count": len(tree.nodes),
        "link_count": len(tree.links),
        "interface_item_count": len(interface),
        "dependencies": _gn_node_group_dependencies(tree),
    }

def _gn_load_node_asset_library(
    library_path,
    *,
    source_scope="ESSENTIALS",
    configured_library=None,
):
    """Inspect one library and remove every ID appended during inspection."""
    before = _gn_blend_data_ids()
    records = []
    cleanup_error = None
    try:
        try:
            loader = bpy.data.libraries.load(library_path, link=False, assets_only=True)
        except TypeError:
            loader = bpy.data.libraries.load(library_path, link=False)
        with loader as (data_from, data_to):
            data_to.node_groups = list(data_from.node_groups)
        for tree in data_to.node_groups:
            if tree is None or tree.asset_data is None:
                continue
            records.append(_gn_node_asset_record(
                tree,
                library_path,
                source_scope=source_scope,
                configured_library=configured_library,
            ))
    finally:
        after = _gn_blend_data_ids()
        appended = [item for pointer, item in after.items() if pointer not in before]
        if appended:
            try:
                bpy.data.batch_remove(ids=appended)
            except Exception as exc:
                cleanup_error = exc
        remaining = [
            item for pointer, item in _gn_blend_data_ids().items()
            if pointer not in before
        ]
        if remaining:
            names = ", ".join(
                f"{item.bl_rna.identifier}/{item.name}" for item in remaining[:10]
            )
            raise _GNAssetCleanupError(
                f"Asset inspection leaked {len(remaining)} datablocks: {names}"
            ) from cleanup_error
        if cleanup_error is not None:
            raise _GNAssetCleanupError(
                f"Asset inspection cleanup failed: {cleanup_error}"
            ) from cleanup_error
    return records

def _gn_official_node_asset_catalog():
    paths = _gn_essentials_library_paths()
    key = (
        _gn_node_catalog_cache_key(),
        tuple(
            (path, os.path.getsize(path), int(os.path.getmtime(path)))
            for path in paths
        ),
    )
    cached = _GN_ESSENTIALS_CATALOG_CACHE.get(key)
    if cached is not None:
        return cached
    records = []
    errors = []
    for path in paths:
        try:
            records.extend(_gn_load_node_asset_library(
                path,
                source_scope="ESSENTIALS",
                configured_library={
                    "name": "Blender Essentials",
                    "path": os.path.normpath(os.path.dirname(path)),
                    "import_method": "BUNDLED",
                },
            ))
        except _GNAssetCleanupError:
            raise
        except Exception as exc:
            errors.append({
                "library": os.path.basename(path),
                "path": os.path.normpath(path),
                "error": f"{type(exc).__name__}: {exc}",
            })
    records.sort(key=lambda item: (item["name"].casefold(), item["source_library"]))
    result = {"records": records, "errors": errors, "library_paths": paths}
    _GN_ESSENTIALS_CATALOG_CACHE.clear()
    _GN_ESSENTIALS_CATALOG_CACHE[key] = result
    return result

def _gn_configured_asset_libraries():
    """Return normalized user-configured asset-library roots."""
    libraries = []
    filepaths = getattr(bpy.context.preferences, "filepaths", None)
    configured = getattr(filepaths, "asset_libraries", ())
    for library in configured:
        raw_path = str(getattr(library, "path", "") or "").strip()
        if not raw_path:
            continue
        try:
            expanded = bpy.path.abspath(raw_path)
        except (AttributeError, TypeError, ValueError):
            expanded = raw_path
        normalized = os.path.normpath(os.path.abspath(expanded))
        libraries.append({
            "name": str(getattr(library, "name", "") or os.path.basename(normalized)),
            "path": normalized,
            "real_path": os.path.realpath(normalized),
            "import_method": str(getattr(library, "import_method", "") or ""),
        })
    return sorted(libraries, key=lambda item: (item["name"].casefold(), item["path"]))

def _gn_path_within_root(path, root):
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root)
    except (OSError, ValueError):
        return False

def _gn_configured_library_blend_paths(library):
    """Enumerate a bounded set of .blend files below one configured root."""
    root = library["path"]
    if not os.path.isdir(root):
        return [], [{
            "library": library["name"],
            "path": root,
            "error": "Configured asset-library path is not a directory",
        }]
    paths = []
    errors = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(
            name for name in directory_names if not name.startswith(".")
        )
        for file_name in sorted(file_names):
            if not file_name.lower().endswith(".blend"):
                continue
            path = os.path.normpath(os.path.join(directory, file_name))
            if not _gn_path_within_root(path, library["real_path"]):
                errors.append({
                    "library": library["name"],
                    "path": path,
                    "error": "Skipped asset path outside the configured library root",
                })
                continue
            paths.append(path)
            if len(paths) >= _GN_MAX_CONFIGURED_LIBRARY_BLEND_FILES:
                errors.append({
                    "library": library["name"],
                    "path": root,
                    "error": (
                        "Configured library scan was truncated at "
                        f"{_GN_MAX_CONFIGURED_LIBRARY_BLEND_FILES} .blend files"
                    ),
                })
                return paths, errors
    return paths, errors

def _gn_user_node_asset_catalog(library_filter=""):
    """Inspect configured user asset libraries with bounded disposable loads."""
    filter_text = str(library_filter or "").strip().casefold()
    all_libraries = _gn_configured_asset_libraries()
    identity_matches = [
        item for item in all_libraries
        if filter_text and filter_text in " ".join((item["name"], item["path"])).casefold()
    ]
    libraries = identity_matches if identity_matches else all_libraries
    path_records = []
    errors = []
    for library in libraries:
        paths, path_errors = _gn_configured_library_blend_paths(library)
        errors.extend(path_errors)
        path_records.extend((path, library) for path in paths)

    metadata = []
    for path, library in path_records:
        try:
            metadata.append((path, os.path.getsize(path), int(os.path.getmtime(path)), library["name"]))
        except OSError as exc:
            errors.append({
                "library": library["name"],
                "path": path,
                "error": f"{type(exc).__name__}: {exc}",
            })
    key = (_gn_node_catalog_cache_key(), tuple(metadata))
    cached = _GN_USER_ASSET_CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    valid_paths = {item[0] for item in metadata}
    records = []
    for path, library in path_records:
        if path not in valid_paths:
            continue
        try:
            records.extend(_gn_load_node_asset_library(
                path,
                source_scope="USER",
                configured_library={
                    "name": library["name"],
                    "path": library["path"],
                    "import_method": library["import_method"],
                },
            ))
        except _GNAssetCleanupError:
            raise
        except Exception as exc:
            errors.append({
                "library": library["name"],
                "path": path,
                "error": f"{type(exc).__name__}: {exc}",
            })
    records.sort(key=lambda item: (
        item["name"].casefold(),
        item["configured_library"].get("name", "").casefold(),
        item["source_path"],
    ))
    result = {
        "records": records,
        "errors": errors,
        "library_paths": [item[0] for item in path_records],
        "configured_libraries": [
            {key: value for key, value in item.items() if key != "real_path"}
            for item in libraries
        ],
    }
    _GN_USER_ASSET_CATALOG_CACHE.clear()
    _GN_USER_ASSET_CATALOG_CACHE[key] = result
    return result

def _gn_node_asset_summary(record):
    inputs = []
    outputs = []
    panels = []
    for item in record["interface"]:
        if item["item_type"] == "PANEL":
            panels.append(item["name"])
        elif item.get("in_out") == "INPUT":
            inputs.append(item["name"])
        elif item.get("in_out") == "OUTPUT":
            outputs.append(item["name"])
    return {
        key: record[key]
        for key in (
            "name", "description", "author", "catalog_id", "tags",
            "tree_type", "source_scope", "source_library", "source_path",
            "configured_library", "node_count", "link_count",
            "interface_item_count", "dependencies",
        )
    } | {
        "interface_summary": {
            "inputs": inputs,
            "outputs": outputs,
            "panels": panels,
        },
    }

def _gn_node_asset_catalog_for_scope(scope, library=""):
    scope_value = str(scope or "USER").strip().upper()
    if scope_value not in _GN_ASSET_SCOPES:
        raise ValueError("scope must be 'ESSENTIALS', 'USER', or 'ALL'")
    catalogs = []
    if scope_value in {"ESSENTIALS", "ALL"}:
        catalogs.append(_gn_official_node_asset_catalog())
    if scope_value in {"USER", "ALL"}:
        catalogs.append(_gn_user_node_asset_catalog(library))
    return scope_value, {
        "records": [item for catalog in catalogs for item in catalog["records"]],
        "errors": [item for catalog in catalogs for item in catalog["errors"]],
    }

def _gn_import_node_asset(
    source_path,
    asset_name,
    *,
    tree_type="",
    scope="USER",
    library="",
    conflict_policy="REJECT",
):
    if not isinstance(source_path, str) or not source_path.strip():
        raise ValueError("source_path must be a non-empty string from asset search")
    if not isinstance(asset_name, str) or not asset_name.strip():
        raise ValueError("asset_name must be a non-empty string")
    asset_name = asset_name.strip()
    tree_type = str(tree_type or "").strip()
    if tree_type and tree_type not in NODE_TREE_TYPES:
        raise ValueError(
            "tree_type must be GeometryNodeTree, ShaderNodeTree, or CompositorNodeTree"
        )
    policy = str(conflict_policy or "REJECT").strip().upper()
    if policy not in {"REJECT", "RENAME"}:
        raise ValueError("conflict_policy must be 'REJECT' or 'RENAME'")

    requested_path = os.path.normcase(os.path.realpath(os.path.abspath(source_path)))
    scope_value, catalog = _gn_node_asset_catalog_for_scope(scope, library)
    matches = [
        record for record in catalog["records"]
        if os.path.normcase(os.path.realpath(record["source_path"])) == requested_path
        and record["name"] == asset_name
        and (not tree_type or record["tree_type"] == tree_type)
    ]
    if not matches:
        raise ValueError(
            "Asset identity was not found in the requested configured/bundled "
            "catalog. Run search_blender_node_assets and pass its exact "
            "source_path, name, tree_type, scope, and library identity."
        )
    if len(matches) > 1:
        raise ValueError("Asset identity is ambiguous across the requested catalogs")
    record = matches[0]

    existing = bpy.data.node_groups.get(asset_name)
    if existing is not None and policy == "REJECT":
        return {
            "schema": BLENDER_NODE_ASSET_IMPORT_SCHEMA,
            "status": "rejected",
            "imported": False,
            "mutated": False,
            "asset": _gn_node_asset_summary(record),
            "node_group": None,
            "diagnostics": [_gn_patch_diagnostic(
                "error",
                "node_group_name_conflict",
                "/conflict_policy",
                f"Node group {asset_name!r} already exists. Use conflict_policy "
                "'RENAME' to import a distinct local copy.",
            )],
        }

    before = _gn_blend_data_ids()
    try:
        try:
            loader = bpy.data.libraries.load(
                record["source_path"], link=False, assets_only=True
            )
        except TypeError:
            loader = bpy.data.libraries.load(record["source_path"], link=False)
        with loader as (data_from, data_to):
            if asset_name not in data_from.node_groups:
                raise RuntimeError(
                    f"Node asset {asset_name!r} disappeared from {record['source_path']!r}"
                )
            data_to.node_groups = [asset_name]
        imported = data_to.node_groups[0] if data_to.node_groups else None
        if imported is None or imported.bl_idname != record["tree_type"]:
            raise RuntimeError("Blender did not append the requested node asset")
        imported["blender_mcp_asset_name"] = record["name"]
        imported["blender_mcp_asset_source_path"] = os.path.normpath(
            record["source_path"]
        )
        imported["blender_mcp_asset_source_scope"] = record["source_scope"]
        after = _gn_blend_data_ids()
        imported_ids = [
            item for pointer, item in after.items() if pointer not in before
        ]
        if imported not in imported_ids:
            raise RuntimeError("Imported node group was not isolated as a new datablock")
        return {
            "schema": BLENDER_NODE_ASSET_IMPORT_SCHEMA,
            "status": "imported",
            "imported": True,
            "mutated": True,
            "asset": _gn_node_asset_summary(record),
            "node_group": {
                "name": imported.name,
                "tree_type": imported.bl_idname,
                "library": _node_id_library(imported),
                "editable": _node_id_editable(imported),
            },
            "imported_ids": [
                {
                    "id_type": item.bl_rna.identifier,
                    "name": item.name,
                }
                for item in sorted(
                    imported_ids,
                    key=lambda item: (item.bl_rna.identifier, item.name),
                )
            ],
            "import_method": "APPEND_LOCAL",
            "conflict_policy": policy,
            "scope": scope_value,
            "diagnostics": [],
        }
    except Exception as exc:
        appended = [
            item for pointer, item in _gn_blend_data_ids().items()
            if pointer not in before
        ]
        cleanup_error = None
        if appended:
            try:
                bpy.data.batch_remove(ids=appended)
            except Exception as rollback_exc:
                cleanup_error = rollback_exc
        remaining = [
            item for pointer, item in _gn_blend_data_ids().items()
            if pointer not in before
        ]
        if remaining or cleanup_error is not None:
            details = ", ".join(
                f"{item.bl_rna.identifier}/{item.name}" for item in remaining[:10]
            )
            raise RuntimeError(
                f"Asset import failed and rollback was incomplete: {exc}; "
                f"cleanup={cleanup_error}; remaining={details}"
            ) from exc
        raise RuntimeError(f"Asset import failed and was rolled back: {exc}") from exc

def _gn_export_node_asset(
    source_path,
    asset_name,
    *,
    tree_type="",
    scope="USER",
    library="",
    view="auto",
    node_names=None,
    neighbor_depth=0,
):
    """Inspect one exact asset through a disposable library load."""
    requested_path = os.path.normcase(os.path.realpath(os.path.abspath(source_path)))
    _scope_value, catalog = _gn_node_asset_catalog_for_scope(scope, library)
    matches = [
        record for record in catalog["records"]
        if os.path.normcase(os.path.realpath(record["source_path"])) == requested_path
        and record["name"] == asset_name
        and (not tree_type or record["tree_type"] == tree_type)
    ]
    if len(matches) != 1:
        raise ValueError(
            "Asset identity must match exactly one current catalog result; run "
            "search_blender_node_assets and reuse its exact fields"
        )
    record = matches[0]
    before = _gn_blend_data_ids()
    snapshot = None
    cleanup_error = None
    try:
        try:
            loader = bpy.data.libraries.load(record["source_path"], link=False, assets_only=True)
        except TypeError:
            loader = bpy.data.libraries.load(record["source_path"], link=False)
        with loader as (data_from, data_to):
            if asset_name not in data_from.node_groups:
                raise RuntimeError("The selected asset disappeared from its library")
            data_to.node_groups = [asset_name]
        tree = data_to.node_groups[0] if data_to.node_groups else None
        if tree is None or tree.bl_idname != record["tree_type"]:
            raise RuntimeError("Blender did not load the selected node asset")
        snapshot = _node_export_tree(
            tree,
            view,
            node_names or [],
            neighbor_depth,
            schema=NODE_TREE_SNAPSHOT_SCHEMA,
        )
    finally:
        appended = [
            item for pointer, item in _gn_blend_data_ids().items()
            if pointer not in before
        ]
        if appended:
            try:
                bpy.data.batch_remove(ids=appended)
            except Exception as error:
                cleanup_error = error
        remaining = [
            item for pointer, item in _gn_blend_data_ids().items()
            if pointer not in before
        ]
        if remaining or cleanup_error:
            names = ", ".join(item.name for item in remaining[:10])
            raise _GNAssetCleanupError(
                f"Disposable node-asset export cleanup failed: {cleanup_error}; remaining={names}"
            )
    return {
        "schema": BLENDER_NODE_ASSET_EXPORT_SCHEMA,
        "asset": _gn_node_asset_summary(record),
        "snapshot": _node_soft_limit_response(snapshot),
        "cleanup": {"appended_datablocks_remaining": 0, "file_mutated": False},
    }
