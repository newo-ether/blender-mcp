"""Validation and workspace-safe persistence for generic node-tree snapshots."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

SNAPSHOT_SCHEMA = "blender-node-tree/1"
TREE_TYPES = {"GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree"}
OWNER_KINDS = {"MATERIAL", "WORLD", "LIGHT", "SCENE", "NODE_GROUP"}
VIEWS = {"semantic", "operations", "layout", "all"}
_REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

# Paired-zone operations share the same field contract across the Geometry and
# generic node-tree protocols (input_id/output_id required; input_name/output_name/
# location optional). Declared once here so a new zone type is added in exactly one
# place instead of being scattered across both protocol modules.
ZONE_OPERATIONS = ("add_foreach_zone", "add_closure_zone", "add_repeat_zone")
ZONE_OPERATION_FIELDS = (
    {"op", "input_id", "output_id"},
    {"input_name", "output_name", "location"},
)
ZONE_OPERATION_CONTRACT = {name: ZONE_OPERATION_FIELDS for name in ZONE_OPERATIONS}
ZONE_CAPABILITY = {name: "dynamic" for name in ZONE_OPERATIONS}

# A zone is one input node + one output node bound by pairing. The two halves are
# not legal `add_node` targets: they can only exist paired, and an unpaired half is a
# state the Node Editor never produces. Adding one through `add_node` creates that
# unreachable state and the next tree evaluation can hang or crash Blender. Map each
# half to the zone op that builds the pair atomically so the rejection can point at it.
ZONE_HALF_PAIR_NODE_TYPES = {
    "GeometryNodeRepeatInput": "add_repeat_zone",
    "GeometryNodeRepeatOutput": "add_repeat_zone",
    "GeometryNodeForeachGeometryElementInput": "add_foreach_zone",
    "GeometryNodeForeachGeometryElementOutput": "add_foreach_zone",
    "GeometryNodeSimulationInput": "add_simulation_zone",
    "GeometryNodeSimulationOutput": "add_simulation_zone",
    "NodeClosureInput": "add_closure_zone",
    "NodeClosureOutput": "add_closure_zone",
}


class NodeTreeSchemaError(ValueError):
    """Raised when a generic node-tree document or path is invalid."""


def validate_tree_ref(tree_ref: Any) -> None:
    if not isinstance(tree_ref, Mapping):
        raise NodeTreeSchemaError("snapshot.tree_ref must be an object")
    tree_type = tree_ref.get("tree_type")
    if tree_type not in TREE_TYPES:
        raise NodeTreeSchemaError("snapshot.tree_ref.tree_type is invalid")
    owner = tree_ref.get("owner")
    if not isinstance(owner, Mapping):
        raise NodeTreeSchemaError("snapshot.tree_ref.owner must be an object")
    if owner.get("kind") not in OWNER_KINDS:
        raise NodeTreeSchemaError("snapshot.tree_ref.owner.kind is invalid")
    if not isinstance(owner.get("name"), str) or not owner["name"]:
        raise NodeTreeSchemaError("snapshot.tree_ref.owner.name must be non-empty")


def validate_snapshot_structure(snapshot: Any) -> None:
    if not isinstance(snapshot, Mapping):
        raise NodeTreeSchemaError("snapshot must be an object")
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise NodeTreeSchemaError(
            f"Expected schema {SNAPSHOT_SCHEMA!r}, got {snapshot.get('schema')!r}"
        )
    validate_tree_ref(snapshot.get("tree_ref"))
    if snapshot.get("view") not in VIEWS:
        choices = ", ".join(sorted(VIEWS))
        raise NodeTreeSchemaError(f"snapshot.view must be one of: {choices}")
    revision = snapshot.get("revision")
    if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
        raise NodeTreeSchemaError("snapshot.revision must be sha256:<64 lowercase hex>")
    tree = snapshot.get("tree")
    if not isinstance(tree, Mapping):
        raise NodeTreeSchemaError("snapshot.tree must be an object")
    if tree.get("bl_idname") != snapshot["tree_ref"]["tree_type"]:
        raise NodeTreeSchemaError("snapshot tree type does not match tree_ref")
    if not isinstance(tree.get("nodes"), Mapping):
        raise NodeTreeSchemaError("snapshot.tree.nodes must be an object map")
    if not isinstance(tree.get("links"), list):
        raise NodeTreeSchemaError("snapshot.tree.links must be an array")
    if not isinstance(tree.get("interface"), list):
        raise NodeTreeSchemaError("snapshot.tree.interface must be an array")
    if not isinstance(snapshot.get("scope"), Mapping):
        raise NodeTreeSchemaError("snapshot.scope must be an object")
    if not isinstance(snapshot.get("stats"), Mapping):
        raise NodeTreeSchemaError("snapshot.stats must be an object")


def resolve_workspace_json_path(
    output_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str] | None = None,
) -> Path:
    root_value = workspace_root or os.environ.get("BLENDER_MCP_WORKSPACE") or Path.cwd()
    root = Path(root_value).expanduser().resolve()
    candidate_input = Path(output_path).expanduser()
    candidate = (
        candidate_input.resolve()
        if candidate_input.is_absolute()
        else (root / candidate_input).resolve()
    )
    if candidate.suffix.lower() != ".json":
        raise NodeTreeSchemaError("Node-tree exports must use a .json file")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NodeTreeSchemaError(
            f"Export path must remain inside workspace root: {root}"
        ) from exc
    return candidate


def write_snapshot_json(
    snapshot: Mapping[str, Any],
    output_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str] | None = None,
) -> Path:
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
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return destination
