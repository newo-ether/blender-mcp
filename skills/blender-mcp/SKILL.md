---
name: blender-mcp
description: Use Blender MCP when a request depends on inspecting or modifying a live Blender scene, object, node graph, node asset, material, render setup, or open .blend file. Prefer structured Blender MCP tools and safe verification workflows. Do not probe Blender for purely conceptual questions that do not depend on current Blender state.
---

# Blender MCP

Use the connected Blender MCP server as the primary interface for live Blender work. Refer to tools by their logical names; the client may expose a qualified variant.

## Route the request

1. Decide whether the request depends on live Blender state.
2. For a conceptual Blender question, answer from reliable knowledge or version-correct documentation without probing the scene.
3. For a live task, confirm that relevant Blender MCP tools are available. If they are absent, report that Blender MCP is not installed or enabled in this client; do not silently switch to GUI automation.
4. Call list_blender_instances before inspecting scene state. When one instance is ready, select it; when several are ready, require an explicit instance_id or an exact unique match on visible file/scene metadata. Never choose by port, registry order, foreground window, or recency.
5. Claiming is automatic: the first command of any kind, read or write, claims the single available instance. When several are registered, no command runs until one is named, so select the instance up front rather than after a `multiple_instances_require_selection` error. Respect `manual` and `claimed_by_other_client`; do not bypass a human-reserved Blender window.
6. Start with the smallest read-only inspection that can identify the target and constraints.
7. Prefer, in order:
   - a dedicated structured tool;
   - a runtime schema plus a validated transactional node patch;
   - a small, auditable execute_blender_code call only for the exact primitive the structured surface cannot express.

Treat execute_blender_code as a capability-gap fallback, not a shortcut. In node workflows, do not use arbitrary Python for nodes, interfaces, links, properties, defaults, node-group creation, or Geometry Nodes modifier setup when structured tools can express them. If one primitive still requires Python, isolate that primitive in the smallest call possible, then return immediately to structured export, validation, patching, and readback. Use Python for an entire graph only when the structured surface genuinely cannot express the graph and the missing primitive cannot be isolated; state that limitation before execution.

Do not call every status or inspection tool preemptively. Let the requested outcome determine what evidence is necessary.

## Inspect before changing

- Use get_scene_info only when broad scene context is necessary.
- Use get_object_info when the target object is already known.
- Use audit_external_dependencies, inspect_evaluated_mesh, and get_simulation_status instead of diagnostic Python when they answer the question.
- Use node-tree indexes and targeted exports instead of loading a large graph. Read graphs through the default view=auto, which selects the compact slim view; escalate to operations, semantic, or all only for an identified missing fact, since each step roughly doubles context for less information.
- When the user means the visible, current, or selected nodes, call
  get_node_editor_context; it reports active_node and selected_nodes as exact
  stable names. Continue automatically only for UNIQUE_EDITOR or PINNED_EDITOR.
  For MULTIPLE_EDITORS require an explicit choice, and refresh after
  STALE_CONTEXT; never infer a Node Editor from focus, window order, or recency.
- When a request points at a node by role and no index or query resolves it to
  one candidate, ask the user to select it in the Node Editor rather than
  guessing through several rounds of names.
- Use get_runtime_automation_context before version-sensitive Blender Python.
- Resolve version-correct official Blender documentation with get_blender_documentation_context, search_blender_docs, and get_blender_doc_page when an API or feature contract is uncertain.
- Treat returned revisions, editability, owner identity, exact names, and runtime schemas as authoritative.

## Apply changes safely

- Preserve the user's current project and unrelated datablocks.
- Validate a node patch before applying it. Keep transactional backups unless the user explicitly prefers otherwise.
- Prefer modify_verify_save when the task benefits from candidate-count assertions and the user has stated a save policy; its default remains unsaved.
- Read back the affected object or targeted subgraph after mutation.
- Before sending the final response for any live Blender task, call release_blender_instance if this MCP process selected or claimed an instance. Release after read-only work as well as mutation, and also before failure or early-stop handoffs, since a claim taken to read still holds the lock until it is released or expires. If release cannot reach Blender, report that the lease will expire as a fallback. The viewport border marks the instance as AI-occupied for the life of the claim, and a Node Editor joins it once the AI writes that kind of tree; neither is an input lock.
- Use get_viewport_screenshot only when appearance or spatial composition materially affects success. A screenshot is not a substitute for structured verification.
- Do not save, overwrite, or change the path of a .blend file unless the user asked for that outcome.
- Do not begin a provider download, paid generation job, or destructive cleanup unless the request already authorizes it or the user confirms it.
- When arbitrary Python is necessary, confine it to the unsupported primitive, avoid global context assumptions, return compact evidence, and resume the structured workflow immediately afterward.

## Handle failures

- Stop after `no_registered_instances` or a clear disconnected response. Tell the user to open Blender and enable or start the Blender MCP add-on; endpoint allocation and registration are automatic.
- On `multiple_instances_require_selection`, show bounded file/scene summaries and ask which instance to use. Never probe windows through client-specific computer control to guess.
- On `instance_manual` or `instance_claimed_by_other_client`, preserve human control and ask the user to enable or release that Blender instance.
- On `file_session_changed` or `instance_changed`, discard the stale target, list instances again, and require a fresh claim before mutation.
- When Blender reports a missing node type, socket, property, or edit capability, inspect the live schema or documentation once. If no equivalent exists, report the limitation instead of guessing.
- Do not bypass linked-library or read-only restrictions with arbitrary Python.
- On validation failure, correct the patch from diagnostics and revalidate before any application attempt.
- On a partial or uncertain result, report what changed, what did not, and the safest recovery action.

## Load focused guidance

- Read [references/node-workflows.md](references/node-workflows.md) for Geometry, Shader, or Compositor node work, including Blender 5.2 List migration checks.
- Read [references/asset-workflows.md](references/asset-workflows.md) for bundled node assets, PolyHaven, Sketchfab, Hyper3D, or Hunyuan3D.
- Read [references/render-workflows.md](references/render-workflows.md) when visual output, render configuration, or viewport verification is part of acceptance.
- Read [references/recovery.md](references/recovery.md) after transport, validation, transaction, or arbitrary-code failures.

## Report completion

Summarize the target, the mutation performed, verification evidence, release status, and any retained backup or unsaved state. State explicitly when the Blender file has not been saved.
