# Node layout tools

Layout follows two distinct boundaries: functional modules are node subgroups
with meaningful interfaces; processing stages are Frames. Every non-Frame node
belongs to a Frame, including inputs, outputs, reroutes, and auxiliary nodes.
The default policy is 4-19 direct children per Frame. A nested Frame counts as
one child and must independently satisfy the same range. Semantic boundaries
are authored before automatic placement; the planner does not invent them or
add filler nodes to satisfy counts.

See the bundled [reference style](../skills/blender-mcp/references/node-layout-style.md)
for the C01-02 composition, native appearance, local inputs, and acceptance
rules. These tools support Geometry, Shader, and Compositor trees.

## Read, diagnose, plan, apply, verify

| Tool | Result | Mutation |
| --- | --- | --- |
| `inspect_node_layout` | Complete parent/local/absolute positions, measured bounds, links, canonical revision, editor measurement status | No graph edit; optional redraw can update Blender's auto-shrinking Frames |
| `audit_node_layout` | Membership/count violations, containment, unrelated box overlaps/clearance, edge direction and feeder order | Same optional redraw behavior |
| `plan_node_layout` | Deterministic hierarchical candidate patch and advisory predicted audit | Does not apply the patch |

1. Resolve the exact `tree_ref` using the existing owner-aware tools. Finalize
   module boundaries, Frame membership, node settings, widths, and socket
   visibility with structured operations.
2. Inspect the complete tree with `refresh=true` when its Node Editor is already
   open. For several matching editors, pass an exact `context_id` obtained from
   `get_node_editor_context`. A stale context is rejected. Refresh does not
   change the editor's tree, selection, or view.
3. Audit and resolve unframed nodes, invalid parents, cycles, or Frame counts
   outside 4-19. The planner blocks these cases without making structural edits.
4. Call `plan_node_layout` with the inspected `expected_revision` and exact
   `main_path` node names. The path prioritizes the upper corridor. The planner
   treats child Frames as layout units, arranges them from the inside out, ranks
   dependencies left to right, pulls supporting calculations toward consumers,
   orders feeders by target input index, and packs measured heights. Columns
   containing only auxiliary nodes leave space for the incoming main path.
5. Review the returned `patch`, validate, then apply it with the existing pair:
   Geometry uses `validate_geometry_node_patch` / `apply_geometry_node_patch`;
   Shader and Compositor use `validate_node_tree_patch` / `apply_node_tree_patch`.
   Keep normal backup and shared-tree policies. Geometry plans default to
   `shared_tree_policy="reject"`; changing that remains an explicit ownership
   decision. The three layout tools never save the `.blend` file.
6. Reinspect and audit after applying and redrawing, because Blender can shrink
   Frames and change their local origins. Inspect actual wires in the Node
   Editor at both stage and overall scale. Repeat for each authored subgroup.

The candidate contains only `set_node_layout` operations. Nodes, parents,
names, links, socket defaults, and interfaces are preserved. It formats the
**whole addressed tree**, not a selected region. Use targeted structured layout
operations for a local edit that must preserve unrelated placement. Inspection
requires a full tree and supports 1500 nodes; planning is capped at 500 nodes
to match the existing patch operation limit. Cyclic dependencies between layout
units block the plan; evaluation is never rewired to satisfy a visual rule.

Default gaps in canvas units are horizontal 80, vertical 40, and top-level stage
140. Frame padding is 30 plus 36 for its title area. These are placement starting
points; the skill's visual acceptance rules still apply. `main_path` expresses
priority, not a promise of exact socket alignment. A graph with several branches
or unusually large controls can need a reviewed follow-up layout patch.

## Measurements and diagnostic meaning

`blender-node-layout/1` adds derived measurements to a complete snapshot. Unlike
the legacy `export_node_tree(view="layout")`, its `tree.links` contains actual
links. Measurements are not included in the source graph revision. The revision
is read after optional redraw, so auto-shrink changes are represented correctly.

Bounds are `[left, top, right, bottom]` in canvas coordinates with positive Y up.
Positions remain parent-local for patching; `absolute_location` resolves the
complete Frame ancestry. Display dimensions are normalized by UI scale and
checked against expanded node width. `node.height` is not used as displayed
height. Bounds have one of these statuses:

Headless Blender may report `ui_scale=0`; this means the scale is unavailable
and the tool does not infer measured bounds from it.

| Status | Meaning |
| --- | --- |
| `refreshed` | An identified editor was redrawn and its expanded dimensions passed the unit check |
| `cached` | Dimensions exist, but freshness was not established by this inspection |
| `estimated` | Explicit `size_hints`, or provisional collapsed/reroute bounds whose drawn origin offsets are unavailable |
| `unavailable` | No usable dimensions; undrawn/headless trees commonly return this |

Planning without functional-node dimensions is blocked. Explicit
`size_hints={"Exact Node Name": [width, height]}` allows provisional headless
planning; Frame sizes are derived from their children. Hints and predicted
bounds never count as verified display evidence.

The audit checks Frame ancestry, exact counts, containment, and collisions
between unrelated nodes/Frames, excluding intentional ancestor containment.
Wire checks use endpoint sides and target socket index; actual socket positions
and rendered Bezier intersections are unavailable. `visual_layout_checked` is
always false. `valid=true` means **no detected hard errors**, not final visual
acceptance. Inspect `bounds_complete`, `bounds_verified`, warnings, and the
actual editor. Counts cover every diagnostic even when the detailed list is
truncated by `diagnostic_limit`.

`inspect_node_layout` and `plan_node_layout` accept workspace-relative JSON
`output_path` with atomic writes. A saved plan is the complete plan envelope;
extract its `patch` before passing a patch file to the apply tool.

Schemas are [measurement](../schemas/node-layout-v1.json),
[audit](../schemas/node-layout-audit-v1.json), and
[plan](../schemas/node-layout-plan-v1.json). The old snapshot schemas are unchanged.

## Parent-local assignment

Both Geometry and generic `add_node` / `set_node_layout` handlers now assign the
parent first, then an explicit `location`, `width`, and `height`. A combined
`parent` + `location` therefore means coordinates relative to the **final**
parent. Parent-only changes preserve Blender's native canvas position. Non-Frame
parents and ancestry cycles are rejected. Older extensions without these
tools need parent and location in separate successive operations.

## Verification

`tests/test_node_layout.py` checks deterministic non-mutating plans, nested
Frames, membership limits, cycles, measurements, partial evidence, socket order,
main-path corridors, consumer locality, schemas, and workspace output paths.
`tests/blender_node_layout.py` runs the actual transaction in Geometry, Shader,
and Compositor on the portable Blender acceptance matrix.
`tests/blender_node_layout_visual.py` is an opt-in GUI fixture for a separate
factory-startup process, using isolated Blender user config and temporary paths.
It redraws, plans, validates, applies, audits, and captures the actual editor.
It must never be executed in a user's working Blender session.
