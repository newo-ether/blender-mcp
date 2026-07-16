"""Shared node patch operation primitives."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic
from .constants import (
    NODE_INTERFACE_PANEL_PROPERTIES,
    NODE_INTERFACE_SOCKET_PROPERTIES,
)
from .dynamic import _NODE_DYNAMIC_COLLECTION_ALLOWLIST
from .patch_values import (
    _gn_decode_patch_value,
    _gn_resolve_patch_socket,
    _gn_validate_value,
)

_NODE_EFFECT_SENSITIVE_TYPES = {
    "ShaderNodeScript",
    "CompositorNodeOutputFile",
}

_NODE_EFFECT_SENSITIVE_PROPERTIES = {
    "base_path", "bytecode", "filepath", "file_slots", "layer_slots", "script",
}

def _node_validation_copy(target):
    owner_kind = target["owner_kind"]
    original_owner = target["owner_id"]
    standalone_tree = None
    if owner_kind == "NODE_GROUP":
        working_owner = original_owner.copy()
        working_tree = working_owner
        standalone_tree = working_tree
    elif owner_kind in {"MATERIAL", "WORLD", "LIGHT"}:
        working_owner = original_owner.copy()
        working_tree = working_owner.node_tree
    elif target["adapter"] == "scene_embedded_node_tree":
        working_owner = original_owner.copy()
        working_tree = working_owner.node_tree
    else:
        working_owner = original_owner.copy()
        working_tree = target["tree"].copy()
        working_owner.compositing_node_group = working_tree
        standalone_tree = working_tree
    working_owner.name = ".BlenderMCP Node Patch Validation"
    if standalone_tree is not None:
        standalone_tree.name = ".BlenderMCP Node Patch Validation"
    working_target = dict(target)
    working_target.update({
        "owner_id": working_owner,
        "tree": working_tree,
    })
    return working_target, working_owner, standalone_tree

def _node_remove_validation_copy(target, working_owner, standalone_tree):
    owner_kind = target["owner_kind"]
    if owner_kind == "NODE_GROUP":
        if working_owner.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(working_owner, do_unlink=True)
        return
    if owner_kind == "MATERIAL" and working_owner.name in bpy.data.materials:
        bpy.data.materials.remove(working_owner, do_unlink=True)
    elif owner_kind == "WORLD" and working_owner.name in bpy.data.worlds:
        bpy.data.worlds.remove(working_owner, do_unlink=True)
    elif owner_kind == "LIGHT" and working_owner.name in bpy.data.lights:
        bpy.data.lights.remove(working_owner, do_unlink=True)
    elif owner_kind == "SCENE" and working_owner.name in bpy.data.scenes:
        bpy.data.scenes.remove(working_owner, do_unlink=True)
    if (
        standalone_tree is not None
        and standalone_tree.name in bpy.data.node_groups
    ):
        bpy.data.node_groups.remove(standalone_tree, do_unlink=True)

def _node_interface_mutable(target):
    return (
        target["owner_kind"] == "NODE_GROUP"
        or target["adapter"] == "scene_compositing_node_group"
    )

def _node_interface_property_allowed(item, property_name):
    if item is None:
        return False
    if item.item_type == "PANEL":
        return property_name in NODE_INTERFACE_PANEL_PROPERTIES
    if item.item_type == "SOCKET":
        return property_name in NODE_INTERFACE_SOCKET_PROPERTIES
    return False

def _node_mutation_allowed(node, operation, path, diagnostics, property_name=None):
    if getattr(node.__class__, "__module__", None) != "bpy.types":
        diagnostics.append(_gn_patch_diagnostic(
            "error", "custom_node_read_only", path,
            f"{node.bl_idname} is provided by Python or an add-on and is read-only "
            "until an explicit mutation capability is implemented",
        ))
        return False
    if node.bl_idname in _NODE_EFFECT_SENSITIVE_TYPES:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "effect_sensitive_node_read_only", path,
            f"{node.bl_idname} is read-only because mutation may configure external effects",
        ))
        return False
    if property_name in _NODE_EFFECT_SENSITIVE_PROPERTIES:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "effect_sensitive_property_read_only", path,
            f"Property {property_name!r} is read-only in the generic patch protocol",
        ))
        return False
    return True

def _node_apply_color_ramp(node, operation):
    ramp = getattr(node, "color_ramp", None)
    if ramp is None:
        raise ValueError(f"Node {node.name!r} has no Color Ramp")
    if "interpolation" in operation:
        ramp.interpolation = operation["interpolation"]
    requested = operation["elements"]
    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])
    for index, element in enumerate(requested):
        target = (
            ramp.elements[index]
            if index < 2
            else ramp.elements.new(float(element["position"]))
        )
        target.position = float(element["position"])
        target.color = element["color"]

def _node_apply_curve_mapping(node, operation):
    mapping = getattr(node, "mapping", None)
    if mapping is None:
        raise ValueError(f"Node {node.name!r} has no Curve Mapping")
    curves = operation["curves"]
    if len(curves) != len(mapping.curves):
        raise ValueError(
            f"Expected {len(mapping.curves)} curves, received {len(curves)}"
        )
    if "use_clip" in operation:
        mapping.use_clip = bool(operation["use_clip"])
    for curve, curve_patch in zip(mapping.curves, curves):
        requested = curve_patch["points"]
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-1])
        for index, point_patch in enumerate(requested):
            location = point_patch["location"]
            point = (
                curve.points[index]
                if index < 2
                else curve.points.new(float(location[0]), float(location[1]))
            )
            point.location = location
            if "handle_type" in point_patch:
                point.handle_type = point_patch["handle_type"]
    mapping.update()

def _node_dynamic_collection(node, collection_name):
    allowed = _NODE_DYNAMIC_COLLECTION_ALLOWLIST.get(node.bl_idname, set())
    if collection_name not in allowed:
        raise ValueError(
            f"Dynamic collection {collection_name!r} is not allowlisted for {node.bl_idname}"
        )
    collection = getattr(node, collection_name, None)
    if collection is None or not hasattr(collection, "new") or not hasattr(collection, "remove"):
        raise ValueError(
            f"Dynamic collection {collection_name!r} is unavailable in Blender {bpy.app.version_string}"
        )
    return collection

def _node_add_paired_zone(tree, input_type, output_type, operation):
    input_node = tree.nodes.new(input_type)
    output_node = tree.nodes.new(output_type)
    input_node.name = operation.get("input_name") or input_node.name
    output_node.name = operation.get("output_name") or output_node.name
    base_location = operation.get("location", [0.0, 0.0])
    input_node.location = base_location
    output_node.location = [float(base_location[0]) + 300.0, float(base_location[1])]
    try:
        input_node.pair_with_output(output_node)
    except Exception:
        tree.nodes.remove(input_node)
        tree.nodes.remove(output_node)
        raise
    return input_node, output_node

def _node_execute_patch_operations(target, patch):
    tree = target["tree"]
    diagnostics = []
    plan = []
    diff = {
        "nodes_added": 0,
        "nodes_removed": 0,
        "nodes_renamed": 0,
        "node_properties_changed": 0,
        "socket_defaults_changed": 0,
        "sockets_hidden": 0,
        "links_added": 0,
        "links_removed": 0,
        "layouts_changed": 0,
        "annotations_changed": 0,
        "interface_panels_added": 0,
        "interface_sockets_added": 0,
        "interface_sockets_removed": 0,
        "interface_items_changed": 0,
        "dynamic_structures_changed": 0,
    }
    node_refs = {
        node.name: {"node": node, "removed": False, "existing": True}
        for node in tree.nodes
    }
    interface_refs = {
        (getattr(item, "identifier", "") or item.name): {
            "item": item,
            "removed": False,
        }
        for item in tree.interface.items_tree
    }
    created_nodes = {}
    created_interface = {}

    def resolve_node(reference, path):
        item = node_refs.get(reference)
        if item is None:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "node_not_found", path,
                f"Node reference not found: {reference}",
            ))
            return None
        if item["removed"]:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "node_already_removed", path,
                f"Node was already removed: {reference}",
            ))
            return None
        return item["node"]

    for index, operation in enumerate(patch["operations"]):
        path = f"/operations/{index}"
        errors_before = sum(item["severity"] == "error" for item in diagnostics)
        op = operation["op"]
        summary = op
        try:
            if op == "add_node":
                reference = operation["id"]
                if reference in node_refs:
                    raise ValueError(f"Node reference already exists: {reference}")
                if operation["node_type"] in _NODE_EFFECT_SENSITIVE_TYPES:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "effect_sensitive_node_read_only", f"{path}/node_type",
                        f"{operation['node_type']} cannot be added by the patch protocol",
                    ))
                    node = None
                else:
                    node = tree.nodes.new(operation["node_type"])
                    if not _node_mutation_allowed(
                        node, op, f"{path}/node_type", diagnostics
                    ):
                        tree.nodes.remove(node)
                        node = None
                if node is not None:
                    requested_name = operation.get("name")
                    if (
                        requested_name
                        and tree.nodes.get(requested_name) not in {None, node}
                    ):
                        tree.nodes.remove(node)
                        raise ValueError(f"Node name already exists: {requested_name}")
                    if requested_name:
                        node.name = requested_name
                    node_refs[reference] = {
                        "node": node, "removed": False, "existing": False,
                    }
                    created_nodes[reference] = node.name
                    for property_name, value in operation.get("properties", {}).items():
                        property_path = f"{path}/properties/{property_name}"
                        if not _node_mutation_allowed(
                            node, op, property_path, diagnostics, property_name
                        ):
                            continue
                        if _gn_validate_value(
                            node, property_name, value, property_path,
                            diagnostics, node_refs,
                        ):
                            setattr(
                                node, property_name,
                                _gn_decode_patch_value(value, node_refs),
                            )
                    layout = operation.get("layout", {})
                    for field in ("location", "width", "height"):
                        if field in layout:
                            setattr(node, field, layout[field])
                    if layout.get("parent") is not None:
                        parent = resolve_node(
                            layout["parent"], f"{path}/layout/parent"
                        )
                        if parent is not None:
                            if parent.bl_idname != "NodeFrame":
                                raise ValueError("Node parent must be a Frame")
                            node.parent = parent
                    diff["nodes_added"] += 1
                    summary = f"Add {node.bl_idname} as {reference}"

            elif op == "remove_node":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    tree.nodes.remove(node)
                    node_refs[operation["node"]]["removed"] = True
                    created_nodes.pop(operation["node"], None)
                    diff["nodes_removed"] += 1
                    summary = f"Remove {operation['node']}"

            elif op == "rename_node":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    if operation["name"] != node.name and operation["name"] in tree.nodes:
                        raise ValueError(f"Node name already exists: {operation['name']}")
                    node.name = operation["name"]
                    if operation["node"] in created_nodes:
                        created_nodes[operation["node"]] = node.name
                    diff["nodes_renamed"] += 1
                    summary = f"Rename {operation['node']} to {node.name}"

            elif op == "set_node_property":
                node = resolve_node(operation["node"], f"{path}/node")
                property_name = operation["property"]
                if node is not None and _node_mutation_allowed(
                    node, op, f"{path}/property", diagnostics, property_name
                ) and _gn_validate_value(
                    node, property_name, operation["value"], f"{path}/value",
                    diagnostics, node_refs, property_path=f"{path}/property",
                ):
                    setattr(
                        node, property_name,
                        _gn_decode_patch_value(operation["value"], node_refs),
                    )
                    diff["node_properties_changed"] += 1
                    summary = f"Set {operation['node']}.{property_name}"

            elif op == "set_socket_default":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    socket = _gn_resolve_patch_socket(
                        node, operation["socket"], "input", f"{path}/socket", diagnostics
                    )
                    if socket is not None and _gn_validate_value(
                        socket, "default_value", operation["value"], f"{path}/value",
                        diagnostics, node_refs,
                    ):
                        if socket.is_linked:
                            diagnostics.append(_gn_patch_diagnostic(
                                "warning", "linked_socket_default", f"{path}/socket",
                                "The default is stored but has no effect while the socket is linked",
                            ))
                        socket.default_value = _gn_decode_patch_value(
                            operation["value"], node_refs
                        )
                        diff["socket_defaults_changed"] += 1
                        summary = f"Set default for {operation['node']}"

            elif op == "set_socket_hide":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    socket_id = operation["socket"]
                    direction = "output" if socket_id.startswith("output:") else "input"
                    socket = _gn_resolve_patch_socket(
                        node, socket_id, direction, f"{path}/socket", diagnostics
                    )
                    if socket is not None:
                        if not hasattr(socket, "hide"):
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "socket_not_hideable", f"{path}/socket",
                                f"Socket {socket_id} does not support hide",
                            ))
                        else:
                            socket.hide = bool(operation["value"])
                            diff["sockets_hidden"] += 1
                            summary = (
                                f"{'Hide' if operation['value'] else 'Show'} "
                                f"{operation['node']}:{socket_id}"
                            )

            elif op in {"add_link", "remove_link"}:
                from_node = resolve_node(operation["from_node"], f"{path}/from_node")
                to_node = resolve_node(operation["to_node"], f"{path}/to_node")
                if from_node is not None and to_node is not None:
                    if not _node_mutation_allowed(from_node, op, path, diagnostics):
                        from_node = None
                    if not _node_mutation_allowed(to_node, op, path, diagnostics):
                        to_node = None
                if from_node is not None and to_node is not None:
                    from_socket = _gn_resolve_patch_socket(
                        from_node, operation["from_socket"], "output",
                        f"{path}/from_socket", diagnostics,
                    )
                    to_socket = _gn_resolve_patch_socket(
                        to_node, operation["to_socket"], "input",
                        f"{path}/to_socket", diagnostics,
                    )
                    if from_socket is not None and to_socket is not None:
                        existing = next((
                            link for link in tree.links
                            if link.from_socket == from_socket and link.to_socket == to_socket
                        ), None)
                        if op == "add_link":
                            if existing is not None:
                                raise ValueError("Link already exists")
                            if not getattr(to_socket, "is_multi_input", False) and to_socket.is_linked:
                                raise ValueError(
                                    "Remove the existing link before linking this input"
                                )
                            link = tree.links.new(from_socket, to_socket, verify_limits=True)
                            if not link.is_valid:
                                raise ValueError("Blender rejected the projected link")
                            if from_socket.type != to_socket.type:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "warning", "implicit_socket_conversion", path,
                                    f"Blender converts {from_socket.type} to {to_socket.type}",
                                ))
                            diff["links_added"] += 1
                        else:
                            if existing is None:
                                raise ValueError("Link does not exist")
                            tree.links.remove(existing)
                            diff["links_removed"] += 1
                        summary = f"{op} {operation['from_node']} -> {operation['to_node']}"

            elif op == "set_node_layout":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    for field in ("location", "width", "height"):
                        if field in operation:
                            setattr(node, field, operation[field])
                    if "parent" in operation:
                        if operation["parent"] is None:
                            node.parent = None
                        else:
                            parent = resolve_node(operation["parent"], f"{path}/parent")
                            if parent is not None:
                                if parent == node or parent.bl_idname != "NodeFrame":
                                    raise ValueError("Node parent must be a different Frame")
                                node.parent = parent
                    diff["layouts_changed"] += 1
                    summary = f"Update layout for {operation['node']}"

            elif op == "set_annotation":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    if node.bl_idname != "NodeFrame":
                        raise ValueError("Annotations may only be attached to Frame nodes")
                    if operation["text"]:
                        node["blender_mcp_note"] = operation["text"]
                    elif "blender_mcp_note" in node:
                        del node["blender_mcp_note"]
                    diff["annotations_changed"] += 1
                    summary = f"Set annotation for {operation['node']}"

            elif op == "add_interface_socket":
                if not _node_interface_mutable(target):
                    raise ValueError("This owner does not expose a mutable node interface")
                if operation["id"] in interface_refs:
                    raise ValueError(f"Interface reference already exists: {operation['id']}")
                parent = None
                if operation.get("parent"):
                    parent_record = interface_refs.get(operation["parent"])
                    if parent_record is None or parent_record["removed"]:
                        raise ValueError(f"Interface parent not found: {operation['parent']}")
                    parent = parent_record["item"]
                    if parent.item_type != "PANEL":
                        raise ValueError("Interface parent must be a panel")
                item = tree.interface.new_socket(
                    name=operation["name"],
                    in_out=operation["in_out"],
                    socket_type=operation["socket_type"],
                    parent=parent,
                )
                if "default" in operation and hasattr(item, "default_value"):
                    if _gn_validate_value(
                        item, "default_value", operation["default"], f"{path}/default",
                        diagnostics, node_refs,
                    ):
                        item.default_value = _gn_decode_patch_value(
                            operation["default"], node_refs
                        )
                interface_refs[operation["id"]] = {"item": item, "removed": False}
                created_interface[operation["id"]] = (
                    getattr(item, "identifier", "") or item.name
                )
                diff["interface_sockets_added"] += 1
                summary = f"Add interface socket {operation['id']}"

            elif op == "add_interface_panel":
                if not _node_interface_mutable(target):
                    raise ValueError("This owner does not expose a mutable node interface")
                if operation["id"] in interface_refs:
                    raise ValueError(f"Interface reference already exists: {operation['id']}")
                item = tree.interface.new_panel(
                    name=operation["name"],
                    description=operation.get("description", ""),
                    default_closed=operation.get("default_closed", False),
                )
                interface_refs[operation["id"]] = {"item": item, "removed": False}
                created_interface[operation["id"]] = (
                    getattr(item, "identifier", "") or item.name
                )
                diff["interface_panels_added"] += 1
                summary = f"Add interface panel {operation['id']}"

            elif op == "remove_interface_socket":
                if not _node_interface_mutable(target):
                    raise ValueError("This owner does not expose a mutable node interface")
                record = interface_refs.get(operation["identifier"])
                if record is None or record["removed"] or record["item"].item_type != "SOCKET":
                    raise ValueError(
                        f"Interface socket not found: {operation['identifier']}"
                    )
                tree.interface.remove(record["item"])
                record["removed"] = True
                created_interface.pop(operation["identifier"], None)
                diff["interface_sockets_removed"] += 1
                summary = f"Remove interface socket {operation['identifier']}"

            elif op == "set_interface_item":
                if not _node_interface_mutable(target):
                    raise ValueError("This owner does not expose a mutable node interface")
                record = interface_refs.get(operation["identifier"])
                if record is None or record["removed"]:
                    raise ValueError(
                        f"Interface item not found: {operation['identifier']}"
                    )
                item = record["item"]
                property_name = operation["property"]
                if not _node_interface_property_allowed(item, property_name):
                    raise ValueError(
                        f"Interface {item.item_type.lower()} property is not supported: "
                        f"{property_name}"
                    )
                if _gn_validate_value(
                    item, property_name, operation["value"], f"{path}/value",
                    diagnostics, node_refs,
                ):
                    setattr(
                        item,
                        property_name,
                        _gn_decode_patch_value(operation["value"], node_refs),
                    )
                    diff["interface_items_changed"] += 1
                    summary = (
                        f"Set interface {operation['identifier']}.{property_name}"
                    )

            elif op == "set_color_ramp":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    _node_apply_color_ramp(node, operation)
                    diff["dynamic_structures_changed"] += 1
                    summary = f"Set Color Ramp on {operation['node']}"

            elif op == "set_curve_mapping":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(
                    node, op, path, diagnostics
                ):
                    _node_apply_curve_mapping(node, operation)
                    diff["dynamic_structures_changed"] += 1
                    summary = f"Set Curve Mapping on {operation['node']}"

            elif op == "add_dynamic_item":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(node, op, path, diagnostics):
                    collection = _node_dynamic_collection(node, operation["collection"])
                    item = collection.new(operation["socket_type"], operation["name"])
                    diff["dynamic_structures_changed"] += 1
                    summary = (
                        f"Add {getattr(item, 'name', operation['name'])} to "
                        f"{operation['node']}.{operation['collection']}"
                    )

            elif op == "remove_dynamic_item":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(node, op, path, diagnostics):
                    collection = _node_dynamic_collection(node, operation["collection"])
                    item_index = operation["index"]
                    if item_index >= len(collection):
                        raise ValueError(f"Dynamic item index {item_index} is out of range")
                    collection.remove(collection[item_index])
                    diff["dynamic_structures_changed"] += 1
                    summary = f"Remove item {item_index} from {operation['node']}.{operation['collection']}"

            elif op == "set_dynamic_item":
                node = resolve_node(operation["node"], f"{path}/node")
                if node is not None and _node_mutation_allowed(node, op, path, diagnostics):
                    collection = _node_dynamic_collection(node, operation["collection"])
                    item_index = operation["index"]
                    if item_index >= len(collection):
                        raise ValueError(f"Dynamic item index {item_index} is out of range")
                    item = collection[item_index]
                    property_name = operation["property"]
                    if not _gn_validate_value(
                        item, property_name, operation["value"], f"{path}/value",
                        diagnostics, node_refs, property_path=f"{path}/property",
                    ):
                        raise ValueError(f"Dynamic item property is not writable: {property_name}")
                    setattr(item, property_name, _gn_decode_patch_value(operation["value"], node_refs))
                    diff["dynamic_structures_changed"] += 1
                    summary = f"Set dynamic item {item_index}.{property_name}"

            elif op in {"add_foreach_zone", "add_closure_zone"}:
                input_id, output_id = operation["input_id"], operation["output_id"]
                if input_id in node_refs or output_id in node_refs:
                    raise ValueError("Zone node references must be unique")
                if op == "add_foreach_zone":
                    input_type = "GeometryNodeForeachGeometryElementInput"
                    output_type = "GeometryNodeForeachGeometryElementOutput"
                else:
                    input_type = "NodeClosureInput"
                    output_type = "NodeClosureOutput"
                input_node, output_node = _node_add_paired_zone(
                    tree, input_type, output_type, operation
                )
                node_refs[input_id] = {"node": input_node, "removed": False, "existing": False}
                node_refs[output_id] = {"node": output_node, "removed": False, "existing": False}
                created_nodes[input_id] = input_node.name
                created_nodes[output_id] = output_node.name
                diff["nodes_added"] += 2
                diff["dynamic_structures_changed"] += 1
                summary = f"Add paired {op.removeprefix('add_')}"
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            unavailable_node_type = (
                op == "add_node"
                and isinstance(exc, RuntimeError)
                and operation.get("id") not in node_refs
            )
            if unavailable_node_type:
                version = ".".join(str(item) for item in bpy.app.version[:3])
                code = "unsupported_node_type"
                diagnostic_path = f"{path}/node_type"
                message = (
                    f"{operation['node_type']} is unavailable for "
                    f"{target['tree_type']}/{target['owner_kind']} in Blender "
                    f"{version}: {exc}"
                )
            else:
                code = "operation_rejected"
                diagnostic_path = path
                message = f"{type(exc).__name__}: {exc}"
            diagnostics.append(_gn_patch_diagnostic(
                "error", code, diagnostic_path, message,
            ))
        errors_after = sum(item["severity"] == "error" for item in diagnostics)
        plan.append({
            "index": index,
            "op": op,
            "status": "ready" if errors_after == errors_before else "invalid",
            "summary": summary,
        })
    return {
        "diagnostics": diagnostics,
        "plan": plan,
        "semantic_diff": diff,
        "created_nodes": created_nodes,
        "created_interface": created_interface,
    }
