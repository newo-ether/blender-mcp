from __future__ import annotations

GEOMETRY_NODES_SNAPSHOT_SCHEMA = "blender-geometry-nodes/1"

GEOMETRY_NODES_PATCH_SCHEMA = "blender-geometry-nodes-patch/1"

GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA = "blender-geometry-nodes-patch-validation/1"

GEOMETRY_NODES_VIEWS = {"slim", "semantic", "operations", "layout", "all"}

GEOMETRY_NODE_TYPE_SCHEMA_DETAILS = {"compact", "full"}

GEOMETRY_NODE_TYPE_CATALOG_SCHEMA = "blender-geometry-node-type-catalog/1"

BLENDER_NODE_ASSET_CATALOG_SCHEMA = "blender-node-asset-catalog/1"

BLENDER_NODE_ASSET_IMPORT_SCHEMA = "blender-node-asset-import/1"

BLENDER_NODE_ASSET_EXPORT_SCHEMA = "blender-node-asset-export/1"

BLENDER_RUNTIME_AUTOMATION_CONTEXT_SCHEMA = "blender-runtime-automation-context/1"

SCENE_COMPOSITOR_TREE_SCHEMA = "blender-scene-compositor-tree/1"

NODE_GROUP_CREATION_SCHEMA = "blender-node-group-creation/1"

GEOMETRY_NODES_MODIFIER_SCHEMA = "blender-geometry-nodes-modifier/1"
NODE_INTERFACE_PANEL_PROPERTIES = frozenset({
    "name",
    "description",
    "default_closed",
})
NODE_INTERFACE_SOCKET_PROPERTIES = frozenset({
    "name",
    "description",
    "hide_value",
    "default_value",
    "default_attribute_name",
    "attribute_domain",
    "default_input",
    "structure_type",
    "force_non_field",
    "min_value",
    "max_value",
})

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
    # Static display metadata declared at class registration time. These are
    # writable and non-hidden, so the non-default filter in _gn_operation_properties
    # would otherwise include them (their RNA prop.default is the base-type default
    # such as '' or 0.0, not the subclass value). They carry no per-instance state
    # and must not leak into operation/semantic snapshots or patch round-trips.
    "bl_label", "bl_description", "bl_icon",
    "bl_width_default", "bl_width_min", "bl_width_max",
    "bl_height_default", "bl_height_min", "bl_height_max",
}

# Additional properties dropped by the slim view. These are retained by
# operations/semantic/all because a patch may legitimately round-trip them, but
# they describe presentation or diagnostics rather than what a node computes, so
# the slim reading view omits them.
_GN_SLIM_NODE_PROPERTY_EXCLUDES = _GN_NODE_PROPERTY_EXCLUDES | {
    "warning_propagation",
}

# Node types the slim view omits entirely. Only types that carry no operation
# and participate in no link belong here: dropping a node that carries links
# would leave the slim adjacency referencing a node that is not in the record.
# NodeFrame qualifies (visual grouping only, no sockets). NodeReroute does NOT:
# it relays real links and must stay so paths remain traversable.
_GN_SLIM_NODE_TYPE_EXCLUDES = {
    "NodeFrame",
}
