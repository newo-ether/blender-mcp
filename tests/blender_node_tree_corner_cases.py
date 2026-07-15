"""Live N5 acceptance for flat graphs, concurrency, limits, and corner cases."""

from __future__ import annotations

import base64
import json
import runpy
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_NODE_CORNERS__"
RESULT_PREFIX = "BLENDER_MCP_NODE_CORNERS_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]
CUSTOM_REGISTERED = False


class BlenderMCPTestCustomShaderNode(bpy.types.ShaderNode):
    bl_idname = "BlenderMCPTestCustomShaderNode"
    bl_label = "Blender MCP Test Custom Node"

    def init(self, _context):
        self.outputs.new("NodeSocketFloat", "Value")


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def cleanup():
    for collection_name in (
        "objects", "meshes", "materials", "worlds", "lights", "scenes",
        "node_groups", "images", "masks",
    ):
        collection = getattr(bpy.data, collection_name)
        for value in list(collection):
            if value.name.startswith(PREFIX):
                collection.remove(value, do_unlink=True)


def unregister_custom():
    global CUSTOM_REGISTERED
    if CUSTOM_REGISTERED:
        bpy.utils.unregister_class(BlenderMCPTestCustomShaderNode)
        CUSTOM_REGISTERED = False


def reference(tree_type, name):
    return {
        "tree_type": tree_type,
        "owner": {"kind": "NODE_GROUP", "name": name},
    }


def patch(tree_ref, revision, operations, capabilities):
    return {
        "schema": "blender-node-tree-patch/1",
        "tree_ref": tree_ref,
        "base_revision": revision,
        "capabilities": capabilities,
        "operations": operations,
    }


def diagnostic_codes(result):
    return {item["code"] for item in result["diagnostics"]}


def run_test():
    global CUSTOM_REGISTERED
    cleanup()
    active_scene = bpy.context.scene
    bpy.utils.register_class(BlenderMCPTestCustomShaderNode)
    CUSTOM_REGISTERED = True
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_node_corner_cases_test",
    )
    server = namespace["BlenderMCPServer"]()
    runtime_globals = namespace["BlenderMCPServer"].export_node_tree.__globals__
    from blender_extension.nodes import node_validation as patch_runtime

    unicode_name = PREFIX + "着色器_ノード_🎨"
    shader = bpy.data.node_groups.new(unicode_name, "ShaderNodeTree")
    panel = shader.interface.new_panel("阶段一 · Controls")
    interface_socket = shader.interface.new_socket(
        name="强度 🌟",
        in_out="INPUT",
        socket_type="NodeSocketFloat",
        parent=panel,
    )
    interface_socket.default_value = 0.75
    frame = shader.nodes.new("NodeFrame")
    frame.name = "注释框 · 全角"
    frame["blender_mcp_note"] = "这是一段 Unicode 教程注释。"
    vector_math = shader.nodes.new("ShaderNodeVectorMath")
    vector_math.name = "Duplicate Vector Sockets"
    vector_math.parent = frame
    mix = shader.nodes.new("ShaderNodeMix")
    mix.name = "Conditional Sockets"
    mix.data_type = "FLOAT"
    reroute = shader.nodes.new("NodeReroute")
    reroute.name = "Reroute → Stable"
    reroute.parent = frame
    image = bpy.data.images.new(PREFIX + "GeneratedImage", width=8, height=8)
    image.generated_color = (0.1, 0.2, 0.3, 1.0)
    packed = False
    try:
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        image.pack(data=png_data, data_len=len(png_data))
        packed = bool(image.packed_file)
    except RuntimeError:
        packed = False
    image_node = shader.nodes.new("ShaderNodeTexImage")
    image_node.name = "Generated/Packed Image"
    image_node.image = image

    cycle_peer = bpy.data.node_groups.new(PREFIX + "CyclePeer", "ShaderNodeTree")
    to_peer = shader.nodes.new("ShaderNodeGroup")
    to_peer.name = "Flat Group Reference"
    to_peer.node_tree = cycle_peer
    to_shader = cycle_peer.nodes.new("ShaderNodeGroup")
    cycle_rejected = False
    try:
        to_shader.node_tree = shader
    except RuntimeError:
        cycle_rejected = True

    shader_ref = reference("ShaderNodeTree", shader.name)
    exported = server.export_node_tree(shader_ref, "all")
    vector_record = exported["tree"]["nodes"][vector_math.name]
    duplicate_vectors = [
        item for item in vector_record["inputs"] if item["name"] == "Vector"
    ]
    assert_true(len(duplicate_vectors) >= 2, "duplicate-name socket fixture missing")
    assert_true(
        len({item["id"] for item in duplicate_vectors}) == len(duplicate_vectors),
        "duplicate-name sockets do not have stable distinct IDs",
    )
    mix_record = exported["tree"]["nodes"][mix.name]
    assert_true(
        any(not item["enabled"] for item in mix_record["inputs"]),
        "disabled conditional sockets were not serialized",
    )
    assert_true(
        exported["tree"]["nodes"][frame.name]["annotation"]["text"].startswith("这是"),
        "Unicode annotation was not preserved",
    )
    assert_true(
        exported["tree"]["nodes"][image_node.name]["properties"]["image"]["name"]
        == image.name,
        "generated image ID reference was not serialized",
    )
    assert_true(
        isinstance(exported["tree"]["nodes"], dict)
        and all(isinstance(link, dict) for link in exported["tree"]["links"]),
        "canonical graph is not flat normalized JSON",
    )
    if not cycle_rejected:
        peer_export = server.export_node_tree(
            reference("ShaderNodeTree", cycle_peer.name), "all"
        )
        assert_true(
            peer_export["stats"]["node_count"] == 1,
            "accepted cyclic group reference caused recursive export",
        )

    geometry = bpy.data.node_groups.new(PREFIX + "MultiInput", "GeometryNodeTree")
    join = geometry.nodes.new("GeometryNodeJoinGeometry")
    geometry_export = server.export_node_tree(
        reference("GeometryNodeTree", geometry.name), "all"
    )
    join_record = geometry_export["tree"]["nodes"][join.name]
    assert_true(
        any(item["multi_input"] for item in join_record["inputs"]),
        "multi-input socket metadata missing",
    )

    custom_group = bpy.data.node_groups.new(PREFIX + "CustomReadOnly", "ShaderNodeTree")
    custom_node = custom_group.nodes.new(BlenderMCPTestCustomShaderNode.bl_idname)
    custom_node.name = "Add-on Node"
    custom_ref = reference("ShaderNodeTree", custom_group.name)
    custom_before = server.export_node_tree(custom_ref, "all")
    assert_true(
        custom_before["tree"]["nodes"][custom_node.name]["bl_idname"]
        == BlenderMCPTestCustomShaderNode.bl_idname,
        "custom node was not exported read-only",
    )
    custom_patch = patch(
        custom_ref,
        custom_before["revision"],
        [{"op": "rename_node", "node": custom_node.name, "name": "Must Not Rename"}],
        ["graph"],
    )
    custom_validation = server.validate_node_tree_patch(custom_patch)
    assert_true(not custom_validation["valid"], "custom node mutation validated")
    assert_true(
        "custom_node_read_only" in diagnostic_codes(custom_validation),
        "custom node rejection reason missing",
    )
    assert_true(
        server.export_node_tree(custom_ref, "all") == custom_before,
        "custom node validation mutated the live graph",
    )

    concurrency_name = PREFIX + "Concurrent_同时编辑"
    concurrency = bpy.data.node_groups.new(concurrency_name, "ShaderNodeTree")
    concurrency.nodes.new("ShaderNodeValue").name = "Control"
    concurrency_ref = reference("ShaderNodeTree", concurrency_name)
    concurrency_before = server.export_node_tree(concurrency_ref, "all")
    first_patch = patch(
        concurrency_ref,
        concurrency_before["revision"],
        [{"op": "add_node", "id": "first", "node_type": "NodeFrame", "name": "First Edit"}],
        ["graph"],
    )
    second_patch = patch(
        concurrency_ref,
        concurrency_before["revision"],
        [{"op": "add_node", "id": "second", "node_type": "NodeFrame", "name": "Second Edit"}],
        ["graph"],
    )
    first_application = server.apply_node_tree_patch(first_patch, keep_backup=False)
    second_application = server.apply_node_tree_patch(second_patch, keep_backup=False)
    assert_true(first_application["status"] == "applied", first_application)
    assert_true(second_application["status"] == "rejected", second_application)
    assert_true(
        "stale_revision" in diagnostic_codes(second_application),
        "concurrent stale edit was not rejected",
    )
    committed_concurrency = bpy.data.node_groups[concurrency_name]
    assert_true(
        committed_concurrency.nodes.get("First Edit") is not None
        and committed_concurrency.nodes.get("Second Edit") is None,
        "concurrent rejection partially applied",
    )

    id_group = bpy.data.node_groups.new(PREFIX + "MissingID", "ShaderNodeTree")
    id_node = id_group.nodes.new("ShaderNodeTexImage")
    id_ref = reference("ShaderNodeTree", id_group.name)
    id_before = server.export_node_tree(id_ref, "all")
    missing_id = server.validate_node_tree_patch(patch(
        id_ref,
        id_before["revision"],
        [{
            "op": "set_node_property",
            "node": id_node.name,
            "property": "image",
            "value": {"$type": "ID", "id_type": "Image", "name": PREFIX + "Missing"},
        }],
        ["graph", "id_reference"],
    ))
    assert_true(
        not missing_id["valid"] and "invalid_pointer_value" in diagnostic_codes(missing_id),
        "missing typed ID did not fail closed",
    )

    full_limit = runtime_globals["NODE_TREE_MAX_RESPONSE_BYTES"]
    runtime_globals["NODE_TREE_MAX_RESPONSE_BYTES"] = 1
    full_response_rejected = False
    try:
        server.export_node_tree(shader_ref, "semantic")
    except ValueError as exc:
        full_response_rejected = "get_node_tree_index" in str(exc)
    targeted = server.export_node_tree(
        shader_ref, "semantic", [vector_math.name], 1
    )
    runtime_globals["NODE_TREE_MAX_RESPONSE_BYTES"] = full_limit
    assert_true(full_response_rejected, "oversized full response was not redirected")
    assert_true(targeted["scope"]["kind"] == "subgraph", "targeted export was blocked")

    depth_rejected = index_rejected = False
    try:
        server.export_node_tree(shader_ref, "semantic", [vector_math.name], 6)
    except ValueError:
        depth_rejected = True
    try:
        server.get_node_tree_index(shader_ref, "", 0, 501)
    except ValueError:
        index_rejected = True
    assert_true(depth_rejected and index_rejected, "traversal/index bounds were not enforced")

    limit_ref = reference("ShaderNodeTree", concurrency_name)
    limit_before = server.export_node_tree(limit_ref, "all")
    limit_patch = patch(
        limit_ref,
        limit_before["revision"],
        [{"op": "add_node", "id": "limit", "node_type": "NodeFrame"}],
        ["graph"],
    )
    mutation_limit = patch_runtime.NODE_TREE_MAX_MUTATION_NODES
    patch_runtime.NODE_TREE_MAX_MUTATION_NODES = len(committed_concurrency.nodes)
    projected_limit = server.validate_node_tree_patch(limit_patch)
    patch_runtime.NODE_TREE_MAX_MUTATION_NODES = mutation_limit
    assert_true(
        "projected_tree_node_limit_exceeded" in diagnostic_codes(projected_limit),
        "projected graph-size limit was not enforced",
    )
    validation_limit = patch_runtime.NODE_TREE_MAX_VALIDATION_SECONDS
    patch_runtime.NODE_TREE_MAX_VALIDATION_SECONDS = 0.0
    timed_limit = server.validate_node_tree_patch(limit_patch)
    patch_runtime.NODE_TREE_MAX_VALIDATION_SECONDS = validation_limit
    assert_true(
        "validation_time_limit_exceeded" in diagnostic_codes(timed_limit),
        "validation time limit was not reported",
    )

    result = {
        "version": list(bpy.app.version[:3]),
        "unicode_round_trip": True,
        "duplicate_socket_ids": [item["id"] for item in duplicate_vectors],
        "disabled_socket_count": sum(
            not item["enabled"] for item in mix_record["inputs"]
        ),
        "multi_input": True,
        "cycle_rejected_by_blender": cycle_rejected,
        "generated_image_packed": packed,
        "custom_node_read_only": not custom_validation["valid"],
        "concurrent_stale_rejected": not second_application["applied"],
        "limits": {
            "full_response": full_response_rejected,
            "neighbor_depth": depth_rejected,
            "index_page": index_rejected,
            "projected_nodes": not projected_limit["valid"],
            "validation_time": not timed_limit["valid"],
        },
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    unregister_custom()
    result["leaks"] = {
        collection_name: [
            value.name for value in getattr(bpy.data, collection_name)
            if value.name.startswith(PREFIX)
        ]
        for collection_name in ("node_groups", "images", "materials", "scenes")
    }
    assert_true(not any(result["leaks"].values()), f"corner-case leak: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True, ensure_ascii=False))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    unregister_custom()
    raise
