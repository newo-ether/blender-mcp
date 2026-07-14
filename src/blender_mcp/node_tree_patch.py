"""Pure-Python contracts for generic owner-addressed node-tree patches."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from .node_tree_schema import (
    NodeTreeSchemaError,
    resolve_workspace_json_path,
    validate_tree_ref,
)


PATCH_SCHEMA = "blender-node-tree-patch/1"
PATCH_VALIDATION_SCHEMA = "blender-node-tree-patch-validation/1"
PATCH_APPLICATION_SCHEMA = "blender-node-tree-patch-application/1"
SUPPORTED_CAPABILITIES = frozenset({
    "graph", "layout", "interface", "annotation", "dynamic", "id_reference",
})
SUPPORTED_OPERATIONS = frozenset({
    "add_node",
    "remove_node",
    "rename_node",
    "set_node_property",
    "set_socket_default",
    "add_link",
    "remove_link",
    "set_node_layout",
    "set_annotation",
    "add_interface_socket",
    "remove_interface_socket",
    "set_color_ramp",
    "set_curve_mapping",
})
MAX_OPERATIONS = 500
MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_ANNOTATION_CHARS = 16384

_REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOCKET_ID_PATTERN = re.compile(r"^(input|output):[0-9]+:.+$")
_TOP_LEVEL_FIELDS = {
    "schema", "tree_ref", "base_revision", "capabilities", "operations",
}
_OPERATION_FIELDS = {
    "add_node": ({"op", "id", "node_type"}, {"name", "properties", "layout"}),
    "remove_node": ({"op", "node"}, set()),
    "rename_node": ({"op", "node", "name"}, set()),
    "set_node_property": ({"op", "node", "property", "value"}, set()),
    "set_socket_default": ({"op", "node", "socket", "value"}, set()),
    "add_link": (
        {"op", "from_node", "from_socket", "to_node", "to_socket"}, set()
    ),
    "remove_link": (
        {"op", "from_node", "from_socket", "to_node", "to_socket"}, set()
    ),
    "set_node_layout": (
        {"op", "node"}, {"location", "width", "height", "parent"}
    ),
    "set_annotation": ({"op", "node", "text"}, set()),
    "add_interface_socket": (
        {"op", "id", "name", "in_out", "socket_type"}, {"parent", "default"}
    ),
    "remove_interface_socket": ({"op", "identifier"}, set()),
    "set_color_ramp": ({"op", "node", "elements"}, {"interpolation"}),
    "set_curve_mapping": ({"op", "node", "curves"}, {"use_clip"}),
}
_OPERATION_CAPABILITY = {
    "add_node": "graph",
    "remove_node": "graph",
    "rename_node": "graph",
    "set_node_property": "graph",
    "set_socket_default": "graph",
    "add_link": "graph",
    "remove_link": "graph",
    "set_node_layout": "layout",
    "set_annotation": "annotation",
    "add_interface_socket": "interface",
    "remove_interface_socket": "interface",
    "set_color_ramp": "dynamic",
    "set_curve_mapping": "dynamic",
}
_STRING_FIELDS = {
    "id", "node_type", "name", "node", "property", "socket", "from_node",
    "from_socket", "to_node", "to_socket", "identifier", "in_out",
    "socket_type", "interpolation", "handle_type",
}


class NodeTreePatchError(NodeTreeSchemaError):
    """Raised with stable diagnostics when a generic patch is invalid."""

    def __init__(self, diagnostics: list[dict[str, str]]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(item["message"] for item in diagnostics))


def diagnostic(code: str, path: str, message: str) -> dict[str, str]:
    return {"severity": "error", "code": code, "path": path, "message": message}


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _require_string(value: Any, path: str, diagnostics: list[dict[str, str]]) -> None:
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(diagnostic("invalid_string", path, "Expected a non-empty string"))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_finite_json_value(
    value: Any,
    path: str,
    diagnostics: list[dict[str, str]],
) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return False
    if isinstance(value, float):
        if not math.isfinite(value):
            diagnostics.append(diagnostic(
                "non_finite_number", path, "Patch numbers must be finite"
            ))
        return False
    if isinstance(value, list):
        found_id = False
        for index, item in enumerate(value):
            found_id = _validate_finite_json_value(
                item, f"{path}/{index}", diagnostics
            ) or found_id
        return found_id
    if isinstance(value, Mapping):
        value_type = value.get("$type")
        if value_type == "ID":
            allowed = {"$type", "id_type", "name", "library"}
            for field in sorted(set(value) - allowed):
                diagnostics.append(diagnostic(
                    "unknown_field", f"{path}/{_pointer_token(str(field))}",
                    f"Unknown typed ID field: {field}",
                ))
            _require_string(value.get("id_type"), f"{path}/id_type", diagnostics)
            _require_string(value.get("name"), f"{path}/name", diagnostics)
            if value.get("library") is not None and not isinstance(value.get("library"), str):
                diagnostics.append(diagnostic(
                    "invalid_string", f"{path}/library", "library must be a string or null"
                ))
            return True
        if value_type == "NodeRef":
            allowed = {"$type", "name"}
            for field in sorted(set(value) - allowed):
                diagnostics.append(diagnostic(
                    "unknown_field", f"{path}/{_pointer_token(str(field))}",
                    f"Unknown NodeRef field: {field}",
                ))
            _require_string(value.get("name"), f"{path}/name", diagnostics)
            return False
        diagnostics.append(diagnostic(
            "unsupported_object_value", path,
            "Object values must be typed as ID or NodeRef",
        ))
        return False
    diagnostics.append(diagnostic(
        "unsupported_value", path, f"Unsupported JSON value type: {type(value).__name__}"
    ))
    return False


def _validate_layout(
    operation: Mapping[str, Any], path: str, diagnostics: list[dict[str, str]]
) -> None:
    if not any(key in operation for key in ("location", "width", "height", "parent")):
        diagnostics.append(diagnostic(
            "empty_layout_operation", path,
            "set_node_layout requires at least one layout field",
        ))
    if "location" in operation:
        location = operation["location"]
        if (
            not isinstance(location, list)
            or len(location) != 2
            or any(not _is_number(item) or not math.isfinite(float(item)) for item in location)
        ):
            diagnostics.append(diagnostic(
                "invalid_location", f"{path}/location",
                "location must contain two finite numbers",
            ))
    for field in ("width", "height"):
        if field in operation and (
            not _is_number(operation[field])
            or not math.isfinite(float(operation[field]))
        ):
            diagnostics.append(diagnostic(
                "invalid_number", f"{path}/{field}",
                f"{field} must be a finite number",
            ))
    if operation.get("parent") is not None:
        _require_string(operation.get("parent"), f"{path}/parent", diagnostics)


def _validate_color_ramp(
    operation: Mapping[str, Any], path: str, diagnostics: list[dict[str, str]]
) -> None:
    elements = operation.get("elements")
    if not isinstance(elements, list) or not 2 <= len(elements) <= 32:
        diagnostics.append(diagnostic(
            "invalid_color_ramp_elements", f"{path}/elements",
            "elements must contain 2 to 32 Color Ramp elements",
        ))
        return
    positions = []
    for index, element in enumerate(elements):
        item_path = f"{path}/elements/{index}"
        if not isinstance(element, Mapping):
            diagnostics.append(diagnostic(
                "invalid_color_ramp_element", item_path, "Element must be an object"
            ))
            continue
        for field in sorted(set(element) - {"position", "color"}):
            diagnostics.append(diagnostic(
                "unknown_field", f"{item_path}/{_pointer_token(str(field))}",
                f"Unknown Color Ramp field: {field}",
            ))
        position = element.get("position")
        color = element.get("color")
        if not _is_number(position) or not math.isfinite(float(position)) or not 0 <= position <= 1:
            diagnostics.append(diagnostic(
                "invalid_ramp_position", f"{item_path}/position",
                "position must be a finite number from 0 to 1",
            ))
        else:
            positions.append(float(position))
        if (
            not isinstance(color, list)
            or len(color) != 4
            or any(not _is_number(item) or not math.isfinite(float(item)) for item in color)
        ):
            diagnostics.append(diagnostic(
                "invalid_ramp_color", f"{item_path}/color",
                "color must contain four finite numbers",
            ))
    if len(positions) == len(elements) and any(
        right <= left for left, right in zip(positions, positions[1:])
    ):
        diagnostics.append(diagnostic(
            "unordered_ramp_positions", f"{path}/elements",
            "Color Ramp positions must be strictly increasing",
        ))


def _validate_curve_mapping(
    operation: Mapping[str, Any], path: str, diagnostics: list[dict[str, str]]
) -> None:
    curves = operation.get("curves")
    if not isinstance(curves, list) or not 1 <= len(curves) <= 8:
        diagnostics.append(diagnostic(
            "invalid_curves", f"{path}/curves", "curves must contain 1 to 8 curves"
        ))
        return
    for curve_index, curve in enumerate(curves):
        curve_path = f"{path}/curves/{curve_index}"
        if not isinstance(curve, Mapping) or set(curve) != {"points"}:
            diagnostics.append(diagnostic(
                "invalid_curve", curve_path, "Each curve must contain only points"
            ))
            continue
        points = curve.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 64:
            diagnostics.append(diagnostic(
                "invalid_curve_points", f"{curve_path}/points",
                "points must contain 2 to 64 points",
            ))
            continue
        x_positions = []
        for point_index, point in enumerate(points):
            point_path = f"{curve_path}/points/{point_index}"
            if not isinstance(point, Mapping):
                diagnostics.append(diagnostic(
                    "invalid_curve_point", point_path, "Point must be an object"
                ))
                continue
            for field in sorted(set(point) - {"location", "handle_type"}):
                diagnostics.append(diagnostic(
                    "unknown_field", f"{point_path}/{_pointer_token(str(field))}",
                    f"Unknown curve-point field: {field}",
                ))
            location = point.get("location")
            if (
                not isinstance(location, list)
                or len(location) != 2
                or any(not _is_number(item) or not math.isfinite(float(item)) for item in location)
            ):
                diagnostics.append(diagnostic(
                    "invalid_curve_location", f"{point_path}/location",
                    "location must contain two finite numbers",
                ))
            else:
                x_positions.append(float(location[0]))
            if "handle_type" in point:
                _require_string(
                    point.get("handle_type"), f"{point_path}/handle_type", diagnostics
                )
        if len(x_positions) == len(points) and any(
            right <= left for left, right in zip(x_positions, x_positions[1:])
        ):
            diagnostics.append(diagnostic(
                "unordered_curve_points", f"{curve_path}/points",
                "Curve point X positions must be strictly increasing",
            ))


def validate_patch_structure(patch: Any) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if not isinstance(patch, Mapping):
        return [diagnostic("invalid_patch", "", "Patch must be a JSON object")]
    for field in sorted(set(patch) - _TOP_LEVEL_FIELDS):
        diagnostics.append(diagnostic(
            "unknown_field", f"/{_pointer_token(str(field))}",
            f"Unknown patch field: {field}",
        ))
    for field in ("schema", "tree_ref", "base_revision", "capabilities", "operations"):
        if field not in patch:
            diagnostics.append(diagnostic(
                "missing_field", f"/{field}", f"Required field is missing: {field}"
            ))
    if patch.get("schema") != PATCH_SCHEMA:
        diagnostics.append(diagnostic(
            "unsupported_schema", "/schema", f"Expected patch schema {PATCH_SCHEMA!r}"
        ))
    try:
        validate_tree_ref(patch.get("tree_ref"))
    except NodeTreeSchemaError as exc:
        diagnostics.append(diagnostic("invalid_tree_ref", "/tree_ref", str(exc)))
    revision = patch.get("base_revision")
    if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
        diagnostics.append(diagnostic(
            "invalid_revision", "/base_revision",
            "base_revision must be sha256 followed by 64 lowercase hex characters",
        ))
    capabilities = patch.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        diagnostics.append(diagnostic(
            "invalid_capabilities", "/capabilities",
            "capabilities must be a non-empty array",
        ))
        declared: set[str] = set()
    else:
        declared = set()
        for index, capability in enumerate(capabilities):
            if capability not in SUPPORTED_CAPABILITIES:
                diagnostics.append(diagnostic(
                    "unsupported_capability", f"/capabilities/{index}",
                    f"Unsupported capability: {capability!r}",
                ))
            elif capability in declared:
                diagnostics.append(diagnostic(
                    "duplicate_capability", f"/capabilities/{index}",
                    f"Capability is duplicated: {capability}",
                ))
            declared.add(capability)

    operations = patch.get("operations")
    if not isinstance(operations, list):
        diagnostics.append(diagnostic(
            "invalid_operations", "/operations", "operations must be an array"
        ))
        return diagnostics
    if not operations:
        diagnostics.append(diagnostic(
            "empty_operations", "/operations", "operations must not be empty"
        ))
    if len(operations) > MAX_OPERATIONS:
        diagnostics.append(diagnostic(
            "too_many_operations", "/operations",
            f"A patch may contain at most {MAX_OPERATIONS} operations",
        ))

    created_ids: set[str] = set()
    required_capabilities: set[str] = set()
    contains_id_reference = False
    for index, operation in enumerate(operations):
        path = f"/operations/{index}"
        if not isinstance(operation, Mapping):
            diagnostics.append(diagnostic(
                "invalid_operation", path, "Operation must be a JSON object"
            ))
            continue
        operation_name = operation.get("op")
        if operation_name not in SUPPORTED_OPERATIONS:
            diagnostics.append(diagnostic(
                "unsupported_operation", f"{path}/op",
                f"Unsupported operation: {operation_name!r}",
            ))
            continue
        required_capabilities.add(_OPERATION_CAPABILITY[operation_name])
        required, optional = _OPERATION_FIELDS[operation_name]
        for field in sorted(required - set(operation)):
            diagnostics.append(diagnostic(
                "missing_field", f"{path}/{field}",
                f"Required operation field is missing: {field}",
            ))
        for field in sorted(set(operation) - required - optional):
            diagnostics.append(diagnostic(
                "unknown_field", f"{path}/{_pointer_token(str(field))}",
                f"Unknown field for {operation_name}: {field}",
            ))
        for field in sorted((set(operation) & _STRING_FIELDS) - {"op"}):
            _require_string(operation[field], f"{path}/{field}", diagnostics)

        if operation_name == "add_node":
            reference = operation.get("id")
            if isinstance(reference, str):
                if reference in created_ids:
                    diagnostics.append(diagnostic(
                        "duplicate_created_id", f"{path}/id",
                        f"Created node id is duplicated: {reference}",
                    ))
                created_ids.add(reference)
            properties = operation.get("properties", {})
            if not isinstance(properties, Mapping):
                diagnostics.append(diagnostic(
                    "invalid_properties", f"{path}/properties",
                    "properties must be an object",
                ))
            else:
                for property_name, value in properties.items():
                    if not isinstance(property_name, str) or not property_name:
                        diagnostics.append(diagnostic(
                            "invalid_property_name", f"{path}/properties",
                            "Property names must be non-empty strings",
                        ))
                    contains_id_reference = _validate_finite_json_value(
                        value,
                        f"{path}/properties/{_pointer_token(str(property_name))}",
                        diagnostics,
                    ) or contains_id_reference
            if "layout" in operation:
                if not isinstance(operation["layout"], Mapping):
                    diagnostics.append(diagnostic(
                        "invalid_layout", f"{path}/layout", "layout must be an object"
                    ))
                else:
                    _validate_layout(
                        {"node": reference, **operation["layout"]},
                        f"{path}/layout", diagnostics,
                    )
        elif operation_name == "set_node_property":
            contains_id_reference = _validate_finite_json_value(
                operation.get("value"), f"{path}/value", diagnostics
            ) or contains_id_reference
        elif operation_name == "set_socket_default":
            contains_id_reference = _validate_finite_json_value(
                operation.get("value"), f"{path}/value", diagnostics
            ) or contains_id_reference
            value = operation.get("socket")
            if isinstance(value, str) and (
                not _SOCKET_ID_PATTERN.fullmatch(value)
                or not value.startswith("input:")
            ):
                diagnostics.append(diagnostic(
                    "invalid_socket_id", f"{path}/socket",
                    "Socket defaults require input:<index>:<identifier>",
                ))
        elif operation_name in {"add_link", "remove_link"}:
            for field, direction in (("from_socket", "output"), ("to_socket", "input")):
                value = operation.get(field)
                if isinstance(value, str) and (
                    not _SOCKET_ID_PATTERN.fullmatch(value)
                    or not value.startswith(direction + ":")
                ):
                    diagnostics.append(diagnostic(
                        "invalid_socket_id", f"{path}/{field}",
                        f"{field} must use {direction}:<index>:<identifier>",
                    ))
        elif operation_name == "set_node_layout":
            _validate_layout(operation, path, diagnostics)
        elif operation_name == "set_annotation":
            text = operation.get("text")
            if not isinstance(text, str) or len(text) > MAX_ANNOTATION_CHARS:
                diagnostics.append(diagnostic(
                    "invalid_annotation", f"{path}/text",
                    f"text must be a string no longer than {MAX_ANNOTATION_CHARS} characters",
                ))
        elif operation_name == "add_interface_socket":
            if operation.get("in_out") not in {"INPUT", "OUTPUT"}:
                diagnostics.append(diagnostic(
                    "invalid_direction", f"{path}/in_out",
                    "in_out must be INPUT or OUTPUT",
                ))
            if "default" in operation:
                contains_id_reference = _validate_finite_json_value(
                    operation["default"], f"{path}/default", diagnostics
                ) or contains_id_reference
        elif operation_name == "set_color_ramp":
            _validate_color_ramp(operation, path, diagnostics)
        elif operation_name == "set_curve_mapping":
            _validate_curve_mapping(operation, path, diagnostics)
            if "use_clip" in operation and not isinstance(operation["use_clip"], bool):
                diagnostics.append(diagnostic(
                    "invalid_boolean", f"{path}/use_clip", "use_clip must be boolean"
                ))

    if contains_id_reference:
        required_capabilities.add("id_reference")
    for capability in sorted(required_capabilities - declared):
        diagnostics.append(diagnostic(
            "undeclared_capability", "/capabilities",
            f"Patch operations require undeclared capability: {capability}",
        ))
    return diagnostics


def assert_valid_patch(patch: Any) -> dict[str, Any]:
    diagnostics = validate_patch_structure(patch)
    if diagnostics:
        raise NodeTreePatchError(diagnostics)
    return deepcopy(dict(patch))


def read_patch_json(
    patch_path: str | Path,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    source = resolve_workspace_json_path(patch_path, workspace_root)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise NodeTreeSchemaError(f"Patch file not found: {source}") from exc
    if size > MAX_PATCH_BYTES:
        raise NodeTreeSchemaError(
            f"Patch file exceeds {MAX_PATCH_BYTES} bytes: {source}"
        )
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NodeTreeSchemaError(f"Unable to read patch JSON: {source}: {exc}") from exc
    return assert_valid_patch(value)
