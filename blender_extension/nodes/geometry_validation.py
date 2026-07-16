"""Geometry Nodes runtime patch validation."""

from __future__ import annotations

import bpy

from .common import _gn_patch_diagnostic
from .constants import GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA
from .geometry_operations import _gn_apply_operations_to_working
from .patch_operations import (
    _node_add_paired_zone,
    _node_dynamic_collection,
    _node_interface_property_allowed,
)
from .patch_values import (
    _gn_decode_patch_value,
    _gn_resolve_patch_node,
    _gn_resolve_patch_socket,
    _gn_validate_value,
)
from .serialization import (
    _gn_export_tree,
    _gn_find_socket_index,
    _gn_socket_id,
    _gn_tree_users,
)


def _gn_validate_patch_runtime(tree, patch):
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
        "interface_panels_added": 0,
        "interface_sockets_added": 0,
        "interface_sockets_removed": 0,
        "interface_items_changed": 0,
        "modifier_inputs_changed": 0,
        "dynamic_structures_changed": 0,
    }
    current_revision = _gn_export_tree(tree, "semantic")["revision"]
    if patch.get("base_revision") != current_revision:
        diagnostics.append(_gn_patch_diagnostic(
            "error", "stale_revision", "/base_revision",
            f"Patch revision {patch.get('base_revision')!r} does not match current {current_revision!r}",
        ))
    if not bool(getattr(tree, "is_editable", tree.library is None)):
        diagnostics.append(_gn_patch_diagnostic(
            "error", "tree_not_editable", "/tree_name",
            f"Geometry Node tree {tree.name!r} is linked or otherwise read-only",
        ))

    users = _gn_tree_users(tree)
    policy = patch.get("shared_tree_policy", "reject")
    if len(users) > 1 and policy == "reject":
        diagnostics.append(_gn_patch_diagnostic(
            "error", "shared_tree_rejected", "/shared_tree_policy",
            f"Tree has {len(users)} users; select single_user_copy or mutate_shared explicitly",
        ))
    elif len(users) > 1 and policy == "mutate_shared":
        diagnostics.append(_gn_patch_diagnostic(
            "warning", "shared_tree_mutation", "/shared_tree_policy",
            f"Patch would affect all {len(users)} users of this tree",
        ))
    if policy == "single_user_copy":
        target_user = patch.get("target_user")
        if not isinstance(target_user, dict) or not any(
            all(user.get(key) == value for key, value in target_user.items())
            for user in users
        ):
            diagnostics.append(_gn_patch_diagnostic(
                "error", "target_user_not_found", "/target_user",
                "target_user does not identify a current modifier or group-node user of this tree",
            ))

    node_refs = {
        node.name: {
            "node": node,
            "existing": True,
            "removed": False,
            "projected_name": node.name,
        }
        for node in tree.nodes
    }
    projected_names = {node.name for node in tree.nodes}
    projected_links = {
        (
            link.from_node.name,
            _gn_socket_id(link.from_socket, "OUTPUT", _gn_find_socket_index(link.from_node, link.from_socket, "OUTPUT")),
            link.to_node.name,
            _gn_socket_id(link.to_socket, "INPUT", _gn_find_socket_index(link.to_node, link.to_socket, "INPUT")),
        )
        for link in tree.links
    }
    interface_items = {
        (getattr(item, "identifier", "") or item.name): {
            "item": item,
            "removed": False,
            "in_out": getattr(item, "in_out", None),
            "item_type": item.item_type,
            "existing": True,
        }
        for item in tree.interface.items_tree
    }

    # Probe added nodes on a copy of the real tree rather than an empty group.
    # NodeGroupInput/NodeGroupOutput sockets are mirrored from the tree
    # interface, so an interface-less probe tree leaves them with only the
    # __extend__ socket and makes same-patch links/hides to those sockets fail
    # spuriously. Copying also makes projected auto-names (e.g. "Grid.001",
    # "Group Input.009") match what apply actually produces, avoiding false
    # duplicate_node_name diagnostics when a node type already exists by name.
    temp_tree = tree.copy()
    temp_tree.name = ".BlenderMCP_PatchValidation"
    try:
        for index, operation in enumerate(patch.get("operations", ())):
            path = f"/operations/{index}"
            errors_before = sum(item["severity"] == "error" for item in diagnostics)
            op = operation["op"]
            summary = op

            if op == "add_node":
                reference = operation["id"]
                if reference in node_refs:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "duplicate_node_reference", f"{path}/id",
                        f"Node reference already exists: {reference}",
                    ))
                else:
                    try:
                        node = temp_tree.nodes.new(operation["node_type"])
                    except RuntimeError as exc:
                        version = ".".join(str(item) for item in bpy.app.version[:3])
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "unsupported_node_type", f"{path}/node_type", str(exc),
                        ))
                        diagnostics[-1]["message"] = (
                            f"{operation['node_type']} is unavailable for "
                            f"{tree.bl_idname}/NODE_GROUP in Blender "
                            f"{version}: {exc}"
                        )
                        node = None
                    if node is not None:
                        projected_name = operation.get("name") or node.name
                        if projected_name in projected_names:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "duplicate_node_name", f"{path}/name",
                                f"Projected node name already exists: {projected_name}",
                            ))
                        node_refs[reference] = {
                            "node": node,
                            "existing": False,
                            "removed": False,
                            "projected_name": projected_name,
                        }
                        projected_names.add(projected_name)
                        layout = operation.get("layout", {})
                        if "parent" in layout and layout["parent"] is not None:
                            parent = _gn_resolve_patch_node(
                                node_refs, layout["parent"], f"{path}/layout/parent", diagnostics,
                            )
                            if parent and parent["node"].bl_idname != "NodeFrame":
                                diagnostics.append(_gn_patch_diagnostic(
                                    "error", "layout_parent_not_frame", f"{path}/layout/parent",
                                    "Node parent must reference a Frame node",
                                ))
                        for layout_field in ("location", "width", "height"):
                            if layout_field in layout:
                                try:
                                    setattr(node, layout_field, layout[layout_field])
                                except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                    diagnostics.append(_gn_patch_diagnostic(
                                        "error", "layout_assignment_rejected",
                                        f"{path}/layout/{layout_field}", str(exc),
                                    ))
                        for property_name, value in operation.get("properties", {}).items():
                            value_path = f"{path}/properties/{property_name}"
                            if _gn_validate_value(node, property_name, value, value_path, diagnostics, node_refs):
                                try:
                                    setattr(node, property_name, _gn_decode_patch_value(value, node_refs))
                                except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                    diagnostics.append(_gn_patch_diagnostic(
                                        "error", "rna_assignment_rejected", value_path, str(exc),
                                    ))
                        diff["nodes_added"] += 1
                        summary = f"Add {operation['node_type']} as {reference}"

            elif op == "remove_node":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item:
                    item["removed"] = True
                    projected_names.discard(item["projected_name"])
                    projected_links = {
                        link for link in projected_links
                        if link[0] != operation["node"] and link[2] != operation["node"]
                    }
                    diff["nodes_removed"] += 1
                    summary = f"Remove node {operation['node']}"

            elif op == "rename_node":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                new_name = operation["name"]
                if item:
                    if new_name != item["projected_name"] and new_name in projected_names:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "duplicate_node_name", f"{path}/name",
                            f"Projected node name already exists: {new_name}",
                        ))
                    else:
                        projected_names.discard(item["projected_name"])
                        projected_names.add(new_name)
                        item["projected_name"] = new_name
                        if not item["existing"]:
                            item["node"].name = new_name
                        diff["nodes_renamed"] += 1
                        summary = f"Rename {operation['node']} to {new_name}"

            elif op == "set_node_property":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item and _gn_validate_value(
                    item["node"], operation["property"], operation["value"],
                    f"{path}/value", diagnostics, node_refs,
                    property_path=f"{path}/property",
                ):
                    if not item["existing"]:
                        try:
                            setattr(
                                item["node"], operation["property"],
                                _gn_decode_patch_value(operation["value"], node_refs),
                            )
                        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "rna_assignment_rejected", f"{path}/value", str(exc),
                            ))
                    diff["node_properties_changed"] += 1
                    summary = f"Set {operation['node']}.{operation['property']}"

            elif op == "set_socket_default":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item:
                    socket = _gn_resolve_patch_socket(
                        item["node"], operation["socket"], "input", f"{path}/socket", diagnostics,
                    )
                    if socket and _gn_validate_value(
                        socket, "default_value", operation["value"], f"{path}/value", diagnostics, node_refs,
                    ):
                        if socket.is_linked and item["existing"]:
                            diagnostics.append(_gn_patch_diagnostic(
                                "warning", "linked_socket_default", f"{path}/socket",
                                "Default is stored but has no effect while the socket is linked",
                            ))
                        if not item["existing"]:
                            try:
                                socket.default_value = _gn_decode_patch_value(operation["value"], node_refs)
                            except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "error", "rna_assignment_rejected", f"{path}/value", str(exc),
                                ))
                        diff["socket_defaults_changed"] += 1
                        summary = f"Set default {operation['node']}:{operation['socket']}"

            elif op == "set_socket_hide":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                if item:
                    socket_id = operation["socket"]
                    direction = "output" if socket_id.startswith("output:") else "input"
                    socket = _gn_resolve_patch_socket(
                        item["node"], socket_id, direction, f"{path}/socket", diagnostics,
                    )
                    if socket is not None:
                        if not hasattr(socket, "hide"):
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "socket_not_hideable", f"{path}/socket",
                                f"Socket {socket_id} does not support hide",
                            ))
                        else:
                            if not item["existing"]:
                                try:
                                    socket.hide = bool(operation["value"])
                                except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                    diagnostics.append(_gn_patch_diagnostic(
                                        "error", "rna_assignment_rejected", f"{path}/value", str(exc),
                                    ))
                            diff["sockets_hidden"] += 1
                            summary = f"{'Hide' if operation['value'] else 'Show'} {operation['node']}:{socket_id}"

            elif op in {"add_link", "remove_link"}:
                from_item = _gn_resolve_patch_node(
                    node_refs, operation["from_node"], f"{path}/from_node", diagnostics,
                )
                to_item = _gn_resolve_patch_node(
                    node_refs, operation["to_node"], f"{path}/to_node", diagnostics,
                )
                from_socket = _gn_resolve_patch_socket(
                    from_item["node"], operation["from_socket"], "output", f"{path}/from_socket", diagnostics,
                ) if from_item else None
                to_socket = _gn_resolve_patch_socket(
                    to_item["node"], operation["to_socket"], "input", f"{path}/to_socket", diagnostics,
                ) if to_item else None
                link_key = (
                    operation["from_node"], operation["from_socket"],
                    operation["to_node"], operation["to_socket"],
                )
                if from_socket and to_socket:
                    if op == "add_link":
                        if link_key in projected_links:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "duplicate_link", path, "Link already exists",
                            ))
                        elif not getattr(to_socket, "is_multi_input", False) and any(
                            link[2:] == link_key[2:] for link in projected_links
                        ):
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "input_socket_occupied", f"{path}/to_socket",
                                "Remove the existing link before linking a non-multi-input socket",
                            ))
                        else:
                            projected_links.add(link_key)
                            if from_socket.type != to_socket.type:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "warning", "implicit_socket_conversion", path,
                                    f"Blender must convert {from_socket.type} to {to_socket.type}",
                                ))
                            diff["links_added"] += 1
                            summary = f"Link {operation['from_node']} to {operation['to_node']}"
                    elif link_key not in projected_links:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "link_not_found", path, "Link does not exist in projected graph",
                        ))
                    else:
                        projected_links.remove(link_key)
                        diff["links_removed"] += 1
                        summary = f"Unlink {operation['from_node']} from {operation['to_node']}"

            elif op == "set_node_layout":
                item = _gn_resolve_patch_node(node_refs, operation["node"], f"{path}/node", diagnostics)
                parent_ref = operation.get("parent")
                if item and parent_ref is not None:
                    parent = _gn_resolve_patch_node(
                        node_refs, parent_ref, f"{path}/parent", diagnostics,
                    )
                    if parent and parent["node"].bl_idname != "NodeFrame":
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "layout_parent_not_frame", f"{path}/parent",
                            "Node parent must reference a Frame node",
                        ))
                    if parent is item:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "layout_parent_cycle", f"{path}/parent",
                            "A node cannot be its own parent",
                        ))
                if item:
                    for layout_field in ("width", "height"):
                        if layout_field in operation:
                            _gn_validate_value(
                                item["node"], layout_field, operation[layout_field],
                                f"{path}/{layout_field}", diagnostics, node_refs,
                            )
                    diff["layouts_changed"] += 1
                    summary = f"Update layout for {operation['node']}"

            elif op == "add_interface_socket":
                reference = operation["id"]
                if reference in interface_items and not interface_items[reference]["removed"]:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "duplicate_interface_reference", f"{path}/id",
                        f"Interface reference already exists: {reference}",
                    ))
                try:
                    probe_socket = temp_tree.interface.new_socket(
                        name=operation["name"],
                        in_out=operation["in_out"],
                        socket_type=operation["socket_type"],
                    )
                    socket_type_valid = True
                except (TypeError, ValueError, RuntimeError):
                    socket_type_valid = False
                if not socket_type_valid:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "unsupported_interface_socket", f"{path}/socket_type",
                        f"Socket type is not valid for Geometry Nodes: {operation['socket_type']}",
                    ))
                elif "default" in operation and hasattr(probe_socket, "default_value"):
                    if _gn_validate_value(
                        probe_socket, "default_value", operation["default"],
                        f"{path}/default", diagnostics, node_refs,
                    ):
                        try:
                            probe_socket.default_value = _gn_decode_patch_value(
                                operation["default"], node_refs,
                            )
                        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                            diagnostics.append(_gn_patch_diagnostic(
                                "error", "rna_assignment_rejected", f"{path}/default", str(exc),
                            ))
                parent_ref = operation.get("parent")
                if parent_ref:
                    parent = interface_items.get(parent_ref)
                    if not parent or parent["removed"] or parent["item_type"] != "PANEL":
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "interface_parent_not_found", f"{path}/parent",
                            f"Interface panel not found: {parent_ref}",
                        ))
                interface_items[reference] = {
                    "item": probe_socket if socket_type_valid else None, "removed": False,
                    "in_out": operation["in_out"], "item_type": "SOCKET",
                    "existing": False,
                }
                diff["interface_sockets_added"] += 1
                summary = f"Add {operation['in_out']} interface socket {reference}"

            elif op == "add_interface_panel":
                reference = operation["id"]
                if reference in interface_items and not interface_items[reference]["removed"]:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "duplicate_interface_reference", f"{path}/id",
                        f"Interface reference already exists: {reference}",
                    ))
                try:
                    probe_panel = temp_tree.interface.new_panel(
                        name=operation["name"],
                        description=operation.get("description", ""),
                        default_closed=operation.get("default_closed", False),
                    )
                except (TypeError, ValueError, RuntimeError) as exc:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "unsupported_interface_panel", path, str(exc),
                    ))
                    probe_panel = None
                interface_items[reference] = {
                    "item": probe_panel,
                    "removed": False,
                    "in_out": None,
                    "item_type": "PANEL",
                    "existing": False,
                }
                diff["interface_panels_added"] += 1
                summary = f"Add interface panel {reference}"

            elif op == "remove_interface_socket":
                reference = operation["identifier"]
                item = interface_items.get(reference)
                if not item or item["removed"] or item["item_type"] != "SOCKET":
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "interface_socket_not_found", f"{path}/identifier",
                        f"Interface socket not found: {reference}",
                    ))
                else:
                    item["removed"] = True
                    diff["interface_sockets_removed"] += 1
                    summary = f"Remove interface socket {reference}"

            elif op == "set_interface_item":
                reference = operation["identifier"]
                record = interface_items.get(reference)
                if not record or record["removed"] or record["item"] is None:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "interface_item_not_found", f"{path}/identifier",
                        f"Interface item not found: {reference}",
                    ))
                else:
                    item = record["item"]
                    property_name = operation["property"]
                    if not _node_interface_property_allowed(item, property_name):
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "unsupported_interface_property", f"{path}/property",
                            f"Interface {item.item_type.lower()} property is not supported: "
                            f"{property_name}",
                        ))
                    elif _gn_validate_value(
                        item, property_name, operation["value"], f"{path}/value",
                        diagnostics, node_refs,
                    ):
                        if not record["existing"]:
                            try:
                                setattr(
                                    item,
                                    property_name,
                                    _gn_decode_patch_value(operation["value"], node_refs),
                                )
                            except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                                diagnostics.append(_gn_patch_diagnostic(
                                    "error", "rna_assignment_rejected", f"{path}/value", str(exc),
                                ))
                        diff["interface_items_changed"] += 1
                        summary = f"Set interface {reference}.{property_name}"

            elif op in {"add_dynamic_item", "remove_dynamic_item", "set_dynamic_item"}:
                item = _gn_resolve_patch_node(
                    node_refs, operation["node"], f"{path}/node", diagnostics
                )
                if item:
                    try:
                        source_node = item["node"]
                        probe_node = temp_tree.nodes.new(source_node.bl_idname)
                        collection = _node_dynamic_collection(
                            probe_node if op == "add_dynamic_item" else source_node,
                            operation["collection"],
                        )
                        if op == "add_dynamic_item":
                            collection.new(operation["socket_type"], operation["name"])
                        else:
                            item_index = operation["index"]
                            if item_index >= len(collection):
                                raise ValueError(f"Dynamic item index {item_index} is out of range")
                            if op == "set_dynamic_item":
                                _gn_validate_value(
                                    collection[item_index], operation["property"], operation["value"],
                                    f"{path}/value", diagnostics, node_refs,
                                    property_path=f"{path}/property",
                                )
                        diff["dynamic_structures_changed"] += 1
                        summary = f"{op} on {operation['node']}.{operation['collection']}"
                    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "dynamic_operation_rejected", path,
                            f"{type(exc).__name__}: {exc}",
                        ))

            elif op in {"add_foreach_zone", "add_closure_zone"}:
                input_id, output_id = operation["input_id"], operation["output_id"]
                if input_id in node_refs or output_id in node_refs:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "duplicate_node_reference", path,
                        "Zone node references must be unique",
                    ))
                else:
                    try:
                        if op == "add_foreach_zone":
                            input_type = "GeometryNodeForeachGeometryElementInput"
                            output_type = "GeometryNodeForeachGeometryElementOutput"
                        else:
                            input_type = "NodeClosureInput"
                            output_type = "NodeClosureOutput"
                        input_node, output_node = _node_add_paired_zone(
                            temp_tree, input_type, output_type, operation
                        )
                        node_refs[input_id] = {
                            "node": input_node, "existing": False, "removed": False,
                            "projected_name": input_node.name,
                        }
                        node_refs[output_id] = {
                            "node": output_node, "existing": False, "removed": False,
                            "projected_name": output_node.name,
                        }
                        diff["nodes_added"] += 2
                        diff["dynamic_structures_changed"] += 1
                        summary = f"Add paired {op.removeprefix('add_')}"
                    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "unsupported_zone", path,
                            f"{type(exc).__name__}: {exc}",
                        ))

            elif op == "set_modifier_input":
                obj = bpy.data.objects.get(operation["object"])
                modifier = obj.modifiers.get(operation["modifier"]) if obj else None
                interface = interface_items.get(operation["socket"])
                if obj is None:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "object_not_found", f"{path}/object",
                        f"Object not found: {operation['object']}",
                    ))
                elif modifier is None or modifier.type != "NODES":
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "modifier_not_found", f"{path}/modifier",
                        f"Geometry Nodes modifier not found: {operation['modifier']}",
                    ))
                elif modifier.node_group != tree:
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "modifier_tree_mismatch", f"{path}/modifier",
                        "Modifier does not use the patched node tree",
                    ))
                if not interface or interface["removed"] or interface["in_out"] != "INPUT":
                    diagnostics.append(_gn_patch_diagnostic(
                        "error", "modifier_input_not_found", f"{path}/socket",
                        f"Input interface socket not found: {operation['socket']}",
                    ))
                elif interface["item"] is not None and hasattr(interface["item"], "default_value"):
                    _gn_validate_value(
                        interface["item"], "default_value", operation["value"],
                        f"{path}/value", diagnostics, node_refs,
                    )
                if policy == "single_user_copy":
                    target_user = patch.get("target_user", {})
                    if (
                        target_user.get("kind") != "MODIFIER"
                        or target_user.get("object") != operation["object"]
                        or target_user.get("modifier") != operation["modifier"]
                    ):
                        diagnostics.append(_gn_patch_diagnostic(
                            "error", "modifier_input_outside_copy_target", path,
                            "single_user_copy may only change inputs on its target modifier",
                        ))
                diff["modifier_inputs_changed"] += 1
                summary = f"Set modifier input {operation['object']}/{operation['modifier']}"

            errors_after = sum(item["severity"] == "error" for item in diagnostics)
            plan.append({
                "index": index,
                "op": op,
                "status": "ready" if errors_after == errors_before else "invalid",
                "summary": summary,
            })
    finally:
        bpy.data.node_groups.remove(temp_tree)

    candidate_revision = None
    candidate_stats = None
    created_interface = None
    if not any(item["severity"] == "error" for item in diagnostics):
        execution_probe = tree.copy()
        execution_probe.name = f".{tree.name}.MCP Dry Run"
        try:
            dry_run = _gn_apply_operations_to_working(execution_probe, patch)
            created_interface = dry_run.get("created_interface") or None
            invalid_links = [link for link in execution_probe.links if not link.is_valid]
            if invalid_links:
                raise RuntimeError(
                    f"Projected tree contains {len(invalid_links)} invalid links"
                )
            projected_snapshot = _gn_export_tree(execution_probe, "all")
            candidate_revision = projected_snapshot["revision"]
            candidate_stats = projected_snapshot["stats"]
        except Exception as exc:
            diagnostics.append(_gn_patch_diagnostic(
                "error", "dry_run_execution_rejected", "",
                f"{type(exc).__name__}: {exc}",
            ))
        finally:
            if execution_probe.users == 0:
                bpy.data.node_groups.remove(execution_probe)

    result = {
        "schema": GEOMETRY_NODES_PATCH_VALIDATION_SCHEMA,
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "stage": "runtime",
        "will_mutate": False,
        "tree_name": tree.name,
        "base_revision": patch.get("base_revision"),
        "current_revision": current_revision,
        "shared_tree_policy": policy,
        "users": users,
        "diagnostics": diagnostics,
        "plan": plan,
        "semantic_diff": diff,
    }
    if candidate_revision is not None:
        result["candidate_revision"] = candidate_revision
        result["candidate_stats"] = candidate_stats
    if created_interface:
        # Echo the reference -> Blender identifier map (e.g. "GEO" -> "Socket_0")
        # so callers can wire Group Input/Output sockets in a follow-up patch
        # without applying first. Mirrors apply's created_interface_sockets.
        result["created_interface_sockets"] = created_interface
    return result
