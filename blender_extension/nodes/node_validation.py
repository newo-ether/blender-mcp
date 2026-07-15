"""Generic node-tree runtime patch validation."""

from __future__ import annotations

import time

from .common import _gn_patch_diagnostic
from .constants import (
    NODE_TREE_MAX_MUTATION_NODES,
    NODE_TREE_MAX_VALIDATION_SECONDS,
    NODE_TREE_SNAPSHOT_SCHEMA,
)
from .diagnostics import _node_graph_diagnostics
from .patch_operations import (
    _node_execute_patch_operations,
    _node_remove_validation_copy,
    _node_validation_copy,
)
from .serialization import _node_export_tree
from .targets import (
    _node_export_target,
    _node_normalize_tree_ref,
    _node_target_capabilities,
)


def _node_validate_patch_runtime(target, patch):
    validation_started = time.perf_counter()
    current_snapshot = _node_export_target(target, "all")
    current_revision = current_snapshot["revision"]
    diagnostics = []
    try:
        patch_ref = _node_normalize_tree_ref(patch.get("tree_ref"))
    except ValueError as exc:
        patch_ref = patch.get("tree_ref")
        diagnostics.append(_gn_patch_diagnostic(
            "error", "invalid_tree_ref", "/tree_ref", str(exc),
        ))
    if patch_ref != target["tree_ref"]:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_ref_mismatch", "/tree_ref",
            "Patch tree_ref does not identify the resolved target",
        ))
    if patch.get("base_revision") != current_revision:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "stale_revision", "/base_revision",
            f"Patch revision {patch.get('base_revision')!r} does not match current {current_revision!r}",
        ))
    capabilities = _node_target_capabilities(target)
    if not capabilities["editable"]:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_not_editable", "/tree_ref",
            "The selected owner or NodeTree is linked or otherwise read-only",
        ))
    if len(target["tree"].nodes) > NODE_TREE_MAX_MUTATION_NODES:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_node_limit_exceeded", "/tree_ref",
            f"Mutation supports at most {NODE_TREE_MAX_MUTATION_NODES} nodes; "
            f"the selected tree has {len(target['tree'].nodes)}",
        ))

    execution = {
        "diagnostics": [],
        "plan": [],
        "semantic_diff": {},
    }
    candidate_snapshot = None
    if not any(item["severity"] == "error" for item in diagnostics):
        working_target = working_owner = standalone_tree = None
        try:
            working_target, working_owner, standalone_tree = _node_validation_copy(target)
            execution = _node_execute_patch_operations(working_target, patch)
            diagnostics.extend(execution["diagnostics"])
            projected_node_count = len(working_target["tree"].nodes)
            if projected_node_count > NODE_TREE_MAX_MUTATION_NODES:
                diagnostics.append(_gn_patch_diagnostic(
                    "error", "projected_tree_node_limit_exceeded", "/operations",
                    f"The patch would create {projected_node_count} nodes; the limit is "
                    f"{NODE_TREE_MAX_MUTATION_NODES}",
                ))
            if not any(item["severity"] == "error" for item in diagnostics):
                invalid_links = [link for link in working_target["tree"].links if not link.is_valid]
                if invalid_links:
                    raise RuntimeError(
                        f"Projected tree contains {len(invalid_links)} invalid links"
                    )
                candidate_snapshot = _node_export_tree(
                    working_target["tree"],
                    "all",
                    schema=NODE_TREE_SNAPSHOT_SCHEMA,
                    tree_ref=target["tree_ref"],
                    owner=target["owner"],
                    capabilities=capabilities,
                )
                diagnostics.extend(
                    _node_graph_diagnostics(working_target["tree"])
                )
        except Exception as exc:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "dry_run_execution_rejected", "",
                f"{type(exc).__name__}: {exc}",
            ))
        finally:
            if working_owner is not None:
                _node_remove_validation_copy(target, working_owner, standalone_tree)

    elapsed_ms = (time.perf_counter() - validation_started) * 1000.0
    if elapsed_ms > NODE_TREE_MAX_VALIDATION_SECONDS * 1000.0:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "validation_time_limit_exceeded", "",
            f"Validation took {elapsed_ms:.3f} ms; the limit is "
            f"{NODE_TREE_MAX_VALIDATION_SECONDS * 1000.0:.0f} ms",
        ))
    result = {
        "schema": "blender-node-tree-patch-validation/1",
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "stage": "runtime",
        "will_mutate": False,
        "tree_ref": target["tree_ref"],
        "base_revision": patch.get("base_revision"),
        "current_revision": current_revision,
        "capabilities": capabilities,
        "users": current_snapshot["users"],
        "diagnostics": diagnostics,
        "plan": execution["plan"],
        "semantic_diff": execution["semantic_diff"],
        "timing_ms": round(elapsed_ms, 3),
    }
    if candidate_snapshot is not None:
        result["candidate_revision"] = candidate_snapshot["revision"]
        result["candidate_stats"] = candidate_snapshot["stats"]
    return result
