from __future__ import annotations

GEOMETRY_NODES_SNAPSHOT_SCHEMA = "blender-geometry-nodes/1"

GEOMETRY_NODES_PATCH_SCHEMA = "blender-geometry-nodes-patch/1"

GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA = "blender-geometry-nodes-patch-validation/1"

GEOMETRY_NODES_VIEWS = {"semantic", "operations", "layout", "all"}

GEOMETRY_NODE_TYPE_SCHEMA_DETAILS = {"compact", "full"}

GEOMETRY_NODE_TYPE_CATALOG_SCHEMA = "blender-geometry-node-type-catalog/1"

BLENDER_NODE_ASSET_CATALOG_SCHEMA = "blender-node-asset-catalog/1"

BLENDER_NODE_ASSET_IMPORT_SCHEMA = "blender-node-asset-import/1"

BLENDER_NODE_ASSET_EXPORT_SCHEMA = "blender-node-asset-export/1"

BLENDER_RUNTIME_AUTOMATION_CONTEXT_SCHEMA = "blender-runtime-automation-context/1"

SCENE_COMPOSITOR_TREE_SCHEMA = "blender-scene-compositor-tree/1"

NODE_TREE_SNAPSHOT_SCHEMA = "blender-node-tree/1"

NODE_TREE_INDEX_SCHEMA = "blender-node-tree-index/1"

NODE_EDITOR_CONTEXT_SCHEMA = "blender-node-editor-context/1"

NODE_TYPE_SCHEMA = "blender-node-type-schema/1"

NODE_TREE_TYPES = {"GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree"}

NODE_TREE_OWNER_KINDS = {"MATERIAL", "WORLD", "LIGHT", "SCENE", "NODE_GROUP"}

NODE_TREE_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

NODE_TREE_SOFT_RESPONSE_BYTES = 512 * 1024

NODE_TREE_MAX_MUTATION_NODES = 10000

NODE_TREE_MAX_VALIDATION_SECONDS = 30.0

_GN_NODE_TYPE_CATALOG_CACHE = {}

_GN_ESSENTIALS_CATALOG_CACHE = {}

_GN_USER_ASSET_CATALOG_CACHE = {}

_GN_ASSET_SCOPES = {"ESSENTIALS", "USER", "ALL"}

_GN_MAX_CONFIGURED_LIBRARY_BLEND_FILES = 500

class _GNAssetCleanupError(RuntimeError):
    """Raised when disposable asset inspection cannot cleanly unwind."""

BLENDER_VERSION_CONTEXT_SCHEMA = "blender-version-context/1"

_GN_NODE_PROPERTY_EXCLUDES = {
    "rna_type", "name", "label", "location", "width", "width_hidden",
    "height", "dimensions", "parent", "select", "show_options",
    "show_preview", "show_texture", "use_custom_color", "color",
    "inputs", "outputs", "internal_links", "type", "bl_idname",
}
