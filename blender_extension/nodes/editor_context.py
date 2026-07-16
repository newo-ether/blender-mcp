"""Deterministic, read-only inspection of live Node Editor UI contexts."""

from __future__ import annotations

import hashlib

import bpy

from .constants import NODE_EDITOR_CONTEXT_SCHEMA
from .serialization import _gn_canonical_json
from .targets import _node_id_library, _node_iter_targets


_NODE_EDITOR_CONTEXT_MAX_EDITORS = 32


def _pointer_id(prefix, value):
    if value is None:
        return None
    try:
        pointer = int(value.as_pointer())
    except (AttributeError, TypeError, ValueError):
        pointer = id(value)
    return f"{prefix}:{pointer:x}"


def _safe_name(value):
    return str(getattr(value, "name", "") or "")


def _id_record(value):
    if value is None:
        return None
    rna = getattr(value, "bl_rna", None)
    return {
        "id_type": str(getattr(rna, "identifier", type(value).__name__) or type(value).__name__),
        "name": _safe_name(value),
        "library": _node_id_library(value),
    }


def _tree_ref_key(tree_ref):
    return (
        tree_ref["tree_type"],
        tree_ref["owner"]["kind"],
        tree_ref["owner"]["name"],
    )


def _tree_refs(tree, owner, targets):
    if tree is None:
        return []
    matches = [target for target in targets if target["tree"] is tree]
    owner_matches = [target for target in matches if target["owner_id"] is owner]
    if owner_matches:
        matches = owner_matches
    refs = { _gn_canonical_json(target["tree_ref"]): target["tree_ref"] for target in matches }
    return sorted(refs.values(), key=_tree_ref_key)


def _tree_record(tree, owner, targets):
    if tree is None:
        return None
    candidates = _tree_refs(tree, owner, targets)
    return {
        "name": _safe_name(tree),
        "bl_idname": str(getattr(tree, "bl_idname", "") or ""),
        "library": _node_id_library(tree),
        "tree_ref": candidates[0] if len(candidates) == 1 else None,
        "tree_ref_candidates": candidates,
    }


def _node_selection(tree):
    nodes = getattr(tree, "nodes", None) if tree is not None else None
    if nodes is None:
        return None, []
    active = getattr(nodes, "active", None)
    selected = sorted(
        _safe_name(node)
        for node in nodes
        if bool(getattr(node, "select", False))
    )
    return (_safe_name(active) if active is not None else None), selected


def _space_path(space, owner, targets):
    records = []
    try:
        elements = list(getattr(space, "path", ()) or ())
    except (ReferenceError, RuntimeError, TypeError):
        elements = []
    for element in elements:
        tree = getattr(element, "node_tree", None)
        if tree is None:
            continue
        record = _tree_record(tree, owner, targets)
        if record is not None:
            records.append(record)
    return records


def _editor_record(window, area, space, targets):
    owner = getattr(space, "id", None)
    node_tree = getattr(space, "node_tree", None)
    edit_tree = getattr(space, "edit_tree", None)
    current_tree = edit_tree or node_tree
    active_node, selected_nodes = _node_selection(current_tree)
    current = _tree_record(current_tree, owner, targets)
    window_id = _pointer_id("window", window)
    area_id = _pointer_id("area", area)
    context_id = f"{window_id}/{area_id}"
    screen = getattr(window, "screen", None)
    workspace = getattr(window, "workspace", None)
    return {
        "context_id": context_id,
        "window_id": window_id,
        "area_id": area_id,
        "screen": _safe_name(screen),
        "workspace": _safe_name(workspace),
        "area": {
            "x": int(getattr(area, "x", 0)),
            "y": int(getattr(area, "y", 0)),
            "width": int(getattr(area, "width", 0)),
            "height": int(getattr(area, "height", 0)),
            "ui_type": str(getattr(area, "ui_type", "") or ""),
        },
        "pinned": bool(getattr(space, "pin", False)),
        "space_tree_type": str(getattr(space, "tree_type", "") or ""),
        "shader_type": (
            str(getattr(space, "shader_type", "") or "") or None
        ),
        "owner": _id_record(owner),
        "node_tree": _tree_record(node_tree, owner, targets),
        "edit_tree": _tree_record(edit_tree, owner, targets),
        "path": _space_path(space, owner, targets),
        "tree_ref": current["tree_ref"] if current is not None else None,
        "tree_ref_candidates": (
            current["tree_ref_candidates"] if current is not None else []
        ),
        "active_node": active_node,
        "selected_nodes": selected_nodes,
    }


def _normalize_max_editors(max_editors):
    try:
        value = int(max_editors)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_editors must be an integer") from exc
    if not 1 <= value <= _NODE_EDITOR_CONTEXT_MAX_EDITORS:
        raise ValueError(
            f"max_editors must be from 1 to {_NODE_EDITOR_CONTEXT_MAX_EDITORS}"
        )
    return value


def _validate_expected(value, label):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value.strip()


def _observed_state(editors):
    if not editors:
        return "NO_EDITOR"
    if len(editors) > 1:
        return "MULTIPLE_EDITORS"
    return "PINNED_EDITOR" if editors[0]["pinned"] else "UNIQUE_EDITOR"


def _next_action(state):
    return {
        "NO_EDITOR": "open_a_node_editor",
        "UNIQUE_EDITOR": "use_selected_tree_ref",
        "PINNED_EDITOR": "use_selected_tree_ref",
        "MULTIPLE_EDITORS": "choose_an_editor_explicitly",
        "STALE_CONTEXT": "refresh_node_editor_context",
    }[state]


def _node_editor_context_from_windows(
    windows,
    file_session_id,
    *,
    targets=None,
    expected_file_session_id="",
    expected_context_revision="",
    max_editors=32,
):
    """Build the public context payload from real or duck-typed UI windows."""
    limit = _normalize_max_editors(max_editors)
    expected_file = _validate_expected(
        expected_file_session_id, "expected_file_session_id"
    )
    expected_revision = _validate_expected(
        expected_context_revision, "expected_context_revision"
    )
    if expected_revision and not (
        len(expected_revision) == 71
        and expected_revision.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in expected_revision[7:])
    ):
        raise ValueError("expected_context_revision must be a sha256 revision")

    resolved_targets = list(_node_iter_targets() if targets is None else targets)
    editors = []
    for window in list(windows or ()):
        screen = getattr(window, "screen", None)
        for area in list(getattr(screen, "areas", ()) or ()):
            if getattr(area, "type", "") != "NODE_EDITOR":
                continue
            spaces = getattr(area, "spaces", None)
            space = getattr(spaces, "active", None) if spaces is not None else None
            if space is None:
                continue
            editors.append(_editor_record(window, area, space, resolved_targets))
    editors.sort(key=lambda item: item["context_id"])

    observed_state = _observed_state(editors)
    revision_source = {
        "file_session_id": str(file_session_id),
        "total_editors": len(editors),
        "editors": editors,
    }
    context_revision = "sha256:" + hashlib.sha256(
        _gn_canonical_json(revision_source).encode("utf-8")
    ).hexdigest()
    stale_reasons = []
    if expected_file and expected_file != str(file_session_id):
        stale_reasons.append("file_session_changed")
    if expected_revision and expected_revision != context_revision:
        stale_reasons.append("context_changed")
    state = "STALE_CONTEXT" if stale_reasons else observed_state
    selected_context_id = (
        editors[0]["context_id"]
        if state in {"UNIQUE_EDITOR", "PINNED_EDITOR"}
        else None
    )
    return {
        "schema": NODE_EDITOR_CONTEXT_SCHEMA,
        "file_session_id": str(file_session_id),
        "context_revision": context_revision,
        "state": state,
        "observed_state": observed_state,
        "selected_context_id": selected_context_id,
        "next_action": _next_action(state),
        "stale_reasons": stale_reasons,
        "total_editors": len(editors),
        "editors": editors[:limit],
        "truncated": len(editors) > limit,
    }


def _node_editor_context(
    file_session_id,
    expected_file_session_id="",
    expected_context_revision="",
    max_editors=32,
):
    window_manager = getattr(bpy.context, "window_manager", None)
    windows = getattr(window_manager, "windows", ()) if window_manager else ()
    return _node_editor_context_from_windows(
        windows,
        file_session_id,
        expected_file_session_id=expected_file_session_id,
        expected_context_revision=expected_context_revision,
        max_editors=max_editors,
    )
