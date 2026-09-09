"""Read-only frame membership/count audit for a complete Blender layout snapshot.

This does not measure visual bounds, wire crossings, or semantic grouping.
Exit codes: 0 compliant; 1 structural violations; 2 incomplete/invalid input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MIN_CHILDREN = 4
MAX_CHILDREN = 19


def audit_snapshot(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        raise ValueError("Expected a structured node-tree snapshot object")
    if snapshot.get("view") not in {"layout", "all"}:
        raise ValueError("Use view=layout or view=all; other views omit frame parents")
    if snapshot.get("scope", {}).get("kind") != "full":
        raise ValueError("A complete tree snapshot is required; subgraphs can omit children")
    tree = snapshot.get("tree", {})
    nodes = tree.get("nodes")
    if not isinstance(nodes, dict):
        raise ValueError("Expected tree.nodes keyed by stable node name")
    stats = snapshot.get("stats", {})
    if stats.get("total_node_count") != len(nodes) or stats.get("node_count") != len(nodes):
        raise ValueError("Node counts do not establish a complete snapshot")
    for name, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != name or not node.get("bl_idname"):
            raise ValueError(f"Invalid node identity: {name!r}")
        if not isinstance(node.get("layout"), dict) or "parent" not in node["layout"]:
            raise ValueError(f"Missing parent evidence: {name!r}")
        parent = node["layout"]["parent"]
        if parent is not None and not isinstance(parent, str):
            raise ValueError(f"Invalid parent value: {name!r}")

    frames = {name for name, node in nodes.items() if node["bl_idname"] == "NodeFrame"}
    children = {name: [] for name in frames}
    unframed = []
    invalid_parents = []
    for name, node in nodes.items():
        parent = node["layout"]["parent"]
        if parent is None:
            if name not in frames:
                unframed.append(name)
        elif parent not in frames:
            invalid_parents.append({"node": name, "parent": parent})
        else:
            children[parent].append(name)

    cycles = set()
    for start in sorted(frames):
        chain = []
        seen = {}
        current = start
        while current in frames:
            if current in seen:
                cycles.add(tuple(sorted(chain[seen[current]:])))
                break
            seen[current] = len(chain)
            chain.append(current)
            current = nodes[current]["layout"]["parent"]

    frame_records = [
        {
            "name": name,
            "label": nodes[name].get("label", ""),
            "direct_child_count": len(children[name]),
            "children": sorted(children[name]),
        }
        for name in sorted(frames)
    ]
    too_small = [record for record in frame_records if record["direct_child_count"] < MIN_CHILDREN]
    too_large = [record for record in frame_records if record["direct_child_count"] > MAX_CHILDREN]
    functional_count = len(nodes) - len(frames)
    invalid_functional = sum(item["node"] not in frames for item in invalid_parents)
    return {
        "schema": "blender-node-frame-audit/1",
        "tree": tree.get("name"),
        "revision": snapshot.get("revision"),
        "scope": "full",
        "checks": ["frame_membership", "direct_child_count", "parent_cycles"],
        "visual_layout_checked": False,
        "valid": not (unframed or invalid_parents or cycles or too_small or too_large),
        "functional_node_count": functional_count,
        "functional_nodes_with_frame_parent": functional_count - len(unframed) - invalid_functional,
        "frame_count": len(frames),
        "limits": {"min_direct_children": MIN_CHILDREN, "max_direct_children": MAX_CHILDREN},
        "unframed_nodes": sorted(unframed),
        "invalid_parents": sorted(invalid_parents, key=lambda item: item["node"]),
        "parent_cycles": [list(cycle) for cycle in sorted(cycles)],
        "undersized_frames": too_small,
        "oversized_frames": too_large,
        "frames": frame_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Complete layout/all node-tree JSON export")
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        result = audit_snapshot(snapshot)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
