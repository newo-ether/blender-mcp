# Failure and recovery

## MCP or Blender unavailable

Stop after a clear disconnected response. Treat a clear connection error as terminal for the current attempt:

1. Do not repeat several live-state calls.
2. Call list_blender_instances once. If none are registered, tell the user to open Blender and enable or start the Blender MCP add-on; registration and endpoint allocation are automatic.
3. Preserve the intended next read-only call so work can resume after reconnection.

When multiple instances are registered, do not use foreground-window or port heuristics. Require an exact instance selection. This applies to reads too: `multiple_instances_require_selection` answers any command, so a read is not a way to inspect a scene without first choosing an instance. Respect `manual`, `claimed_by_other_client`, and the occupancy border; release the current claim before switching targets.

If the tools themselves are absent, distinguish client MCP registration from Blender add-on connectivity.

## Validation rejected

1. Confirm that exactly one patch source was supplied.
2. Read every diagnostic, its stage, code, and path.
3. Re-export the target if the base revision is stale.
4. Inspect live node schema for missing node types, sockets, properties, or owner-context differences.
5. Correct only the diagnosed issue and validate again.
6. Never call apply while validation is invalid or transport failed.

## Transaction failed

- Check mutated, applied, rollback, backup, and revision fields in the result.
- Re-export the exact target before deciding whether to retry.
- If rollback succeeded, report that live users were restored.
- If state is uncertain, stop mutations and give the user the backup or manual recovery information returned by the server.
- Do not delete backups until the user has accepted the result.

## Arbitrary Python failed

1. Stop and inspect the error instead of rerunning the same code.
2. Call get_runtime_automation_context or inspect official version-correct documentation when the failure is API- or version-related.
3. Reduce the next attempt to the smallest reversible operation.
4. Reinspect affected objects or datablocks to detect partial mutation.
5. Do not save over the current file as a recovery technique.

## Unsupported operation

Report the exact unsupported layer:

- missing client tool;
- disconnected Blender add-on;
- unsupported Blender version or runtime node contract;
- read-only or linked-library data;
- structured transaction safety rejection;
- disabled external provider;
- operation available only through arbitrary Python.

Offer the safest available alternative. Never imply completion when only a plan, validation, or partial mutation succeeded.
