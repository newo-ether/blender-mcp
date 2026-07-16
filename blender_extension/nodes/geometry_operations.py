"""Geometry Nodes patch operation execution."""

from __future__ import annotations

from .patch_operations import _node_add_paired_zone, _node_dynamic_collection
from .patch_values import _gn_decode_patch_value, _gn_resolve_patch_socket


def _gn_apply_operations_to_working(working, patch):
    node_refs = {
        node.name: {"node": node, "existing": True, "removed": False}
        for node in working.nodes
    }
    interface_refs = {
        (getattr(item, "identifier", "") or item.name): {
            "item": item,
            "actual_identifier": getattr(item, "identifier", "") or item.name,
            "removed": False,
        }
        for item in working.interface.items_tree
    }
    created_nodes = {}
    created_interface = {}
    deferred_modifier_inputs = []
    operation_results = []

    for index, operation in enumerate(patch["operations"]):
        op = operation["op"]
        result = {"index": index, "op": op, "status": "applied"}

        if op == "add_node":
            node = working.nodes.new(operation["node_type"])
            reference = operation["id"]
            node_refs[reference] = {"node": node, "existing": False, "removed": False}
            if "name" in operation:
                node.name = operation["name"]
            for property_name, value in operation.get("properties", {}).items():
                setattr(node, property_name, _gn_decode_patch_value(value, node_refs))
            layout = operation.get("layout", {})
            for field in ("location", "width", "height"):
                if field in layout:
                    setattr(node, field, layout[field])
            if layout.get("parent") is not None:
                node.parent = node_refs[layout["parent"]]["node"]
            created_nodes[reference] = node.name
            result["node_name"] = node.name

        elif op == "remove_node":
            item = node_refs[operation["node"]]
            working.nodes.remove(item["node"])
            item["removed"] = True
            if not item["existing"]:
                created_nodes.pop(operation["node"], None)

        elif op == "rename_node":
            node = node_refs[operation["node"]]["node"]
            node.name = operation["name"]
            if operation["node"] in created_nodes:
                created_nodes[operation["node"]] = node.name
            result["node_name"] = node.name

        elif op == "set_node_property":
            node = node_refs[operation["node"]]["node"]
            setattr(
                node,
                operation["property"],
                _gn_decode_patch_value(operation["value"], node_refs),
            )

        elif op == "set_socket_default":
            node = node_refs[operation["node"]]["node"]
            socket = _gn_resolve_patch_socket(
                node, operation["socket"], "input", "", [],
            )
            if socket is None:
                raise RuntimeError(f"Validated input socket disappeared: {operation['socket']}")
            socket.default_value = _gn_decode_patch_value(operation["value"], node_refs)

        elif op in {"add_link", "remove_link"}:
            from_node = node_refs[operation["from_node"]]["node"]
            to_node = node_refs[operation["to_node"]]["node"]
            from_socket = _gn_resolve_patch_socket(
                from_node, operation["from_socket"], "output", "", [],
            )
            to_socket = _gn_resolve_patch_socket(
                to_node, operation["to_socket"], "input", "", [],
            )
            if from_socket is None or to_socket is None:
                raise RuntimeError("Validated link socket disappeared")
            if op == "add_link":
                working.links.new(from_socket, to_socket, verify_limits=True)
            else:
                match = next(
                    (
                        link for link in working.links
                        if link.from_socket == from_socket and link.to_socket == to_socket
                    ),
                    None,
                )
                if match is None:
                    raise RuntimeError("Validated link disappeared before removal")
                working.links.remove(match)

        elif op == "set_node_layout":
            node = node_refs[operation["node"]]["node"]
            for field in ("location", "width", "height"):
                if field in operation:
                    setattr(node, field, operation[field])
            if "parent" in operation:
                node.parent = (
                    node_refs[operation["parent"]]["node"]
                    if operation["parent"] is not None else None
                )

        elif op == "add_interface_socket":
            parent_ref = operation.get("parent")
            parent = interface_refs[parent_ref]["item"] if parent_ref else None
            item = working.interface.new_socket(
                name=operation["name"],
                in_out=operation["in_out"],
                socket_type=operation["socket_type"],
                parent=parent,
            )
            if "default" in operation and hasattr(item, "default_value"):
                item.default_value = _gn_decode_patch_value(operation["default"], node_refs)
            reference = operation["id"]
            actual_identifier = getattr(item, "identifier", "") or item.name
            interface_refs[reference] = {
                "item": item,
                "actual_identifier": actual_identifier,
                "removed": False,
            }
            created_interface[reference] = actual_identifier
            result["interface_identifier"] = actual_identifier

        elif op == "add_interface_panel":
            item = working.interface.new_panel(
                name=operation["name"],
                description=operation.get("description", ""),
                default_closed=operation.get("default_closed", False),
            )
            reference = operation["id"]
            actual_identifier = getattr(item, "identifier", "") or item.name
            interface_refs[reference] = {
                "item": item,
                "actual_identifier": actual_identifier,
                "removed": False,
            }
            created_interface[reference] = actual_identifier
            result["interface_identifier"] = actual_identifier

        elif op == "remove_interface_socket":
            item = interface_refs[operation["identifier"]]
            working.interface.remove(item["item"])
            item["removed"] = True
            created_interface.pop(operation["identifier"], None)

        elif op == "set_interface_item":
            item = interface_refs[operation["identifier"]]["item"]
            setattr(
                item,
                operation["property"],
                _gn_decode_patch_value(operation["value"], node_refs),
            )

        elif op == "add_dynamic_item":
            node = node_refs[operation["node"]]["node"]
            collection = _node_dynamic_collection(node, operation["collection"])
            item = collection.new(operation["socket_type"], operation["name"])
            result["dynamic_item"] = {
                "index": len(collection) - 1,
                "name": getattr(item, "name", operation["name"]),
            }

        elif op == "remove_dynamic_item":
            node = node_refs[operation["node"]]["node"]
            collection = _node_dynamic_collection(node, operation["collection"])
            collection.remove(collection[operation["index"]])

        elif op == "set_dynamic_item":
            node = node_refs[operation["node"]]["node"]
            collection = _node_dynamic_collection(node, operation["collection"])
            setattr(
                collection[operation["index"]],
                operation["property"],
                _gn_decode_patch_value(operation["value"], node_refs),
            )

        elif op in {"add_foreach_zone", "add_closure_zone"}:
            if op == "add_foreach_zone":
                input_type = "GeometryNodeForeachGeometryElementInput"
                output_type = "GeometryNodeForeachGeometryElementOutput"
            else:
                input_type = "NodeClosureInput"
                output_type = "NodeClosureOutput"
            input_node, output_node = _node_add_paired_zone(
                working, input_type, output_type, operation
            )
            input_id, output_id = operation["input_id"], operation["output_id"]
            node_refs[input_id] = {"node": input_node, "existing": False, "removed": False}
            node_refs[output_id] = {"node": output_node, "existing": False, "removed": False}
            created_nodes[input_id] = input_node.name
            created_nodes[output_id] = output_node.name
            result["input_node"] = input_node.name
            result["output_node"] = output_node.name

        elif op == "set_modifier_input":
            deferred_modifier_inputs.append({
                **operation,
                "actual_identifier": interface_refs[operation["socket"]]["actual_identifier"],
            })

        operation_results.append(result)

    return {
        "node_refs": node_refs,
        "interface_refs": interface_refs,
        "created_nodes": created_nodes,
        "created_interface": created_interface,
        "deferred_modifier_inputs": deferred_modifier_inputs,
        "operation_results": operation_results,
    }
