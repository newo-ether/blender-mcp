"""Compatibility facade for split node patch validation and transactions."""

from .constants import (
    NODE_TREE_MAX_MUTATION_NODES,
    NODE_TREE_MAX_VALIDATION_SECONDS,
)
from .geometry_operations import (
    _gn_apply_operations_to_working,
)
from .geometry_transactions import (
    _gn_apply_patch_transaction,
    _gn_assign_user,
)
from .geometry_validation import (
    _gn_validate_patch_runtime,
)
from .modifiers import (
    _gn_modifier_input_record,
    _gn_modifier_input_value,
    _gn_modifier_state,
    _gn_restore_modifier_input_record,
    _gn_restore_modifier_state,
    _gn_set_modifier_input_value,
    _gn_user_handle,
)
from .node_transactions import (
    _node_apply_patch_transaction,
    _node_apply_scene_tree_transaction,
    _node_direct_user_pointers,
    _node_owner_collection,
)
from .node_validation import (
    _node_validate_patch_runtime,
)
from .patch_operations import (
    _node_add_paired_zone,
    _node_apply_color_ramp,
    _node_apply_curve_mapping,
    _node_dynamic_collection,
    _node_execute_patch_operations,
    _node_interface_mutable,
    _node_mutation_allowed,
    _node_remove_validation_copy,
    _node_validation_copy,
)
from .patch_values import (
    _gn_decode_patch_value,
    _gn_resolve_id_reference,
    _gn_resolve_patch_node,
    _gn_resolve_patch_socket,
    _gn_validate_value,
)
from .workflow import (
    _blendermcp_check_workflow_assertions,
)

__all__ = (
    "_gn_resolve_id_reference",
    "_gn_decode_patch_value",
    "_gn_validate_value",
    "_gn_resolve_patch_node",
    "_gn_resolve_patch_socket",
    "_gn_validate_patch_runtime",
    "_gn_modifier_input_value",
    "_gn_modifier_input_record",
    "_gn_restore_modifier_input_record",
    "_gn_set_modifier_input_value",
    "_gn_modifier_state",
    "_gn_restore_modifier_state",
    "_gn_user_handle",
    "_node_validation_copy",
    "_node_remove_validation_copy",
    "_node_interface_mutable",
    "_node_mutation_allowed",
    "_node_apply_color_ramp",
    "_node_apply_curve_mapping",
    "_node_dynamic_collection",
    "_node_add_paired_zone",
    "_node_execute_patch_operations",
    "_node_validate_patch_runtime",
    "_node_owner_collection",
    "_node_direct_user_pointers",
    "_node_apply_scene_tree_transaction",
    "_node_apply_patch_transaction",
    "_gn_apply_operations_to_working",
    "_gn_assign_user",
    "_gn_apply_patch_transaction",
    "_blendermcp_check_workflow_assertions",
    "NODE_TREE_MAX_MUTATION_NODES",
    "NODE_TREE_MAX_VALIDATION_SECONDS",
)
