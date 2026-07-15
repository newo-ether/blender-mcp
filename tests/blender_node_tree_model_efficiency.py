"""Measure N5 flat/indexed model context and representative task success."""

from __future__ import annotations

import json
import math
import runpy
import time
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_NODE_EFFICIENCY__"
RESULT_PREFIX = "BLENDER_MCP_NODE_EFFICIENCY_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 256, 2048)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def cleanup():
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def encoded_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def estimated_tokens(byte_count):
    return int(math.ceil(byte_count / 4.0))


def timed(callback):
    start = time.perf_counter()
    value = callback()
    return round((time.perf_counter() - start) * 1000.0, 3), value


def tree_ref(tree):
    return {
        "tree_type": tree.bl_idname,
        "owner": {"kind": "NODE_GROUP", "name": tree.name},
    }


def socket_id(node, socket, direction="input"):
    sockets = node.inputs if direction == "input" else node.outputs
    index = next(index for index, candidate in enumerate(sockets) if candidate == socket)
    identifier = getattr(socket, "identifier", "") or socket.name
    return f"{direction}:{index}:{identifier}"


def make_patch(reference, revision, operations, capabilities):
    return {
        "schema": "blender-node-tree-patch/1",
        "tree_ref": reference,
        "base_revision": revision,
        "capabilities": capabilities,
        "operations": operations,
    }


def build_benchmark_tree(domain, count):
    if domain == "shader":
        tree_type = "ShaderNodeTree"
        candidates = ("ShaderNodeMath",)
    else:
        tree_type = "CompositorNodeTree"
        candidates = ("ShaderNodeMath", "CompositorNodeMath")
    tree = bpy.data.node_groups.new(
        f"{PREFIX}{domain.title()}_{count:04d}", tree_type
    )
    node_type = None
    for candidate in candidates:
        try:
            probe = tree.nodes.new(candidate)
        except RuntimeError:
            continue
        tree.nodes.remove(probe)
        node_type = candidate
        break
    assert_true(node_type is not None, f"no Math node is available for {tree_type}")
    for index in range(count):
        node = tree.nodes.new(node_type)
        node.name = f"{domain.title()} Node {index:04d}"
        node.label = f"Stage {index // 64:02d}"
        node.location = ((index % 32) * 180.0, -(index // 32) * 120.0)
    return tree


def benchmark_tree(server, tree, raw_export):
    reference = tree_ref(tree)
    middle = sorted(node.name for node in tree.nodes)[len(tree.nodes) // 2]
    full_ms, full = timed(lambda: raw_export(reference, "semantic"))
    operations_ms, operations = timed(
        lambda: raw_export(reference, "operations")
    )
    public_ms, public = timed(lambda: server.export_node_tree(reference, "auto"))
    targeted_ms, targeted = timed(
        lambda: server.export_node_tree(reference, "semantic", [middle], 1)
    )
    index_ms, index = timed(
        lambda: server.get_node_tree_index(reference, middle, 0, 10)
    )
    full_bytes = full["stats"]["json_bytes"]
    operations_bytes = operations["stats"]["json_bytes"]
    targeted_bytes = targeted["stats"]["json_bytes"]
    index_bytes = encoded_bytes(index)
    combined_bytes = targeted_bytes + index_bytes
    assert_true(
        full["revision"] == operations["revision"] == targeted["revision"] == index["revision"],
        "revision drift",
    )
    assert_true(targeted["scope"]["kind"] == "subgraph", "targeted scope missing")
    assert_true(targeted_bytes < full_bytes, "targeted result is not smaller")
    assert_true(
        operations_bytes < full_bytes * 0.75,
        "operations view is not materially smaller",
    )
    if public.get("status") == "summary":
        assert_true(bool(public.get("next_action")), "soft-limit summary lacks next action")
        assert_true("nodes" not in public["tree"], "soft-limit summary leaked the full graph")
    if len(tree.nodes) >= 2048:
        assert_true(public.get("status") == "summary", "oversized auto export was not summarized")
    if len(tree.nodes) >= 256:
        assert_true(combined_bytes < full_bytes * 0.15, "index-first context is not materially smaller")
    return {
        "nodes": len(tree.nodes),
        "flat_nodes_object": isinstance(full["tree"]["nodes"], dict),
        "full": {
            "ms": full_ms,
            "bytes": full_bytes,
            "estimated_tokens": estimated_tokens(full_bytes),
        },
        "targeted": {
            "ms": targeted_ms,
            "bytes": targeted_bytes,
            "estimated_tokens": estimated_tokens(targeted_bytes),
        },
        "operations": {
            "ms": operations_ms,
            "bytes": operations_bytes,
            "estimated_tokens": estimated_tokens(operations_bytes),
            "semantic_ratio": round(operations_bytes / full_bytes, 5),
        },
        "public_auto": {
            "ms": public_ms,
            "view": public["view"],
            "summary": public.get("status") == "summary",
            "bytes_before_summary": public["stats"]["json_bytes"],
            "has_actionable_next_step": bool(public.get("next_action")) if public.get("status") == "summary" else True,
        },
        "index": {
            "ms": index_ms,
            "bytes": index_bytes,
            "estimated_tokens": estimated_tokens(index_bytes),
            "matches": index["total_matches"],
        },
        "index_targeted_ratio": round(combined_bytes / full_bytes, 5),
    }


def model_tasks(server, namespace):
    tree = bpy.data.node_groups.new(PREFIX + "ModelTasks", "ShaderNodeTree")
    value_a = tree.nodes.new("ShaderNodeValue")
    value_a.name = "Control A"
    value_b = tree.nodes.new("ShaderNodeValue")
    value_b.name = "Control B"
    math_node = tree.nodes.new("ShaderNodeMath")
    math_node.name = "Combine Controls"
    frame = tree.nodes.new("NodeFrame")
    frame.name = "Human Notes"
    tree.links.new(value_a.outputs[0], math_node.inputs[0])
    reference = tree_ref(tree)
    snapshot = server.export_node_tree(reference, "all")
    revision = snapshot["revision"]

    discover_ms, discover = timed(
        lambda: server.get_node_tree_index(reference, "Control", 0, 10)
    )
    explain_ms, explain = timed(
        lambda: server.export_node_tree(
            reference, "semantic", [math_node.name], 1
        )
    )
    tasks = {
        "discover": {
            "success": {value_a.name, value_b.name}.issubset({
                item["name"] for item in discover["nodes"]
            }),
            "context_bytes": encoded_bytes(discover),
            "ms": discover_ms,
            "matches": discover["total_matches"],
        },
        "explain": {
            "success": value_a.name in explain["tree"]["nodes"],
            "context_bytes": explain["stats"]["json_bytes"],
            "ms": explain_ms,
        },
    }
    task_patches = {
        "add_node": make_patch(
            reference,
            revision,
            [{
                "op": "add_node",
                "id": "new_control",
                "node_type": "ShaderNodeValue",
                "name": "New Control",
            }],
            ["graph"],
        ),
        "reconnect": make_patch(
            reference,
            revision,
            [
                {
                    "op": "remove_link",
                    "from_node": value_a.name,
                    "from_socket": socket_id(value_a, value_a.outputs[0], "output"),
                    "to_node": math_node.name,
                    "to_socket": socket_id(math_node, math_node.inputs[0], "input"),
                },
                {
                    "op": "add_link",
                    "from_node": value_b.name,
                    "from_socket": socket_id(value_b, value_b.outputs[0], "output"),
                    "to_node": math_node.name,
                    "to_socket": socket_id(math_node, math_node.inputs[0], "input"),
                },
            ],
            ["graph"],
        ),
        "tune_property": make_patch(
            reference,
            revision,
            [{
                "op": "set_socket_default",
                "node": math_node.name,
                "socket": socket_id(math_node, math_node.inputs[1], "input"),
                "value": 0.625,
            }],
            ["graph"],
        ),
        "annotate": make_patch(
            reference,
            revision,
            [{
                "op": "set_annotation",
                "node": frame.name,
                "text": "Explain the control flow without nesting the graph.",
            }],
            ["annotation"],
        ),
    }
    for name, task_patch in task_patches.items():
        validation = server.validate_node_tree_patch(task_patch)
        patch_bytes = encoded_bytes(task_patch)
        tasks[name] = {
            "success": validation["valid"],
            "patch_bytes": patch_bytes,
            "estimated_tokens": estimated_tokens(patch_bytes),
            "validation_ms": validation["timing_ms"],
        }

    rollback_patch = task_patches["add_node"]

    def rollback_guard(stage, _original, _working):
        if stage == "after_working_verified":
            raise RuntimeError("N5 injected rollback")

    rollback = namespace["_node_apply_patch_transaction"](
        namespace["_node_resolve_tree_ref"](reference),
        rollback_patch,
        True,
        _commit_guard=rollback_guard,
    )
    tasks["rollback"] = {
        "success": rollback["status"] == "rolled_back",
        "patch_bytes": encoded_bytes(rollback_patch),
        "live_revision_unchanged": (
            server.export_node_tree(reference, "all")["revision"] == revision
        ),
    }
    assert_true(all(task["success"] for task in tasks.values()), tasks)
    assert_true(tasks["rollback"]["live_revision_unchanged"], "rollback changed live tree")
    for task in tasks.values():
        context_bytes = task.get("context_bytes", task.get("patch_bytes", 0))
        task["estimated_context_tokens"] = estimated_tokens(context_bytes)
    return tasks


def run_test():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_node_efficiency_test",
    )
    server = namespace["BlenderMCPServer"]()
    raw_export = lambda reference, view: namespace["_node_export_target"](
        namespace["_node_resolve_tree_ref"](reference), view
    )
    trees = {
        domain: [build_benchmark_tree(domain, size) for size in SIZES]
        for domain in ("shader", "compositor")
    }
    metrics = {
        domain: {
            str(len(tree.nodes)): benchmark_tree(server, tree, raw_export)
            for tree in domain_trees
        }
        for domain, domain_trees in trees.items()
    }
    tasks = model_tasks(server, namespace)
    result = {
        "version": list(bpy.app.version[:3]),
        "sizes": list(SIZES),
        "metrics": metrics,
        "tasks": tasks,
        "all_tasks_succeeded": all(task["success"] for task in tasks.values()),
        "flat_json_default": all(
            item["flat_nodes_object"]
            for domain in metrics.values()
            for item in domain.values()
        ),
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["flat_json_default"], "canonical graph was not flat")
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    result["leaks"] = {
        "node_groups": [
            tree.name for tree in bpy.data.node_groups if tree.name.startswith(PREFIX)
        ]
    }
    assert_true(not result["leaks"]["node_groups"], result["leaks"])
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
