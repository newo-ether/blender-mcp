# Node workflows

## Select the exact tree

1. When the request refers to the visible, open, current, selected, active, or highlighted nodes -- anything pointing at what is on the user's screen rather than naming a tree -- call get_node_editor_context first.
   - UNIQUE_EDITOR or PINNED_EDITOR: use the selected editor and its tree_ref.
   - MULTIPLE_EDITORS: show the bounded editor identities and require an explicit choice; never choose by focus, order, or recency.
   - STALE_CONTEXT: refresh before using any UI-derived target.
   - NO_EDITOR: ask the user to open one, or continue with owner discovery only when the request already names the target.

   Each editor reports active_node and selected_nodes. Those are the exact stable
   node names, so "change the node I have selected" is answerable directly and
   needs no guessing from labels or position. Selection is live UI state: read it
   when it matters and use it immediately, rather than carrying it forward as if
   it were part of the graph.
2. When a request names a node by role rather than identity -- "the node controlling the detail", "that colour ramp" -- and the index or a query cannot resolve it to one candidate, ask the user to select it in the Node Editor and say so, then read selected_nodes. In a file with dozens of trees and hundreds of similarly named nodes, one click is faster and more reliable than several rounds of guessing, and it settles which tree is meant at the same time. Do not fall back to this when the request already names the node, or when the index already resolves it.
3. Otherwise call list_node_trees and filter by tree type or owner kind when possible.
4. Select the returned owner-addressed tree_ref; do not identify embedded Shader or Compositor trees by display name alone.
5. Check edit capability, library state, graph size, users, and revision before planning a mutation.
6. For a missing Scene compositor tree, call ensure_scene_compositor_tree read-only first. Set create_if_missing=true only when the user requested creation or the requested edit clearly requires it.
7. For a missing standalone Geometry, Shader, or Compositor group, call create_node_group and continue from its returned tree_ref and initial revision. For a Geometry Nodes modifier workflow, call ensure_geometry_nodes_modifier read-only first; enable object creation, modifier creation, or reassignment only when the requested outcome authorizes that mutation.

The Geometry-specific list_geometry_node_trees family remains useful for exact group-name workflows. Prefer the generic owner-addressed family when Material, World, Light, Scene, or cross-domain identity matters.

## Inspect only the relevant subgraph

1. Search get_node_tree_index with a distinctive node name, label, or type.
2. Use query_node_graph before export when the question is bounded:
   - fields for an allowlisted projection of compact node records. inputs
     reports only the sockets whose value differs from the node type's own
     default, so an untouched node reports none and a reported socket is one
     somebody deliberately set;
   - socket_links for incident links or one exact socket;
   - named_attributes for Named Attribute readers and writers;
   - shortest_path for one route between exact nodes;
   - upstream, downstream, or slice for bounded reachability.

   These are the right tools for reading one node or a few: a fields projection
   of a single node costs a few hundred bytes against tens of kilobytes for a
   whole-graph export, and every query returns the same revision an export
   does, so it is directly usable as a patch base_revision.
3. Call export_node_tree with view=auto by default. It selects slim for a complete graph and semantic for a targeted subgraph. Pass selected node_names and neighbor_depth=1 for local rewiring.
4. Treat the views as materially different context costs. Each step up roughly doubles the bytes and buys strictly less per byte:

   | view | ~bytes, 30-node group | ~bytes, 2048-node graph | what it adds |
   | --- | --- | --- | --- |
   | slim | ~9 KB | ~330 KB | node types, operation enums, links, only defaults that are neither linked over nor at the node type's own default |
   | operations | ~29 KB (3x slim) | ~820 KB | every socket record, including linked and default-valued ones |
   | semantic | ~43 KB (5x slim) | ~1.5 MB | full RNA properties and socket contracts |
   | all | ~45 KB (5x slim) | ~1.6 MB | layout on top of semantic |

   Those figures are a Geometry group, where slim's saving is mostly structural. On a Shader or Compositor graph, whose nodes ship many non-zero defaults, slim also drops them: one Principled BSDF reads as 2 sockets rather than 30.

   - slim is the reading view. Prefer it to understand what a graph computes: it preserves every operation enum and every link, and states omitted Frame nodes in stats.omitted_node_count rather than hiding them. A socket it reports is one somebody deliberately set.
   - operations adds complete socket records. Request it when a socket's presence, order, or default matters even where slim judged it uninformative.
   - semantic adds full RNA detail. Request it only for an identified missing socket contract, property, or default.
   - layout contains node placement, dimensions, and parent frames for presentation work.
   - all combines semantic and layout. Never request it speculatively or merely for completeness.
5. Treat any single export above roughly 25 KB as a context hazard: it may be truncated or spilled to a file by the client, leaving you unable to read it directly. When a full-graph export approaches that size, do not escalate the view. Narrow with get_node_tree_index or query_node_graph, or export a targeted subgraph, and only then escalate detail on the nodes that matter.
6. Remember that auto selects semantic for a targeted subgraph, which is the expensive view. Explicitly request view=slim when a targeted inspection only needs operations, links, or meaningful defaults.
7. Increase neighbor depth only when the current export omits a required connection. Escalate detail only for an identified missing fact, never to "see everything".
8. Record the returned revision and stable node names. Never invent node names from UI labels. Every view reports the same source revision, so a revision read from slim is valid for a patch.

Use this routing rule:

```text
one node, before a patch   -> get_node_tree_index + query_node_graph
fields, paths, links       -> query_node_graph
whole graph, what it does  -> export slim
local formulas and wiring  -> export slim with node_names
every socket record        -> export operations
exact socket/RNA contract  -> semantic export or node-type schema
presentation               -> layout export
```

The fields query does not replace a targeted export when incident links and
socket defaults are required together. Treat unsupported-field and invalid
parameter diagnostics as authoritative; do not retry with guessed field names.

For Geometry-specific tools, use get_geometry_node_tree_index and export_geometry_node_tree with the same targeted pattern.

## Confirm runtime contracts

- Search search_geometry_node_types when the exact Blender node identifier is unknown.
- Inspect candidate types with get_node_type_schema or get_geometry_node_type_schema.
- Use the exact tree type and owner kind because sockets and output nodes can differ by context and Blender version.
- Prefer compact schema detail first; request full inherited RNA only for a property that compact output does not resolve.
- If runtime schema conflicts with remembered or documented behavior, trust the connected Blender runtime.

## Patch transaction

1. Select the mutation pair from the exact tree domain:
   - GeometryNodeTree / NODE_GROUP: validate_geometry_node_patch, then apply_geometry_node_patch;
   - ShaderNodeTree or CompositorNodeTree: validate_node_tree_patch, then apply_node_tree_patch.
2. Build a patch against the exact tree reference and exported base revision. Read the nodes you intend to change with get_node_tree_index for the exact name, then query_node_graph fields for their current values and socket_links for the exact socket ids and incident wiring. That is a few hundred bytes per node and carries the revision the patch needs; a whole-graph export to change one socket is the expensive mistake.
3. Limit operations to the requested nodes, links, properties, socket defaults, or interface changes.
4. Submit exactly one of an inline patch or a workspace-relative patch path.
5. Call the selected validator. Do not send a Geometry patch to the generic validator.
6. Require valid=true, inspect diagnostics and semantic diff, and confirm that the plan matches the request.
7. Call the matching apply tool with keep_backup=true unless the user declines a backup.
8. Read back the changed nodes plus one-hop neighbors and compare the new revision and intended connections.

For a single guarded sequence, use modify_verify_save with the same Patch plus
bounded assertions over node_count, link_count, or interface_item_count. Keep
save_policy=never unless the user asked to save; on_success saves an existing
file after verification, while required rejects an Untitled file before mutation.

Use add_dynamic_item, remove_dynamic_item, and set_dynamic_item only on collections reported by the live node schema. Prefer add_foreach_zone and add_closure_zone over constructing paired zone nodes manually. Blender-version rejection is authoritative and the transaction must leave the original tree unchanged.

Never apply after transport-stage validation failure. Script nodes and File Output mutations fail closed in the generic transaction surface; do not evade those safeguards.

## Minimize Python in node workflows

Use the structured surface for node-group creation, Geometry Nodes host/modifier setup, interface panels and sockets, nodes, links, properties, defaults, validation, transactions, and readback. execute_blender_code is reserved for a confirmed capability gap.

When a gap remains:

1. Name the missing structured primitive precisely.
2. Use Python only for that primitive; do not rebuild the surrounding graph in the same script.
3. Export the affected tree immediately after the call and obtain a fresh revision.
4. Resume with runtime schema inspection and validated patches for all remaining work.

An entire graph may be scripted only when the graph cannot be represented by the structured protocol and the unsupported part cannot be isolated. Do not choose whole-graph Python merely because it is shorter to write.

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
