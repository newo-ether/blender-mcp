# Blender MCP — Structured Node Automation Fork

[![Release](https://img.shields.io/github/v/release/newo-ether/blender-mcp)](https://github.com/newo-ether/blender-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Blender 4.2+](https://img.shields.io/badge/Blender-4.2%2B-orange.svg)](https://www.blender.org/)

**English** | [中文](README_CN.md)

This community fork connects MCP clients to Blender and adds a structured,
revision-safe workflow for reading and editing Geometry, Shader, and
Compositor node trees.

It retains the upstream BlenderMCP scene, object, viewport, asset, and
model-generation tools while adding:

- owner-aware Geometry, Shader, and Compositor discovery, export, validation,
  and transactional edits;
- version-aware official Manual, Python API, Release Notes, live node-schema,
  and installed Essentials queries;
- a checksummed GitHub Release containing the Blender Extension, Python wheel,
  Claude Desktop MCPB, and portable Blender MCP Agent Skill;
- a one-command Windows installer with automatic client and Blender detection;
- terminal and graphical target selectors for multi-version installation.

> This is a third-party community project. It is not affiliated with or endorsed
> by Blender, OpenAI, or Anthropic.

## Quick start on Windows

### Requirements

- Windows PowerShell 5.1 or newer
- Python 3.10 or newer
- Blender 4.2 or newer for the Extension package
- at least one MCP client, such as Codex, ChatGPT with Codex mode, Claude Code,
  or Claude Desktop

### One-command installation

Open PowerShell and run:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/bootstrap.ps1 | iex
```

`-Scope Process` applies only to the current PowerShell window; it does not
permanently change the user or machine execution policy. The ASCII
[bootstrap.ps1](bootstrap.ps1) only fetches and launches the human-readable
[install.ps1](install.ps1). For a reproducible, version-pinned install, use:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create(([string](irm https://raw.githubusercontent.com/newo-ether/blender-mcp/v1.14.1/install.ps1)).TrimStart([char]0xFEFF))) -ReleaseTag v1.14.1
```

The explicit `TrimStart` removes the UTF-8 BOM carried by the localized full
installer when it is parsed directly from HTTP. The bootstrap performs the same
single-character normalization automatically; the BOM is retained so Windows
PowerShell 5.1 can also run `install.ps1` from disk without corrupting Chinese.

Before changing the machine, the installer:

1. detects supported MCP clients and every local Blender installation;
2. opens a terminal checklist;
3. downloads the latest stable [GitHub Release](https://github.com/newo-ether/blender-mcp/releases/latest);
4. verifies the wheel, Extension ZIP, portable Skill ZIP, and optional fallback MCPB against `SHA256SUMS.txt`;
5. installs into a versioned environment such as
   `%LOCALAPPDATA%\BlenderMCP\venv-1.14.1`;
6. installs the server and the Extension into each selected Blender version without resetting existing Blender preferences;
7. adds or updates the canonical `blender_mcp` entry for selected clients;
8. installs the same portable Skill for selected Codex and Claude Code clients,
   and prepares a verified upload ZIP for Claude Desktop with an explicit
   action-required reminder; Desktop upload is not reported as installed.

Updates are idempotent: an exact matching Codex entry is left alone, while a
different `blender_mcp` Codex, Claude Code, or Claude Desktop user entry is replaced in place.
Use `-PreserveExistingMcpEntries` when an existing custom entry must not change.
Versioned environments allow an update while an older server is still running;
the current session finishes on the old process and restarted clients use the
new verified environment. Older `venv-<version>` directories are retained so a
live process is never deleted; after all MCP clients are closed, obsolete ones
may be removed manually.

### Target selector

The default terminal UI uses:

- Up/Down to move
- Space to toggle
- A to toggle all available entries
- Enter to install
- Esc or Q to cancel without making changes

Use `-Gui` for a WinForms checkbox window.

| Target | Installer behavior |
| --- | --- |
| Codex / ChatGPT | One combined target that adds or updates their shared per-user `blender_mcp` stdio configuration and installs one shared Skill under `~/.agents/skills`. |
| Claude Code CLI | Adds or updates `blender_mcp` in user scope and installs the Skill under `~/.claude/skills`; project/local MCP entries are not removed. |
| Claude Desktop | Safely merges `blender_mcp` into `%APPDATA%\Claude\claude_desktop_config.json`, preserving other settings and making a backup before replacement. It also prepares a verified Skill ZIP for explicit upload. Invalid or unwritable JSON falls back to the checksummed MCPB and Claude's in-app confirmation. |
| Blender 4.2+ | Every detected supported version is selected by default; deselect any version you do not want to update. |
| Blender below 4.2 | Shown for clarity but disabled for Extension installation. |

### Finish setup

1. Open a selected Blender version.
2. In the 3D View, press N and open the **BlenderMCP** tab.
3. The bridge registers this Blender instance automatically; endpoint allocation is internal and requires no port setup.
4. Start a new task or restart the selected MCP clients so they discover the
   server and filesystem Skill.
5. Claude Desktop normally needs only a restart. If the installer reports an
   MCPB fallback, approve it under **Settings > Extensions > Advanced settings >
   Install Extension...**; this path does not require a Windows `.mcpb` file
   association.
6. For Claude Desktop, follow the installer path under **Customize > Skills >
   Create skill > Upload a skill** and select the verified
   `blender-mcp-skill-<version>.zip`. Skills do not synchronize between
   Claude Desktop and Claude Code.

## Installer reference

Pass parameters to the remote script by creating a script block:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
$installer = [scriptblock]::Create(([string](irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1)).TrimStart([char]0xFEFF))
& $installer -Gui
```

From a clone, call the file directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

A cloned checkout installs editable local source. Add `-UseRelease` to
exercise the published Release path instead.

| Option | Purpose |
| --- | --- |
| `-DryRun` | Print detection, paths, downloads, and commands without changing state. |
| `-Gui` | Use the graphical selector instead of the default TUI. |
| `-NonInteractive` | Skip both selectors and use detected defaults. |
| `-Language <Auto\|en-US\|zh-CN>` | Choose installer text. `Auto` uses Chinese for a `zh-CN`/`zh-Hans` Windows UI and English otherwise. |
| `-BlenderPath <path[]>` | Limit Blender targets to explicit executable paths. |
| `-PythonPath <path>` | Choose the Python 3.10+ interpreter used to create the venv. |
| `-WorkspacePath <path>` | Set the structured node-tree JSON workspace. |
| `-ReleaseTag <tag>` | Install an exact Release instead of the latest stable release. |
| `-InstallRoot <path>` | Override the per-user Release installation directory. |
| `-UseRelease` | Use Release assets even when the script is run from a clone. |
| `-SkipBlenderExtension` | Install only the Python MCP server. |
| `-SkipCodexRegistration` | Leave Codex/ChatGPT configuration unchanged. |
| `-PreserveExistingMcpEntries` | Keep a different same-name Codex, Claude Code, or Claude Desktop entry instead of updating it. |
| `-SkipClaudeCodeRegistration` | Leave Claude Code configuration unchanged. |
| `-SkipClaudeDesktop` | Leave Claude Desktop unchanged and do not download its fallback MCPB. |
| `-SkipSkillInstallation` | Register selected MCP clients without installing or preparing the Agent Skill. |
| `-SkillScope <User\|Project>` | Install Codex and Claude Code filesystem Skills for the user or a project. |
| `-SkillProjectPath <path>` | Explicit project root for `-SkillScope Project`; the current directory is the default. |
| `-ForceSkillUpdate` | Replace a locally modified or unowned same-name Skill. By default, local edits are preserved. |

Examples:

```powershell
# Inspect the Release path without writing state
& $installer -DryRun

# Install into two explicit Blender versions
& $installer -BlenderPath @(
    "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)

# Install only the server
& $installer -SkipBlenderExtension -SkipCodexRegistration `
    -SkipClaudeCodeRegistration -SkipClaudeDesktop
```

The TUI requires a real interactive console. CI, SSH, redirected input/output,
and `-NonInteractive` use detected defaults, including every supported Blender
installation. If
`Console.ReadKey()` is unavailable, the installer tries WinForms and
then falls back to detected defaults with a warning.

## Architecture

```text
Codex / ChatGPT / Claude
          |
          | MCP over stdio
          v
Python MCP server
          |
          | local discovery + one claimed loopback connection
          v
one selected Blender Extension <-> scene and node trees
```

The MCP server is launched by the client. The Blender Extension hosts the local
bridge. The installer installs the Extension but does not launch Blender. Start
Blender from the logged-on interactive desktop, then start the bridge before
asking the client to use Blender tools; a background or Windows Session 0
process cannot provide a visible window.

### Multiple Blender instances

Every open Blender process registers a bounded local identity automatically. One MCP server can discover several instances but controls at most one at a time:

1. `list_blender_instances` reports the open file, active scene, Blender version, dirty/available state, and claim status.
2. Starting the MCP server only discovers instances; it does not claim Blender. The first live Blender operation may automatically select the sole available instance. With several, call `claim_blender_instance` using an explicit `instance_id`; selection never uses foreground-window or port heuristics.
3. One claim is kept for the complete AI task so related reads and mutations stay on the same file. `get_active_blender_instance` reports that selected identity.
4. Before giving its final answer, stopping early, or handing the task back, the AI calls `release_blender_instance`. If Blender is already unreachable, the bounded lease remains the fallback and expires automatically.
5. Instance heartbeats survive `.blend` loads, and a stale registry timestamp is checked against the live endpoint before the instance is rejected.
6. Disable **Allow AI control** to reserve a window for manual work. A cyan hollow border inside every 3D View means that Blender process is currently claimed; it does not capture mouse or keyboard input.

The add-on first tries the historical loopback endpoint for compatibility, then asks the operating system for a private loopback endpoint when another Blender already owns it. This transport detail is not a user mode and is not shown in the normal UI.

## Blender knowledge

The server can combine published explanations with the exact capabilities of
the connected Blender build. The documentation tools return bounded,
source-attributed JSON instead of loading an entire Manual into context.

| Tool | Purpose | Blender required |
| --- | --- | --- |
| `get_blender_documentation_context` | Resolve the exact build, official source channels, language, and fallbacks without fetching a page. | Only for `version="auto"` |
| `search_blender_docs` | Search official Manual, Python API, and Release Notes indexes with compact ranked results. | Only for `version="auto"` |
| `get_blender_doc_page` | Read one sanitized page or exact heading section returned by search. | Only for `version="auto"` |
| `search_geometry_node_types` | Find node types constructible in Geometry Nodes in the running build. | Yes |
| `get_geometry_node_type_schema` | Read compact live sockets, node-owned properties, and dynamic items; inherited RNA is opt-in. | Yes |
| `get_node_type_schema` | Inspect a Geometry, Shader, or Compositor node in its exact owner context. | Yes |
| `get_runtime_automation_context` | Probe live render-engine/output, layered Action, compositor, and Object Info compatibility without retaining probe data. | Yes |
| `search_blender_node_assets` | Inspect bundled Essentials or configured user node assets without leaving data blocks in the project. | Yes |
| `export_blender_node_asset` | Export an exact asset graph through a disposable load with zero retained datablocks. | Yes |
| `import_blender_node_asset` | Append one exact searched asset locally after revalidating its configured/bundled source identity. | Yes |
| `audit_external_dependencies` | List missing libraries, images, caches, fonts, and other external files without mutation. | Yes |
| `plan_external_dependency_relinks` / `apply_external_dependency_relinks` | Build a bounded, ambiguity-preserving relink plan, then explicitly apply that exact revision with rollback on failure. | Yes |
| `inspect_evaluated_mesh` | Report bounded evaluated topology, components, edge statistics, bounds, and Named Attributes. | Yes |
| `get_simulation_status` | Inspect Geometry Nodes simulation/bake capability and state. | Yes |
| `clear_simulation_cache` / `reset_simulation` / `bake_simulation` | Target one exact Geometry Nodes modifier and bake ID; the current synchronous bake reports `cancellable=false`. | Yes |

Use `version="auto"` for build-correct answers or an explicit version such as
`"5.1"` while Blender is disconnected. Prerelease, channel, and English
fallbacks are always reported. Documentation access is restricted to official
Blender HTTPS origins and cached per user with visible freshness and stale
fallback metadata.

For query strategy, version/language behavior, cache locations, offline rules,
and security boundaries, read
[Blender Manual and runtime knowledge](docs/blender-knowledge.md).

## Structured node automation

Node trees are represented as a flat normalized graph (`nodes{}`, `links[]`,
and interface records), never as a recursively nested connectivity tree or an
opaque generated Python script. Every full and targeted read carries the same
graph revision. A stale patch is rejected before mutation.

Supported generic owners:

| Domain | Owner references | Transaction |
| --- | --- | --- |
| Shader | Material, World, Light, Shader node group | Owner copy/remap or NodeTree copy/remap |
| Compositor | Scene, Compositor node group | Blender 4.2 Scene copy/remap; Blender 5.1+ selected-Scene tree swap; group copy/remap |
| Geometry | Geometry node group | Read/index/schema through the generic tools; mutation remains on the compatible Geometry v1 tools |

### Generic tools

| Tool | Purpose | Mutates Blender |
| --- | --- | --- |
| `get_node_editor_context` | Resolve visible Node Editors, pin state, active/selected nodes, and owner-addressed `tree_ref` values without guessing by focus or order. | No |
| `list_node_trees` | List owner-addressed Geometry, Shader, and Compositor trees, users, capabilities, limits, and revisions. | No |
| `ensure_scene_compositor_tree` | Inspect an exact Scene, or explicitly create and verify its missing compositor tree with rollback. | Only with `create_if_missing=true` |
| `get_node_tree_index` | Search and page a compact index without putting the full graph in model context. | No |
| `export_node_tree` | Return or write a full flat graph or targeted N-hop subgraph. | No |
| `query_node_graph` | Project allowlisted fields or query socket links, Named Attributes, shortest paths, upstream/downstream reachability, and bounded slices. | No |
| `get_node_type_schema` | Probe exact runtime sockets, properties, dynamic structures, and owner restrictions. | No |
| `validate_node_tree_patch` | Check structure, stale state, typed references, runtime semantics, and limits on a disposable copy. | No |
| `apply_node_tree_patch` | Revalidate, commit through the owner adapter, re-export, and roll back exactly on failure. | Yes |
| `modify_verify_save` | Validate a reviewed node Patch, assert candidate graph counts, commit and read back transactionally, then save only under an explicit `save_policy`. | Yes |

The eight `*_geometry_node_*` tools remain available as the Geometry Nodes v1
compatibility contract, including modifier inputs and explicit shared-tree
policy. Asset discovery additionally supports Blender-configured user libraries;
import is a separate opt-in transaction.

### Recommended workflow

1. When the request refers to "the open/current nodes", call
   `get_node_editor_context`. Continue automatically only for
   `UNIQUE_EDITOR` or `PINNED_EDITOR`; require an explicit editor choice for
   `MULTIPLE_EDITORS`, and refresh on `STALE_CONTEXT`.
2. For a Scene with no compositor tree, call `ensure_scene_compositor_tree`
   read-only first, then repeat with `create_if_missing=true` only when wanted.
3. Call `list_node_trees` and retain the exact `tree_ref` when no visible
   editor uniquely identifies the target.
4. Search large graphs with `get_node_tree_index`.
5. Export only the relevant nodes and neighbors. Keep `view="auto"`: complete
   graphs select operations, while targeted subgraphs select semantic detail.
6. Put the returned `revision` and `tree_ref` into a small patch JSON file.
7. Edit that file with the client's normal file-edit tool.
8. For Geometry use `validate_geometry_node_patch`; for Shader or Compositor
   use `validate_node_tree_patch`.
9. Apply with the matching Geometry or generic tool only after validation, then
   inspect `actual_diff`, `new_revision`, users,
   and backup disposition.

For an atomic agent-facing sequence, `modify_verify_save` combines dry-run
validation, declarative `node_count`/`link_count`/`interface_item_count`
assertions, transactional application, and revision readback. It defaults to
`save_policy="never"`; `on_success` and `required` are explicit save requests.

The patch protocols support common graph/layout operations, Frame
annotations, group interfaces, Color Ramps, Curve Mappings, allowlisted dynamic
List/Repeat/Simulation items, paired For Each and Blender 5.2 Closure zones, and typed Blender
IDs and View Layers. The workspace boundary is controlled by
`BLENDER_MCP_WORKSPACE`; files outside it, non-JSON files, files over 2 MiB, and
patches over 500 operations are rejected. A public full response is capped at
8 MiB and redirects callers to index plus targeted export.

Read [Structured node automation](docs/structured-node-automation.md) for the
operation model, transaction adapters, safety rules, version differences,
performance measurements, and compatibility details. Geometry-specific modifier
and asset behavior remains documented in
[Geometry Nodes automation](docs/geometry-nodes.md).

Examples and contracts:

- [Shader snapshot](examples/shader-node-tree-snapshot.json) and
  [patch](examples/shader-node-tree-patch.json)
- [Compositor snapshot](examples/compositor-node-tree-snapshot.json) and
  [patch](examples/compositor-node-tree-patch.json)
- [Geometry snapshot](examples/geometry-nodes-snapshot.json) and
  [patch](examples/geometry-nodes-patch.json)
- [Public JSON schemas](schemas)

## Other capabilities

The server exposes MCP tools including:

- scene and object inspection;
- viewport screenshots;
- arbitrary Blender Python execution;
- object, material, camera, lighting, and scene manipulation through Blender code;
- Poly Haven search, download, and texture application;
- Sketchfab search, preview, and model import;
- Hyper3D Rodin text/image generation and import;
- Hunyuan3D generation and import;
- the three official-documentation tools;
- owner-aware structured-node tools, runtime compatibility probing,
  configured node-asset import, and the eight Geometry Nodes v1 tools.

Example requests:

- “Inspect the scene and frame the camera around the selected object.”
- “Create a low-poly dungeon scene with studio-quality lighting.”
- “Find the Geometry Nodes group used by the selected object and list its inputs.”
- “For this Blender build, search the official docs and live schema for the
  XPBD Solver node.”
- “Export the nodes around Join Geometry and validate a patch that inserts a
  Transform Geometry node.”
- “Index the material node tree, export the neighborhood around Principled
  BSDF, then validate a patch that adds a readable look-development Frame.”
- “Insert RGB Curves, Denoise, and Glare before this Scene's final compositor
  output without rendering or configuring File Output.”
- “Search Poly Haven for a concrete material and apply it to the floor.”

## Manual and cross-platform installation

The automated bootstrap and Claude Desktop launcher are Windows-only. The
Python wheel and Blender Extension ZIP are platform-independent.

Download the latest assets from
[Releases](https://github.com/newo-ether/blender-mcp/releases/latest):

- `blender_mcp-<version>.zip` — Blender Extension
- `blender_mcp-<version>-py3-none-any.whl` — Python MCP server
- `blender_mcp-<version>.mcpb` — Windows Claude Desktop package
- `blender-mcp-skill-<version>.zip` — portable Agent Skill for Claude Desktop upload and filesystem installation
- `SHA256SUMS.txt` — integrity checks

Install the server on Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\blender_mcp-1.14.1-py3-none-any.whl
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./blender_mcp-1.14.1-py3-none-any.whl
```

Install the Extension in Blender 4.2+:

1. Open **Edit > Preferences > Add-ons**.
2. Choose **Install from Disk...**.
3. Select `blender_mcp-<version>.zip` without extracting it.
4. Enable **Blender MCP**.

The supported add-on distribution is the Blender 4.2+ Extension ZIP. The former
single-file Blender 3.x source install was removed so the Blender runtime can be
maintained as tested domain modules instead of one generated add-on file.

### Install the Agent Skill manually

The Skill complements MCP registration; it does not start or register the MCP
server by itself. Use the same canonical folder from
[skills/blender-mcp](skills/blender-mcp) on every client:

- Codex Desktop and Codex CLI: copy the folder to
  `~/.agents/skills/blender-mcp`, or to
  `<project>/.agents/skills/blender-mcp` for project scope.
- Claude Code: copy it to `~/.claude/skills/blender-mcp`, or to
  `<project>/.claude/skills/blender-mcp`.
- Claude Desktop: upload `blender-mcp-skill-<version>.zip` from
  **Customize > Skills**. The ZIP contains the `blender-mcp` folder as its
  root.

Codex and Claude Code discover filesystem Skills separately. Claude Desktop
uploads are also separate; installing on one surface does not synchronize the
others. The Windows installer records hashes beside managed filesystem
installations, skips identical content, updates unmodified managed copies, and
preserves local edits unless `-ForceSkillUpdate` is used.

### Register a client manually

Use the absolute path to the installed `blender-mcp` executable.

Codex CLI and ChatGPT with Codex mode:

```powershell
codex mcp add blender_mcp `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

Claude Code:

```powershell
claude mcp add --scope user blender_mcp `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

Other stdio MCP clients can use the same executable and environment variables.
For Claude Desktop on Windows, the installer adds or updates only the
`mcpServers.blender_mcp` object in
`%APPDATA%\Claude\claude_desktop_config.json`. It preserves unrelated fields,
backs up an existing file, and uses absolute paths—there is no `${HOME}` value
for Claude to expand. If that JSON is malformed or cannot be written, the
installer leaves it untouched and falls back to the published MCPB through
**Settings > Extensions > Advanced settings > Install Extension...**.

## Configuration

### MCP server environment

| Variable | Default | Description |
| --- | --- | --- |
| `BLENDER_MCP_RUNTIME_DIR` | platform user state | Optional registry override for tests and advanced local deployment. |
| `BLENDER_HOST` / `BLENDER_PORT` | `localhost` / `9876` | Legacy fallback only when no discovery-aware add-on is registered. |
| `BLENDER_MCP_WORKSPACE` | server working directory | Allowed root for structured node-tree JSON files. |
| `BLENDER_MCP_CACHE_DIR` | platform user cache | Optional parent directory for versioned Blender documentation cache entries. |
| `DISABLE_TELEMETRY` | unset | Set to `true` to disable MCP server telemetry. |

`BLENDER_MCP_DISABLE_TELEMETRY` and
`MCP_DISABLE_TELEMETRY` are also accepted as complete-disable
switches.

### Blender preferences

Persistent settings are available under
**Edit > Preferences > Add-ons > Blender MCP**:

- telemetry consent, auto-connect, and **Allow AI control** (enabled by default);
- Poly Haven enablement;
- Hyper3D provider and API key;
- Sketchfab API key;
- Hunyuan3D mode, credentials, endpoint, and generation defaults.

Headless environments may provide credentials with:

- `BLENDERMCP_SKETCHFAB_API_KEY`
- `BLENDERMCP_HYPER3D_API_KEY`
- `BLENDERMCP_HUNYUAN3D_SECRET_ID`
- `BLENDERMCP_HUNYUAN3D_SECRET_KEY`
- `BLENDERMCP_HUNYUAN3D_API_URL`

Disabling telemetry consent in Blender removes prompts, code, screenshots, and
other rich metadata but retains minimal anonymous operational events. Set
`DISABLE_TELEMETRY=true` in the MCP client environment to disable all
MCP server telemetry.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No checklist appears | Use a normal PowerShell console. Redirected streams, CI, SSH, and `-NonInteractive` intentionally use defaults. Use `-Gui` for WinForms. |
| A client is unavailable | Install it, open a new PowerShell session, and rerun the installer. |
| A custom MCP entry must not be updated | Rerun with `-PreserveExistingMcpEntries`, or use the client-specific skip switch. |
| Claude Desktop does not show Blender MCP | Restart Claude Desktop, then inspect `%APPDATA%\Claude\claude_desktop_config.json` and `%APPDATA%\Claude\logs`. The installer preserves malformed JSON and reports the MCPB fallback instead of overwriting it. |
| Claude Code still uses another same-name entry | Run `claude mcp get blender_mcp` and check its scope. Local and project entries take precedence and are intentionally not removed by this installer. |
| The client cannot find Blender | Open Blender and confirm auto-connect is enabled, or click **Start MCP connection**. Then call `list_blender_instances`; endpoint registration is automatic. |
| More than one Blender is open | Choose the exact file/scene summary and call `claim_blender_instance`. Do not choose by window focus or endpoint order. |
| A Blender window must remain manual | Disable **Allow AI control** in that window. If it is occupied, click **Release AI control**; the hollow viewport border then disappears. |
| Blender is running but no window is visible | The installer does not launch Blender. Start it from the logged-on desktop; a background or Windows Session 0 process is not an interactive GUI launch. |
| An old `blender: uvx blender-mcp` entry still appears | Rerun the installer. It removes only semantically matched legacy entries unless `-PreserveExistingMcpEntries` is set; unrelated `uvx` services are retained. |
| The Extension is absent from one Blender | Rerun and select that version, or pass its executable with `-BlenderPath`. |
| Windows asks how to open `.mcpb` | This is only the fallback path. In Claude Desktop use **Settings > Extensions > Advanced settings > Install Extension...** and select the downloaded MCPB. The installer also tries the detected Claude executable directly and can highlight the file. |
| A Geometry Nodes patch is stale | Re-index or re-export the tree and rebuild the patch with the new revision. |
| A Shader or Compositor patch is stale | Re-index or re-export the exact owner-addressed `tree_ref`; do not reuse a patch for another owner with the same display tree name. |
| A linked or override node tree cannot be edited | Linked data is read-only. Local library overrides can be inspected and dry-run, but apply is intentionally disabled. |
| A full graph exceeds the response limit | Use `get_node_tree_index`, then call `export_node_tree` with `view="operations"`, selected `node_names`, and a small `neighbor_depth`. |
| Documentation is unavailable offline | Warm the same source/version/language first. Only marked stale entries fall back during network or server failure; a 404 does not. |
| A prerelease Manual page is missing | Check the structured fallback, then query live node types/schema and installed Essentials for the exact build. |
| Old versioned environments remain | Close all MCP clients, then remove obsolete `%LOCALAPPDATA%\BlenderMCP\venv-<old-version>` directories. Keep the path named in `current-server.txt`. |

Dry-run command:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create(([string](irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1)).TrimStart([char]0xFEFF))) -DryRun
```

## Security and known limitations

- `execute_blender_code` can run arbitrary Python inside Blender. Save
  the `.blend` file first and review high-impact operations.
- The generic mutation protocol covers local Shader and Compositor owners.
  Texture Nodes and unknown add-on/custom nodes are read-only.
- Published prerelease documentation and localized Manual pages can be
  incomplete. Fallbacks are explicit; live runtime schema is authoritative for
  the connected build.
- Linked-library trees are exportable but read-only. Local library overrides
  are not apply targets.
- `ShaderNodeScript`, `CompositorNodeOutputFile`, script/path/slot settings,
  render, composite, bake, simulation, and image-save execution are outside the
  generic mutation protocol.
- Shader owner and node-group transactions remap their existing users.
  Blender 5.1+ Scene compositor edits switch only the selected Scene; unrelated
  Scenes sharing the original tree stay unchanged.
- Structured exports and patch validation warn when a local Object Info target
  is hidden from render, `As Instance` is a fixed true value, and its geometry
  reaches Group Output.
- Automatic Claude Desktop JSON registration uses its documented local-server
  configuration. Only the MCPB fallback requires final in-app approval.
- Automatic installation is Windows-only.
- Blender 4.2.22 LTS, 5.1.2, and 5.2 LTS RC passed the local runtime,
  transactional, linked/override, 2,048-node efficiency, and corner-case suites.
- Optional asset providers may transmit requests or files to their services.

## Development

```powershell
git clone https://github.com/newo-ether/blender-mcp.git
cd blender-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --editable ".[test]"
```

Run the Python unit and structure tests:

```powershell
.\.venv\Scripts\ruff.exe check blender_extension src scripts tests --select F401,F821,F822,F823
.\.venv\Scripts\python.exe -m pytest
```

Run the Blender preference-preservation installer test:

```powershell
.\tests\test_installer_preferences.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

Run the MCP client-registration installer test:

```powershell
.\tests\test_installer_client_registration.ps1
```

Build the Blender Extension:

```powershell
.\.venv\Scripts\python.exe scripts\build_blender_extension.py `
  --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

Build all Release assets:

```powershell
.\scripts\build_release.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
  -PythonPath .\.venv\Scripts\python.exe
```

| Path | Purpose |
| --- | --- |
| [blender_extension](blender_extension) | Blender 4.2+ Extension source, manifest, bridge, node, provider, and UI modules. |
| [src/blender_mcp/app.py](src/blender_mcp/app.py) | Python MCP stdio application composition root. |
| [src/blender_mcp/tools](src/blender_mcp/tools) | MCP tools grouped by instances, scene, documentation, nodes, and providers. |
| [src/blender_mcp/transport](src/blender_mcp/transport) | Local Blender socket transport and instance routing. |
| [src/blender_mcp/protocol](src/blender_mcp/protocol) | Pure-Python errors and structured node contracts. |
| [bootstrap.ps1](bootstrap.ps1) | ASCII one-line entry point that fetches the localized installer. |
| [install.ps1](install.ps1) | Human-readable Windows Release installer. |
| [scripts/build_release.ps1](scripts/build_release.ps1) | Release asset builder. |
| [docs/blender-knowledge.md](docs/blender-knowledge.md) | Official documentation and live runtime knowledge guide. |
| [docs/geometry-nodes.md](docs/geometry-nodes.md) | Geometry Nodes protocol guide. |
| [docs/structured-node-automation.md](docs/structured-node-automation.md) | Generic Shader/Compositor protocol and safety guide. |
| [schemas](schemas) | Public JSON contracts. |
| [tests](tests) | Pure-Python and Blender acceptance scripts. |

Contributions and issue reports are welcome.

## Upstream and credits

This project originated as a fork of
[ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp), created by
[Siddharth Ahuja](https://x.com/sidahuj). Its current Extension, MCP host,
installer, protocol, and release structure are maintained independently and do
not require the upstream repository at build time or runtime.

Upstream resources:

- [Project website](https://blendermcp.org/)
- [Discord](https://discord.gg/z5apgR8TFU)
- [Original tutorial](https://www.youtube.com/watch?v=lCyQ717DuzQ)

## License

[MIT](LICENSE)
