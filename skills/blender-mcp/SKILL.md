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
4. Start with the smallest read-only inspection that can identify the target and constraints.
5. Prefer, in order:
   - a dedicated structured tool;
   - a runtime schema plus a validated transactional node patch;
   - small, auditable execute_blender_code calls when the structured surface cannot express the operation.

Do not call every status or inspection tool preemptively. Let the requested outcome determine what evidence is necessary.

## Inspect before changing

- Use get_scene_info only when broad scene context is necessary.
- Use get_object_info when the target object is already known.
- Use node-tree indexes and targeted exports instead of loading a large graph.
- Use get_runtime_automation_context before version-sensitive Blender Python.
- Resolve version-correct official Blender documentation with get_blender_documentation_context, search_blender_docs, and get_blender_doc_page when an API or feature contract is uncertain.
- Treat returned revisions, editability, owner identity, exact names, and runtime schemas as authoritative.

## Apply changes safely

- Preserve the user's current project and unrelated datablocks.
- Validate a node patch before applying it. Keep transactional backups unless the user explicitly prefers otherwise.
- Read back the affected object or targeted subgraph after mutation.
- Use get_viewport_screenshot only when appearance or spatial composition materially affects success. A screenshot is not a substitute for structured verification.
- Do not save, overwrite, or change the path of a .blend file unless the user asked for that outcome.
- Do not begin a provider download, paid generation job, or destructive cleanup unless the request already authorizes it or the user confirms it.
- Break arbitrary Python into small steps, avoid global context assumptions, and return compact evidence from each step.

## Handle failures

- Stop after a clear disconnected response. Tell the user to open Blender, enable the Blender MCP add-on, and confirm the configured host and port, then wait for them to retry.
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

Summarize the target, the mutation performed, verification evidence, and any retained backup or unsaved state. State explicitly when the Blender file has not been saved.
