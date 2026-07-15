"""Geometry Nodes modifier state capture and restoration."""

from __future__ import annotations

import bpy

from .common import _gn_rna_property


def _gn_modifier_input_value(modifier, identifier):
    """Read one modifier input through the active Blender-version adapter."""
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            socket_value = getattr(properties.inputs, identifier)
            if hasattr(socket_value, "value"):
                return socket_value.value
        except (AttributeError, KeyError, TypeError, RuntimeError):
            pass
    try:
        if identifier in modifier.keys():
            return modifier[identifier]
    except (AttributeError, KeyError, TypeError, RuntimeError):
        pass
    raise KeyError(f"Modifier input has no restorable value: {identifier}")

def _gn_modifier_input_record(modifier, identifier):
    """Capture value/attribute state and animation paths for one input."""
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            socket_value = getattr(properties.inputs, identifier)
            fields = {}
            data_paths = {}
            for field in ("type", "attribute_name", "value"):
                if not hasattr(socket_value, field):
                    continue
                try:
                    fields[field] = getattr(socket_value, field)
                except (AttributeError, KeyError, TypeError, RuntimeError):
                    continue
                try:
                    data_paths[field] = socket_value.path_from_id(field)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass
            return {
                "adapter": "geometry_nodes_modifier_interface",
                "fields": fields,
                "data_paths": data_paths,
            }
        except (AttributeError, KeyError, TypeError, RuntimeError):
            pass

    fields = {}
    data_paths = {}
    candidate_keys = (
        identifier,
        f"{identifier}_use_attribute",
        f"{identifier}_attribute_name",
    )
    try:
        keys = set(modifier.keys())
    except (AttributeError, TypeError, RuntimeError):
        keys = set()
    for key in candidate_keys:
        if key not in keys:
            continue
        fields[key] = modifier[key]
        data_paths[key] = f'["{key}"]'
    if identifier not in fields:
        raise KeyError(f"Modifier input has no restorable value: {identifier}")
    return {
        "adapter": "legacy_id_property",
        "fields": fields,
        "data_paths": data_paths,
    }

def _gn_restore_modifier_input_record(modifier, identifier, record):
    adapter = record.get("adapter") if isinstance(record, dict) else None
    fields = record.get("fields", {}) if isinstance(record, dict) else {}
    if adapter == "geometry_nodes_modifier_interface":
        properties = getattr(modifier, "properties", None)
        if properties is None:
            raise RuntimeError("Geometry Nodes modifier interface is unavailable")
        socket_value = getattr(properties.inputs, identifier)
        for field in ("type", "attribute_name", "value"):
            if field not in fields or not hasattr(socket_value, field):
                continue
            prop = _gn_rna_property(socket_value, field)
            if prop is not None and getattr(prop, "is_readonly", False):
                continue
            setattr(socket_value, field, fields[field])
        return adapter
    if adapter == "legacy_id_property":
        for key, value in fields.items():
            modifier[key] = value
        return adapter
    # Compatibility for pre-adapter in-memory state records.
    _gn_set_modifier_input_value(modifier, identifier, record)
    return "legacy_state_value"

def _gn_set_modifier_input_value(modifier, identifier, value):
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            socket_value = getattr(properties.inputs, identifier)
            socket_value.value = value
            return "geometry_nodes_modifier_interface"
        except (AttributeError, KeyError, TypeError, RuntimeError):
            pass
    modifier[identifier] = value
    return "legacy_id_property"

def _gn_modifier_state(modifier, tree):
    state = {}
    for item in tree.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT":
            continue
        identifier = getattr(item, "identifier", "") or item.name
        try:
            state[identifier] = _gn_modifier_input_record(modifier, identifier)
        except (AttributeError, KeyError, TypeError, RuntimeError):
            continue
    return state

def _gn_restore_modifier_state(modifier, state):
    errors = []
    for identifier, value in state.items():
        try:
            _gn_restore_modifier_input_record(modifier, identifier, value)
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            errors.append(f"{identifier}: {type(exc).__name__}: {exc}")
    return errors

def _gn_user_handle(user):
    if user["kind"] == "MODIFIER":
        obj = bpy.data.objects.get(user["object"])
        modifier = obj.modifiers.get(user["modifier"]) if obj else None
        return modifier
    owner_tree = bpy.data.node_groups.get(user["tree"])
    return owner_tree.nodes.get(user["node"]) if owner_tree else None
