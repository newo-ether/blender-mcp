"""Shared dynamic node-collection metadata."""

from __future__ import annotations


_NODE_DYNAMIC_COLLECTION_ALLOWLIST = {
    "GeometryNodeFieldToList": frozenset({"list_items"}),
    "GeometryNodeClosureToList": frozenset({"list_items"}),
    "GeometryNodeRepeatOutput": frozenset({"repeat_items"}),
    "GeometryNodeSimulationOutput": frozenset({"state_items"}),
    "GeometryNodeForeachGeometryElementOutput": frozenset({
        "input_items", "main_items", "generation_items",
    }),
    "NodeClosureOutput": frozenset({"input_items", "output_items"}),
    "NodeEvaluateClosure": frozenset({"input_items", "output_items"}),
}


def _node_dynamic_collection_names(node):
    """Return allowlisted dynamic collections in deterministic order."""
    return tuple(sorted(_NODE_DYNAMIC_COLLECTION_ALLOWLIST.get(node.bl_idname, ())))


def _node_dynamic_collection_record(owner, prop, encode_value, limit=50):
    """Serialize one RNA item collection without inherited node properties."""
    identifier = prop if isinstance(prop, str) else prop.identifier
    if isinstance(prop, str):
        try:
            prop = owner.bl_rna.properties[identifier]
        except (KeyError, TypeError):
            prop = None
    record = {
        "identifier": identifier,
        "type": "COLLECTION",
        "readonly": bool(getattr(prop, "is_readonly", False)),
        "item_rna_type": getattr(getattr(prop, "fixed_type", None), "identifier", None),
        "count": 0,
        "items": [],
        "truncated": False,
    }
    try:
        collection = getattr(owner, identifier)
        record["count"] = len(collection)
        for index, item in enumerate(list(collection)[:limit]):
            values = {}
            for item_prop in item.bl_rna.properties:
                item_identifier = item_prop.identifier
                if item_identifier == "rna_type" or item_prop.type == "COLLECTION":
                    continue
                if getattr(item_prop, "is_hidden", False):
                    continue
                try:
                    values[item_identifier] = encode_value(
                        getattr(item, item_identifier)
                    )
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    continue
            record["items"].append({
                "index": index,
                "rna_type": item.bl_rna.identifier,
                "values": values,
            })
        record["truncated"] = record["count"] > limit
    except (AttributeError, TypeError, ValueError, RuntimeError):
        record["unavailable"] = True
    return record
