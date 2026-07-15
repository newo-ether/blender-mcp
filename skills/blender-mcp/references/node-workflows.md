# Node workflows

## Select the exact tree

1. Call list_node_trees and filter by tree type or owner kind when possible.
2. Select the returned owner-addressed tree_ref; do not identify embedded Shader or Compositor trees by display name alone.
3. Check edit capability, library state, graph size, users, and revision before planning a mutation.
4. For a missing Scene compositor tree, call ensure_scene_compositor_tree read-only first. Set create_if_missing=true only when the user requested creation or the requested edit clearly requires it.

The Geometry-specific list_geometry_node_trees family remains useful for exact group-name workflows. Prefer the generic owner-addressed family when Material, World, Light, Scene, or cross-domain identity matters.

## Inspect only the relevant subgraph

1. Search get_node_tree_index with a distinctive node name, label, or type.
2. Call export_node_tree with view=auto by default. It selects operations for a complete graph and semantic for a targeted subgraph. Pass selected node_names and neighbor_depth=1 for local rewiring.
3. Treat the views as materially different context costs:
   - operations is the compact working view for formulas, operation enums, relevant defaults, interfaces, and links. Prefer it for most graph inspection, including targeted subgraphs that do not need complete socket or RNA records.
   - semantic is high-detail output with complete socket and RNA semantics. Request it only when a specific socket contract, property, or default is missing from operations.
   - layout contains node placement, dimensions, and parent frames for presentation work.
   - all is maximum-detail output combining semantic and layout data. It can consume substantial model context even for a medium graph. Never request all speculatively or merely for completeness.
4. Remember that auto selects semantic for a targeted subgraph. Explicitly request view=operations when a targeted inspection only needs formulas, relevant defaults, interfaces, or links.
5. Increase neighbor depth only when the current export omits a required connection. Escalate detail only for an identified missing fact.
6. Record the returned revision and stable node names. Never invent node names from UI labels.

For Geometry-specific tools, use get_geometry_node_tree_index and export_geometry_node_tree with the same targeted pattern.

## Confirm runtime contracts

- Search search_geometry_node_types when the exact Blender node identifier is unknown.
- Inspect candidate types with get_node_type_schema or get_geometry_node_type_schema.
- Use the exact tree type and owner kind because sockets and output nodes can differ by context and Blender version.
- Prefer compact schema detail first; request full inherited RNA only for a property that compact output does not resolve.
- If runtime schema conflicts with remembered or documented behavior, trust the connected Blender runtime.

## Patch transaction

1. Build a patch against the exact tree reference and exported base revision.
2. Limit operations to the requested nodes, links, properties, socket defaults, or interface changes.
3. Submit exactly one of an inline patch or a workspace-relative patch path.
4. Call validate_node_tree_patch or validate_geometry_node_patch.
5. Require valid=true, inspect diagnostics and semantic diff, and confirm that the plan matches the request.
6. Call apply_node_tree_patch or apply_geometry_node_patch with keep_backup=true unless the user declines a backup.
7. Read back the changed nodes plus one-hop neighbors and compare the new revision and intended connections.

For a single guarded sequence, use modify_verify_save with the same Patch plus
bounded assertions over node_count, link_count, or interface_item_count. Keep
save_policy=never unless the user asked to save; on_success saves an existing
file after verification, while required rejects an Untitled file before mutation.

Use add_dynamic_item, remove_dynamic_item, and set_dynamic_item only on collections reported by the live node schema. Prefer add_foreach_zone and add_closure_zone over constructing paired zone nodes manually. Blender-version rejection is authoritative and the transaction must leave the original tree unchanged.

Never apply after transport-stage validation failure. Script nodes and File Output mutations fail closed in the generic transaction surface; do not evade those safeguards.

## Evaluate Blender 5.2 List migrations

Use this sequence for requests such as replacing an uneven index field implementation built from Points plus For Each with Blender 5.2 List nodes:

1. Locate the existing node group or imported node asset and export only the implementation subgraph.
2. Identify its observable contract: input geometry or fields, ordering, index behavior, data type, empty-input behavior, and output domain.
3. Confirm the connected Blender version from runtime evidence.
4. Search live node types for List and inspect every candidate needed for the proposed replacement. Use export_blender_node_asset first when the implementation is still an asset; do not import it merely to inspect its internals.
5. Compare candidate sockets, supported data types, field evaluation, and ordering semantics with the existing contract.
6. Convert only when the live schemas can reproduce the full observable contract. Keep the Points plus For Each implementation when List support is narrower or ambiguous.
7. Make the smallest patch, validate it, preserve a backup, then verify representative normal, uneven, and empty cases.

Do not infer that a node is suitable merely because Blender 5.2 exposes a List category. Report the specific missing type, socket, or semantic guarantee when conversion is unsafe.

## Respect ownership and linked data

- Do not patch linked or read-only trees unless the server explicitly reports an editable override path.
- Preserve direct users and modifier inputs through the transaction.
- Avoid broad rename operations that could invalidate external references.
- Leave transaction backups identifiable and report their names when returned.
