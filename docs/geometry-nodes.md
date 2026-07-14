# Geometry Nodes automation

This fork provides a revisioned, structured Geometry Nodes workflow for MCP
clients. The normal path does not ask a model to generate arbitrary `bpy` code
and does not rewrite a complete node tree for a localized change.

## Why this needs dedicated tools

At this fork's upstream baseline, BlenderMCP could inspect general scene data
and execute Python inside Blender, so a model could technically reach
`bpy.data.node_groups`. That is capability, but not a reliable node-editing
protocol: there was no normalized tree read, stable socket selector, stale-state
check, dry-run, shared-user policy, transactional commit, or machine-readable
actual diff.

Blender's Python API exposes node trees and remains the runtime authority. The
MCP layer here adds the safety and context-management contract needed by an
agent. `execute_blender_code` remains available only as an explicit fallback for
operations outside the supported patch schema.

## Supported MCP tools

| Tool | Purpose | Mutates Blender |
| --- | --- | --- |
| `list_geometry_node_trees` | List trees, users, editability, size, and revisions | No |
| `get_geometry_node_tree_index` | Search/page compact node names and types | No |
| `export_geometry_node_tree` | Return or write a full graph or N-hop subgraph | No |
| `get_geometry_node_type_schema` | Probe sockets and editable RNA properties in the running Blender version | No |
| `validate_geometry_node_patch` | Structurally validate and execute a disposable-copy dry-run | No |
| `apply_geometry_node_patch` | Validate, copy, verify, remap users, and report the actual diff | Yes |

The public contracts are in [`schemas/`](../schemas):

- [`geometry-nodes-v1.json`](../schemas/geometry-nodes-v1.json)
- [`geometry-nodes-index-v1.json`](../schemas/geometry-nodes-index-v1.json)
- [`geometry-nodes-patch-v1.json`](../schemas/geometry-nodes-patch-v1.json)
- [`geometry-nodes-patch-validation-v1.json`](../schemas/geometry-nodes-patch-validation-v1.json)
- [`geometry-nodes-patch-application-v1.json`](../schemas/geometry-nodes-patch-application-v1.json)

## Recommended file-edit workflow

Set `BLENDER_MCP_WORKSPACE` to the project directory in which the MCP process is
allowed to read and write JSON files. If it is unset, the server's current
working directory is the boundary. Paths outside this boundary, non-JSON files,
and patch files larger than 4 MiB are rejected.

1. Call `list_geometry_node_trees` and select an editable tree.
2. For a non-trivial graph, call `get_geometry_node_tree_index`. Search by node
   name, label, `bl_idname`, or Blender label and page through results if needed.
3. Call `export_geometry_node_tree` with the chosen `node_names` and a
   `neighbor_depth` from 0 to 5. Use `view="semantic"` unless layout is relevant.
4. Copy the returned `revision` into `base_revision` in a patch file. Create or
   edit that file with the client's existing file-edit tool.
5. Call `validate_geometry_node_patch` with `patch_path`. Do not apply unless it
   returns `valid: true`, `stage: "runtime"`, and `will_mutate: false`.
6. Call `apply_geometry_node_patch` with the same file. Keep the default backup
   unless the caller deliberately accepts its removal.
7. Check `status`, `new_revision`, `actual_diff`, `verification`, and any
   warnings. Re-index or re-export before another edit.

An illustrative normalized snapshot and patch are available in
[`examples/geometry-nodes-snapshot.json`](../examples/geometry-nodes-snapshot.json)
and [`examples/geometry-nodes-patch.json`](../examples/geometry-nodes-patch.json).
Their all-zero revisions are placeholders and must be replaced with the exact
revision returned by the current Blender file.

### Inline versus file patches

Both validation and application accept exactly one of:

- `patch`: an inline JSON object; or
- `patch_path`: a workspace-relative `.json` path.

Use `patch_path` for iterative agent work. It gives the user a durable,
reviewable artifact and lets ordinary file diff/edit tools make small changes.
Use the inline form for short, programmatically generated calls.

## Patch operations

Version 1 supports:

- `add_node`, `remove_node`, and `rename_node`
- `set_node_property` and `set_socket_default`
- `add_link` and `remove_link`
- `set_node_layout`
- `add_interface_socket` and `remove_interface_socket`
- `set_modifier_input`

Node names and exported socket IDs are selectors. A socket ID has the form
`input:<index>:<name>` or `output:<index>:<name>`. Do not invent an index from
the UI label: use the exported socket ID or call
`get_geometry_node_type_schema` for the active Blender version.

New nodes use a patch-local `id`. Later operations in the same patch may refer
to that ID; the application result maps it to the actual Blender node name.

## Revision and transaction behavior

- `revision` hashes the complete semantic and layout graph: interface, nodes,
  sockets/properties, and links. It excludes the data-block name, user count,
  library path, and editability metadata.
- Full, semantic, layout, and subgraph exports of the same source share the same
  full-graph `revision`. `scope.content_revision` identifies the returned view.
- Validation rejects a patch when `base_revision` is stale.
- Dry-run applies every operation to a disposable copy, re-exports it, and
  reports the candidate revision without changing the source.
- Application uses another verified copy. User remapping occurs only after the
  copy matches the dry-run result.
- The original tree is retained by default as a fake-user backup named from its
  revision. `keep_backup=false` removes it only after verification.
- An application exception attempts to restore names, users, and modifier input
  values. The result distinguishes `rolled_back` from `rollback_failed`; callers
  must treat the latter as requiring manual inspection.

## Shared and linked node groups

The default `shared_tree_policy` is `reject` when a tree has multiple users.
Choose deliberately:

- `single_user_copy` requires a `target_user` identifying one modifier or one
  group node. Only that user is remapped to the patched copy.
- `mutate_shared` explicitly accepts changing every user and returns a warning
  describing the blast radius.

Linked-library trees are exportable but read-only. Validation returns
`tree_not_editable`, and application is rejected without creating a working
copy. Library overrides are not claimed as editable in version 1.

## Flat graph and model-context efficiency

The snapshot stores nodes in a name-keyed map and links as normalized endpoint
records. This flattened graph is effective for deterministic serialization,
stable selectors, random access, and small diffs. It does not make a complete
large graph cheap for a Transformer: attention and context use still grow with
all serialized sockets and RNA properties.

Synthetic chain measurements from the local acceptance gate are:

| Blender | Graph | Semantic JSON | 1-hop subgraph | 50-node index | 2-op patch |
| --- | ---: | ---: | ---: | ---: | ---: |
| 5.1.2 | 252 nodes / 251 links | 475,520 B | 6,760 B | 5,510 B | 375 B |
| 5.2 LTS RC | 252 nodes / 251 links | 480,058 B | 6,814 B | 5,510 B | 375 B |

The subgraph is about 1.42% of the full semantic payload, the index page about
1.15%, and the patch about 0.08%. Semantic-only output is still about 96% of the
all-view payload in this fixture because socket and RNA metadata dominate;
omitting layout alone is not enough. The recommended context strategy is
therefore **index -> targeted subgraph -> type probe when needed -> patch**.

Token figures should be measured with the actual client model's tokenizer. The
acceptance test's byte/4 estimate is only a coarse budget signal, not a model
benchmark or a guaranteed task success rate.

## Compatibility

| Blender | Extension/package status | Geometry Nodes test status |
| --- | --- | --- |
| 4.2 LTS | Manifest minimum; intended version boundary | Not locally verified |
| 5.1.2 | Build, validate, fresh install, enable/disable pass | Full acceptance pass |
| 5.2 LTS RC | Validate, fresh install, enable/disable pass | Full acceptance pass |
| 3.x | Legacy `addon.py` installation remains available | Not supported or claimed for this protocol |

The implementation uses runtime feature detection for modifier interface values:
Blender 5.1 uses legacy modifier ID properties, while Blender 5.2 exposes the
Geometry Nodes modifier interface. Node type schemas are also probed at runtime
instead of relying on a hard-coded cross-version catalog.

## Known exclusions

Version 1 is Geometry Nodes only. Shader, Compositor, Texture, and World trees,
animation/drivers, simulation bake state, lossless cross-version migration,
linked-tree mutation, and arbitrary node-specific operators are not part of the
contract. Unsupported work may use `execute_blender_code`, but doing so bypasses
the revision, schema, dry-run, copy-on-write, and actual-diff guarantees above.

## Build and install the ZIP

Build with Blender's official extension command wrapper:

```powershell
python scripts/build_blender_extension.py --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

The output is `dist/blender_mcp-<version>.zip`. In Blender 4.2 or newer, choose
**Edit > Preferences > Add-ons > Install from Disk...**, select the ZIP without
extracting it, and enable **Blender MCP**.
