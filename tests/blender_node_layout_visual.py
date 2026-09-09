"""Opt-in isolated GUI acceptance; invoke on a factory-startup Blender process."""

import json
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blender_extension.bridge.nodes import NodeCommandsMixin

OUT = Path(sys.argv[sys.argv.index("--") + 1])
OUT.mkdir(parents=True, exist_ok=True)
server = NodeCommandsMixin()
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.smooth_view = 0
tree = bpy.data.node_groups.new("Layout Tool Example", "GeometryNodeTree")
tree.is_modifier = True
tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
tree.interface.new_socket(name="Offset", in_out="INPUT", socket_type="NodeSocketFloat")
tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
module = bpy.data.node_groups.new("Transform Module", "GeometryNodeTree")
module.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
module.interface.new_socket(name="Offset", in_out="INPUT", socket_type="NodeSocketFloat")
module.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
inner_frame = module.nodes.new("NodeFrame")
inner_frame.label = "Transform Steps"
inner_frame.label_size = 20
inner_nodes = [module.nodes.new(kind) for kind in ("NodeGroupInput", "GeometryNodeTransform", "GeometryNodeTransform", "NodeGroupOutput")]
for node in inner_nodes:
    node.parent = inner_frame
for a, b in zip(inner_nodes, inner_nodes[1:]):
    module.links.new(a.outputs[0], b.inputs[0])
module.links.new(inner_nodes[0].outputs[1], inner_nodes[1].inputs["Translation"])

frames = [tree.nodes.new("NodeFrame") for _ in range(2)]
for frame, label in zip(frames, ["Prepare", "Finish"]):
    frame.label = label
    frame.label_size = 20
nodes = []
for kind, frame in zip(
    ["NodeGroupInput", "NodeGroupInput", "GeometryNodeGroup", "GeometryNodeTransform", "NodeGroupInput", "GeometryNodeGroup", "GeometryNodeTransform", "NodeGroupOutput"],
    [frames[0]] * 4 + [frames[1]] * 4,
):
    node = tree.nodes.new(kind)
    node.parent = frame
    if kind == "GeometryNodeGroup":
        node.node_tree = module
        node.width = 180
    nodes.append(node)
for a, b in zip([nodes[0], nodes[2], nodes[3], nodes[5], nodes[6]], [nodes[2], nodes[3], nodes[5], nodes[6], nodes[7]]):
    tree.links.new(a.outputs[0], b.inputs[0])
tree.links.new(nodes[1].outputs[1], nodes[2].inputs[1])
tree.links.new(nodes[4].outputs[1], nodes[5].inputs[1])
for node in [nodes[0], nodes[1], nodes[4]]:
    for socket in node.outputs:
        if not socket.is_linked:
            socket.hide = True
obj = bpy.context.active_object
modifier = obj.modifiers.new("Layout Example", "NODES")
modifier.node_group = tree
window = bpy.context.window
area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
area.type = "NODE_EDITOR"
area.ui_type = "GeometryNodeTree"
area.spaces.active.pin = True
area.spaces.active.node_tree = tree
area.spaces.active.show_region_ui = False
ref = {"tree_type": "GeometryNodeTree", "owner": {"kind": "NODE_GROUP", "name": tree.name}}
main_path = [node.name for node in [nodes[0], nodes[2], nodes[3], nodes[5], nodes[6], nodes[7]]]
targets = [
    ({"tree_type": "GeometryNodeTree", "owner": {"kind": "NODE_GROUP", "name": module.name}},
     [node.name for node in inner_nodes], "module"),
    (ref, main_path, "layout"),
]
target_index = 0
area.spaces.active.node_tree = module
phase = 0


def tick():
    global phase, tree, target_index
    ref, main_path, prefix = targets[target_index]
    try:
        if phase == 0:
            source = server.inspect_node_layout(ref, refresh=True)
            if not source["measurement"]["refreshed"]:
                raise AssertionError("UI redraw did not run")
            plan = server.plan_node_layout(ref, source["revision"], main_path=main_path)
            if plan["status"] != "planned":
                raise AssertionError(repr(plan))
            # This isolated fixture intentionally shares its module twice.
            if prefix == "module":
                plan["patch"]["shared_tree_policy"] = "mutate_shared"
            validation = server.validate_geometry_node_patch(plan["patch"])
            if not validation["valid"]:
                raise AssertionError(repr(validation))
            result = server.apply_geometry_node_patch(plan["patch"], keep_backup=False)
            if not result["applied"]:
                raise AssertionError(repr(result))
            tree = bpy.data.node_groups[ref["owner"]["name"]]
            area.spaces.active.node_tree = tree
            phase = 1
            return 1.0
        if phase == 1:
            # Shrinking frames update on redraw. Fit the view only after that
            # draw, otherwise view_all frames the old, overlapping bounds.
            server.inspect_node_layout(ref, refresh=True)
            with bpy.context.temp_override(window=window, area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
                bpy.ops.node.select_all(action="DESELECT")
                bpy.ops.node.view_all()
            phase = 2
            return 1.0
        snapshot = server.inspect_node_layout(ref, refresh=True)
        audit = server.audit_node_layout(ref, refresh=True)
        if not audit["valid"] or not audit["bounds_verified"]:
            raise AssertionError(repr(audit))
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screenshot_area(filepath=str(OUT / f"{prefix}.png"))
        (OUT / f"{prefix}-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        (OUT / f"{prefix}-result.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        print("LAYOUT_VISUAL=" + json.dumps({"tree": ref["owner"]["name"], "valid": audit["valid"], "bounds_verified": audit["bounds_verified"], "codes": audit["diagnostic_counts"]}), flush=True)
        target_index += 1
        if target_index < len(targets):
            area.spaces.active.node_tree = bpy.data.node_groups[targets[target_index][0]["owner"]["name"]]
            phase = 0
            return 1.0
    except Exception:
        error = traceback.format_exc()
        (OUT / "error.txt").write_text(error, encoding="utf-8")
        print(error, flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=2.0)
