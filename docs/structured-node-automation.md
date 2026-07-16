# Structured node automation

Blender MCP 1.9 provides one owner-addressed JSON protocol for reading and
incrementally editing Geometry, Shader, and Compositor node trees. Shader and
Compositor mutations use the generic transaction tools. Geometry mutations keep
the existing Geometry Nodes v1 contract so modifier inputs and shared-tree
policies remain explicit.

## Why the graph is flat

Snapshots store nodes in a name-keyed map and links as endpoint records. This is
a graph, not a recursively nested tree: a Blender graph may branch, merge, use
reroutes and frames, and contain cyclic group references. The flat form provides
stable random access, deterministic serialization, and small reviewable patches.

Flattening does not make a complete large graph cheap for a Transformer. Socket
and RNA metadata still dominate the payload, so the intended context path is:

1. `get_node_editor_context` when the request refers to the visible/current
   editor, continuing only when its state identifies one editor unambiguously.
2. `list_node_trees` to choose an exact owner when UI context is absent or the
   request names a datablock directly.
3. `get_node_tree_index` to search and page node names and types.
4. `query_node_graph` for projected fields, links, paths, Named Attributes, or
   a bounded directional slice.
5. `export_node_tree` with `view="operations"`, selected names, and a small
   neighbor depth when only formulas, socket values, and links are needed.
6. `get_node_type_schema` only when an exact runtime socket or property is
   needed.
7. Edit a small patch with the client's normal file-edit tool.
8. Validate, apply, and inspect the actual diff.

## Owner references

`tree_ref` is the identity of a live graph. Embedded trees are addressed by
their owning Blender ID because their display names are not reliable identities.

| Tree type | Owner kinds | Mutation contract |
| --- | --- | --- |
| `GeometryNodeTree` | `NODE_GROUP` | Generic read/schema; Geometry Nodes v1 mutation |
| `ShaderNodeTree` | `MATERIAL`, `WORLD`, `LIGHT`, `NODE_GROUP` | Generic owner-aware transaction |
| `CompositorNodeTree` | `SCENE`, `NODE_GROUP` | Generic version-aware transaction |

Always retain the complete `tree_ref` returned by `list_node_trees`; do not
reconstruct it from a UI label.

Mutation tools are selected by tree domain, not by whichever validator is
easiest to discover:

```text
GeometryNodeTree / NODE_GROUP
  -> validate_geometry_node_patch
  -> apply_geometry_node_patch

ShaderNodeTree or CompositorNodeTree
  -> validate_node_tree_patch
  -> apply_node_tree_patch
```

For a local Geometry tree, generic capabilities report `validate=false`,
`apply=false`, and `mutation_reason="geometry_uses_v1_mutation_tools"`.
Linked/read-only targets report both false; local library overrides may be
dry-run with the generic validator but remain non-applicable. A
`recommended_tools` capability is deliberately not added to v1 because its
strict schema rejects additional capability properties; first-class tool names
belong in a versioned v2 envelope.

## Public tools

| Tool | Role | Changes Blender |
| --- | --- | --- |
| `get_node_editor_context` | Resolve visible Node Editors and their current owner-addressed tree without implicit focus/order selection | No |
| `list_node_trees` | Discover owners, capabilities, revisions, size, users, and limits | No |
| `ensure_scene_compositor_tree` | Inspect a Scene or explicitly initialize its missing compositor tree | Only with `create_if_missing=true` |
| `get_node_tree_index` | Search/page a compact index | No |
| `query_node_graph` | Project fields or query links, Named Attributes, paths, and bounded slices | No |
| `export_node_tree` | Return or atomically write a full graph or targeted N-hop subgraph | No |
| `get_node_type_schema` | Probe the live Blender version in an exact owner context | No |
| `validate_node_tree_patch` | Run structure and runtime checks on a disposable copy | No |
| `apply_node_tree_patch` | Revalidate, commit a verified copy, and verify or roll back | Yes |

## Live Node Editor context

`get_node_editor_context` returns `blender-node-editor-context/1` with a
file-session-scoped `context_id`, deterministic `context_revision`, pin state,
active and selected node names, navigation path, and resolvable `tree_ref`
values. Its state machine is explicit:

| State | Meaning | Action |
| --- | --- | --- |
| `NO_EDITOR` | No Node Editor is open | Open one or select by owner with `list_node_trees` |
| `UNIQUE_EDITOR` | Exactly one unpinned editor exists | Use `selected_context_id` and its `tree_ref` |
| `PINNED_EDITOR` | Exactly one pinned editor exists | Honor the pinned target |
| `MULTIPLE_EDITORS` | More than one editor exists | Require an explicit choice; never infer from focus, order, or recency |
| `STALE_CONTEXT` | The expected file session or context revision changed | Refresh before using any editor target |

Pass the previous `file_session_id` and `context_revision` back as expectations
when a later operation depends on the same UI target. `selected_context_id` is
null for ambiguous and stale states.

The JSON contracts live in [`schemas/`](../schemas). Both validation and
application accept exactly one of an inline `patch` or a workspace-relative
`patch_path`. File-backed patches are recommended because they are durable,
diffable, and easy to edit incrementally.

The `operations` export view omits inherited RNA metadata while retaining node
operation enums, non-default writable scalars, enabled/linked socket defaults,
interfaces, and links. It uses the same full-graph revision as every other view.

## Bounded graph queries

`query_node_graph` returns `blender-node-graph-query/1` and the same full-graph
revision as exports. Its query types are:

| Need | Query | Required parameters |
| --- | --- | --- |
| Selected compact fields | `fields` | Optional `node_names`; optional allowlisted `fields` |
| Incident or exact-socket links | `socket_links` | Optional `node_names`; `socket_id` requires exactly one name |
| Named Attribute readers/writers | `named_attributes` | Optional `node_names` and exact `attribute_name` |
| One shortest path | `shortest_path` | `from_node`, `to_node`; optional `direction` |
| Directional reachability | `upstream`, `downstream` | `node_names` |
| Bounded bidirectional/directional reachability | `slice` | `node_names`; optional `direction` |

Use this routing rule:

```text
fields, paths, links       -> query_node_graph
local formulas and wiring -> export operations
exact socket/RNA contract  -> semantic export or node-type schema
presentation               -> layout export
```

The `fields` query projects compact node records. It does not replace a
targeted operations export when incident links and socket defaults are both
needed. Unknown projected fields and invalid parameter combinations fail with
parameter-specific errors instead of silently producing partial records.

## Empty Scene compositor setup

An empty Scene may have no compositor tree. In Blender 5.1+, setting
`Scene.use_nodes` does not create the required `Scene.compositing_node_group`.
Call `ensure_scene_compositor_tree` first with its default
`create_if_missing=false` to inspect state. Repeat with `true` only when the
caller explicitly wants a tree.

For Blender 5.1+, creation builds a standalone `CompositorNodeTree`, adds its
Image output interface and Group Output, assigns only the selected Scene, and
verifies the canonical `tree_ref`. Failure restores the Scene pointer and
removes the new tree. Existing trees return `ready` without mutation; linked or
override Scenes are rejected.

## Patch model

A `blender-node-tree-patch/1` document contains the exact `tree_ref`, the full
graph `base_revision`, declared capabilities, and at most 500 operations. The
all-zero revisions in [`examples/`](../examples) are placeholders; replace them
with the revision exported from the open Blender file.

Supported generic operations are:

- graph: `add_node`, `remove_node`, `rename_node`, `add_link`, `remove_link`;
- values: `set_node_property`, `set_socket_default`;
- presentation: `set_node_layout`, `set_annotation`;
- groups: `add_interface_socket`, `remove_interface_socket`;
- dynamic data: `set_color_ramp`, `set_curve_mapping`.

New nodes use a patch-local `id`. Later operations in the same patch may use
that ID, and the application result maps it to the final Blender node name.
Socket selectors use the exported `input:<index>:<name>` and
`output:<index>:<name>` form. The index disambiguates duplicate labels; never
guess it from the UI.

Typed values can refer to supported Blender IDs or a View Layer without hiding
raw Python in the patch. Missing IDs, an owner mismatch, unavailable node types,
read-only properties, invalid links, and stale revisions produce structured
diagnostics.

## Validation and commit

Validation checks the JSON shape in the MCP process, resolves the owner in
Blender, verifies the revision and limits, copies the owner/tree, applies every
operation to that disposable copy, and re-exports the candidate. A successful
result has `valid: true`, `stage: "runtime"`, and `will_mutate: false`.

Application repeats validation immediately before commit. It then uses the
owner adapter below, re-exports the committed graph, and compares it with the
validated candidate. The original is retained as a fake-user backup by default.

| Owner | Commit adapter |
| --- | --- |
| Material, World, Light | Copy the owner and embedded Shader tree, then remap owner users |
| Shader/Compositor node group | Copy the NodeTree and remap its users |
| Scene, Blender 4.2 | Copy the Scene with its embedded compositor tree and remap users |
| Scene, Blender 5.1+ | Copy the compositor NodeTree and swap only the selected Scene pointer |

The Blender 5.1+ Scene adapter deliberately leaves other scenes that shared the
old compositor group untouched. If any commit or post-commit check fails, the
transaction restores owner pointers, names, fake-user state, and graph identity.
Treat `rollback_failed` as requiring manual inspection.

## Safety and limits

- Linked-library owners are readable but not mutable.
- Library overrides are readable and may be dry-run, but apply is rejected.
- Python/add-on custom nodes are exportable but generic mutation is denied.
- legacy Texture node trees are outside this protocol.
- `ShaderNodeScript` and `CompositorNodeOutputFile`, plus effect-sensitive
  properties such as paths and scripts, fail closed.
- Validation creates no renders, output files, bakes, or persistent temporary
  data-blocks.
- A patch file is limited to 2 MiB and 500 operations.
- Runtime mutation is limited to 10,000 nodes and 30 seconds of validation.
- A public full response is limited to 8 MiB. Use index plus targeted export if
  the graph is larger.
- Geometry exports and patch dry-runs warn when a local Object Info source is
  hidden from render, `As Instance` is a fixed true value, and its geometry
  reaches Group Output. Keep the prototype render-visible outside the camera,
  disable instancing, or realize/author the prototype inside the graph.

`BLENDER_MCP_WORKSPACE` bounds all snapshot and patch paths. Paths outside that
directory and non-JSON files are rejected.

## Performance acceptance

The 2,048-node acceptance fixture passed on Blender 4.2.22, 5.1.2, and 5.2 LTS
RC. On Blender 5.2, the full Shader snapshot was 1,502,614 bytes and the
index-plus-targeted path used 0.224% of that payload. The full Compositor
snapshot was 4,973,998 bytes and its targeted path used 0.103%. Results on 4.2
and 5.1 were materially similar.

The same acceptance sequence discovers, explains, adds, reconnects, tunes,
annotates, and rolls back changes. These byte ratios measure protocol payload,
not model accuracy or tokenizer-specific token counts.

## Compatibility

| Blender | Shader | Compositor | Notes |
| --- | --- | --- | --- |
| 4.2.22 LTS | Passed | Passed | Embedded Scene compositor adapter |
| 5.1.2 | Passed | Passed | `Scene.compositing_node_group` adapter |
| 5.2 LTS RC | Passed | Passed | Runtime node schemas include new version features |

Node availability and sockets are never assumed from a hard-coded cross-version
catalog. Query `get_node_type_schema` against the connected Blender build, and
use the official-manual tools when behavior—not just RNA shape—needs explanation.
