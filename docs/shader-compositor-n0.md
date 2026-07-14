# Shader and Compositor Nodes: N0 Runtime Decisions

This record captures the cross-version runtime and transaction gates that must
hold before generic Shader or Compositor mutation tools are exposed. The probes
run headlessly against Blender 4.2.22 LTS, 5.1.2, and 5.2 LTS RC without changing
the active Scene.

## Owner and transaction matrix

| Domain | Owner | Runtime tree access | Selected transaction |
|---|---|---|---|
| Shader | Material | Embedded, read-only `node_tree` pointer | Copy Material, edit its independent tree, remap all ID users |
| Shader | World | Embedded, read-only `node_tree` pointer | Copy World, edit its independent tree, remap all ID users |
| Shader | Light | Embedded, read-only `node_tree` pointer | Copy Light data, edit its independent tree, remap all ID users |
| Shader | Node group | Standalone `ShaderNodeTree` | Copy tree and remap group-node users |
| Compositor | Scene, Blender 4.2 | Embedded, read-only `node_tree` pointer | Copy Scene and remap Scene users |
| Compositor | Scene, Blender 5.1/5.2 | Writable `compositing_node_group` | Copy tree and switch only the selected Scene pointer |
| Compositor | Node group | Standalone `CompositorNodeTree` | Copy tree and remap group-node users |

The compositor API transition is present in the tested Blender 5.1 build, not
only in Blender 5.2. The adapter must therefore inspect the Scene instance for
`compositing_node_group`; it must not branch on a presumed 5.2 boundary.

Every selected path was tested with an injected failure after the user switch.
Material slots, Scene worlds, shared Light objects, group nodes, legacy Scene
references, and modern Scene tree pointers all returned to the exact original
ID. Temporary owners and trees were removed after rollback.

## Runtime differences that affect validation

- Material, World, and Light Shader outputs use different output-node types and
  must be validated against the owner context.
- Blender 4.2 provides `CompositorNodeComposite` and the older Scene compositor
  contract. It restricts Render Layers creation to a Scene-owned tree.
- The tested Blender 5.1 and 5.2 builds do not register
  `CompositorNodeComposite`; final render output is represented by the Scene
  compositor group's output interface.
- Node availability does not imply validity in every owner context. Catalog and
  schema cache keys must include tree type and owner kind.
- Color ramps, curve mappings, image/clip/mask/Scene references, and legacy File
  Output slots are dynamic or typed structures. They require dedicated patch
  operations. Generic RNA property assignment is insufficient.
- File Output mutation and Script-node configuration remain disabled until a
  narrower effect-safety gate is accepted.

Linked Materials, Worlds, Lights, Shader groups, and Compositor groups report
both the owner and tree as non-editable in all three builds. Generic resolvers
must expose them read-only and reject validation/apply before making a copy.

## Side-effect boundary

N0 transaction probes create only temporary datablocks, never render or invoke
the compositor, and verify that no external output file appears. The current
Scene pointer remains unchanged. A generic patch may change graph data only; it
must never render, bake, save an image, run a script, or write a File Output.

## Flat-graph baseline

The canonical graph stays flat. With 252 nodes, the candidate serializer
produced approximately 173–179 KiB for Shader trees and 421–426 KiB for
Compositor trees. A one-node targeted response was approximately 1.4 KiB and
2.4 KiB respectively, with stable revisions in all tested builds.

The current targeted prototype still walks and hashes the complete graph to
obtain the stale-state revision, so targeted CPU time remains close to a full
export even though model context is much smaller. N5 owns this optimization;
the protocol must not trade stale-state safety for speed.

## Reproducible probes

- `tests/blender_shader_compositor_capabilities.py`
- `tests/blender_shader_compositor_dynamic.py`
- `tests/blender_shader_compositor_transactions.py`
- `tests/blender_shader_compositor_linked.py`
- `tests/blender_shader_compositor_scale.py`

Each script prints one machine-readable result line and removes every temporary
datablock before Blender exits.
