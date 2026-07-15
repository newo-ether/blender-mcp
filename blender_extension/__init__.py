"""Blender MCP Extension composition root."""

from . import state

state.configure(__package__ or __name__)

from .automation import _blender_version_context
from .bridge.server import BlenderMCPServer
from .errors import BlenderMCPAddonError
from .nodes.constants import _GN_USER_ASSET_CATALOG_CACHE
from .nodes.export import (
    _gn_export_tree,
    _gn_node_record,
    _gn_socket_id,
    _node_ensure_scene_compositor_tree,
    _node_export_target,
    _node_resolve_tree_ref,
    _node_scene_tree,
)
from .nodes.patch import (
    _gn_apply_patch_transaction,
    _gn_modifier_input_record,
    _gn_modifier_input_value,
    _gn_set_modifier_input_value,
    _node_apply_patch_transaction,
)
from .nodes.schema import _gn_blend_data_ids
from .ui import register, unregister
from .version import BLENDER_MCP_ADDON_VERSION, __version__


def __getattr__(name):
    state_names = {
        "_BLENDER_MCP_INSTANCE_ID": "instance_id",
        "_BLENDER_MCP_FILE_SESSION_ID": "file_session_id",
        "_BLENDER_MCP_OVERLAY_HANDLE": "overlay_handle",
    }
    if name in state_names:
        return getattr(state, state_names[name])
    raise AttributeError(name)


__all__ = [
    "BLENDER_MCP_ADDON_VERSION",
    "BlenderMCPAddonError",
    "BlenderMCPServer",
    "__version__",
    "_GN_USER_ASSET_CATALOG_CACHE",
    "_blender_version_context",
    "_gn_apply_patch_transaction",
    "_gn_blend_data_ids",
    "_gn_export_tree",
    "_gn_modifier_input_record",
    "_gn_modifier_input_value",
    "_gn_node_record",
    "_gn_set_modifier_input_value",
    "_gn_socket_id",
    "_node_apply_patch_transaction",
    "_node_ensure_scene_compositor_tree",
    "_node_export_target",
    "_node_resolve_tree_ref",
    "_node_scene_tree",
    "register",
    "unregister",
]
