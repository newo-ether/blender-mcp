# Reference layout style

Read this when creating, extending, or formatting node graphs. This is the default visual style for generated graphs, based on the user-selected C01-02 reference: its outer Geometry Nodes tree and the reusable implementation inside it. Subsequent user requirements override exceptions in that reference: **all nodes must be inside frames; every Frame contains at least four and at most nineteen direct child nodes; frames may nest and must not become a pile of tiny boxes.** No access to the original project is required to follow this guide. A different explicit user style takes precedence.

The user's restored strict formatting rules below also override exceptions in the reference, including Group Inputs exposing related parameters together. Apply them to every generated/formatted tree and each authored subgroup.

## Required frame membership

Every non-Frame node must have a NodeFrame parent in every tree being generated or reformatted. Include Group Input, Group Output, Reroute, constants, debug/viewer nodes, zone endpoints, and nodes that merely carry a wire. Check newly authored nested node groups as well as the outer tree. Only root NodeFrame nodes may be unparented; nested frames belong to their enclosing stage.

Put Group Output in the final processing/assembly frame. Use a separate output frame only when it has at least four meaningful nodes. Put parameters, constants, and reroutes in the frame of the stage they serve. Combine one-, two-, or three-node steps with adjacent related work instead of giving each step its own frame. Do not put the whole graph in one giant frame merely to pass membership: each frame should identify a coherent stage or component.

Count direct children, including Group Inputs and nested Frame nodes. A nested Frame counts as one visible child of its parent, and its own direct children must also number 4-19. This avoids counting every descendant against an enclosing frame while still preventing stacks of nearly empty containers. Nineteen is the ceiling for the user's "十几个"; prefer fewer when the stage remains clear. Split an oversized frame into real substeps with at least four nodes each. For example, four repeated units of five nodes can become four child frames inside a component frame with one join: five children in the parent and five in each child.

Do not create dummy nodes, redundant reroutes, pass-through processing, or empty frames to meet the minimum. If a requested computation or isolated wrapper intrinsically has fewer than four functional nodes, the size rule cannot be met honestly: report that specific conflict without changing computation or claiming that the result matches the style. Do not introduce needless node-group boundaries that create such tiny wrappers.

For a local edit of an existing graph, this rule applies to all nodes in the formatted region; preserve unrelated layout and state the scope. Do not mutate a read-only reference project to make it match a rule.

## Strict layout constraints

All six rules are mandatory: the five restored rules plus compact vertical spacing. A candidate from the planner is provisional until each rule passes after application and redraw.

1. **Zero overlap or touching.** Unrelated nodes and Frames must have disjoint displayed bounds with positive clearance; edge-touching also fails. Every child must fit fully inside its parent with space for padding and the title. Intentional ancestor-descendant containment is allowed for nested Frames and must not be reported as a collision. Never compare only node origins or substitute `node.height` for measured display height.
2. **Strictly positive X flow for every link.** The source output side must be to the left of the target input side with a positive edge gap: `target.bounds.left - source.bounds.right > 0`. Zero or negative flow fails. Apply this to auxiliary and cross-frame links as well as the main path. Center ordering alone cannot establish positive edge clearance. Reorder placement and stage boundaries instead of leaving a backward wire.
3. **Exactly one socket and one wire per Group Input.** Each Group Input exposes exactly one used output socket and has exactly one outgoing link in total. Hide all other outputs, including the extension socket when the runtime permits it. Remove unused Group Inputs with zero outgoing links. If a parameter feeds several consumers, create one local Group Input for each connection and preserve the existing parameter/socket identity. Multiple links from the same output also fail. Related parameters and short local fan-out are not exceptions. This restriction is on Group Input nodes, not all node types.
4. **Group Inputs stay close and socket-aligned.** A Group Input belongs to its consumer's Frame and sits immediately to its left. The target-center X minus source-center X must be greater than zero and at most **250 canvas units**, with positive edge clearance as above. Align the source output to the actual receiving input socket's height; do not stack all feeders at a tall target's center or at one generic row. Use measured bounds and visible socket order. This distance is in canvas units, not screenshot pixels.
5. **Ordered fan-in.** Distinct source nodes feeding different sockets on one target must follow the target's visible top-to-bottom input order. A source feeding an upper socket must not sit below the source feeding a lower socket. Inspect actual wires and repair crossings and node-body occlusion. Repeated connections from the same source into several sockets on that target are exempt from the distinct-source ordering comparison; preserve multi-input evaluation order.
6. **Compact vertical spacing.** Stack neighboring nodes closely within each local column or vertical stack under the same Frame parent. Measure from the upper node's displayed bottom edge to the next node's displayed top edge: target **20-40 canvas units**, with an absolute maximum of **60**. Do not use origin-to-origin distance, a fixed oversized row height, or the tallest node elsewhere in the graph to space short nodes. Each node's actual height determines where the next one starts. Keep repeated stacks consistently compact and size Frames around their contents; do not spread nodes out to fill a tall Frame. This is a check on neighboring boxes in a local vertical stack, not arbitrary pairs in different columns or ancestor-descendant containment. If socket alignment or wire readability conflicts with the maximum, revise column placement, feeder positions, or meaningful stage boundaries and recheck; do not silently retain a large gap. An unavoidable conflict remains an explicit unfinished item.

The 4-19 Frame rule and the one-wire Group Input rule both apply. Organize real substeps or module call sites to satisfy both; never combine Group Input outputs, add filler nodes, or invent tiny subgroups merely to make the count pass. If preserving the requested computation makes these rules impossible, identify the exact conflict and report the graph as unfinished instead of silently relaxing a rule or changing the result.

Keep built-in node names and labels unchanged. Explain roles using Frame labels and name actual subgroup datablocks for their function. Use short Chinese Frame labels for this user's graphs; English Frame labels use title case. Keep subgroup nesting shallow (normally one to three levels); deeper functional nesting needs a concrete reason. This does not prohibit meaningful Frame nesting.

## Shortest wiring within each step

Within a processing step (its semantic Frame), the layout objective is to **minimize the total length of its internal node connections, subject to every constraint above**. Frame membership/counts, module boundaries, positive flow, one-wire local Group Inputs, socket/fan-in order, compact vertical gaps, and native naming all remain mandatory. An arrangement that shortens wires but violates any rule is not a candidate.

- Optimize the whole step's internal wiring, not one visually prominent connection at the cost of making the remaining wires long. Bring strongly connected nodes together, place a supporting input/calculation near its consumer, and remove unnecessary column gaps and vertical detours. Avoid spreading sparse nodes evenly across the Frame.
- Compare dependency-respecting node orders, legal column assignments, and compact positions within the same semantic Frame. Begin with the planner's candidate, then improve it wherever a legal reorder or move reduces total wire length. Stop accepting improvements only when they would violate a constraint or no considered legal change shortens the step's wiring; merely passing the structural audit is not the optimization criterion.
- Resolve multiple consumers using their combined internal connections when positioning a processing node. Keep each Group Input dedicated to its single receiving socket. Do not duplicate computation, change links/defaults/interfaces, reorder multi-input evaluation, or move nodes into unrelated steps merely to shorten wires.
- For nested Frames, optimize each child step first. Treat the finished child Frames and subgroup instances as units for the enclosing step, shortening connections between those units while retaining their validated internal layout. Recheck internal constraints if a subsequent change affects their contents.
- Compare actual visible socket-to-socket routes when that evidence is available. Current layout tools do not return exact rendered Bezier lengths; if using endpoint displacement or another geometric proxy to compare candidates, identify it as an estimate and inspect the actual wires afterward. Never claim a mathematically proven global minimum from a heuristic plan, a bounding-box audit, or a single unexamined candidate.

The final layout must pass the six strict checks and show that avoidable internal wire spans and detours have been removed. If reducing a remaining span would conflict with a mandatory constraint, keep the constraint and explain that specific tradeoff instead of silently weakening it.

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
- Enforce the one-socket/one-wire rule for every Group Input with `set_socket_hide` after wiring. Use separate local Group Inputs for related controls or for the same parameter's multiple consumers. Do not combine outputs or permit fan-out to reduce node count.
- Align feeders with the relevant visible sockets, in their top-to-bottom order. A tall target can need several vertically spaced input nodes. Aligning every input to the target's center would pile them up.
- Hide unused outputs after wiring, including unused Group Input extension sockets when the runtime permits it. Preserve useful unlinked controls on processing nodes. Do not collapse whole nodes to conceal an overcrowded layout; the reference uses expanded nodes.
- Read socket order after properties, interface, and visibility are finalized. Multi-input order can affect computation: preserve it when moving feeders.

## Spacing and appearance

Use these as starting measurements in **canvas units**, not screen pixels. The strict limits above remain mandatory, including the 250-unit Group Input distance and 60-unit maximum vertical gap. Adjust other reference measurements for wider labels, expanded controls, curves, or a different interface without violating those limits. Verify displayed bounds after drawing.

| Element | Reference starting point |
| --- | --- |
| Ordinary node / Group Input width | 140 |
| Repeated outer group width | 180 |
| Typical node column pitch | 200-240; outer local input to group: 220 |
| Clear horizontal edge gap | About 60-100 for local connections |
| Vertical edge gap between stacked neighbors | Target 20-40; must not exceed 60 |
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
| Secondary parameter Group Input | (30, -396) | 140 |
| Reusable processing group | (250, -116) | 180 |
| Local Join Geometry | (510, -36) | 140 |

These values describe the reference's local columns, not a template to stamp onto arbitrary nodes. The strict variant uses separate one-socket Group Inputs for every connection. At the reference's measured UI scale of 1.25, single-socket inputs displayed at roughly 51-52 canvas units high and the processing group at 416.8. An 80-unit input pitch works there; it does not work for an arbitrary 100-unit-tall node. Choose pitch from actual displayed height plus clearance, then align each feeder to its receiving socket.

For this Blender 5.2 reference, `dimensions / ui_scale` agrees with the canvas widths: 175 / 1.25 = 140. Verify that relationship in the current runtime before using it. `node.height` remained 100 even when the processing group visibly occupied 416.8 units. Zero dimensions or an undrawn/stale tree require another measurement or explicit visual inspection, not a fabricated successful check.

Keep built-in node names and labels at their defaults, including Blender's numeric suffixes; explain the stage through its Frame label. Name reusable groups for their function. Match the user's language for frame/group labels (Chinese in this reference); use short component names or verb-object phrases. English titles use title case. Do not copy the reference's duplicate/mismatched labels as a style requirement.

## Placement procedure

1. Identify functional module boundaries and their subgroup interfaces first; then identify the main path, processing/assembly Frames, and semantic subframes inside each tree. A processing step alone is not a reason for subgroup extraction. Assign every functional node to a frame, including Group Output in the final stage. Check planned direct-child counts: merge tiny steps and subdivide oversized stages meaningfully before placing them.
2. Create the nodes and intended connections with structured operations. Give each Group Input exactly one connection and hide every unused output. Finalize width, operation settings, and visible sockets before measuring heights.
3. Create frame parents before children need them. Updated `add_node` / `set_node_layout` handlers assign the final parent before explicit local coordinates. On older installations without the layout tools, split parent and location into successive operations. Never mix absolute measurements with parent-local patch locations.
4. Inspect with `inspect_node_layout`, then run `audit_node_layout` to check all parents and counts. For whole-tree formatting, use `plan_node_layout` with the current revision and exact main-path nodes as a starting candidate. Optimize internal wire length in each step among legal orders/columns/positions as described above; the planner does not itself prove shortest wiring or enforce every strict rule. Frames retain natural heights. Review the final candidate, validate it, and apply through the existing domain transaction tools. Semantic module/frame boundaries must already be correct; the planner does not decide those boundaries or fabricate nodes.
5. Read back after display/auto-shrink and run the layout post pass in node-workflows.md. Use a fresh layout audit, not the planner's predicted audit, for current bounds. Compare boxes in absolute canvas space. Preserve ancestor containment, reserve the frame title area, and keep unrelated boxes clear. Check actual output/input sides and wires, not just center coordinates. The tool reports endpoint-order warnings; it does not prove exact socket alignment or zero rendered crossings.

## Acceptance evidence

For each generated/formatted tree, report scope, non-Frame node count, number with a valid Frame parent, an explicit list of unframed nodes, and each Frame's direct-child count. Require no unframed functional nodes and no Frame outside 4-19 direct children. Root frames are excluded from the functional-node count. Check containment as well as the parent property; assigning a frame without moving the child inside it is insufficient.

Prefer `audit_node_layout` for the live tree: check `diagnostic_counts`, `frame_child_counts`, `bounds_complete`, `bounds_verified`, and the diagnostics themselves. `valid=true` means no detected hard errors; missing/cached/estimated measurements and wire-order warnings still need attention. Inspect each newly authored nested node group separately. `inspect_node_layout` includes links; legacy `export_node_tree(view=layout)` does not.

Also report the six strict checks explicitly: unrelated overlap/touch count; non-positive edge-flow count; Group Inputs with zero or multiple outgoing links and with multiple visible used outputs; Group Inputs beyond the 250-unit limit or misaligned with target sockets; fan-in ordering/crossing violations; and neighboring vertical gaps exceeding 60 canvas units. Require zero violations in the generated/formatted scope. The current audit does not enforce all of these: supplement it with exact link counts, measured horizontal/vertical distance calculations, socket visibility/order reads, and actual Node Editor inspection. For vertical checks, identify the local stacks, sort members from top to bottom, compare consecutive measured boxes, and report the largest gap and any violations. Treat relevant audit warnings as failed style checks even when `valid=true`. Unknown measurements or unseen socket/wire geometry remain unverified, never an assumed pass.

For the per-step wiring objective, state which legal ordering/placement alternatives were compared, what wire-length measurement or estimate was used, and which long spans remain necessary because of a hard constraint. Do not describe an unaudited first planner result as an optimized shortest layout.

For an offline snapshot or an older installation without the live audit, run the bundled [frame audit](../scripts/audit_node_frames.py) against a complete structured `view=layout`, `view=all`, or `inspect_node_layout` snapshot saved as JSON:

```text
python <skill-directory>/scripts/audit_node_frames.py <snapshot.json>
```

The read-only helper reports unframed nodes, invalid/cyclic frame parents, and undersized/oversized frames, exiting nonzero on violations or incomplete evidence. It validates membership and counts only, not visible containment, overlaps, wires, or semantic grouping. For a local edit, use the full tree evidence to assess the affected frames, report unrelated pre-existing violations separately, and do not claim whole-tree compliance.

Inspect a representative stage at readable zoom and the overall stage sequence, including output and auxiliary branches. Verify that functional modules have subgroup interfaces and ordinary processing steps have not been needlessly extracted. Check local inputs, hidden unused sockets, native names/colors, consistent repeated columns, frame titles, and clear wires. Use complete layout/parent data plus link-bearing data at the same revision. Inspect all created stages for collisions; one clean example does not prove the rest are clean.

Patch validation proves the edit is supported, not that this appearance has been achieved. If display evidence is unavailable, identify that limit. Do not claim a screenshot or zero visual violations from a layout-only export.
