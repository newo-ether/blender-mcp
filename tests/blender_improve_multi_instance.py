"""Live acceptance for improve-plan diagnostics, dynamic Patch, and claim safety."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import bpy


def parse_args():
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    return parser.parse_args(arguments)


def load_addon(path):
    spec = importlib.util.spec_from_file_location("blender_mcp_improve_acceptance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    addon = load_addon(Path(parse_args().addon).resolve())
    server = addon.BlenderMCPServer()

    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = "Improve Probe"

    mesh_result = server.inspect_evaluated_mesh(obj.name)
    assert mesh_result["original"]["vertices"] == 8
    assert mesh_result["evaluated"]["vertices"] == 8
    assert mesh_result["cleanup"]["temporary_mesh_cleared"]

    tree = bpy.data.node_groups.new("Improve Dynamic", "GeometryNodeTree")
    modifier = obj.modifiers.new("Geometry Nodes", "NODES")
    modifier.node_group = tree
    scale_socket = tree.interface.new_socket(
        name="Scale", in_out='INPUT', socket_type="NodeSocketFloat"
    )
    list_node = tree.nodes.new("GeometryNodeFieldToList")
    list_node.name = "List Builder"
    value_node = tree.nodes.new("ShaderNodeValue")
    value_node.name = "Query Source"
    math_node = tree.nodes.new("ShaderNodeMath")
    math_node.name = "Query Target"
    tree.links.new(value_node.outputs[0], math_node.inputs[0])
    modifier_adapter = addon._gn_set_modifier_input_value(
        modifier, scale_socket.identifier, 2.5
    )

    object_info = server.get_object_info(obj.name, include_modifiers=True)
    assert object_info["modifiers"][0]["node_group"]["name"] == tree.name
    scale_input = next(
        item for item in object_info["modifiers"][0]["inputs"]
        if item["identifier"] == scale_socket.identifier
    )
    assert scale_input["fields"].get("value", scale_input["fields"].get(scale_socket.identifier)) == 2.5
    assert scale_input["adapter"] == modifier_adapter

    missing_image = bpy.data.images.new("Missing dependency", 4, 4)
    missing_image.source = 'FILE'
    missing_image.filepath = "//missing-texture-for-mcp-test.png"
    dependency_audit = server.audit_external_dependencies(missing_only=True)
    assert any(item["name"] == missing_image.name for item in dependency_audit["dependencies"])
    with tempfile.TemporaryDirectory(prefix="blender-mcp-relink-") as directory:
        replacement = Path(directory) / "missing-texture-for-mcp-test.png"
        replacement.write_bytes(b"not-an-image-but-an-existing-relink-target")
        relink_plan = server.plan_external_dependency_relinks([directory])
        assert len(relink_plan["actions"]) == 1, relink_plan
        relink_result = server.apply_external_dependency_relinks(relink_plan)
        assert relink_result["status"] == "applied", relink_result
        assert Path(bpy.path.abspath(missing_image.filepath)).resolve() == replacement.resolve()

    tree_ref = {
        "tree_type": "GeometryNodeTree",
        "owner": {"kind": "NODE_GROUP", "name": tree.name},
    }
    query = server.query_node_graph(
        tree_ref, "fields", node_names=[list_node.name], fields=["name", "bl_idname"]
    )
    assert query["records"][0]["bl_idname"] == "GeometryNodeFieldToList"
    socket_query = server.query_node_graph(
        tree_ref,
        "socket_links",
        node_names=[value_node.name],
        socket_id=addon._gn_socket_id(value_node.outputs[0], "OUTPUT", 0),
    )
    assert socket_query["total_matches"] == 1, socket_query
    assert socket_query["records"][0]["to_node"] == math_node.name

    operations = [{
        "op": "add_dynamic_item",
        "node": list_node.name,
        "collection": "list_items",
        "socket_type": "FLOAT",
        "name": "Weight",
    }, {
        "op": "add_foreach_zone",
        "input_id": "foreach_input",
        "output_id": "foreach_output",
        "location": [200.0, 0.0],
    }]
    if bpy.app.version >= (5, 2, 0):
        operations.append({
            "op": "add_closure_zone",
            "input_id": "closure_input",
            "output_id": "closure_output",
            "location": [700.0, 0.0],
        })
    patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": addon._gn_export_tree(tree, "all")["revision"],
        "shared_tree_policy": "reject",
        "operations": operations,
    }
    validation = server.validate_geometry_node_patch(patch)
    assert validation["valid"], validation
    before_failed_assertion = addon._gn_export_tree(tree, "all")["revision"]
    rejected_workflow = server.modify_verify_save(
        "geometry_nodes",
        patch,
        assertions=[{"field": "node_count", "op": "gte", "value": 100}],
        keep_backup=False,
        save_policy="never",
    )
    assert rejected_workflow["status"] == "assertion_failed", rejected_workflow
    assert addon._gn_export_tree(tree, "all")["revision"] == before_failed_assertion
    try:
        server.modify_verify_save(
            "geometry_nodes", patch, keep_backup=False, save_policy="required"
        )
    except addon.BlenderMCPAddonError as error:
        assert error.code == "file_permission_error"
    else:
        raise AssertionError("save_policy=required accepted an Untitled file")
    workflow = server.modify_verify_save(
        "geometry_nodes",
        patch,
        assertions=[{"field": "node_count", "op": "gte", "value": 3}],
        keep_backup=False,
        save_policy="never",
    )
    assert workflow["status"] == "completed", workflow
    assert workflow["verification"]["matches_candidate"]
    assert not workflow["saved"]
    application = workflow["application"]
    assert application["status"] == "applied", application
    applied_tree = bpy.data.node_groups["Improve Dynamic"]
    applied_list = applied_tree.nodes["List Builder"]
    assert any(item.name == "Weight" for item in applied_list.list_items)
    assert any(node.bl_idname == "GeometryNodeForeachGeometryElementOutput" for node in applied_tree.nodes)
    if bpy.app.version >= (5, 2, 0):
        assert any(node.bl_idname == "NodeClosureOutput" for node in applied_tree.nodes)

    weight_index = next(
        index for index, item in enumerate(applied_list.list_items) if item.name == "Weight"
    )
    set_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": applied_tree.name,
        "base_revision": addon._gn_export_tree(applied_tree, "all")["revision"],
        "shared_tree_policy": "reject",
        "operations": [{
            "op": "set_dynamic_item",
            "node": applied_list.name,
            "collection": "list_items",
            "index": weight_index,
            "property": "name",
            "value": "Renamed Weight",
        }],
    }
    set_result = server.apply_geometry_node_patch(set_patch, keep_backup=False)
    assert set_result["status"] == "applied", set_result
    applied_tree = bpy.data.node_groups["Improve Dynamic"]
    applied_list = applied_tree.nodes["List Builder"]
    assert applied_list.list_items[weight_index].name == "Renamed Weight"

    remove_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": applied_tree.name,
        "base_revision": addon._gn_export_tree(applied_tree, "all")["revision"],
        "shared_tree_policy": "reject",
        "operations": [{
            "op": "remove_dynamic_item",
            "node": applied_list.name,
            "collection": "list_items",
            "index": weight_index,
        }],
    }
    remove_result = server.apply_geometry_node_patch(remove_patch, keep_backup=False)
    assert remove_result["status"] == "applied", remove_result
    applied_tree = bpy.data.node_groups["Improve Dynamic"]
    assert all(
        item.name != "Renamed Weight"
        for item in applied_tree.nodes["List Builder"].list_items
    )

    sim_tree = bpy.data.node_groups.new("Simulation Probe", "GeometryNodeTree")
    sim_modifier = obj.modifiers.new("Simulation", "NODES")
    sim_modifier.node_group = sim_tree
    sim_input = sim_tree.nodes.new("GeometryNodeSimulationInput")
    sim_output = sim_tree.nodes.new("GeometryNodeSimulationOutput")
    sim_input.pair_with_output(sim_output)
    simulation = server.get_simulation_status(obj.name, sim_modifier.name)
    assert simulation["simulation_count"] == 1
    assert simulation["capabilities"]["clear"]
    simulation_info = server.get_object_info(obj.name, include_modifiers=True)
    simulation_modifier_info = next(
        item for item in simulation_info["modifiers"] if item["name"] == sim_modifier.name
    )
    assert simulation_modifier_info["simulation"]["zone_count"] == 1
    frame_before_reset = bpy.context.scene.frame_current
    clear_result = server.clear_simulation_cache(
        obj.name, sim_modifier.name, simulation["modifiers"][0]["bakes"][0]["bake_id"]
    )
    assert clear_result["status"] == "completed", clear_result
    reset_result = server.reset_simulation(obj.name, sim_modifier.name)
    assert reset_result["status"] == "completed", reset_result
    assert bpy.context.scene.frame_current == frame_before_reset

    claim = server.claim_blender_instance(
        addon._BLENDER_MCP_INSTANCE_ID,
        addon._BLENDER_MCP_FILE_SESSION_ID,
        "acceptance-client",
        "Acceptance",
        30,
    )
    envelope = {
        "_instance_id": addon._BLENDER_MCP_INSTANCE_ID,
        "_file_session_id": addon._BLENDER_MCP_FILE_SESSION_ID,
        "_client_id": "acceptance-client",
        "_claim_token": claim["claim_token"],
    }
    server._authorize_command("apply_node_tree_patch", envelope)
    try:
        server._authorize_command("apply_node_tree_patch", {})
    except addon.BlenderMCPAddonError as error:
        assert error.code in {"claim_expired", "instance_changed"}
    else:
        raise AssertionError("Mutation without a claim envelope was accepted")
    assert server.release_blender_instance("acceptance-client", claim["claim_token"])["released"]

    print("BLENDER_MCP_IMPROVE_MULTI=" + json.dumps({
        "version": bpy.app.version_string,
        "dynamic_operations": len(operations),
        "dynamic_set_remove": True,
        "evaluated_vertices": mesh_result["evaluated"]["vertices"],
        "simulation_count": simulation["simulation_count"],
        "claim_released": True,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
