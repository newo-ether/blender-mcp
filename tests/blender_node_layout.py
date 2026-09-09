"""Isolated Blender runtime acceptance for layout measurement/planning/patches."""

import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blender_extension.bridge.nodes import NodeCommandsMixin
from blender_extension.nodes.geometry_operations import _gn_apply_operations_to_working
from blender_extension.nodes.geometry_validation import _gn_validate_patch_runtime
from blender_extension.nodes.node_validation import _node_validate_patch_runtime
from blender_extension.nodes.patch_operations import _node_execute_patch_operations
from blender_extension.nodes.targets import _node_resolve_tree_ref


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    server = NodeCommandsMixin()
    results = []
    for domain in ("GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree"):
        tree = bpy.data.node_groups.new("LayoutAcceptance" + domain, domain)
        ref = {"tree_type": domain, "owner": {"kind": "NODE_GROUP", "name": tree.name}}
        frame = tree.nodes.new("NodeFrame")
        frame.label = "Processing"
        frame.location = (1000, 200)
        kind = "CompositorNodeMath" if domain == "CompositorNodeTree" and bpy.app.version < (5, 0, 0) else "ShaderNodeMath"
        functions = [tree.nodes.new(kind) for _ in range(4)]
        for node in functions:
            node.parent = frame
        for a, b in zip(functions, functions[1:]):
            tree.links.new(a.outputs[0], b.inputs[0])
        before = server.inspect_node_layout(ref)
        measure = server.inspect_node_layout(ref)
        check(before["revision"] == measure["revision"], "Measurement changed source revision")
        hints = {node.name: [140, 180] for node in functions}
        missing = server.plan_node_layout(ref, before["revision"])
        check(missing["status"] == "blocked", "Undrawn dimensions were silently accepted")
        plan = server.plan_node_layout(ref, before["revision"], size_hints=hints)
        check(plan["status"] == "planned", repr(plan))
        patch = plan["patch"]
        if domain == "GeometryNodeTree":
            validation = _gn_validate_patch_runtime(tree, patch)
        else:
            validation = _node_validate_patch_runtime(_node_resolve_tree_ref(ref), patch)
        check(validation["valid"], repr(validation))
        check(server.inspect_node_layout(ref)["revision"] == before["revision"], "Plan/validation mutated source")
        # Exercise actual transaction, retaining the existing save/backup rules.
        if domain == "GeometryNodeTree":
            applied = server.apply_geometry_node_patch(patch, keep_backup=False)
        else:
            applied = server.apply_node_tree_patch(patch, keep_backup=False)
        check(applied.get("applied", applied.get("status") == "applied"), repr(applied))
        tree = bpy.data.node_groups[ref["owner"]["name"]]
        check(len(tree.nodes) == 5 and len(tree.links) == 3, "Layout changed computation")
        stale = server.plan_node_layout(ref, before["revision"], size_hints=hints)
        check(stale["status"] == "stale_revision", "Old revision was accepted")
        snapshot = server.inspect_node_layout(ref)
        audit = server.audit_node_layout(ref)
        check(not audit["bounds_verified"], "Headless scene claimed verified display bounds")
        # Regression: combined parent + location is final-parent local.
        new_frame = tree.nodes.new("NodeFrame")
        new_frame.location = (1200, 500)
        moved = next(n for n in tree.nodes if n.bl_idname == kind)
        operation = {"op": "set_node_layout", "node": moved.name, "parent": new_frame.name, "location": [50, -80]}
        assignment_patch = dict(patch, base_revision=server.inspect_node_layout(ref)["revision"], operations=[operation])
        validation = server.validate_geometry_node_patch(assignment_patch) if domain == "GeometryNodeTree" else server.validate_node_tree_patch(assignment_patch)
        check(validation["valid"], repr(validation))
        if domain == "GeometryNodeTree":
            _gn_apply_operations_to_working(tree, {"operations": [operation]})
        else:
            result = _node_execute_patch_operations(_node_resolve_tree_ref(ref), {"operations": [operation]})
            check(not result["diagnostics"], repr(result))
        check(tuple(moved.location) == (50, -80), "Reparenting broke explicit local coordinates")
        addition = {"op": "add_node", "id": "added", "name": "AddedLayoutNode", "node_type": kind,
                    "layout": {"parent": new_frame.name, "location": [75, -120]}}
        if domain == "GeometryNodeTree":
            _gn_apply_operations_to_working(tree, {"operations": [addition]})
        else:
            result = _node_execute_patch_operations(_node_resolve_tree_ref(ref), {"operations": [addition]})
            check(not result["diagnostics"], repr(result))
        check(tuple(tree.nodes["AddedLayoutNode"].location) == (75, -120), "add_node used the old parent coordinates")
        cycle = {"op": "set_node_layout", "node": new_frame.name, "parent": new_frame.name}
        if domain == "GeometryNodeTree":
            try:
                _gn_apply_operations_to_working(tree, {"operations": [cycle]})
            except ValueError:
                pass
            else:
                raise AssertionError("Parent cycle accepted")
        else:
            result = _node_execute_patch_operations(_node_resolve_tree_ref(ref), {"operations": [cycle]})
            check(bool(result["diagnostics"]), "Generic parent cycle accepted")
        results.append({"domain": domain, "snapshot": snapshot, "audit": audit, "plan": plan})
        # Four child Frames count as four units in their parent; each child has
        # four real processing nodes. Exercise parent-local coordinates through
        # the real transaction in every domain, not only in the pure planner.
        nested = bpy.data.node_groups.new("NestedLayout" + domain, domain)
        nested_ref = {"tree_type": domain, "owner": {"kind": "NODE_GROUP", "name": nested.name}}
        outer = nested.nodes.new("NodeFrame")
        outer.label = "Assembly"
        functions = []
        for index in range(4):
            child = nested.nodes.new("NodeFrame")
            child.parent = outer
            child.label = f"Stage {index + 1}"
            for _ in range(4):
                node = nested.nodes.new(kind)
                node.parent = child
                functions.append(node)
        for a, b in zip(functions, functions[1:]):
            nested.links.new(a.outputs[0], b.inputs[0])
        before_nested = server.inspect_node_layout(nested_ref)
        nested_plan = server.plan_node_layout(nested_ref, before_nested["revision"],
                                             size_hints={n.name: [140, 180] for n in functions})
        check(nested_plan["status"] == "planned", repr(nested_plan))
        check(nested_plan["predicted_audit"]["valid"], "Nested plan failed bounds/count audit")
        check(set(nested_plan["predicted_audit"]["frame_child_counts"].values()) == {4}, "Nested child count changed")
        if domain == "GeometryNodeTree":
            applied = server.apply_geometry_node_patch(nested_plan["patch"], keep_backup=False)
        else:
            applied = server.apply_node_tree_patch(nested_plan["patch"], keep_backup=False)
        check(applied.get("applied", applied.get("status") == "applied"), repr(applied))
        nested_snapshot = server.inspect_node_layout(nested_ref)
        nested_audit = server.audit_node_layout(nested_ref)
        check(nested_audit["valid"] and set(nested_audit["frame_child_counts"].values()) == {4}, "Nested parents did not survive apply")
        results.append({"domain": domain, "nested": True, "snapshot": nested_snapshot, "audit": nested_audit, "plan": nested_plan})
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    destination = Path(arguments[0]) if arguments else None
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print("BLENDER_NODE_LAYOUT_RESULT=" + json.dumps({"ok": True, "domains": 3, "fixtures": len(results), "version": bpy.app.version_string}))


run()
