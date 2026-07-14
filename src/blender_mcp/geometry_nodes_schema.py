"""Pure-Python contracts shared by Geometry Nodes MCP tools.

This module intentionally has no Blender or MCP imports so its canonicalization,
revision, and workspace-boundary behavior can be tested outside Blender.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


SNAPSHOT_SCHEMA = "blender-geometry-nodes/1"
SUPPORTED_VIEWS = frozenset({"semantic", "layout", "all"})


class GeometryNodesSchemaError(ValueError):
    """Raised when a snapshot or export path violates the public contract."""


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


def snapshot_revision(snapshot: Mapping[str, Any]) -> str:
    """Hash graph state while excluding observations such as users and stats."""
    revision_input = {
        "schema": snapshot.get("schema"),
        "view": snapshot.get("view"),
        "tree": snapshot.get("tree"),
    }
    digest = hashlib.sha256(canonical_json(revision_input).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


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
    result["revision"] = snapshot_revision(result)
    return result


def resolve_workspace_json_path(
    output_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve an export path and reject writes outside the configured workspace."""
    root_value = workspace_root or os.environ.get("BLENDER_MCP_WORKSPACE") or Path.cwd()
    root = Path(root_value).expanduser().resolve()
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
            f"Export path must remain inside workspace root: {root}"
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
