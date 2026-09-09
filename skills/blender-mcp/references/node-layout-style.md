# Reference layout style

Read this when creating, extending, or formatting node graphs. This is the default visual style for generated graphs, based on the user-selected C01-02 reference: its outer Geometry Nodes tree and the reusable implementation inside it. Subsequent user requirements override exceptions in that reference: **all nodes must be inside frames; every Frame contains at least four and at most nineteen direct child nodes; frames may nest and must not become a pile of tiny boxes.** No access to the original project is required to follow this guide. A different explicit user style takes precedence.

## Required frame membership

Every non-Frame node must have a NodeFrame parent in every tree being generated or reformatted. Include Group Input, Group Output, Reroute, constants, debug/viewer nodes, zone endpoints, and nodes that merely carry a wire. Check newly authored nested node groups as well as the outer tree. Only root NodeFrame nodes may be unparented; nested frames belong to their enclosing stage.

Put Group Output in the final processing/assembly frame. Use a separate output frame only when it has at least four meaningful nodes. Put parameters, constants, and reroutes in the frame of the stage they serve. Combine one-, two-, or three-node steps with adjacent related work instead of giving each step its own frame. Do not put the whole graph in one giant frame merely to pass membership: each frame should identify a coherent stage or component.

Count direct children, including Group Inputs and nested Frame nodes. A nested Frame counts as one visible child of its parent, and its own direct children must also number 4-19. This avoids counting every descendant against an enclosing frame while still preventing stacks of nearly empty containers. Nineteen is the ceiling for the user's "十几个"; prefer fewer when the stage remains clear. Split an oversized frame into real substeps with at least four nodes each. For example, four repeated units of five nodes can become four child frames inside a component frame with one join: five children in the parent and five in each child.

Do not create dummy nodes, redundant reroutes, pass-through processing, or empty frames to meet the minimum. If a requested computation or isolated wrapper intrinsically has fewer than four functional nodes, the size rule cannot be met honestly: report that specific conflict without changing computation or claiming that the result matches the style. Do not introduce needless node-group boundaries that create such tiny wrappers.

For a local edit of an existing graph, this rule applies to all nodes in the formatted region; preserve unrelated layout and state the scope. Do not mutate a read-only reference project to make it match a rule.

## Overall composition

- **Modules use subgroups; processing steps use Frames.** Encapsulate an identifiable functional module in a node group with meaningful inputs/outputs. A reusable assembly, path-generation, or material-function module can justify a subgroup. Import, compare, store an attribute, and transform are processing steps; keep them in stage Frames, combining small related steps to meet the frame-size rule. Do not create a subgroup for every frame or operation, and do not use a named Frame as a substitute for a module interface.
- Lay out the main data path horizontally, left to right, with processing stages in that same order. Let a long pipeline be wide. Do not snake connected stages back to the left or spread them into equal-sized dashboard cards to fit a screen.
- Keep the main geometry path in a readable horizontal corridor near the top of its stages. Place supporting field calculations above or below it according to the stage's dependencies. Parameter wires stay within their local stage whenever possible.
- At the outer level, call module subgroups rather than exposing their internal processing steps. A calling/assembly Frame contains local parameter inputs, one or more module instances, and any local aggregation/output. The subgroup provides encapsulation; the Frame organizes the call site. When accumulating geometry, carry the previous stage's result along the upper corridor into the next stage's Join Geometry. Do not add Join Geometry to unrelated computations just to copy a shape.
- Inside implementation groups, frame meaningful steps such as import, store original state, compute positions, instance, and transform. Complex stages contain subframes for real substeps; frame nesting and node-group encapsulation serve different purposes.
- Repeated units share input, processing, and output columns and a consistent ordering of controls. Stack related units inside one frame while staying within 4-19 direct children. Use meaningful nested subframes when the component grows. The reference's one-node frames and oversized flat frames are not exceptions to the user's updated count requirement.

```text
[ Component A frame ]                  [ Component B frame ]
    local inputs -> reusable group        local inputs -> reusable group
                         \                                     \
                         Join -------------------------------> Join
                                                                 |
                                              Group Output inside Component B
```

The diagram shows stage membership and dataflow, not exact socket elevations or a complete node inventory. Each frame needs 4-19 meaningful direct children. Group Output belongs to the final component's frame.

## Local inputs and visible sockets

- Place Group Input nodes immediately to the left of the consumers they serve, inside the same stage frame. Repeat the local input at a distant consumer rather than dragging parameter wires across the entire graph.
- Expose only the relevant sockets; hide the rest with `set_socket_hide`. A single scalar often uses a one-socket Group Input. A related pair such as horizontal/vertical controls or length/width may stay together in one compact Group Input. Small local fan-out inside one stage is allowed when readable. Do not split it solely to force exactly one outgoing link.
- Align feeders with the relevant visible sockets, in their top-to-bottom order. A tall target can need several vertically spaced input nodes. Aligning every input to the target's center would pile them up.
- Hide unused outputs after wiring, including unused Group Input extension sockets when the runtime permits it. Preserve useful unlinked controls on processing nodes. Do not collapse whole nodes to conceal an overcrowded layout; the reference uses expanded nodes.
- Read socket order after properties, interface, and visibility are finalized. Multi-input order can affect computation: preserve it when moving feeders.

## Spacing and appearance

Use these as starting measurements in **canvas units**, not screen pixels or immutable limits. Increase clearance for wider labels, expanded controls, curves, or a different interface. Verify displayed bounds after drawing.

| Element | Reference starting point |
| --- | --- |
| Ordinary node / Group Input width | 140 |
| Repeated outer group width | 180 |
| Typical node column pitch | 200-240; outer local input to group: 220 |
| Clear horizontal edge gap | About 60-100 for local connections |
| Frame child inset | About 30 on the left; first row about 36 below the frame origin |
| Gap between stage frames | Usually 80-180; allow more for joins or long labels |
| Frame label | Size 20, short semantic title |
| Styling | Native node colors, neutral frames, no custom color scheme; expanded nodes |

A representative outer component uses this parent-local arrangement:

| Role | Location | Width |
| --- | --- | --- |
| Offset Group Input | (30, -156) | 140 |
| Progress Group Input | (30, -236) | 140 |
| Display Group Input | (30, -316) | 140 |
| Related two-parameter Group Input | (30, -396) | 140 |
| Reusable processing group | (250, -116) | 180 |
| Local Join Geometry | (510, -36) | 140 |

These values describe one reference module, not a template to stamp onto arbitrary nodes. At its measured UI scale of 1.25, single-socket inputs displayed at roughly 51-52 canvas units high, the two-socket input at 72.8, and the processing group at 416.8. An 80-unit input pitch works there; it does not work for an arbitrary 100-unit-tall node. Choose pitch from the actual displayed height plus clearance.

For this Blender 5.2 reference, `dimensions / ui_scale` agrees with the canvas widths: 175 / 1.25 = 140. Verify that relationship in the current runtime before using it. `node.height` remained 100 even when the processing group visibly occupied 416.8 units. Zero dimensions or an undrawn/stale tree require another measurement or explicit visual inspection, not a fabricated successful check.

Keep built-in node names and labels at their defaults, including Blender's numeric suffixes; explain the stage through its Frame label. Name reusable groups for their function. Match the user's language for frame/group labels (Chinese in this reference); use short component names or verb-object phrases. English titles use title case. Do not copy the reference's duplicate/mismatched labels as a style requirement.

## Placement procedure

1. Identify functional module boundaries and their subgroup interfaces first; then identify the main path, processing/assembly Frames, and semantic subframes inside each tree. A processing step alone is not a reason for subgroup extraction. Assign every functional node to a frame, including Group Output in the final stage. Check planned direct-child counts: merge tiny steps and subdivide oversized stages meaningfully before placing them.
2. Create the nodes and intended connections with structured operations. Finalize width, operation settings, and visible sockets before measuring their heights.
3. Create frame parents before children need them. Updated `add_node` / `set_node_layout` handlers assign the final parent before explicit local coordinates. On older installations without the layout tools, split parent and location into successive operations. Never mix absolute measurements with parent-local patch locations.
4. Inspect with `inspect_node_layout`, then run `audit_node_layout` to check all parents and counts. For whole-tree formatting, use `plan_node_layout` with the current revision and exact main-path nodes. It places nested frames from the inside out, packs measured columns, brings auxiliary calculations near consumers, reserves the upper corridor, and orders feeders by input index. Frames retain natural heights. Review the candidate, validate it, and apply through the existing domain transaction tools. Semantic module/frame boundaries must already be correct; the planner does not decide those boundaries or fabricate nodes.
5. Read back after display/auto-shrink and run the layout post pass in node-workflows.md. Use a fresh layout audit, not the planner's predicted audit, for current bounds. Compare boxes in absolute canvas space. Preserve ancestor containment, reserve the frame title area, and keep unrelated boxes clear. Check actual output/input sides and wires, not just center coordinates. The tool reports endpoint-order warnings; it does not prove exact socket alignment or zero rendered crossings.

## Acceptance evidence

For each generated/formatted tree, report scope, non-Frame node count, number with a valid Frame parent, an explicit list of unframed nodes, and each Frame's direct-child count. Require no unframed functional nodes and no Frame outside 4-19 direct children. Root frames are excluded from the functional-node count. Check containment as well as the parent property; assigning a frame without moving the child inside it is insufficient.

Prefer `audit_node_layout` for the live tree: check `diagnostic_counts`, `frame_child_counts`, `bounds_complete`, `bounds_verified`, and the diagnostics themselves. `valid=true` means no detected hard errors; missing/cached/estimated measurements and wire-order warnings still need attention. Inspect each newly authored nested node group separately. `inspect_node_layout` includes links; legacy `export_node_tree(view=layout)` does not.

For an offline snapshot or an older installation without the live audit, run the bundled [frame audit](../scripts/audit_node_frames.py) against a complete structured `view=layout`, `view=all`, or `inspect_node_layout` snapshot saved as JSON:

```text
python <skill-directory>/scripts/audit_node_frames.py <snapshot.json>
```

The read-only helper reports unframed nodes, invalid/cyclic frame parents, and undersized/oversized frames, exiting nonzero on violations or incomplete evidence. It validates membership and counts only, not visible containment, overlaps, wires, or semantic grouping. For a local edit, use the full tree evidence to assess the affected frames, report unrelated pre-existing violations separately, and do not claim whole-tree compliance.

Inspect a representative stage at readable zoom and the overall stage sequence, including output and auxiliary branches. Verify that functional modules have subgroup interfaces and ordinary processing steps have not been needlessly extracted. Check local inputs, hidden unused sockets, native names/colors, consistent repeated columns, frame titles, and clear wires. Use complete layout/parent data plus link-bearing data at the same revision. Inspect all created stages for collisions; one clean example does not prove the rest are clean.

Patch validation proves the edit is supported, not that this appearance has been achieved. If display evidence is unavailable, identify that limit. Do not claim a screenshot or zero visual violations from a layout-only export.
