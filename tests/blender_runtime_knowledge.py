"""Live Blender acceptance for compact node schema and node discovery."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    return parser.parse_args(arguments)


def main() -> None:
    addon_path = Path(parse_args().addon).resolve()
    spec = importlib.util.spec_from_file_location(
        "blender_mcp_runtime_knowledge_acceptance",
        addon_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on module from {addon_path}")
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    try:
        spec.loader.exec_module(addon)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    server = object.__new__(addon.BlenderMCPServer)

    before_scenes = len(bpy.data.scenes)
    automation = server.get_runtime_automation_context()
    assert automation["schema"] == "blender-runtime-automation-context/1"
    assert automation["render"]["available_engines"]
    assert len(bpy.data.scenes) == before_scenes
    if bpy.app.version >= (5, 1, 0):
        assert automation["render"]["preferred"]["eevee"] == "BLENDER_EEVEE"
        assert automation["animation"]["has_layers"]
        assert automation["compositor"]["scene_property"] == "compositing_node_group"

    before_groups = len(bpy.data.node_groups)
    node_type = (
        "GeometryNodeXPBDSolver"
        if bpy.app.version >= (5, 2, 0)
        else "GeometryNodeJoinGeometry"
    )
    compact = server.get_geometry_node_type_schema(node_type, detail="compact")
    full = server.get_geometry_node_type_schema(node_type, detail="full")
    compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
    full_bytes = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
    assert compact["detail"] == "compact"
    assert full["detail"] == "full"
    assert compact["node_type"] == node_type
    assert compact_bytes < full_bytes
    assert compact_bytes < 30_000
    assert compact["build_hash"]
    if node_type == "GeometryNodeXPBDSolver":
        input_names = {item["name"] for item in compact["inputs"]}
        assert {"World", "Substeps", "Constraint Iterations"}.issubset(input_names)
        assert compact["outputs"][0]["name"] == "World"
        assert compact["inputs"][0]["bl_idname"] == "NodeSocketBundle"
        assert compact_bytes * 4 < full_bytes
    else:
        assert any(item["multi_input"] for item in compact["inputs"])

    repeat = server.get_geometry_node_type_schema(
        "GeometryNodeRepeatOutput",
        detail="compact",
    )
    repeat_items = next(
        item for item in repeat["dynamic_items"]
        if item["identifier"] == "repeat_items"
    )
    assert repeat_items["type"] == "COLLECTION"
    assert repeat_items["count"] >= 1

    query = "XPBD" if bpy.app.version >= (5, 2, 0) else "Join Geometry"
    first_catalog = server.search_geometry_node_types(query=query, limit=20)
    second_catalog = server.search_geometry_node_types(query=query, limit=20)
    assert first_catalog == second_catalog
    assert first_catalog["total_types"] > 100
    assert first_catalog["total_matches"] >= 1
    assert any(
        item["bl_idname"] == node_type
        for item in first_catalog["node_types"]
    )
    assert len(bpy.data.node_groups) == before_groups

    before_ids = set(addon._gn_blend_data_ids())
    if bpy.app.version >= (5, 2, 0):
        assets = server.search_blender_node_assets(
            library="geometry_nodes_dynamics_assets.blend",
            detail="summary",
            limit=20,
        )
        expected_assets = {
            "Set Effector",
            "Hair Dynamics",
            "Custom Force",
            "Custom Effector",
            "Collider",
            "Cloth Dynamics (Experimental)",
        }
        assert {item["name"] for item in assets["assets"]} == expected_assets
        assert assets["total_matches"] == 6
        full_asset = server.search_blender_node_assets(
            query="Cloth Dynamics",
            library="geometry_nodes_dynamics_assets.blend",
            detail="full",
            limit=1,
        )
        assert full_asset["assets"][0]["interface"]
        assert full_asset["assets"][0]["node_count"] > 20
    else:
        assets = server.search_blender_node_assets(
            tree_type="GeometryNodeTree",
            detail="summary",
            limit=1,
        )
        assert assets["total_assets"] > 0
        assert assets["total_matches"] > 0
    cached_assets = server.search_blender_node_assets(
        tree_type="GeometryNodeTree",
        detail="summary",
        limit=1,
    )
    assert cached_assets["errors"] == []
    assert set(addon._gn_blend_data_ids()) == before_ids

    fixture_library = None
    fixture_tree = None
    fixture_import_before = None
    with tempfile.TemporaryDirectory(prefix="blender-mcp-user-assets-") as temp_root:
        try:
            fixture_tree = bpy.data.node_groups.new(
                "Index Field Fixture", "GeometryNodeTree"
            )
            fixture_tree.interface.new_socket(
                name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat"
            )
            fixture_tree.asset_mark()
            fixture_tree.asset_data.description = "Configured user-library fixture"
            fixture_path = Path(temp_root) / "Index Field Fixture.blend"
            bpy.data.libraries.write(str(fixture_path), {fixture_tree})
            bpy.data.node_groups.remove(fixture_tree, do_unlink=True)
            fixture_tree = None

            libraries = bpy.context.preferences.filepaths.asset_libraries
            fixture_library = libraries.new(
                name="Blender MCP Test Library",
                directory=temp_root,
            )
            before_user_search = set(addon._gn_blend_data_ids())
            user_assets = server.search_blender_node_assets(
                query="Index Field Fixture",
                library="Blender MCP Test Library",
                tree_type="GeometryNodeTree",
                detail="full",
                scope="USER",
                limit=5,
            )
            assert user_assets["scope"] == "USER"
            assert user_assets["configured_library_count"] == 1
            assert user_assets["total_matches"] == 1
            record = user_assets["assets"][0]
            assert record["name"] == "Index Field Fixture"
            assert record["source_scope"] == "USER"
            assert record["configured_library"]["name"] == "Blender MCP Test Library"
            assert Path(record["source_path"]).resolve() == fixture_path.resolve()
            assert record["interface"][0]["name"] == "Value"
            assert set(addon._gn_blend_data_ids()) == before_user_search

            fixture_import_before = addon._gn_blend_data_ids()
            imported_asset = server.import_blender_node_asset(
                source_path=record["source_path"],
                asset_name=record["name"],
                tree_type=record["tree_type"],
                scope="USER",
                library="Blender MCP Test Library",
                conflict_policy="REJECT",
            )
            assert imported_asset["status"] == "imported"
            assert imported_asset["node_group"]["name"] == "Index Field Fixture"
            imported_tree = bpy.data.node_groups[imported_asset["node_group"]["name"]]
            assert imported_tree["blender_mcp_asset_name"] == "Index Field Fixture"
            conflict = server.import_blender_node_asset(
                source_path=record["source_path"],
                asset_name=record["name"],
                tree_type=record["tree_type"],
                scope="USER",
                library="Blender MCP Test Library",
                conflict_policy="REJECT",
            )
            assert conflict["status"] == "rejected"
            imported_ids = [
                item for pointer, item in addon._gn_blend_data_ids().items()
                if pointer not in fixture_import_before
            ]
            bpy.data.batch_remove(ids=imported_ids)
            assert addon._gn_blend_data_ids() == fixture_import_before
            fixture_import_before = None
        finally:
            if fixture_import_before is not None:
                appended = [
                    item for pointer, item in addon._gn_blend_data_ids().items()
                    if pointer not in fixture_import_before
                ]
                if appended:
                    bpy.data.batch_remove(ids=appended)
            if fixture_library is not None:
                bpy.context.preferences.filepaths.asset_libraries.remove(fixture_library)
            if fixture_tree is not None and fixture_tree.name in bpy.data.node_groups:
                bpy.data.node_groups.remove(fixture_tree, do_unlink=True)
            addon._GN_USER_ASSET_CATALOG_CACHE.clear()

    result = {
        "blender_version": bpy.app.version_string,
        "node_type": node_type,
        "compact_bytes": compact_bytes,
        "full_bytes": full_bytes,
        "compression_ratio": round(full_bytes / compact_bytes, 2),
        "catalog_total": first_catalog["total_types"],
        "query_matches": first_catalog["total_matches"],
        "repeat_item_count": repeat_items["count"],
        "node_asset_total": assets["total_assets"],
        "node_asset_libraries": assets["library_count"],
        "user_asset_match": user_assets["assets"][0]["name"],
        "user_asset_import": imported_asset["status"],
        "runtime_action_model": automation["animation"]["model"],
        "runtime_engines": automation["render"]["available_engines"],
    }
    print("BLENDER_RUNTIME_KNOWLEDGE=" + json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
