"""Live Blender acceptance for compact node schema and node discovery."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

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
    spec.loader.exec_module(addon)
    server = object.__new__(addon.BlenderMCPServer)

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

    result = {
        "blender_version": bpy.app.version_string,
        "node_type": node_type,
        "compact_bytes": compact_bytes,
        "full_bytes": full_bytes,
        "compression_ratio": round(full_bytes / compact_bytes, 2),
        "catalog_total": first_catalog["total_types"],
        "query_matches": first_catalog["total_matches"],
        "repeat_item_count": repeat_items["count"],
    }
    print("BLENDER_RUNTIME_KNOWLEDGE=" + json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
