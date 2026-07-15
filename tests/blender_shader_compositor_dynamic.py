"""Inventory versioned dynamic collections and typed ID references."""

from __future__ import annotations

import json
import traceback

import bpy

PREFIX = "__BLENDER_MCP_SC_DYNAMIC__"
RESULT_PREFIX = "BLENDER_MCP_SC_DYNAMIC_RESULT="


def describe_pointer(value):
    if value is None:
        return None
    record = {"rna_type": value.bl_rna.identifier}
    if hasattr(value, "__len__"):
        try:
            record["count"] = len(value)
        except Exception:
            pass
    return record


def describe_node(node):
    properties = {}
    for prop in node.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        if prop.identifier in {
            "color_ramp", "mapping", "file_slots", "layer_slots", "image",
            "scene", "layer", "view_layer", "mask", "clip", "base_path",
        }:
            try:
                value = getattr(node, prop.identifier)
                if isinstance(value, (str, bool, int, float)) or value is None:
                    encoded = value
                elif isinstance(value, bpy.types.ID):
                    encoded = {"id_type": value.bl_rna.identifier, "name": value.name}
                else:
                    encoded = describe_pointer(value)
                properties[prop.identifier] = {
                    "rna_type": prop.type,
                    "readonly": bool(prop.is_readonly),
                    "value": encoded,
                }
            except Exception as exc:
                properties[prop.identifier] = {"error": f"{type(exc).__name__}: {exc}"}
    details = {}
    if hasattr(node, "color_ramp"):
        ramp = node.color_ramp
        details["color_ramp"] = {
            "elements": [
                {"position": item.position, "color": list(item.color)}
                for item in ramp.elements
            ],
            "interpolation": ramp.interpolation,
        }
    if hasattr(node, "mapping"):
        mapping = node.mapping
        details["mapping"] = {
            "curves": [
                {
                    "points": [
                        {"location": list(point.location), "handle_type": point.handle_type}
                        for point in curve.points
                    ]
                }
                for curve in mapping.curves
            ],
        }
    for slot_name in ("file_slots", "layer_slots"):
        if hasattr(node, slot_name):
            slots = getattr(node, slot_name)
            details[slot_name] = {
                "rna_type": slots.bl_rna.identifier,
                "items": [
                    {
                        prop.identifier: getattr(item, prop.identifier)
                        for prop in item.bl_rna.properties
                        if prop.identifier in {"name", "path", "use_node_format"}
                    }
                    for item in slots
                ],
            }
    return {
        "bl_idname": node.bl_idname,
        "properties": properties,
        "details": details,
        "inputs": [socket.name for socket in node.inputs],
        "outputs": [socket.name for socket in node.outputs],
    }


def add(tree, node_type):
    try:
        return {"ok": True, "node": describe_node(tree.nodes.new(node_type))}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cleanup():
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def main():
    cleanup()
    result = {"version": list(bpy.app.version[:3]), "shader": {}, "compositor": {}}
    shader = bpy.data.node_groups.new(PREFIX + "Shader", "ShaderNodeTree")
    for node_type in (
        "ShaderNodeValToRGB", "ShaderNodeRGBCurve", "ShaderNodeTexImage",
        "ShaderNodeTexEnvironment", "ShaderNodeScript",
    ):
        result["shader"][node_type] = add(shader, node_type)

    scene = bpy.data.scenes.new(PREFIX + "Scene")
    if hasattr(scene, "compositing_node_group"):
        compositor = bpy.data.node_groups.new(PREFIX + "Compositor", "CompositorNodeTree")
        scene.compositing_node_group = compositor
    else:
        scene.use_nodes = True
        compositor = scene.node_tree
    for node_type in (
        "CompositorNodeValToRGB", "CompositorNodeCurveRGB", "CompositorNodeImage",
        "CompositorNodeMask", "CompositorNodeMovieClip", "CompositorNodeRLayers",
        "CompositorNodeOutputFile", "CompositorNodeViewer", "CompositorNodeComposite",
    ):
        result["compositor"][node_type] = add(compositor, node_type)

    cleanup()
    result["leaks"] = {
        "scenes": [scene.name for scene in bpy.data.scenes if scene.name.startswith(PREFIX)],
        "node_groups": [tree.name for tree in bpy.data.node_groups if tree.name.startswith(PREFIX)],
    }
    assert result["shader"]["ShaderNodeValToRGB"]["ok"]
    assert result["shader"]["ShaderNodeRGBCurve"]["ok"]
    assert not any(result["leaks"].values())
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    main()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
