"""Record flat/indexed Shader and Compositor graph performance baselines."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import time
import traceback

import bpy


PREFIX = "__BLENDER_MCP_SC_PERF__"
RESULT_PREFIX = "BLENDER_MCP_SC_PERF_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def cleanup():
    for material in list(bpy.data.materials):
        if material.name.startswith(PREFIX):
            bpy.data.materials.remove(material, do_unlink=True)
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def timed(callback, repeats=5):
    values = []
    result = None
    for _index in range(repeats):
        start = time.perf_counter()
        result = callback()
        values.append((time.perf_counter() - start) * 1000.0)
    values.sort()
    return round(values[len(values) // 2], 4), result


def build_group(name, tree_type, node_type, count=252):
    tree = bpy.data.node_groups.new(name, tree_type)
    for index in range(count):
        node = tree.nodes.new(node_type)
        node.name = f"Node {index:04d}"
        node.label = "benchmark"
        node.location = ((index % 30) * 180.0, -(index // 30) * 120.0)
    return tree


def benchmark(tree, export_tree, node_record):
    middle_name = sorted(node.name for node in tree.nodes)[len(tree.nodes) // 2]
    list_ms, listing = timed(
        lambda: [
            (node.name, node.bl_idname)
            for node in sorted(tree.nodes, key=lambda item: item.name)
        ]
    )
    index_ms, index = timed(
        lambda: [item for item in listing if "12" in item[0]]
    )
    targeted_ms, targeted = timed(
        lambda: export_tree(tree, "semantic", [middle_name], 2), repeats=3
    )
    full_ms, full = timed(lambda: export_tree(tree, "semantic"), repeats=3)
    schema_ms, schema = timed(
        lambda: node_record(tree.nodes[middle_name], "semantic")
    )
    copy_ms, copied = timed(lambda: tree.copy(), repeats=1)
    bpy.data.node_groups.remove(copied)
    result = {
        "node_count": len(tree.nodes),
        "list_ms": list_ms,
        "index_ms": index_ms,
        "index_matches": len(index),
        "targeted_export_ms": targeted_ms,
        "targeted_json_bytes": targeted["stats"]["json_bytes"],
        "full_export_ms": full_ms,
        "full_json_bytes": full["stats"]["json_bytes"],
        "schema_ms": schema_ms,
        "schema_json_bytes": len(json.dumps(schema, sort_keys=True).encode("utf-8")),
        "copy_ms": copy_ms,
        "stable_revision": export_tree(tree, "semantic")["revision"] == full["revision"],
    }
    if not result["stable_revision"]:
        raise AssertionError("unchanged graph revision is not stable")
    if result["targeted_json_bytes"] >= result["full_json_bytes"]:
        raise AssertionError("targeted graph output is not smaller than full output")
    return result


def generic_benchmark(server, tree):
    reference = {
        "tree_type": tree.bl_idname,
        "owner": {"kind": "NODE_GROUP", "name": tree.name},
    }
    middle_name = sorted(node.name for node in tree.nodes)[len(tree.nodes) // 2]
    full_ms, full = timed(
        lambda: server.export_node_tree(reference, "semantic"), repeats=3
    )
    targeted_ms, targeted = timed(
        lambda: server.export_node_tree(
            reference, "semantic", [middle_name], 2
        ),
        repeats=3,
    )
    index_ms, index = timed(
        lambda: server.get_node_tree_index(reference, "12", 0, 100)
    )
    if full["schema"] != "blender-node-tree/1":
        raise AssertionError("generic export returned wrong schema")
    if targeted["revision"] != full["revision"]:
        raise AssertionError("targeted and full generic revisions differ")
    if targeted["stats"]["json_bytes"] >= full["stats"]["json_bytes"]:
        raise AssertionError("generic targeted export is not smaller")
    return {
        "full_export_ms": full_ms,
        "full_json_bytes": full["stats"]["json_bytes"],
        "targeted_export_ms": targeted_ms,
        "targeted_json_bytes": targeted["stats"]["json_bytes"],
        "index_ms": index_ms,
        "index_matches": index["total_matches"],
        "stable_revision": server.export_node_tree(reference)["revision"] == full["revision"],
    }


def main():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(str(REPO_ROOT / "addon.py"), run_name="n0_perf_addon")
    export_tree = namespace["_gn_export_tree"]
    node_record = namespace["_gn_node_record"]
    server = object.__new__(namespace["BlenderMCPServer"])
    shader = build_group(PREFIX + "Shader", "ShaderNodeTree", "ShaderNodeValue")
    compositor = build_group(
        PREFIX + "Compositor", "CompositorNodeTree", "CompositorNodeCurveRGB"
    )
    result = {
        "version": list(bpy.app.version[:3]),
        "shader": benchmark(shader, export_tree, node_record),
        "compositor": benchmark(compositor, export_tree, node_record),
        "generic": {
            "shader": generic_benchmark(server, shader),
            "compositor": generic_benchmark(server, compositor),
        },
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    cleanup()
    result["leaks"] = {
        "materials": [item.name for item in bpy.data.materials if item.name.startswith(PREFIX)],
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
    }
    if any(result["leaks"].values()):
        raise AssertionError(f"performance probe leaked datablocks: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    main()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
