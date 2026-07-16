"""Live read-only acceptance for generic owner-addressed node-tree tools."""

from __future__ import annotations

import json
import runpy
import traceback
from pathlib import Path

import bpy

PREFIX = "__BLENDER_MCP_NODE_READONLY__"
RESULT_PREFIX = "BLENDER_MCP_NODE_READONLY_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def cleanup():
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(PREFIX):
            bpy.data.meshes.remove(mesh, do_unlink=True)
    for light in list(bpy.data.lights):
        if light.name.startswith(PREFIX):
            bpy.data.lights.remove(light, do_unlink=True)
    for material in list(bpy.data.materials):
        if material.name.startswith(PREFIX):
            bpy.data.materials.remove(material, do_unlink=True)
    for world in list(bpy.data.worlds):
        if world.name.startswith(PREFIX):
            bpy.data.worlds.remove(world, do_unlink=True)
    for scene in list(bpy.data.scenes):
        if scene.name.startswith(PREFIX):
            bpy.data.scenes.remove(scene, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)
    for image in list(bpy.data.images):
        if image.name.startswith(PREFIX):
            bpy.data.images.remove(image, do_unlink=True)


def tree_ref(tree_type, owner_kind, owner_name):
    return {
        "tree_type": tree_type,
        "owner": {"kind": owner_kind, "name": owner_name},
    }


def create_compositor_tree(scene):
    if hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(PREFIX + "SceneTree", "CompositorNodeTree")
        tree.interface.new_socket(
            name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
        )
        tree.nodes.new("NodeGroupOutput")
        scene.compositing_node_group = tree
        return tree, "scene_compositing_node_group"
    scene.use_nodes = True
    return scene.node_tree, "scene_embedded_node_tree"


def run_test():
    cleanup()
    active_scene = bpy.context.scene
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_node_readonly_test",
    )
    server = namespace["BlenderMCPServer"]()

    material = bpy.data.materials.new(PREFIX + "Material")
    material.use_nodes = True
    frame = material.node_tree.nodes.new("NodeFrame")
    frame.name = "Stage Notes"
    frame.label = "Stage 1: base shader"
    frame["blender_mcp_note"] = "Detailed human-readable stage description."
    image = bpy.data.images.new(PREFIX + "Image", width=4, height=4)
    image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    image_node.name = "Reference Image"
    image_node.image = image
    mesh = bpy.data.meshes.new(PREFIX + "Mesh")
    mesh.materials.append(material)
    obj = bpy.data.objects.new(PREFIX + "Object", mesh)

    world = bpy.data.worlds.new(PREFIX + "World")
    world.use_nodes = True
    world_scene = bpy.data.scenes.new(PREFIX + "WorldScene")
    world_scene.world = world

    light = bpy.data.lights.new(PREFIX + "Light", "POINT")
    light.use_nodes = True
    light_obj = bpy.data.objects.new(PREFIX + "LightObject", light)

    shader_group = bpy.data.node_groups.new(PREFIX + "ShaderGroup", "ShaderNodeTree")
    shader_group.interface.new_socket(
        name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat"
    )
    shader_group.nodes.new("ShaderNodeValue")
    shader_group.nodes.new("NodeGroupOutput")
    shader_group_node = material.node_tree.nodes.new("ShaderNodeGroup")
    shader_group_node.node_tree = shader_group

    compositor_scene = bpy.data.scenes.new(PREFIX + "CompositorScene")
    compositor_scene_tree, expected_scene_adapter = create_compositor_tree(
        compositor_scene
    )
    compositor_scene_tree.nodes.new("CompositorNodeCurveRGB")

    compositor_group = bpy.data.node_groups.new(
        PREFIX + "CompositorGroup", "CompositorNodeTree"
    )
    compositor_group.interface.new_socket(
        name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    compositor_group.nodes.new("NodeGroupOutput")
    compositor_group_node = compositor_scene_tree.nodes.new("CompositorNodeGroup")
    compositor_group_node.node_tree = compositor_group

    geometry_group = bpy.data.node_groups.new(
        PREFIX + "GeometryGroup", "GeometryNodeTree"
    )
    geometry_group.nodes.new("GeometryNodeMeshCube")
    named_attribute_nodes = []
    for suffix in ("A", "B"):
        node = geometry_group.nodes.new("GeometryNodeInputNamedAttribute")
        node.name = f"Velocity Reader {suffix}"
        node.inputs["Name"].default_value = "velocity"
        named_attribute_nodes.append(node.name)
    zone_items = []
    for input_type, output_type, collection_name, item_name in (
        (
            "GeometryNodeSimulationInput",
            "GeometryNodeSimulationOutput",
            "state_items",
            "Velocity",
        ),
        (
            "GeometryNodeRepeatInput",
            "GeometryNodeRepeatOutput",
            "repeat_items",
            "Value",
        ),
    ):
        if (
            getattr(bpy.types, input_type, None) is None
            or getattr(bpy.types, output_type, None) is None
        ):
            continue
        output_node = geometry_group.nodes.new(output_type)
        input_node = geometry_group.nodes.new(input_type)
        input_node.pair_with_output(output_node)
        collection = getattr(output_node, collection_name)
        item = collection.new("FLOAT", item_name)
        zone_items.append({
            "input": input_node.name,
            "output": output_node.name,
            "collection": collection_name,
            "item_name": item.name,
        })

    listing = server.list_node_trees()
    listed_refs = {
        json.dumps(item["tree_ref"], sort_keys=True): item
        for item in listing["trees"]
    }
    expected_refs = [
        tree_ref("ShaderNodeTree", "MATERIAL", material.name),
        tree_ref("ShaderNodeTree", "WORLD", world.name),
        tree_ref("ShaderNodeTree", "LIGHT", light.name),
        tree_ref("ShaderNodeTree", "NODE_GROUP", shader_group.name),
        tree_ref("CompositorNodeTree", "SCENE", compositor_scene.name),
        tree_ref("CompositorNodeTree", "NODE_GROUP", compositor_group.name),
        tree_ref("GeometryNodeTree", "NODE_GROUP", geometry_group.name),
    ]
    for expected in expected_refs:
        key = json.dumps(expected, sort_keys=True)
        assert_true(key in listed_refs, f"tree missing from generic list: {expected}")
        expected_apply = expected["tree_type"] in {
            "ShaderNodeTree", "CompositorNodeTree",
        }
        capabilities = listed_refs[key]["capabilities"]
        assert_true(
            capabilities["apply"] == expected_apply
            and capabilities["validate"] == expected_apply,
            f"unexpected apply capability for {expected}",
        )
        expected_reason = (
            "available"
            if expected_apply
            else "geometry_uses_v1_mutation_tools"
        )
        assert_true(
            capabilities["mutation_reason"] == expected_reason,
            f"unexpected mutation route for {expected}",
        )

    shader_only = server.list_node_trees(
        tree_types=["ShaderNodeTree"], owner_kinds=["MATERIAL"]
    )
    assert_true(
        any(item["tree_ref"]["owner"]["name"] == material.name for item in shader_only["trees"]),
        "filtered material tree missing",
    )
    assert_true(
        all(item["tree_ref"]["owner"]["kind"] == "MATERIAL" for item in shader_only["trees"]),
        "owner-kind filter leaked another owner",
    )

    material_ref = tree_ref("ShaderNodeTree", "MATERIAL", material.name)
    first = server.export_node_tree(material_ref, "semantic")
    second = server.export_node_tree(material_ref, "semantic")
    material_operations = server.export_node_tree(material_ref, "operations")
    assert_true(first == second, "unchanged material export is not deterministic")
    assert_true(first["schema"] == "blender-node-tree/1", "wrong generic schema")
    assert_true(first["tree_ref"] == material_ref, "tree_ref drifted")
    assert_true(
        first["tree"]["nodes"][frame.name]["annotation"]["text"].startswith("Detailed"),
        "Frame annotation missing",
    )
    image_property = first["tree"]["nodes"][image_node.name]["properties"]["image"]
    assert_true(
        image_property["$type"] == "ID" and image_property["name"] == image.name,
        "typed Image reference missing",
    )
    assert_true(
        any(user["id_type"] == "Mesh" for user in first["users"] if user["kind"] == "ID"),
        "Material user map missing Mesh",
    )
    for snapshot in (first, material_operations):
        group_reference = snapshot["tree"]["nodes"][shader_group_node.name][
            "properties"
        ]["node_tree"]
        assert_true(
            group_reference["$type"] == "ID"
            and group_reference["id_type"] == "ShaderNodeTree"
            and group_reference["name"] == shader_group.name
            and group_reference["library"] is None,
            f"Shader group reference missing from {snapshot['view']} view",
        )
    compact_output = material_operations["tree"]["nodes"][shader_group_node.name][
        "outputs"
    ][0]
    semantic_output = first["tree"]["nodes"][shader_group_node.name]["outputs"][0]
    assert_true(
        compact_output["id"] == semantic_output["id"],
        "Compact socket display metadata changed the stable socket ID",
    )
    if semantic_output["name"] != semantic_output["identifier"]:
        assert_true(
            compact_output.get("name") == semantic_output["name"],
            "Compact socket omitted its human-readable name",
        )

    material_link = first["tree"]["links"][0]
    query_cases = {
        "fields": server.query_node_graph(
            material_ref,
            "fields",
            node_names=[shader_group_node.name],
            fields=["name", "bl_idname", "properties"],
        ),
        "socket_links": server.query_node_graph(
            material_ref,
            "socket_links",
            node_names=[material_link["from_node"]],
            socket_id=material_link["from_socket"],
        ),
        "shortest_path": server.query_node_graph(
            material_ref,
            "shortest_path",
            from_node=material_link["from_node"],
            to_node=material_link["to_node"],
        ),
        "upstream": server.query_node_graph(
            material_ref,
            "upstream",
            node_names=[material_link["to_node"]],
        ),
        "downstream": server.query_node_graph(
            material_ref,
            "downstream",
            node_names=[material_link["from_node"]],
        ),
        "slice": server.query_node_graph(
            material_ref,
            "slice",
            node_names=[material_link["to_node"]],
            direction="both",
        ),
    }
    for query_type, query_result in query_cases.items():
        assert_true(
            query_result["schema"] == "blender-node-graph-query/1"
            and query_result["query_type"] == query_type
            and query_result["revision"] == first["revision"],
            f"Invalid {query_type} query envelope",
        )
        repeated = server.query_node_graph(
            material_ref,
            query_type,
            **{
                "fields": {
                    "node_names": [shader_group_node.name],
                    "fields": ["name", "bl_idname", "properties"],
                },
                "socket_links": {
                    "node_names": [material_link["from_node"]],
                    "socket_id": material_link["from_socket"],
                },
                "shortest_path": {
                    "from_node": material_link["from_node"],
                    "to_node": material_link["to_node"],
                },
                "upstream": {"node_names": [material_link["to_node"]]},
                "downstream": {"node_names": [material_link["from_node"]]},
                "slice": {
                    "node_names": [material_link["to_node"]],
                    "direction": "both",
                },
            }[query_type],
        )
        assert_true(repeated == query_result, f"{query_type} query is not deterministic")
    assert_true(
        query_cases["fields"]["records"][0]["properties"]["node_tree"]["name"]
        == shader_group.name,
        "Field projection omitted compact group identity",
    )
    assert_true(
        query_cases["socket_links"]["records"][0] == material_link,
        "Socket-link query changed stable link identity",
    )

    for invalid_kwargs, expected_text in (
        (
            {"query_type": "fields", "fields": ["unknown_field"]},
            "fields contains unsupported values: unknown_field",
        ),
        (
            {"query_type": "upstream", "node_names": ["Missing Node"]},
            "node_names contains unknown nodes: Missing Node",
        ),
        (
            {"query_type": "shortest_path", "from_node": material_link["from_node"]},
            "from_node and to_node are required for shortest_path",
        ),
        (
            {"query_type": "socket_links", "fields": ["name"]},
            "fields is only supported for query_type='fields'",
        ),
    ):
        try:
            server.query_node_graph(material_ref, **invalid_kwargs)
        except ValueError as exc:
            assert_true(expected_text in str(exc), f"Wrong query diagnostic: {exc}")
        else:
            raise AssertionError(f"Invalid query was accepted: {invalid_kwargs}")

    index = server.get_node_tree_index(material_ref, query="Reference", limit=10)
    assert_true(index["revision"] == first["revision"], "index revision mismatch")
    assert_true(
        [node["name"] for node in index["nodes"]] == [image_node.name],
        "index query returned unexpected nodes",
    )
    targeted = server.export_node_tree(
        material_ref, "semantic", [image_node.name], 0
    )
    assert_true(
        list(targeted["tree"]["nodes"]) == [image_node.name],
        "targeted export included unrelated nodes",
    )
    assert_true(
        targeted["stats"]["json_bytes"] < first["stats"]["json_bytes"],
        "targeted export was not smaller",
    )

    scene_ref = tree_ref("CompositorNodeTree", "SCENE", compositor_scene.name)
    scene_export = server.export_node_tree(scene_ref, "all")
    scene_operations = server.export_node_tree(scene_ref, "operations")
    assert_true(
        scene_export["capabilities"]["transaction_adapter"] == expected_scene_adapter,
        "wrong Scene compositor adapter",
    )
    assert_true(
        scene_export["owner"]["kind"] == "SCENE",
        "Scene owner metadata missing",
    )
    for snapshot in (scene_export, scene_operations):
        group_reference = snapshot["tree"]["nodes"][compositor_group_node.name][
            "properties"
        ]["node_tree"]
        assert_true(
            group_reference["id_type"] == "CompositorNodeTree"
            and group_reference["name"] == compositor_group.name,
            f"Compositor group reference missing from {snapshot['view']} view",
        )

    geometry_ref = tree_ref(
        "GeometryNodeTree", "NODE_GROUP", geometry_group.name
    )
    geometry_semantic = server.export_node_tree(geometry_ref, "semantic")
    geometry_operations = server.export_node_tree(geometry_ref, "operations")
    geometry_layout = server.export_node_tree(geometry_ref, "layout")
    assert_true(
        geometry_semantic["revision"]
        == geometry_operations["revision"]
        == geometry_layout["revision"],
        "Geometry revisions drifted across views",
    )
    named_attributes = server.query_node_graph(
        geometry_ref,
        "named_attributes",
        node_names=named_attribute_nodes,
        attribute_name="velocity",
        limit=1,
    )
    assert_true(
        named_attributes["revision"] == geometry_semantic["revision"]
        and named_attributes["total_matches"] == 2
        and named_attributes["truncated"]
        and len(named_attributes["records"]) == 1,
        "Named Attribute query did not filter and truncate deterministically",
    )
    for zone in zone_items:
        compact_input = geometry_operations["tree"]["nodes"][zone["input"]]
        pair = compact_input["properties"].get("paired_output")
        assert_true(
            pair and pair["name"] == zone["output"],
            f"Compact Zone pair missing for {zone['input']}",
        )
        compact_output = geometry_operations["tree"]["nodes"][zone["output"]]
        structure = next(
            item for item in compact_output["special_structures"]
            if item["identifier"] == zone["collection"]
        )
        named_item = next(
            item for item in structure["items"]
            if item["values"].get("name") == zone["item_name"]
        )
        assert_true(
            named_item["index"] >= 0
            and bool(named_item["values"].get("socket_type")),
            f"Dynamic item identity missing for {zone['collection']}",
        )
        layout_output = geometry_layout["tree"]["nodes"][zone["output"]]
        assert_true(
            "special_structures" not in layout_output
            and layout_output["properties"] == {},
            "Layout-only export leaked dynamic semantic metadata",
        )

    ramp_schema = server.get_node_type_schema(
        "ShaderNodeTree", "ShaderNodeValToRGB", "MATERIAL", "compact"
    )
    assert_true(ramp_schema["schema"] == "blender-node-type-schema/1", "schema id wrong")
    assert_true(
        any(item["type"] == "COLOR_RAMP" for item in ramp_schema["special_structures"]),
        "Color Ramp structure missing",
    )
    render_layers_schema = server.get_node_type_schema(
        "CompositorNodeTree", "CompositorNodeRLayers", "SCENE", "compact"
    )
    assert_true(
        render_layers_schema["owner_kind"] == "SCENE",
        "Render Layers owner context missing",
    )
    if bpy.app.version[:2] == (4, 2):
        composite_schema = server.get_node_type_schema(
            "CompositorNodeTree", "CompositorNodeComposite", "SCENE", "compact"
        )
        assert_true(composite_schema["node_type"] == "CompositorNodeComposite", "4.2 output missing")
    else:
        try:
            server.get_node_type_schema(
                "CompositorNodeTree", "CompositorNodeComposite", "SCENE", "compact"
            )
        except ValueError as exc:
            assert_true("Unsupported" in str(exc), "wrong removed-node diagnostic")
        else:
            raise AssertionError("removed Composite node unexpectedly resolved")

    try:
        server.export_node_tree(
            tree_ref("ShaderNodeTree", "SCENE", compositor_scene.name)
        )
    except ValueError as exc:
        assert_true("cannot own" in str(exc), "wrong domain mismatch diagnostic")
    else:
        raise AssertionError("invalid owner/domain reference was accepted")

    geometry_v1 = server.export_geometry_node_tree(geometry_group.name, "semantic")
    assert_true(
        geometry_v1["schema"] == "blender-geometry-nodes/1",
        "Geometry v1 schema changed",
    )
    assert_true("tree_ref" not in geometry_v1, "Geometry v1 envelope changed")

    result = {
        "version": list(bpy.app.version[:3]),
        "listed_expected_trees": len(expected_refs),
        "scene_adapter": expected_scene_adapter,
        "material_revision": first["revision"],
        "material_bytes": first["stats"]["json_bytes"],
        "targeted_bytes": targeted["stats"]["json_bytes"],
        "zone_items": len(zone_items),
        "query_types": len(query_cases) + 1,
        "active_scene_unchanged": bpy.context.scene == active_scene,
    }
    assert_true(result["active_scene_unchanged"], "active Scene changed")
    cleanup()
    result["leaks"] = {
        "objects": [item.name for item in bpy.data.objects if item.name.startswith(PREFIX)],
        "meshes": [item.name for item in bpy.data.meshes if item.name.startswith(PREFIX)],
        "lights": [item.name for item in bpy.data.lights if item.name.startswith(PREFIX)],
        "materials": [item.name for item in bpy.data.materials if item.name.startswith(PREFIX)],
        "worlds": [item.name for item in bpy.data.worlds if item.name.startswith(PREFIX)],
        "scenes": [item.name for item in bpy.data.scenes if item.name.startswith(PREFIX)],
        "node_groups": [item.name for item in bpy.data.node_groups if item.name.startswith(PREFIX)],
        "images": [item.name for item in bpy.data.images if item.name.startswith(PREFIX)],
    }
    assert_true(not any(result["leaks"].values()), f"datablock leak: {result['leaks']}")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))


try:
    run_test()
except Exception:
    traceback.print_exc()
    cleanup()
    raise
