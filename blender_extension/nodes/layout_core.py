"""Deterministic, bpy-free layout diagnostics and hierarchical placement.

Module/frame membership is input, never inferred or changed by the planner.
Rendered Bezier curves are not available here: wire checks cover endpoint order.
"""

from __future__ import annotations

import copy
import math

MAX_LAYOUT_NODES = 1500
MAX_LAYOUT_OPERATIONS = 500


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return float(value)


def _graph(snapshot):
    if snapshot.get("scope", {}).get("kind") != "full":
        raise ValueError("Layout requires a complete tree, including frame ancestors and spatial neighbors")
    nodes = snapshot["tree"]["nodes"]
    if len(nodes) > MAX_LAYOUT_NODES:
        raise ValueError(f"Layout supports at most {MAX_LAYOUT_NODES} nodes")
    if len(nodes) != snapshot["stats"]["total_node_count"]:
        raise ValueError("Incomplete node layout snapshot")
    return nodes, snapshot["tree"]["links"]


def audit_layout(snapshot, min_children=4, max_children=19, min_gap=20, diagnostic_limit=200):
    if not isinstance(min_children, int) or not isinstance(max_children, int) or not 1 <= min_children <= max_children <= 100:
        raise ValueError("Frame limits must be integers with 1 <= min <= max <= 100")
    if not isinstance(diagnostic_limit, int) or not 1 <= diagnostic_limit <= 1000:
        raise ValueError("diagnostic_limit must be from 1 to 1000")
    min_gap = _number(min_gap, "min_gap")
    nodes, links = _graph(snapshot)
    frames = {name for name, node in nodes.items() if node["bl_idname"] == "NodeFrame"}
    children = {name: [] for name in frames}
    parents = {name: node["layout"]["parent"] for name, node in nodes.items()}
    issues = []
    counts = {}
    has_errors = False

    def issue(code, names, severity="error", **details):
        nonlocal has_errors
        counts[code] = counts.get(code, 0) + 1
        has_errors = has_errors or severity == "error"
        if len(issues) < diagnostic_limit:
            issues.append({"code": code, "nodes": names, "severity": severity, **details})

    ancestors = {}
    for name in sorted(nodes):
        parent = parents[name]
        if parent is None and name not in frames:
            issue("unframed_node", [name])
        elif parent is not None:
            if parent not in frames:
                issue("invalid_frame_parent", [name, parent])
            else:
                children[parent].append(name)
        visited = {name}
        lineage = set()
        while parent in frames:
            if parent in visited:
                issue("frame_parent_cycle", [name, parent])
                break
            lineage.add(parent)
            visited.add(parent)
            parent = parents[parent]
        ancestors[name] = lineage
    for name in sorted(frames):
        count = len(children[name])
        if not min_children <= count <= max_children:
            issue("frame_child_count", [name], direct_child_count=count, minimum=min_children, maximum=max_children)
        if not nodes[name].get("label", "").strip():
            issue("frame_label_missing", [name], "warning")

    bounds = {}
    for name, node in nodes.items():
        layout = node["layout"]
        rect = layout.get("bounds")
        if rect is None:
            issue("dimensions_unavailable", [name], "warning")
            continue
        bounds[name] = rect
        if layout.get("bounds_status") != "refreshed":
            issue("dimensions_unverified", [name], "warning", status=layout.get("bounds_status"))
        parent = parents[name]
        outer = nodes.get(parent, {}).get("layout", {}).get("bounds")
        if outer and (rect[0] < outer[0] or rect[1] > outer[1] or rect[2] > outer[2] or rect[3] < outer[3]):
            issue("outside_parent_frame", [name, parent])
    # x sweep avoids all-pairs work for a long horizontal pipeline.
    active = []
    for name in sorted(bounds, key=lambda n: (bounds[n][0], n)):
        rect = bounds[name]
        active = [other for other in active if bounds[other][2] + min_gap > rect[0]]
        for other in active:
            if name in ancestors[other] or other in ancestors[name]:
                continue
            box = bounds[other]
            if rect[1] + min_gap > box[3] and box[1] + min_gap > rect[3]:
                # Rectangles are [left, top, right, bottom], y goes up.
                overlap = rect[0] < box[2] and box[0] < rect[2] and rect[1] > box[3] and box[1] > rect[3]
                issue("node_overlap" if overlap else "insufficient_clearance", [other, name], "error" if overlap else "warning")
        active.append(name)
    fanin = {}
    for link in links:
        source, target = link["from_node"], link["to_node"]
        if source not in nodes or target not in nodes:
            raise ValueError("Layout links refer to missing nodes")
        if source in bounds and target in bounds and bounds[source][2] >= bounds[target][0]:
            issue("backward_or_zero_edge_gap", [source, target], "warning")
        fanin.setdefault(target, []).append(link)
    for target, incoming in fanin.items():
        ordered = sorted(incoming, key=lambda item: (int(item["to_socket"].split(":", 2)[1]), item.get("multi_input_sort_id", 0)))
        for a, b in zip(ordered, ordered[1:]):
            src_a, src_b = a["from_node"], b["from_node"]
            if src_a == src_b or a["to_socket"] == b["to_socket"]:
                continue
            if src_a in bounds and src_b in bounds and parents[src_a] == parents[src_b] and bounds[src_a][1] < bounds[src_b][1]:
                issue("fanin_order", [src_a, src_b, target], "warning")
    return {
        "schema": "blender-node-layout-audit/1", "tree_ref": snapshot["tree_ref"],
        "revision": snapshot["revision"], "will_mutate": False,
        "valid": not has_errors,
        "bounds_complete": len(bounds) == len(nodes),
        "bounds_verified": all(n["layout"].get("bounds_status") == "refreshed" for n in nodes.values()),
        "visual_layout_checked": False, "wire_checks": "endpoint_order_only",
        "functional_node_count": len(nodes) - len(frames),
        "frame_child_counts": {name: len(children[name]) for name in sorted(frames)},
        "diagnostic_counts": counts, "diagnostics": issues,
        "diagnostics_truncated": sum(counts.values()) > len(issues),
    }


def plan_layout(snapshot, main_path=None, horizontal_gap=80, vertical_gap=40, frame_padding=30, stage_gap=140):
    nodes, links = _graph(snapshot)
    if len(nodes) > MAX_LAYOUT_OPERATIONS:
        raise ValueError(f"A single layout patch supports at most {MAX_LAYOUT_OPERATIONS} nodes")
    horizontal_gap = _number(horizontal_gap, "horizontal_gap", 20)
    vertical_gap = _number(vertical_gap, "vertical_gap", 20)
    frame_padding = _number(frame_padding, "frame_padding", 30)
    stage_gap = _number(stage_gap, "stage_gap", 40)
    main_path = main_path or []
    if not isinstance(main_path, list) or any(name not in nodes for name in main_path):
        raise ValueError("main_path must contain exact existing node names")
    audit = audit_layout(snapshot)
    if not nodes:
        return {"schema": "blender-node-layout-plan/1", "status": "blocked", "will_mutate": False,
                "reason": "Tree has no nodes to arrange", "patch": None, "audit": audit}
    blocked = {"unframed_node", "invalid_frame_parent", "frame_parent_cycle", "frame_child_count"}
    missing_dimensions = any(n["bl_idname"] != "NodeFrame" and n["layout"].get("bounds") is None for n in nodes.values())
    if blocked.intersection(audit["diagnostic_counts"]) or missing_dimensions:
        return {"schema": "blender-node-layout-plan/1", "status": "blocked", "will_mutate": False, "audit": audit,
                "reason": "Fix semantic frame membership/counts and obtain node dimensions before planning", "patch": None}
    parents = {name: n["layout"]["parent"] for name, n in nodes.items()}
    children = {None: []}
    sizes = {}
    local = {}
    for name, node in nodes.items():
        children.setdefault(parents[name], []).append(name)
        if node["bl_idname"] == "NodeFrame":
            children.setdefault(name, [])
        rect = node["layout"]["bounds"]
        sizes[name] = (rect[2] - rect[0], rect[1] - rect[3]) if rect else (1, 1)
    priority = set(main_path)
    for name in main_path:
        parent = parents[name]
        while parent is not None:
            priority.add(parent)
            parent = parents[parent]

    def direct_child(name, container):
        while name is not None and parents[name] != container:
            name = parents[name]
        return name

    def arrange(container):
        members = sorted(children[container])
        for name in members:
            if name in children:
                arrange(name)
        adjacent = {name: set() for name in members}
        indegree = dict.fromkeys(members, 0)
        socket_order = dict.fromkeys(members, 1000000)
        for link in links:
            a = direct_child(link["from_node"], container)
            b = direct_child(link["to_node"], container)
            if a not in adjacent or b not in adjacent or a == b:
                continue
            if b not in adjacent[a]:
                adjacent[a].add(b)
                indegree[b] += 1
            socket_order[a] = min(socket_order[a], int(link["to_socket"].split(":", 2)[1]))
        rank = dict.fromkeys(members, 0)
        ready = sorted(name for name in members if not indegree[name])
        visited = []
        while ready:
            name = ready.pop(0)
            visited.append(name)
            for target in sorted(adjacent[name]):
                rank[target] = max(rank[target], rank[name] + 1)
                indegree[target] -= 1
                if not indegree[target]:
                    ready.append(target)
                    ready.sort()
        if len(visited) != len(members):
            raise ValueError("Dependency cycle between layout units; preserve zone semantics or revise stage grouping")
        # Pull supporting calculations toward their earliest consumer rather
        # than placing every leaf input at the far left of a long stage.
        for name in reversed(visited):
            if name not in priority and adjacent[name]:
                rank[name] = min(rank[target] for target in adjacent[name]) - 1
        columns = {}
        for name in members:
            columns.setdefault(rank[name], []).append(name)
        inset = 0 if container is None else frame_padding
        top = 0 if container is None else frame_padding + 36
        x, total_height = inset, 0
        for column in sorted(columns):
            y = -top
            ordered = sorted(columns[column], key=lambda n: (n not in priority, socket_order[n], n))
            if not any(n in priority for n in ordered):
                main_members = [n for n in members if n in priority]
                if main_members:
                    # Leave the upper corridor open for an incoming main-path
                    # wire even if this column contains only auxiliary nodes.
                    nearest_rank = min((rank[n] for n in main_members), key=lambda r: (abs(r - column), r))
                    y -= max(sizes[n][1] for n in main_members if rank[n] == nearest_rank) + vertical_gap
            for name in ordered:
                local[name] = [x, y]
                y -= sizes[name][1] + vertical_gap
            total_height = max(total_height, -y - vertical_gap)
            x += max(sizes[name][0] for name in ordered) + (stage_gap if container is None else horizontal_gap)
        if container is not None:
            sizes[container] = (max(inset * 2, x - horizontal_gap + inset), total_height + inset)

    try:
        arrange(None)
    except ValueError as exc:
        return {"schema": "blender-node-layout-plan/1", "status": "blocked", "will_mutate": False, "reason": str(exc), "patch": None, "audit": audit}
    # Preserve the original canvas anchor, not every preexisting disordered row.
    roots = children[None]
    origin = [min(nodes[n]["layout"]["absolute_location"][0] for n in roots), max(nodes[n]["layout"]["absolute_location"][1] for n in roots)] if roots else [0, 0]
    for name in roots:
        local[name] = [local[name][0] + origin[0], local[name][1] + origin[1]]
    predicted = copy.deepcopy(snapshot)

    def absolute(name):
        parent = parents[name]
        if parent is None:
            return local[name]
        offset = absolute(parent)
        return [offset[0] + local[name][0], offset[1] + local[name][1]]

    operations = []
    for name in sorted(nodes):
        x, y = absolute(name)
        layout = predicted["tree"]["nodes"][name]["layout"]
        layout.update(location=local[name], absolute_location=[x, y], bounds=[x, y, x + sizes[name][0], y - sizes[name][1]], bounds_status="planned")
        operation = {"op": "set_node_layout", "node": name, "location": local[name]}
        if nodes[name]["bl_idname"] == "NodeFrame":
            operation.update(width=sizes[name][0], height=sizes[name][1])
        operations.append(operation)
    patch = {"base_revision": snapshot["revision"], "operations": operations}
    if snapshot["tree_ref"]["tree_type"] == "GeometryNodeTree":
        patch.update(schema="blender-geometry-nodes-patch/1", tree_name=snapshot["tree"]["name"], shared_tree_policy="reject")
    else:
        patch.update(schema="blender-node-tree-patch/1", tree_ref=snapshot["tree_ref"], capabilities=["layout"])
    return {"schema": "blender-node-layout-plan/1", "status": "planned", "will_mutate": False,
            "revision": snapshot["revision"], "tree_ref": snapshot["tree_ref"], "patch": patch,
            "preserves": ["nodes", "links", "parents", "interface", "socket_defaults", "names"],
            "predicted_audit": audit_layout(predicted), "requires_post_apply_measurement": True}
