"""Headless Blender integration test for Geometry Nodes read-only handlers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import traceback

import bpy


PREFIX = "__BLENDER_MCP_GN_READONLY_TEST__"
RESULT_PREFIX = "BLENDER_MCP_GN_READONLY_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]


def remove_fixtures():
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(PREFIX):
            bpy.data.meshes.remove(mesh, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.name.startswith(PREFIX):
            bpy.data.node_groups.remove(tree, do_unlink=True)


def geometry_interface(tree, include_scale=False):
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    scale = None
    if include_scale:
        panel = tree.interface.new_panel(name="Controls")
        scale = tree.interface.new_socket(
            name="Scale",
            in_out="INPUT",
            socket_type="NodeSocketFloat",
            parent=panel,
        )
        scale.default_value = 1.25
    return scale


def build_fixture():
    nested = bpy.data.node_groups.new(PREFIX + "Nested", "GeometryNodeTree")
    geometry_interface(nested)
    nested_input = nested.nodes.new("NodeGroupInput")
    nested_output = nested.nodes.new("NodeGroupOutput")
    nested_transform = nested.nodes.new("GeometryNodeTransform")
    nested.links.new(nested_input.outputs["Geometry"], nested_transform.inputs["Geometry"])
    nested.links.new(nested_transform.outputs["Geometry"], nested_output.inputs["Geometry"])

    tree = bpy.data.node_groups.new(PREFIX + "Main", "GeometryNodeTree")
    scale_interface = geometry_interface(tree, include_scale=True)
    group_input = tree.nodes.new("NodeGroupInput")
    group_output = tree.nodes.new("NodeGroupOutput")
    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (1.0, 2.0, 3.0)
    nested_node = tree.nodes.new("GeometryNodeGroup")
    nested_node.node_tree = nested
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    transform = tree.nodes.new("GeometryNodeTransform")

    tree.links.new(group_input.outputs["Geometry"], nested_node.inputs["Geometry"])
    tree.links.new(cube.outputs["Mesh"], join.inputs["Geometry"])
    tree.links.new(nested_node.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], transform.inputs["Geometry"])
    tree.links.new(transform.outputs["Geometry"], group_output.inputs["Geometry"])

    zones = []
    for input_type, output_type in (
        ("GeometryNodeSimulationInput", "GeometryNodeSimulationOutput"),
        ("GeometryNodeRepeatInput", "GeometryNodeRepeatOutput"),
        (
            "GeometryNodeForeachGeometryElementInput",
            "GeometryNodeForeachGeometryElementOutput",
        ),
    ):
        output_node = tree.nodes.new(output_type)
        input_node = tree.nodes.new(input_type)
        input_node.pair_with_output(output_node)
        zones.append((input_node.name, output_node.name))

    mesh = bpy.data.meshes.new(PREFIX + "Mesh")
    obj = bpy.data.objects.new(PREFIX + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(PREFIX + "Modifier", "NODES")
    modifier.node_group = tree
    return (
        tree,
        nested,
        nested_node.name,
        nested_transform.name,
        join.name,
        cube.name,
        zones,
        obj.name,
        modifier.name,
        scale_interface.identifier,
    )


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def run_test():
    remove_fixtures()
    namespace = runpy.run_path(
        str(REPO_ROOT / "addon.py"),
        run_name="blender_mcp_addon_readonly_test",
    )
    server = namespace["BlenderMCPServer"]()
    (
        tree,
        nested,
        nested_node_name,
        nested_transform_name,
        join_name,
        cube_name,
        zones,
        object_name,
        modifier_name,
        scale_identifier,
    ) = build_fixture()

    first = server.export_geometry_node_tree(tree.name, "semantic")
    second = server.export_geometry_node_tree(tree.name, "semantic")
    layout = server.export_geometry_node_tree(tree.name, "layout")
    subgraph = server.export_geometry_node_tree(tree.name, "semantic", [join_name], 1)
    listing = server.list_geometry_node_trees()

    assert_true(first == second, "Repeated exports must be byte-equivalent as objects")
    assert_true(first["revision"] == second["revision"], "Revision must be deterministic")
    assert_true("layout" not in first["tree"]["nodes"][join_name], "Semantic view leaked layout")
    assert_true(layout["tree"]["links"] == [], "Layout view must omit semantic links")
    assert_true(layout["revision"] == first["revision"], "Source revision must be view-independent")
    assert_true(
        "layout" in layout["tree"]["nodes"][join_name],
        "Layout view omitted node position",
    )
    assert_true(subgraph["scope"]["kind"] == "subgraph", "Subgraph scope missing")
    assert_true(join_name in subgraph["tree"]["nodes"], "Requested node missing")
    assert_true(subgraph["revision"] == first["revision"], "Subgraph lost full-tree revision")
    assert_true(
        subgraph["stats"]["node_count"] < subgraph["stats"]["total_node_count"],
        "Subgraph export did not reduce graph size",
    )
    join_node = tree.nodes[join_name]
    original_location = join_node.location.copy()
    join_node.location.x += 37.0
    layout_changed = server.export_geometry_node_tree(tree.name, "semantic")
    assert_true(
        layout_changed["revision"] != first["revision"],
        "Semantic export revision failed to detect a layout-only source change",
    )
    join_node.location = original_location
    assert_true(
        server.export_geometry_node_tree(tree.name, "semantic") == first,
        "Restoring layout did not restore the deterministic snapshot",
    )
    assert_true(
        first["tree"]["nodes"][nested_node_name]["properties"]["node_tree"]["name"]
        == nested.name,
        "Nested group ID reference was not encoded",
    )
    assert_true(
        sum("multi_input_sort_id" in link for link in first["tree"]["links"]) >= 2,
        "Multi-input link ordering was not exported",
    )
    for input_name, output_name in zones:
        pair = first["tree"]["nodes"][input_name]["properties"].get("paired_output")
        assert_true(pair and pair["name"] == output_name, f"Zone pair missing for {input_name}")

    main_summary = next(item for item in listing["trees"] if item["name"] == tree.name)
    assert_true(main_summary["revision"] == first["revision"], "List/export revisions differ")
    assert_true(
        any(user["kind"] == "MODIFIER" for user in first["users"]),
        "Modifier user missing",
    )
    assert_true(
        any(user["kind"] == "GROUP_NODE" for user in server.export_geometry_node_tree(nested.name)["users"]),
        "Nested group user missing",
    )

    before_groups = len(bpy.data.node_groups)
    type_schema = server.get_geometry_node_type_schema("GeometryNodeJoinGeometry")
    assert_true(type_schema["node_type"] == "GeometryNodeJoinGeometry", "Wrong node schema")
    assert_true(any(item["multi_input"] for item in type_schema["inputs"]), "Missing multi-input socket")
    assert_true(len(bpy.data.node_groups) == before_groups, "Type schema leaked temporary tree")

    patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "operations": [
            {
                "op": "add_node",
                "id": "new_transform",
                "node_type": "GeometryNodeTransform",
                "name": PREFIX + "PatchTransform",
                "layout": {"location": [600.0, 100.0]},
            },
            {
                "op": "set_node_property",
                "node": "new_transform",
                "property": "mute",
                "value": True,
            },
            {
                "op": "set_socket_default",
                "node": "new_transform",
                "socket": "input:2:Translation",
                "value": [1.0, 2.0, 3.0],
            },
            {
                "op": "remove_link",
                "from_node": cube_name,
                "from_socket": "output:0:Mesh",
                "to_node": join_name,
                "to_socket": "input:0:Geometry",
            },
            {
                "op": "add_link",
                "from_node": cube_name,
                "from_socket": "output:0:Mesh",
                "to_node": "new_transform",
                "to_socket": "input:0:Geometry",
            },
            {
                "op": "add_link",
                "from_node": "new_transform",
                "from_socket": "output:0:Geometry",
                "to_node": join_name,
                "to_socket": "input:0:Geometry",
            },
            {
                "op": "rename_node",
                "node": cube_name,
                "name": PREFIX + "RenamedCube",
            },
            {
                "op": "set_node_layout",
                "node": join_name,
                "location": [800.0, 0.0],
                "width": 180.0,
            },
            {
                "op": "add_interface_socket",
                "id": "new_density",
                "name": "Density",
                "in_out": "INPUT",
                "socket_type": "NodeSocketFloat",
                "default": 0.5,
            },
            {
                "op": "remove_interface_socket",
                "identifier": "new_density",
            },
            {
                "op": "set_modifier_input",
                "object": object_name,
                "modifier": modifier_name,
                "socket": scale_identifier,
                "value": 2.0,
            },
            {
                "op": "remove_node",
                "node": "new_transform",
            },
        ],
    }
    groups_before_dry_run = len(bpy.data.node_groups)
    dry_run = server.validate_geometry_node_patch(patch)
    assert_true(dry_run["valid"], f"Valid patch was rejected: {dry_run['diagnostics']}")
    assert_true(not dry_run["will_mutate"], "Dry-run claimed it would mutate")
    assert_true(all(item["status"] == "ready" for item in dry_run["plan"]), "Plan not ready")
    assert_true(len(dry_run["plan"]) == len(patch["operations"]), "Incomplete plan")
    assert_true(
        dry_run.get("candidate_revision"),
        "Dry-run did not execute and re-export a candidate working tree",
    )
    assert_true(
        len(bpy.data.node_groups) == groups_before_dry_run,
        "Dry-run leaked a temporary validation tree",
    )
    after_dry_run = server.export_geometry_node_tree(tree.name, "semantic")
    assert_true(after_dry_run == first, "Dry-run mutated the live node tree")

    stale_patch = {**patch, "base_revision": "sha256:" + "0" * 64, "operations": patch["operations"][:1]}
    stale_result = server.validate_geometry_node_patch(stale_patch)
    assert_true(not stale_result["valid"], "Stale patch was accepted")
    assert_true(
        any(item["code"] == "stale_revision" for item in stale_result["diagnostics"]),
        "Stale revision diagnostic missing",
    )

    invalid_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "operations": [
            {
                "op": "set_node_property",
                "node": cube_name,
                "property": "definitely_missing",
                "value": 1,
            },
            {
                "op": "set_socket_default",
                "node": cube_name,
                "socket": "input:99:Size",
                "value": [1.0, 1.0, 1.0],
            },
        ],
    }
    invalid_result = server.validate_geometry_node_patch(invalid_patch)
    invalid_by_code = {item["code"]: item["path"] for item in invalid_result["diagnostics"]}
    assert_true(
        invalid_by_code.get("unknown_rna_property") == "/operations/0/property",
        "Unknown property diagnostic path is unstable",
    )
    assert_true(
        invalid_by_code.get("socket_index_out_of_range") == "/operations/1/socket",
        "Invalid socket diagnostic path is unstable",
    )

    second_mesh = bpy.data.meshes.new(PREFIX + "SharedMesh")
    second_object = bpy.data.objects.new(PREFIX + "SharedObject", second_mesh)
    bpy.context.scene.collection.objects.link(second_object)
    second_modifier = second_object.modifiers.new(PREFIX + "SharedModifier", "NODES")
    second_modifier.node_group = tree
    shared_operation = [{"op": "set_node_layout", "node": join_name, "width": 190.0}]
    shared_reject = server.validate_geometry_node_patch({
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "operations": shared_operation,
    })
    assert_true(
        any(item["code"] == "shared_tree_rejected" for item in shared_reject["diagnostics"]),
        "Shared-tree rejection diagnostic missing",
    )
    shared_copy = server.validate_geometry_node_patch({
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": tree.name,
        "base_revision": first["revision"],
        "shared_tree_policy": "single_user_copy",
        "target_user": {
            "kind": "MODIFIER",
            "object": object_name,
            "modifier": modifier_name,
        },
        "operations": shared_operation,
    })
    assert_true(shared_copy["valid"], f"Explicit shared copy was rejected: {shared_copy['diagnostics']}")

    bpy.data.objects.remove(second_object, do_unlink=True)
    bpy.data.meshes.remove(second_mesh)
    application = server.apply_geometry_node_patch(patch, keep_backup=True)
    assert_true(application["status"] == "applied", f"Patch apply failed: {application}")
    assert_true(application["applied"] and application["mutated"], "Apply result flags are wrong")
    committed_tree = bpy.data.node_groups[application["tree_name"]]
    committed_snapshot = server.export_geometry_node_tree(committed_tree.name, "all")
    backup_tree = bpy.data.node_groups[application["backup"]["tree_name"]]
    backup_snapshot = server.export_geometry_node_tree(backup_tree.name, "all")
    modifier = bpy.data.objects[object_name].modifiers[modifier_name]
    assert_true(modifier.node_group == committed_tree, "Modifier was not committed to working tree")
    assert_true(backup_snapshot["revision"] == first["revision"], "Backup graph changed")
    assert_true(backup_tree.use_fake_user, "Backup was not protected with a fake user")
    assert_true(
        committed_snapshot["revision"] == application["new_revision"],
        "Applied revision does not match re-export",
    )
    assert_true(
        PREFIX + "RenamedCube" in committed_tree.nodes,
        "Rename operation was not committed",
    )
    assert_true(
        cube_name in application["actual_diff"]["nodes_removed"]
        and PREFIX + "RenamedCube" in application["actual_diff"]["nodes_added"],
        "Actual diff did not report the committed rename",
    )
    assert_true("new_transform" not in application["created_nodes"], "Removed node reported as created")
    assert_true(
        "new_density" not in application["created_interface_sockets"],
        "Removed interface socket reported as created",
    )
    assert_true(
        abs(namespace["_gn_modifier_input_value"](modifier, scale_identifier) - 2.0) < 1e-6,
        "Modifier input was not committed",
    )
    expected_adapter = (
        "geometry_nodes_modifier_interface"
        if bpy.app.version >= (5, 2, 0)
        else "legacy_id_property"
    )
    assert_true(
        application["modifier_input_adapters"][0]["adapter"] == expected_adapter,
        "Wrong modifier input runtime adapter",
    )

    committed_before_stale = server.export_geometry_node_tree(committed_tree.name, "all")
    stale_application = server.apply_geometry_node_patch(patch, keep_backup=True)
    assert_true(stale_application["status"] == "rejected", "Stale application was not rejected")
    assert_true(
        server.export_geometry_node_tree(committed_tree.name, "all") == committed_before_stale,
        "Rejected stale application mutated the committed tree",
    )

    copy_mesh = bpy.data.meshes.new(PREFIX + "CopyMesh")
    copy_object = bpy.data.objects.new(PREFIX + "CopyObject", copy_mesh)
    bpy.context.scene.collection.objects.link(copy_object)
    copy_modifier = copy_object.modifiers.new(PREFIX + "CopyModifier", "NODES")
    copy_modifier.node_group = committed_tree
    copy_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": committed_tree.name,
        "base_revision": committed_snapshot["revision"],
        "shared_tree_policy": "single_user_copy",
        "target_user": {
            "kind": "MODIFIER",
            "object": copy_object.name,
            "modifier": copy_modifier.name,
        },
        "operations": [
            {"op": "set_node_layout", "node": join_name, "width": 210.0},
        ],
    }
    copy_application = server.apply_geometry_node_patch(copy_patch, keep_backup=True)
    assert_true(copy_application["status"] == "applied", f"Single-user copy failed: {copy_application}")
    copied_tree = bpy.data.node_groups[copy_application["tree_name"]]
    assert_true(modifier.node_group == committed_tree, "Non-target user moved during single-user copy")
    assert_true(copy_modifier.node_group == copied_tree, "Target user did not move to its copy")
    assert_true(copy_application["backup"] is None, "Single-user copy created a redundant backup")

    rollback_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": committed_tree.name,
        "base_revision": committed_snapshot["revision"],
        "operations": [
            {"op": "set_node_layout", "node": join_name, "width": 220.0},
        ],
    }
    rollback_before = server.export_geometry_node_tree(committed_tree.name, "all")
    groups_before_rollback = len(bpy.data.node_groups)

    def fail_commit():
        raise RuntimeError("injected commit failure")

    rollback_result = namespace["_gn_apply_patch_transaction"](
        committed_tree,
        rollback_patch,
        True,
        fail_commit,
    )
    assert_true(
        rollback_result["status"] == "rolled_back",
        f"Injected failure did not roll back: {rollback_result}",
    )
    assert_true(modifier.node_group == committed_tree, "Rollback did not restore the user")
    assert_true(
        server.export_geometry_node_tree(committed_tree.name, "all") == rollback_before,
        "Rollback changed the original tree",
    )
    assert_true(len(bpy.data.node_groups) == groups_before_rollback, "Rollback leaked working data")

    nested_revision = server.export_geometry_node_tree(nested.name, "all")["revision"]
    nested_patch = {
        "schema": "blender-geometry-nodes-patch/1",
        "tree_name": nested.name,
        "base_revision": nested_revision,
        "shared_tree_policy": "single_user_copy",
        "target_user": {
            "kind": "GROUP_NODE",
            "tree": committed_tree.name,
            "node": nested_node_name,
        },
        "operations": [
            {
                "op": "set_socket_default",
                "node": nested_transform_name,
                "socket": "input:2:Translation",
                "value": [3.0, 2.0, 1.0],
            },
        ],
    }
    nested_application = server.apply_geometry_node_patch(nested_patch, keep_backup=True)
    assert_true(nested_application["status"] == "applied", "Nested group patch failed")
    nested_copy = bpy.data.node_groups[nested_application["tree_name"]]
    assert_true(
        committed_tree.nodes[nested_node_name].node_tree == nested_copy,
        "Target group node did not move to nested copy",
    )
    assert_true(
        backup_tree.nodes[nested_node_name].node_tree == nested,
        "Unrelated backup group node was changed by nested copy",
    )
    nested_translation = nested_copy.nodes[nested_transform_name].inputs["Translation"].default_value
    assert_true(
        all(abs(value - expected) < 1e-6 for value, expected in zip(nested_translation, (3.0, 2.0, 1.0))),
        "Nested localized socket edit was not committed",
    )

    cleanup_tree = bpy.data.node_groups.new(PREFIX + "NoBackup", "GeometryNodeTree")
    cleanup_node = cleanup_tree.nodes.new("GeometryNodeMeshCube")
    cleanup_width = float(cleanup_node.width)
    cleanup_revision = server.export_geometry_node_tree(cleanup_tree.name, "all")["revision"]
    cleanup_result = server.apply_geometry_node_patch(
        {
            "schema": "blender-geometry-nodes-patch/1",
            "tree_name": cleanup_tree.name,
            "base_revision": cleanup_revision,
            "operations": [
                {"op": "set_node_layout", "node": cleanup_node.name, "width": cleanup_width},
            ],
        },
        keep_backup=False,
    )
    assert_true(cleanup_result["status"] == "applied", "No-backup application failed")
    assert_true(cleanup_result["new_revision"] == cleanup_revision, "No-op patch changed revision")
    assert_true(
        sum(cleanup_result["actual_diff"]["summary"].values()) == 0,
        "No-op patch reported unrelated graph changes",
    )
    assert_true(cleanup_result["backup"] == {"kept": False, "tree_name": None}, "Backup cleanup failed")
    assert_true(
        bpy.data.node_groups.get(cleanup_result["tree_name"]) is not None,
        "No-backup commit removed the new tree",
    )
    assert_true(
        bpy.data.node_groups[cleanup_result["tree_name"]].use_fake_user,
        "Userless committed tree will not persist when the blend file is saved",
    )

    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert_true(first["stats"]["json_bytes"] == len(encoded.encode("utf-8")), "json_bytes mismatch")
    json.loads(encoded)
    output_path = os.environ.get("BLENDER_MCP_TEST_OUTPUT")
    if output_path:
        Path(output_path).write_text(
            json.dumps(first, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    validation_output_path = os.environ.get("BLENDER_MCP_TEST_VALIDATION_OUTPUT")
    if validation_output_path:
        Path(validation_output_path).write_text(
            json.dumps(dry_run, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    application_output_path = os.environ.get("BLENDER_MCP_TEST_APPLICATION_OUTPUT")
    if application_output_path:
        Path(application_output_path).write_text(
            json.dumps(application, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "blender_version": list(bpy.app.version[:3]),
        "revision": first["revision"],
        "nodes": first["stats"]["node_count"],
        "links": first["stats"]["link_count"],
        "interface_items": first["stats"]["interface_item_count"],
        "json_bytes": first["stats"]["json_bytes"],
        "tree_count": listing["tree_count"],
        "dry_run_operations": len(dry_run["plan"]),
        "application_status": application["status"],
        "rollback_status": rollback_result["status"],
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
    remove_fixtures()
