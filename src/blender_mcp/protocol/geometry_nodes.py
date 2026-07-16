"""Pure-Python contracts shared by Geometry Nodes MCP tools.

This module intentionally has no Blender or MCP imports so its canonicalization,
revision, and workspace-boundary behavior can be tested outside Blender.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

SNAPSHOT_SCHEMA = "blender-geometry-nodes/1"
PATCH_SCHEMA = "blender-geometry-nodes-patch/1"
PATCH_VALIDATION_SCHEMA = "blender-geometry-nodes-patch-validation/1"
PATCH_APPLICATION_SCHEMA = "blender-geometry-nodes-patch-application/1"
SUPPORTED_VIEWS = frozenset({"slim", "semantic", "operations", "layout", "all"})
SUPPORTED_PATCH_OPERATIONS = frozenset({
    "add_node",
    "remove_node",
    "rename_node",
    "set_node_property",
    "set_socket_default",
    "set_socket_hide",
    "add_link",
    "remove_link",
    "set_node_layout",
    "add_interface_panel",
    "add_interface_socket",
    "remove_interface_socket",
    "set_interface_item",
    "set_modifier_input",
    "add_dynamic_item",
    "remove_dynamic_item",
    "set_dynamic_item",
    "add_foreach_zone",
    "add_closure_zone",
})

_REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOCKET_ID_PATTERN = re.compile(r"^(input|output):[0-9]+:.+$")
_PATCH_TOP_LEVEL_FIELDS = {
    "schema", "tree_name", "base_revision", "operations", "shared_tree_policy", "target_user",
}
_PATCH_OPERATION_FIELDS = {
    "add_node": (
        {"op", "id", "node_type"},
        {"name", "properties", "layout"},
    ),
    "remove_node": ({"op", "node"}, set()),
    "rename_node": ({"op", "node", "name"}, set()),
    "set_node_property": ({"op", "node", "property", "value"}, set()),
    "set_socket_default": ({"op", "node", "socket", "value"}, set()),
    "set_socket_hide": ({"op", "node", "socket", "value"}, set()),
    "add_link": (
        {"op", "from_node", "from_socket", "to_node", "to_socket"},
        set(),
    ),
    "remove_link": (
        {"op", "from_node", "from_socket", "to_node", "to_socket"},
        set(),
    ),
    "set_node_layout": (
        {"op", "node"},
        {"location", "width", "height", "parent"},
    ),
    "add_interface_panel": (
        {"op", "id", "name"},
        {"description", "default_closed"},
    ),
    "add_interface_socket": (
        {"op", "id", "name", "in_out", "socket_type"},
        {"parent", "default"},
    ),
    "remove_interface_socket": ({"op", "identifier"}, set()),
    "set_interface_item": (
        {"op", "identifier", "property", "value"}, set()
    ),
    "set_modifier_input": (
        {"op", "object", "modifier", "socket", "value"},
        set(),
    ),
    "add_dynamic_item": (
        {"op", "node", "collection", "socket_type", "name"}, set()
    ),
    "remove_dynamic_item": (
        {"op", "node", "collection", "index"}, set()
    ),
    "set_dynamic_item": (
        {"op", "node", "collection", "index", "property", "value"}, set()
    ),
    "add_foreach_zone": (
        {"op", "input_id", "output_id"}, {"input_name", "output_name", "location"}
    ),
    "add_closure_zone": (
        {"op", "input_id", "output_id"}, {"input_name", "output_name", "location"}
    ),
}


class GeometryNodesSchemaError(ValueError):
    """Raised when a snapshot or export path violates the public contract."""


class GeometryNodesPatchError(GeometryNodesSchemaError):
    """Raised with stable diagnostics when a patch is structurally invalid."""

    def __init__(self, diagnostics: list[dict[str, str]]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(item["message"] for item in diagnostics))


def _diagnostic(code: str, path: str, message: str) -> dict[str, str]:
    return {
        "severity": "error",
        "code": code,
        "path": path,
        "message": message,
    }


def _format_field_set(names: Any) -> str:
    """Stable brace-set rendering of field names, e.g. '{op, node, socket, value}'."""
    return "{" + ", ".join(sorted(names)) + "}"


def _operation_fields_hint(operation_name: str, required: Any, optional: Any) -> str:
    """Self-explaining 'accepted fields' suffix for operation diagnostics.

    Embeds the full accepted-field list in missing/unknown_field messages so a
    caller can correct a patch from the error alone, without a separate schema
    lookup — the op-field naming (id vs node vs from_node) is otherwise opaque.
    """
    req = _format_field_set(required)
    if optional:
        return f"{operation_name} accepts required {req}, optional {_format_field_set(optional)}"
    return f"{operation_name} accepts {req}"


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _require_non_empty_string(
    value: Any,
    path: str,
    diagnostics: list[dict[str, str]],
) -> None:
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(
            _diagnostic("invalid_string", path, "Expected a non-empty string")
        )


def _validate_layout(
    operation: Mapping[str, Any],
    path: str,
    diagnostics: list[dict[str, str]],
) -> None:
    allowed_fields = {"op", "node", "location", "width", "height", "parent"}
    for field in sorted(set(operation) - allowed_fields):
        diagnostics.append(
            _diagnostic(
                "unknown_field",
                f"{path}/{_json_pointer_token(str(field))}",
                f"Unknown layout field: {field} (accepted: {_format_field_set(allowed_fields)})",
            )
        )
    if not any(key in operation for key in ("location", "width", "height", "parent")):
        diagnostics.append(
            _diagnostic(
                "empty_layout_operation",
                path,
                "set_node_layout requires at least one layout field",
            )
        )
    if "location" in operation:
        location = operation["location"]
        if (
            not isinstance(location, list)
            or len(location) != 2
            or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in location)
        ):
            diagnostics.append(
                _diagnostic(
                    "invalid_location",
                    f"{path}/location",
                    "location must be an array of two numbers",
                )
            )
    for key in ("width", "height"):
        if key in operation and (
            not isinstance(operation[key], (int, float)) or isinstance(operation[key], bool)
        ):
            diagnostics.append(
                _diagnostic("invalid_number", f"{path}/{key}", f"{key} must be a number")
            )
    if "parent" in operation and operation["parent"] is not None:
        _require_non_empty_string(operation["parent"], f"{path}/parent", diagnostics)


def validate_patch_structure(patch: Any) -> list[dict[str, str]]:
    """Return stable, JSON-pointer-addressed structural diagnostics."""
    diagnostics: list[dict[str, str]] = []
    if not isinstance(patch, Mapping):
        return [_diagnostic("invalid_patch", "", "Patch must be a JSON object")]

    unknown_top_level = sorted(set(patch) - _PATCH_TOP_LEVEL_FIELDS)
    for field in unknown_top_level:
        diagnostics.append(
            _diagnostic(
                "unknown_field",
                f"/{_json_pointer_token(str(field))}",
                f"Unknown patch field: {field} (accepted: {_format_field_set(_PATCH_TOP_LEVEL_FIELDS)})",
            )
        )
    for field in ("schema", "tree_name", "base_revision", "operations"):
        if field not in patch:
            diagnostics.append(
                _diagnostic("missing_field", f"/{field}", f"Required field is missing: {field}")
            )

    if patch.get("schema") != PATCH_SCHEMA:
        diagnostics.append(
            _diagnostic(
                "unsupported_schema",
                "/schema",
                f"Expected patch schema {PATCH_SCHEMA!r}",
            )
        )
    _require_non_empty_string(patch.get("tree_name"), "/tree_name", diagnostics)
    base_revision = patch.get("base_revision")
    if not isinstance(base_revision, str) or not _REVISION_PATTERN.fullmatch(base_revision):
        diagnostics.append(
            _diagnostic(
                "invalid_revision",
                "/base_revision",
                "base_revision must be sha256 followed by 64 lowercase hex characters",
            )
        )
    if patch.get("shared_tree_policy", "reject") not in {
        "reject", "single_user_copy", "mutate_shared",
    }:
        diagnostics.append(
            _diagnostic(
                "invalid_shared_tree_policy",
                "/shared_tree_policy",
                "shared_tree_policy must be reject, single_user_copy, or mutate_shared",
            )
        )

    policy = patch.get("shared_tree_policy", "reject")
    target_user = patch.get("target_user")
    if policy == "single_user_copy" and not isinstance(target_user, Mapping):
        diagnostics.append(
            _diagnostic(
                "missing_target_user",
                "/target_user",
                "single_user_copy requires a target_user object",
            )
        )
    if target_user is not None:
        if not isinstance(target_user, Mapping):
            diagnostics.append(
                _diagnostic("invalid_target_user", "/target_user", "target_user must be an object")
            )
        else:
            kind = target_user.get("kind")
            required_target_fields = {
                "MODIFIER": {"kind", "object", "modifier"},
                "GROUP_NODE": {"kind", "tree", "node"},
            }.get(kind)
            if required_target_fields is None:
                diagnostics.append(
                    _diagnostic(
                        "invalid_target_user_kind",
                        "/target_user/kind",
                        "target_user.kind must be MODIFIER or GROUP_NODE",
                    )
                )
            else:
                for field in sorted(required_target_fields - set(target_user)):
                    diagnostics.append(
                        _diagnostic(
                            "missing_field",
                            f"/target_user/{field}",
                            f"Required target_user field is missing: {field}",
                        )
                    )
                for field in sorted(set(target_user) - required_target_fields):
                    diagnostics.append(
                        _diagnostic(
                            "unknown_field",
                            f"/target_user/{_json_pointer_token(str(field))}",
                            f"Unknown target_user field: {field} (accepted: {_format_field_set(required_target_fields)})",
                        )
                    )
                for field in required_target_fields - {"kind"}:
                    _require_non_empty_string(
                        target_user.get(field), f"/target_user/{field}", diagnostics
                    )

    operations = patch.get("operations")
    if not isinstance(operations, list):
        diagnostics.append(
            _diagnostic("invalid_operations", "/operations", "operations must be an array")
        )
        return diagnostics
    if not operations:
        diagnostics.append(
            _diagnostic("empty_operations", "/operations", "operations must not be empty")
        )
    if len(operations) > 500:
        diagnostics.append(
            _diagnostic("too_many_operations", "/operations", "A patch may contain at most 500 operations")
        )

    created_ids: set[str] = set()
    string_fields = {
        "id", "node_type", "name", "node", "property", "socket",
        "from_node", "from_socket", "to_node", "to_socket", "identifier",
        "object", "modifier", "in_out", "socket_type",
        "collection", "input_id", "output_id", "input_name", "output_name",
    }
    for index, operation in enumerate(operations):
        path = f"/operations/{index}"
        if not isinstance(operation, Mapping):
            diagnostics.append(
                _diagnostic("invalid_operation", path, "Operation must be a JSON object")
            )
            continue
        operation_name = operation.get("op")
        if operation_name not in SUPPORTED_PATCH_OPERATIONS:
            diagnostics.append(
                _diagnostic(
                    "unsupported_operation",
                    f"{path}/op",
                    f"Unsupported operation: {operation_name!r}",
                )
            )
            continue

        required, optional = _PATCH_OPERATION_FIELDS[operation_name]
        hint = _operation_fields_hint(operation_name, required, optional)
        for field in sorted(required - set(operation)):
            diagnostics.append(
                _diagnostic(
                    "missing_field",
                    f"{path}/{field}",
                    f"Required operation field is missing: {field} ({hint})",
                )
            )
        for field in sorted(set(operation) - required - optional):
            diagnostics.append(
                _diagnostic(
                    "unknown_field",
                    f"{path}/{_json_pointer_token(str(field))}",
                    f"Unknown field for {operation_name}: {field} ({hint})",
                )
            )
        for field in sorted((set(operation) & string_fields) - {"op"}):
            _require_non_empty_string(operation[field], f"{path}/{field}", diagnostics)

        if operation_name == "add_node":
            created_id = operation.get("id")
            if isinstance(created_id, str):
                if created_id in created_ids:
                    diagnostics.append(
                        _diagnostic(
                            "duplicate_created_id",
                            f"{path}/id",
                            f"Created node id is duplicated: {created_id}",
                        )
                    )
                created_ids.add(created_id)
            if "properties" in operation and not isinstance(operation["properties"], Mapping):
                diagnostics.append(
                    _diagnostic(
                        "invalid_properties",
                        f"{path}/properties",
                        "properties must be a JSON object",
                    )
                )
            elif "properties" in operation:
                for property_name in operation["properties"]:
                    if not isinstance(property_name, str) or not property_name:
                        diagnostics.append(
                            _diagnostic(
                                "invalid_property_name",
                                f"{path}/properties/{_json_pointer_token(str(property_name))}",
                                "Node property names must be non-empty strings",
                            )
                        )
            if "layout" in operation:
                if not isinstance(operation["layout"], Mapping):
                    diagnostics.append(
                        _diagnostic("invalid_layout", f"{path}/layout", "layout must be an object")
                    )
                else:
                    layout_operation = {"op": "set_node_layout", "node": created_id, **operation["layout"]}
                    _validate_layout(layout_operation, f"{path}/layout", diagnostics)
        elif operation_name in {"add_link", "remove_link"}:
            for field, direction in (("from_socket", "output"), ("to_socket", "input")):
                value = operation.get(field)
                if isinstance(value, str) and (
                    not _SOCKET_ID_PATTERN.fullmatch(value)
                    or not value.startswith(direction + ":")
                ):
                    diagnostics.append(
                        _diagnostic(
                            "invalid_socket_id",
                            f"{path}/{field}",
                            f"{field} must use a {direction}:<index>:<identifier> socket id",
                        )
                    )
        elif operation_name == "set_socket_default":
            value = operation.get("socket")
            if isinstance(value, str) and (
                not _SOCKET_ID_PATTERN.fullmatch(value) or not value.startswith("input:")
            ):
                diagnostics.append(
                    _diagnostic(
                        "invalid_socket_id",
                        f"{path}/socket",
                        "Socket defaults can only target input:<index>:<identifier>",
                    )
                )
        elif operation_name == "set_socket_hide":
            socket_value = operation.get("socket")
            if isinstance(socket_value, str) and not _SOCKET_ID_PATTERN.fullmatch(
                socket_value
            ):
                diagnostics.append(
                    _diagnostic(
                        "invalid_socket_id",
                        f"{path}/socket",
                        "socket must use an input:<index>:<identifier> or "
                        "output:<index>:<identifier> socket id",
                    )
                )
            if not isinstance(operation.get("value"), bool):
                diagnostics.append(
                    _diagnostic(
                        "invalid_boolean",
                        f"{path}/value",
                        "set_socket_hide value must be a boolean",
                    )
                )
        elif operation_name == "set_node_layout":
            _validate_layout(operation, path, diagnostics)
        elif operation_name == "add_interface_socket":
            if operation.get("in_out") not in {"INPUT", "OUTPUT"}:
                diagnostics.append(
                    _diagnostic(
                        "invalid_direction",
                        f"{path}/in_out",
                        "in_out must be INPUT or OUTPUT",
                    )
                )
        elif operation_name == "add_interface_panel":
            if "description" in operation and not isinstance(
                operation["description"], str
            ):
                diagnostics.append(
                    _diagnostic(
                        "invalid_string",
                        f"{path}/description",
                        "description must be a string",
                    )
                )
            if "default_closed" in operation and not isinstance(
                operation["default_closed"], bool
            ):
                diagnostics.append(
                    _diagnostic(
                        "invalid_boolean",
                        f"{path}/default_closed",
                        "default_closed must be boolean",
                    )
                )
        elif operation_name in {"remove_dynamic_item", "set_dynamic_item"}:
            item_index = operation.get("index")
            if not isinstance(item_index, int) or isinstance(item_index, bool) or item_index < 0:
                diagnostics.append(
                    _diagnostic("invalid_index", f"{path}/index", "index must be a non-negative integer")
                )
        elif operation_name in {"add_foreach_zone", "add_closure_zone"}:
            for field in ("input_id", "output_id"):
                created_id = operation.get(field)
                if isinstance(created_id, str):
                    if created_id in created_ids:
                        diagnostics.append(
                            _diagnostic(
                                "duplicate_created_id", f"{path}/{field}",
                                f"Created node id is duplicated: {created_id}",
                            )
                        )
                    created_ids.add(created_id)
            if "location" in operation:
                location = operation["location"]
                if (
                    not isinstance(location, list)
                    or len(location) != 2
                    or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in location)
                ):
                    diagnostics.append(
                        _diagnostic(
                            "invalid_location", f"{path}/location",
                            "location must be an array of two numbers",
                        )
                    )

    return diagnostics


def assert_valid_patch(patch: Any) -> dict[str, Any]:
    diagnostics = validate_patch_structure(patch)
    if diagnostics:
        raise GeometryNodesPatchError(diagnostics)
    return deepcopy(dict(patch))


def normalize_view(view: str) -> str:
    normalized = view.strip().lower()
    if normalized not in SUPPORTED_VIEWS:
        choices = ", ".join(sorted(SUPPORTED_VIEWS))
        raise GeometryNodesSchemaError(
            f"Unsupported Geometry Nodes view {view!r}; expected one of: {choices}"
        )
    return normalized


def canonical_json(value: Any) -> str:
    """Return the unique JSON representation used for revision hashes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot_content_revision(snapshot: Mapping[str, Any]) -> str:
    """Hash exactly the graph content present in this snapshot."""
    tree = snapshot.get("tree") or {}
    revision_input = {
        "schema": snapshot.get("schema"),
        "view": snapshot.get("view"),
        "tree": {
            key: tree.get(key)
            for key in ("bl_idname", "interface", "nodes", "links")
        },
    }
    digest = hashlib.sha256(canonical_json(revision_input).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def snapshot_revision(snapshot: Mapping[str, Any]) -> str:
    """Derive a source revision only from a full all-view snapshot."""
    scope = snapshot.get("scope")
    if snapshot.get("view") != "all" or not isinstance(scope, Mapping) or scope.get("kind") != "full":
        raise GeometryNodesSchemaError(
            "A source revision can only be derived from a full snapshot with view='all'"
        )
    return snapshot_content_revision(snapshot)


def validate_snapshot_structure(snapshot: Mapping[str, Any]) -> None:
    """Perform dependency-free validation of the stable top-level contract."""
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise GeometryNodesSchemaError(
            f"Expected schema {SNAPSHOT_SCHEMA!r}, got {snapshot.get('schema')!r}"
        )
    normalize_view(str(snapshot.get("view", "")))

    tree = snapshot.get("tree")
    if not isinstance(tree, Mapping):
        raise GeometryNodesSchemaError("snapshot.tree must be an object")
    for key in ("name", "bl_idname", "editable", "interface", "nodes", "links"):
        if key not in tree:
            raise GeometryNodesSchemaError(f"snapshot.tree.{key} is required")
    if not isinstance(tree["nodes"], Mapping):
        raise GeometryNodesSchemaError("snapshot.tree.nodes must be an object map")
    if not isinstance(tree["links"], list):
        raise GeometryNodesSchemaError("snapshot.tree.links must be an array")
    if not isinstance(tree["interface"], list):
        raise GeometryNodesSchemaError("snapshot.tree.interface must be an array")
    scope = snapshot.get("scope")
    if not isinstance(scope, Mapping) or scope.get("kind") not in {"full", "subgraph"}:
        raise GeometryNodesSchemaError("snapshot.scope.kind must be full or subgraph")


def finalize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, copy, and attach a deterministic revision."""
    result = deepcopy(dict(snapshot))
    validate_snapshot_structure(result)
    content_revision = snapshot_content_revision(result)
    result["scope"]["content_revision"] = content_revision
    if result["view"] == "all" and result["scope"]["kind"] == "full":
        result["revision"] = content_revision
    elif not isinstance(result.get("revision"), str) or not _REVISION_PATTERN.fullmatch(result["revision"]):
        raise GeometryNodesSchemaError(
            "Scoped or filtered snapshots must preserve Blender's full source revision"
        )
    return result


def _resolve_workspace_root(
    workspace_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the workspace root that bounds relative export/patch paths.

    Precedence: the explicit ``workspace_root`` argument, then the
    ``BLENDER_MCP_WORKSPACE`` environment variable, then the current working
    directory. Relative paths are joined to this root; absolute paths must
    still fall inside it. Centralizing this keeps the boundary discoverable
    from every error message instead of being an implicit runtime default.
    """
    root_value = workspace_root or os.environ.get("BLENDER_MCP_WORKSPACE") or Path.cwd()
    return Path(root_value).expanduser().resolve()


def resolve_workspace_json_path(
    output_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve an export path and reject writes outside the configured workspace.

    ``output_path`` may be absolute or relative; relative paths resolve against
    the workspace root (see :func:`_resolve_workspace_root`). The resolved path
    must end in ``.json`` and stay inside the workspace root.
    """
    root = _resolve_workspace_root(workspace_root)
    candidate_input = Path(output_path).expanduser()
    candidate = (
        candidate_input.resolve()
        if candidate_input.is_absolute()
        else (root / candidate_input).resolve()
    )

    if candidate.suffix.lower() != ".json":
        raise GeometryNodesSchemaError("Geometry Nodes exports must use a .json file")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GeometryNodesSchemaError(
            f"Export path must stay inside the workspace root: {candidate} is outside "
            f"{root}. Set workspace_root (or BLENDER_MCP_WORKSPACE) to cover it, "
            f"or use a path inside that root."
        ) from exc
    return candidate


def write_snapshot_json(
    snapshot: Mapping[str, Any],
    output_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Atomically write a validated snapshot below the workspace root."""
    validate_snapshot_structure(snapshot)
    destination = resolve_workspace_json_path(output_path, workspace_root)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            temporary_name = handle.name
        os.replace(temporary_name, destination)
    except Exception:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise
    return destination


def read_patch_json(
    patch_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str] | None = None,
    max_bytes: int = 4 * 1024 * 1024,
) -> Any:
    """Read a workspace-bound patch file with a conservative size limit.

    ``patch_path`` resolves against the workspace root (see
    :func:`_resolve_workspace_root`); relative paths are joined to that root,
    so the resolved path shown in errors reveals where to place the file.
    """
    source = resolve_workspace_json_path(patch_path, workspace_root)
    try:
        size = source.stat().st_size
    except FileNotFoundError as exc:
        root = _resolve_workspace_root(workspace_root)
        raise GeometryNodesSchemaError(
            f"Patch file not found: {source} (workspace root: {root})"
        ) from exc
    if size > max_bytes:
        raise GeometryNodesSchemaError(
            f"Patch file exceeds {max_bytes} byte limit: {size} bytes"
        )
    try:
        with source.open(encoding="utf-8") as handle:
            patch = json.load(handle)
    except json.JSONDecodeError as exc:
        raise GeometryNodesSchemaError(
            f"Invalid patch JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    return patch
