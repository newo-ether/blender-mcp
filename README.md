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
- a checksummed GitHub Release containing the Blender Extension, Python wheel, and Claude Desktop MCPB;
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
Set-ExecutionPolicy Bypass -Scope Process -Force; irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1 | iex
```

`-Scope Process` applies only to the current PowerShell window; it does not
permanently change the user or machine execution policy. The source is
human-readable: [install.ps1](install.ps1). For a reproducible, version-pinned
install, use:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/v1.9.0/install.ps1))) -ReleaseTag v1.9.0
```

Before changing the machine, the installer:

1. detects supported MCP clients and every local Blender installation;
2. opens a terminal checklist;
3. downloads the latest stable [GitHub Release](https://github.com/newo-ether/blender-mcp/releases/latest);
4. verifies the wheel, Extension ZIP, and optional MCPB against `SHA256SUMS.txt`;
5. installs into a versioned environment such as
   `%LOCALAPPDATA%\BlenderMCP\venv-1.9.0`;
6. installs the server and the Extension into each selected Blender version without resetting existing Blender preferences;
7. adds or updates the canonical `blender_mcp` entry for selected clients.

Updates are idempotent: an exact matching Codex entry is left alone, while a
different `blender_mcp` Codex or Claude Code user entry is replaced in place.
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
| Codex CLI | Adds or updates the per-user `blender_mcp` stdio server. |
| Codex Desktop (ChatGPT) | Uses the same Codex MCP configuration as the CLI. |
| Claude Code CLI | Adds or updates `blender_mcp` in user scope; project/local entries are not removed. |
| Claude Desktop | Opens the checksummed MCPB; Claude Desktop performs final confirmation. |
| Blender 4.2+ | Select one or several detected versions. The newest is selected by default. |
| Blender below 4.2 | Shown for clarity but disabled for Extension installation. |

### Finish setup

1. Open a selected Blender version.
2. In the 3D View, press N and open the **BlenderMCP** tab.
3. The bridge starts automatically on port `9876`; the preference is enabled by default.
4. Restart or reopen the selected MCP clients.
5. If Claude Desktop was selected, approve the MCPB inside Claude Desktop.

## Installer reference

Pass parameters to the remote script by creating a script block:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
$installer = [scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1))
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
| `-BlenderPath <path[]>` | Limit Blender targets to explicit executable paths. |
| `-PythonPath <path>` | Choose the Python 3.10+ interpreter used to create the venv. |
| `-WorkspacePath <path>` | Set the structured node-tree JSON workspace. |
| `-ReleaseTag <tag>` | Install an exact Release instead of the latest stable release. |
| `-InstallRoot <path>` | Override the per-user Release installation directory. |
| `-UseRelease` | Use Release assets even when the script is run from a clone. |
| `-SkipBlenderExtension` | Install only the Python MCP server. |
| `-SkipCodexRegistration` | Leave Codex/ChatGPT configuration unchanged. |
| `-PreserveExistingMcpEntries` | Keep a different same-name Codex or Claude Code entry instead of updating it. |
| `-SkipClaudeCodeRegistration` | Leave Claude Code configuration unchanged. |
| `-SkipClaudeDesktop` | Do not download or open the Claude Desktop MCPB. |

Examples:

```powershell
# Inspect the Release path without writing state
& $installer -DryRun

# Install into two explicit Blender versions
& $installer -BlenderPath @(
    "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    "C:\Program Files\Blender Foundation\Blender 5.2 LTS\blender.exe"
)

# Install only the server
& $installer -SkipBlenderExtension -SkipCodexRegistration `
    -SkipClaudeCodeRegistration -SkipClaudeDesktop
```

The TUI requires a real interactive console. CI, SSH, redirected input/output,
and `-NonInteractive` use detected defaults. If
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
          | JSON over TCP localhost:9876
          v
Blender Extension <-> Blender scene and node trees
```

The MCP server is launched by the client. The Blender Extension hosts the local
bridge. Start the bridge before asking the client to use Blender tools.

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
| `search_blender_node_assets` | Inspect installed official Essentials node assets without leaving data blocks in the project. | Yes |

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
| `list_node_trees` | List owner-addressed Geometry, Shader, and Compositor trees, users, capabilities, limits, and revisions. | No |
| `get_node_tree_index` | Search and page a compact index without putting the full graph in model context. | No |
| `export_node_tree` | Return or write a full flat graph or targeted N-hop subgraph. | No |
| `get_node_type_schema` | Probe exact runtime sockets, properties, dynamic structures, and owner restrictions. | No |
| `validate_node_tree_patch` | Check structure, stale state, typed references, runtime semantics, and limits on a disposable copy. | No |
| `apply_node_tree_patch` | Revalidate, commit through the owner adapter, re-export, and roll back exactly on failure. | Yes |

The eight `*_geometry_node_*` tools remain available as the Geometry Nodes v1
compatibility contract, including modifier inputs, explicit shared-tree policy,
and official Essentials asset search.

### Recommended workflow

1. Call `list_node_trees` and retain the exact `tree_ref`.
2. Search large graphs with `get_node_tree_index`.
3. Export only the relevant nodes and neighbors.
4. Put the returned `revision` and `tree_ref` into a small patch JSON file.
5. Edit that file with the client's normal file-edit tool.
6. Call `validate_node_tree_patch`.
7. Apply only a valid patch, then inspect `actual_diff`, `new_revision`, users,
   and backup disposition.

The generic protocol supports common graph/layout operations, Frame
annotations, group interfaces, Color Ramps, Curve Mappings, and typed Blender
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

The server currently exposes 39 MCP tools, including:

- scene and object inspection;
- viewport screenshots;
- arbitrary Blender Python execution;
- object, material, camera, lighting, and scene manipulation through Blender code;
- Poly Haven search, download, and texture application;
- Sketchfab search, preview, and model import;
- Hyper3D Rodin text/image generation and import;
- Hunyuan3D generation and import;
- the three official-documentation tools;
- six owner-aware structured-node tools and the eight Geometry Nodes v1 tools.

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
- `SHA256SUMS.txt` — integrity checks

Install the server on Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\blender_mcp-1.9.0-py3-none-any.whl
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./blender_mcp-1.9.0-py3-none-any.whl
```

Install the Extension in Blender 4.2+:

1. Open **Edit > Preferences > Add-ons**.
2. Choose **Install from Disk...**.
3. Select `blender_mcp-<version>.zip` without extracting it.
4. Enable **Blender MCP**.

The legacy [addon.py](addon.py) remains available for Blender 3.x, but the
Geometry Nodes v1 protocol is not supported or claimed there.

### Register a client manually

Use the absolute path to the installed `blender-mcp` executable.

Codex CLI and ChatGPT with Codex mode:

```powershell
codex mcp add blender_mcp `
  --env BLENDER_HOST=localhost `
  --env BLENDER_PORT=9876 `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

Claude Code:

```powershell
claude mcp add --scope user blender_mcp `
  --env BLENDER_HOST=localhost `
  --env BLENDER_PORT=9876 `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

Other stdio MCP clients can use the same executable and environment variables.
For Claude Desktop on Windows, open the published MCPB and approve it in
**Settings > Extensions**.

## Configuration

### MCP server environment

| Variable | Default | Description |
| --- | --- | --- |
| `BLENDER_HOST` | `localhost` | Host running the Blender bridge. |
| `BLENDER_PORT` | `9876` | TCP port exposed by the Blender Extension. |
| `BLENDER_MCP_WORKSPACE` | server working directory | Allowed root for structured node-tree JSON files. |
| `BLENDER_MCP_CACHE_DIR` | platform user cache | Optional parent directory for versioned Blender documentation cache entries. |
| `DISABLE_TELEMETRY` | unset | Set to `true` to disable MCP server telemetry. |

`BLENDER_MCP_DISABLE_TELEMETRY` and
`MCP_DISABLE_TELEMETRY` are also accepted as complete-disable
switches.

### Blender preferences

Persistent settings are available under
**Edit > Preferences > Add-ons > Blender MCP**:

- telemetry consent and auto-connect (enabled by default);
- bridge port;
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
| Claude Code still uses another same-name entry | Run `claude mcp get blender_mcp` and check its scope. Local and project entries take precedence and are intentionally not removed by this installer. |
| The client cannot reach Blender | Open Blender and confirm auto-connect is enabled, or click **Connect to Claude** manually; both sides must use the same host and port. |
| The Extension is absent from one Blender | Rerun and select that version, or pass its executable with `-BlenderPath`. |
| A Geometry Nodes patch is stale | Re-index or re-export the tree and rebuild the patch with the new revision. |
| A Shader or Compositor patch is stale | Re-index or re-export the exact owner-addressed `tree_ref`; do not reuse a patch for another owner with the same display tree name. |
| A linked or override node tree cannot be edited | Linked data is read-only. Local library overrides can be inspected and dry-run, but apply is intentionally disabled. |
| A full graph exceeds the response limit | Use `get_node_tree_index`, then call `export_node_tree` with `node_names` and a small `neighbor_depth`. |
| Documentation is unavailable offline | Warm the same source/version/language first. Only marked stale entries fall back during network or server failure; a 404 does not. |
| A prerelease Manual page is missing | Check the structured fallback, then query live node types/schema and installed Essentials for the exact build. |
| Old versioned environments remain | Close all MCP clients, then remove obsolete `%LOCALAPPDATA%\BlenderMCP\venv-<old-version>` directories. Keep the path named in `current-server.txt`. |

Dry-run command:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1))) -DryRun
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
- Claude Desktop always requires final MCPB approval.
- Automatic installation is Windows-only.
- Blender 4.2.22 LTS, 5.1.2, and 5.2 LTS RC passed the local runtime,
  transactional, linked/override, 2,048-node efficiency, and corner-case suites.
- Optional asset providers may transmit requests or files to their services.

## Development

```powershell
git clone https://github.com/newo-ether/blender-mcp.git
cd blender-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --editable .
```

Run the schema tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
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
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

| Path | Purpose |
| --- | --- |
| [addon.py](addon.py) | Blender add-on and Extension source. |
| [src/blender_mcp/server.py](src/blender_mcp/server.py) | Python MCP server. |
| [install.ps1](install.ps1) | Windows Release/bootstrap installer. |
| [scripts/build_release.ps1](scripts/build_release.ps1) | Release asset builder. |
| [docs/blender-knowledge.md](docs/blender-knowledge.md) | Official documentation and live runtime knowledge guide. |
| [docs/geometry-nodes.md](docs/geometry-nodes.md) | Geometry Nodes protocol guide. |
| [docs/structured-node-automation.md](docs/structured-node-automation.md) | Generic Shader/Compositor protocol and safety guide. |
| [schemas](schemas) | Public JSON contracts. |
| [tests](tests) | Pure-Python and Blender acceptance scripts. |

Contributions and issue reports are welcome.

## Upstream and credits

This repository is a fork of
[ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp), created by
[Siddharth Ahuja](https://x.com/sidahuj).

Upstream resources:

- [Project website](https://blendermcp.org/)
- [Discord](https://discord.gg/z5apgR8TFU)
- [Original tutorial](https://www.youtube.com/watch?v=lCyQ717DuzQ)

## License

[MIT](LICENSE)
