"""Compatibility facade for node tools now grouped by capability."""

from .automation import execute_blender_code, get_viewport_screenshot
from .geometry_nodes import (
    apply_geometry_node_patch,
    export_blender_node_asset,
    export_geometry_node_tree,
    get_geometry_node_tree_index,
    get_geometry_node_type_schema,
    import_blender_node_asset,
    list_geometry_node_trees,
    modify_verify_save,
    search_blender_node_assets,
    search_geometry_node_types,
    validate_geometry_node_patch,
)
from .node_trees import (
    apply_node_tree_patch,
    ensure_scene_compositor_tree,
    export_node_tree,
    get_node_editor_context,
    get_node_tree_index,
    get_node_type_schema,
    list_node_trees,
    query_node_graph,
    validate_node_tree_patch,
)

__all__ = [
    "apply_geometry_node_patch",
    "apply_node_tree_patch",
    "ensure_scene_compositor_tree",
    "execute_blender_code",
    "export_blender_node_asset",
    "export_geometry_node_tree",
    "export_node_tree",
    "get_node_editor_context",
    "get_geometry_node_tree_index",
    "get_geometry_node_type_schema",
    "get_node_tree_index",
    "get_node_type_schema",
    "get_viewport_screenshot",
    "import_blender_node_asset",
    "list_geometry_node_trees",
    "list_node_trees",
    "modify_verify_save",
    "query_node_graph",
    "search_blender_node_assets",
    "search_geometry_node_types",
    "validate_geometry_node_patch",
    "validate_node_tree_patch",
]
