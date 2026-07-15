"""Scale and payload-efficiency checks for normalized Geometry Nodes JSON."""

from __future__ import annotations

import json
import os
import runpy
import sys
import time
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_GN_SCALE_TEST__"
RESULT_PREFIX = "BLENDER_MCP_GN_SCALE_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSFORM_COUNT = 250


def cleanup():
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def compact_bytes(value):
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def timed(callback):
    started = time.perf_counter()
    value = callback()
    return value, (time.perf_counter() - started) * 1000.0


def build_chain_tree(suffix, transform_count):
    tree = bpy.data.node_groups.new(PREFIX + suffix, "GeometryNodeTree")
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group_input = tree.nodes.new("NodeGroupInput")
    group_output = tree.nodes.new("NodeGroupOutput")
    transforms = []
    for index in range(transform_count):
        node = tree.nodes.new("GeometryNodeTransform")
        node.name = f"Transform {index:03d}"
        node.location = (index * 180.0, (index % 7) * 80.0)
        node.inputs["Translation"].default_value = (
            index * 0.01,
            index * 0.02,
            index * 0.03,
        )
        transforms.append(node)

    tree.links.new(group_input.outputs["Geometry"], transforms[0].inputs["Geometry"])
    for before, after in zip(transforms, transforms[1:]):
        tree.links.new(before.outputs["Geometry"], after.inputs["Geometry"])
    tree.links.new(transforms[-1].outputs["Geometry"], group_output.inputs["Geometry"])
    return tree, transforms


def run_test():
    cleanup()
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_addon_scale_test",
    )
    server = namespace["BlenderMCPServer"]()

    empty = bpy.data.node_groups.new(PREFIX + "Empty", "GeometryNodeTree")
    empty_snapshot = server.export_geometry_node_tree(empty.name, "all")
    assert_true(empty_snapshot["stats"]["node_count"] == 0, "Empty tree gained nodes")
    assert_true(empty_snapshot["stats"]["link_count"] == 0, "Empty tree gained links")

    medium_tree, _medium_transforms = build_chain_tree("Medium", 50)
    medium, medium_ms = timed(
        lambda: server.export_geometry_node_tree(
            medium_tree.name, "semantic", allow_large_response=True
        )
    )
    tree, transforms = build_chain_tree("Large", TRANSFORM_COUNT)
    semantic, semantic_ms = timed(
        lambda: server.export_geometry_node_tree(
            tree.name, "semantic", allow_large_response=True
        )
    )
    semantic_repeat, semantic_repeat_ms = timed(
        lambda: server.export_geometry_node_tree(
            tree.name, "semantic", allow_large_response=True
        )
    )
    all_view, all_ms = timed(
        lambda: server.export_geometry_node_tree(
            tree.name, "all", allow_large_response=True
        )
    )
    center = transforms[len(transforms) // 2].name
    translation = tree.nodes[center].inputs["Translation"]
    translation_index = next(
        position for position, socket in enumerate(tree.nodes[center].inputs)
        if socket == translation
    )
    translation_identifier = getattr(translation, "identifier", "") or translation.name
    translation_socket_id = (
        f"input:{translation_index}:{translation_identifier}"
    )
    subgraph, subgraph_ms = timed(
        lambda: server.export_geometry_node_tree(
            tree.name,
            "semantic",
            [center],
            1,
        )
    )
    index, index_ms = timed(
        lambda: server.get_geometry_node_tree_index(tree.name, "Transform", 0, 50)
    )

    patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": semantic["revision"],
        "operations": [
            {
                "op": "set_socket_default",
                "node": center,
                "socket": translation_socket_id,
                "value": [9.0, 8.0, 7.0],
            },
            {
                "op": "set_node_layout",
                "node": center,
                "location": [1234.0, 567.0],
            },
        ],
    }
    groups_before_validation = len(bpy.data.node_groups)
    validation, validation_ms = timed(
        lambda: server.validate_geometry_node_patch(patch)
    )

    semantic_size = compact_bytes(semantic)
    medium_size = compact_bytes(medium)
    all_size = compact_bytes(all_view)
    subgraph_size = compact_bytes(subgraph)
    index_size = compact_bytes(index)
    patch_size = compact_bytes(patch)

    assert_true(semantic == semantic_repeat, "Large graph export is not deterministic")
    assert_true(semantic["revision"] == all_view["revision"], "Revision depends on view")
    assert_true(subgraph["revision"] == semantic["revision"], "Subgraph lost source revision")
    assert_true(subgraph["stats"]["node_count"] == 3, "One-hop chain subgraph is not 3 nodes")
    assert_true(index["total_matches"] == TRANSFORM_COUNT, "Index search missed transform nodes")
    assert_true(len(index["nodes"]) == 50 and index["next_offset"] == 50, "Index pagination failed")
    assert_true(index["revision"] == semantic["revision"], "Index revision differs from snapshot")
    assert_true(all_size > semantic_size, "Semantic view did not reduce payload")
    assert_true(subgraph_size < medium_size < semantic_size, "Payload sizes do not scale with graph size")
    assert_true(subgraph_size < semantic_size * 0.10, "Subgraph is more than 10% of full graph")
    assert_true(index_size < semantic_size * 0.10, "50-node index page is more than 10% of snapshot")
    assert_true(patch_size < semantic_size * 0.02, "Incremental patch is more than 2% of snapshot")
    assert_true(validation["valid"], f"Large graph patch dry-run failed: {validation}")
    assert_true(validation.get("candidate_revision"), "Dry-run did not re-export candidate")
    assert_true(
        len(bpy.data.node_groups) == groups_before_validation,
        "Large graph dry-run leaked temporary data-blocks",
    )

    index_output = os.environ.get("BLENDER_MCP_SCALE_INDEX_OUTPUT")
    if index_output:
        Path(index_output).write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "blender_version": list(bpy.app.version[:3]),
        "nodes": semantic["stats"]["node_count"],
        "links": semantic["stats"]["link_count"],
        "semantic_bytes": semantic_size,
        "medium_semantic_bytes": medium_size,
        "all_bytes": all_size,
        "subgraph_bytes": subgraph_size,
        "index_page_bytes": index_size,
        "patch_bytes": patch_size,
        "semantic_estimated_tokens_at_4_bytes": (semantic_size + 3) // 4,
        "medium_estimated_tokens_at_4_bytes": (medium_size + 3) // 4,
        "subgraph_estimated_tokens_at_4_bytes": (subgraph_size + 3) // 4,
        "index_page_estimated_tokens_at_4_bytes": (index_size + 3) // 4,
        "patch_estimated_tokens_at_4_bytes": (patch_size + 3) // 4,
        "semantic_to_all_ratio": round(semantic_size / all_size, 4),
        "subgraph_to_semantic_ratio": round(subgraph_size / semantic_size, 4),
        "index_page_to_semantic_ratio": round(index_size / semantic_size, 4),
        "patch_to_semantic_ratio": round(patch_size / semantic_size, 4),
        "semantic_export_ms": round(semantic_ms, 3),
        "medium_export_ms": round(medium_ms, 3),
        "semantic_repeat_ms": round(semantic_repeat_ms, 3),
        "all_export_ms": round(all_ms, 3),
        "subgraph_export_ms": round(subgraph_ms, 3),
        "index_export_ms": round(index_ms, 3),
        "patch_dry_run_ms": round(validation_ms, 3),
    }


try:
    result = run_test()
    print(RESULT_PREFIX + json.dumps({"ok": True, **result}, sort_keys=True))
except Exception as exc:
    traceback.print_exc()
    print(
        RESULT_PREFIX
        + json.dumps(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            sort_keys=True,
        )
    )
    sys.exit(1)
finally:
    cleanup()
