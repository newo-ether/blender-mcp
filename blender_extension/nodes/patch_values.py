"""Patch value decoding and socket resolution."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic, _gn_rna_property
from .targets import _node_id_library


def _gn_resolve_id_reference(value):
    if not isinstance(value, dict) or value.get("$type") != "ID":
        return None
    id_type = value.get("id_type")
    name = value.get("name")
    collections = {
        "Object": bpy.data.objects,
        "Collection": bpy.data.collections,
        "Material": bpy.data.materials,
        "World": bpy.data.worlds,
        "Light": bpy.data.lights,
        "Image": bpy.data.images,
        "MovieClip": bpy.data.movieclips,
        "Mask": bpy.data.masks,
        "Scene": bpy.data.scenes,
        "Texture": bpy.data.textures,
        "GeometryNodeTree": bpy.data.node_groups,
        "ShaderNodeTree": bpy.data.node_groups,
        "CompositorNodeTree": bpy.data.node_groups,
        "NodeTree": bpy.data.node_groups,
    }
    collection = collections.get(id_type)
    resolved = collection.get(name) if collection is not None and isinstance(name, str) else None
    expected_library = value.get("library")
    if resolved is not None and expected_library is not None:
        actual_library = _node_id_library(resolved)
        if actual_library != expected_library:
            return None
    return resolved

def _gn_decode_patch_value(value, node_refs=None):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_gn_decode_patch_value(item, node_refs) for item in value]
    if isinstance(value, dict):
        value_type = value.get("$type")
        if value_type == "ID":
            resolved = _gn_resolve_id_reference(value)
            if resolved is None:
                raise ValueError(f"Blender ID not found: {value.get('id_type')}/{value.get('name')}")
            return resolved
        if value_type == "ViewLayer":
            scene_name = value.get("scene")
            layer_name = value.get("name")
            scene = bpy.data.scenes.get(scene_name) if isinstance(scene_name, str) else None
            layer = scene.view_layers.get(layer_name) if scene and isinstance(layer_name, str) else None
            if layer is None:
                raise ValueError(
                    f"View Layer not found: {scene_name}/{layer_name}"
                )
            return layer.name
        if value_type == "NodeRef" and node_refs is not None:
            reference = node_refs.get(value.get("name"))
            if reference and not reference["removed"]:
                return reference["node"]
            raise ValueError(f"Node reference not found: {value.get('name')}")
    raise ValueError(f"Unsupported typed patch value: {value!r}")

def _gn_validate_value(
    owner, property_name, value, path, diagnostics, node_refs=None, property_path=None,
):
    property_path = property_path or path
    prop = _gn_rna_property(owner, property_name)
    if prop is None:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "unknown_rna_property", property_path,
            f"RNA property {property_name!r} does not exist on {owner.bl_rna.identifier}",
        ))
        return False
    if getattr(prop, "is_readonly", False):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "readonly_rna_property", property_path,
            f"RNA property {property_name!r} is read-only",
        ))
        return False
    if prop.type == "COLLECTION":
        diagnostics.append(_gn_patch_diagnostic(
            "error", "unsupported_rna_collection", property_path,
            f"RNA collection property {property_name!r} is not patchable",
        ))
        return False

    array_length = int(getattr(prop, "array_length", 0))
    if array_length:
        if not isinstance(value, list) or len(value) != array_length:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "invalid_array_value", path,
                f"Expected an array with {array_length} values",
            ))
            return False
        scalar_type = "FLOAT" if prop.type == "FLOAT" else "INT"
        valid = all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            if scalar_type == "FLOAT"
            else isinstance(item, int) and not isinstance(item, bool)
            for item in value
        )
        if not valid:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "invalid_array_item", path,
                f"Expected {scalar_type.lower()} array values",
            ))
            return False
        hard_min = getattr(prop, "hard_min", None)
        hard_max = getattr(prop, "hard_max", None)
        if hard_min is not None and hard_max is not None and any(
            item < hard_min or item > hard_max for item in value
        ):
            diagnostics.append(_gn_patch_diagnostic(
                "error", "rna_value_out_of_range", path,
                f"Array values must be between {hard_min} and {hard_max}",
            ))
            return False
        return True

    valid = True
    if prop.type == "BOOLEAN":
        valid = isinstance(value, bool)
    elif prop.type == "INT":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif prop.type == "FLOAT":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif prop.type == "STRING":
        valid = isinstance(value, str)
    elif prop.type == "ENUM":
        enum_value = value
        is_view_layer_reference = (
            isinstance(value, dict) and value.get("$type") == "ViewLayer"
        )
        if is_view_layer_reference:
            try:
                enum_value = _gn_decode_patch_value(value, node_refs)
            except ValueError as exc:
                diagnostics.append(_gn_patch_diagnostic(
                    "error", "invalid_view_layer_reference", path, str(exc),
                ))
                return False
            valid = True
        else:
            try:
                identifiers = {item.identifier for item in prop.enum_items}
                valid = isinstance(enum_value, str) and enum_value in identifiers
                if not valid:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "invalid_enum_value", path,
                        f"Expected one of: {', '.join(sorted(identifiers))}",
                    ))
                    return False
            except (AttributeError, RuntimeError, TypeError):
                valid = isinstance(enum_value, str)
    elif prop.type == "POINTER":
        try:
            _gn_decode_patch_value(value, node_refs)
        except ValueError as exc:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "invalid_pointer_value", path, str(exc),
            ))
            return False

    if valid and prop.type in {"INT", "FLOAT"}:
        hard_min = getattr(prop, "hard_min", None)
        hard_max = getattr(prop, "hard_max", None)
        if hard_min is not None and value < hard_min:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "rna_value_out_of_range", path,
                f"Value must be at least {hard_min}",
            ))
            return False
        if hard_max is not None and value > hard_max:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "rna_value_out_of_range", path,
                f"Value must be at most {hard_max}",
            ))
            return False

    if not valid:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "invalid_rna_value", path,
            f"Value is incompatible with RNA type {prop.type}",
        ))
    return valid

def _gn_resolve_patch_node(node_refs, reference, path, diagnostics):
    item = node_refs.get(reference)
    if item is None:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "node_not_found", path,
            f"Node reference not found: {reference}. Within one patch, refer to "
            f"a node by the 'id' its add_node gave it; across patches those ids "
            f"are not valid — use the Blender node name returned in the previous "
            f"patch's 'created_nodes'.",
        ))
        return None
    if item["removed"]:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "node_already_removed", path, f"Node was already removed: {reference}",
        ))
        return None
    return item

def _gn_resolve_patch_socket(node, socket_id, expected_direction, path, diagnostics):
    try:
        direction, index_text, identifier = socket_id.split(":", 2)
        index = int(index_text)
    except (AttributeError, TypeError, ValueError):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "invalid_socket_id", path, f"Invalid socket id: {socket_id!r}",
        ))
        return None
    if direction != expected_direction:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "wrong_socket_direction", path,
            f"Expected a {expected_direction} socket, got {direction}",
        ))
        return None
    sockets = node.outputs if direction == "output" else node.inputs
    if not 0 <= index < len(sockets):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "socket_index_out_of_range", path,
            f"Socket index {index} is out of range for node {node.name!r}",
        ))
        return None
    socket = sockets[index]
    actual_identifier = getattr(socket, "identifier", "") or socket.name
    if identifier != actual_identifier:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "stale_socket_id", path,
            f"Socket index {index} now identifies {actual_identifier!r}, not {identifier!r}",
        ))
        return None
    return socket
