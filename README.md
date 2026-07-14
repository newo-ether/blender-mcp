# Blender MCP — Geometry Nodes Automation Fork

[![Release](https://img.shields.io/github/v/release/newo-ether/blender-mcp)](https://github.com/newo-ether/blender-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Blender 4.2+](https://img.shields.io/badge/Blender-4.2%2B-orange.svg)](https://www.blender.org/)

**English** | [中文](README_CN.md)

This community fork connects MCP clients to Blender and adds a structured,
revision-safe workflow for reading and editing Geometry Nodes.

It retains the upstream BlenderMCP scene, object, viewport, asset, and
model-generation tools while adding:

- first-class Geometry Nodes discovery, export, validation, and transactional edits;
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
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/v1.7.2/install.ps1))) -ReleaseTag v1.7.2
```

Before changing the machine, the installer:

1. detects supported MCP clients and every local Blender installation;
2. opens a terminal checklist;
3. downloads the latest stable [GitHub Release](https://github.com/newo-ether/blender-mcp/releases/latest);
4. verifies the wheel, Extension ZIP, and optional MCPB against `SHA256SUMS.txt`;
5. creates or reuses `%LOCALAPPDATA%\BlenderMCP\venv`;
6. installs the server and the Extension into each selected Blender version without resetting existing Blender preferences;
7. adds or updates the canonical `blender_mcp` entry for selected clients.

Updates are idempotent: an exact matching Codex entry is left alone, while a
different `blender_mcp` Codex or Claude Code user entry is replaced in place.
Use `-PreserveExistingMcpEntries` when an existing custom entry must not change.

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
| `-WorkspacePath <path>` | Set the Geometry Nodes JSON workspace. |
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

## Geometry Nodes automation

This fork treats a node tree as a normalized graph, not as recursive prose or a
single generated `bpy` script. Reads are revisioned; edits are small
semantic patches that can be validated before application.

### Dedicated tools

| Tool | Purpose | Mutates Blender |
| --- | --- | --- |
| `list_geometry_node_trees` | List groups, users, editability, graph size, and revisions. | No |
| `get_geometry_node_tree_index` | Search and page a compact node index. | No |
| `export_geometry_node_tree` | Return or write a full graph or targeted N-hop subgraph. | No |
| `get_geometry_node_type_schema` | Probe sockets and editable properties from the running Blender version. | No |
| `validate_geometry_node_patch` | Validate structure and run a patch against a disposable copy. | No |
| `apply_geometry_node_patch` | Validate, copy, verify, remap users, and report the actual diff. | Yes |

### Recommended workflow

1. Call `list_geometry_node_trees`.
2. Search large graphs with `get_geometry_node_tree_index`.
3. Export only the relevant nodes and neighbors.
4. Put the returned `revision` into a small patch JSON file.
5. Edit that file with the client's normal file-edit tool.
6. Call `validate_geometry_node_patch`.
7. Apply only a valid patch, then inspect `actual_diff` and
   `new_revision`.

The workspace boundary is controlled by `BLENDER_MCP_WORKSPACE`.
Patch files outside it, non-JSON files, and patch files larger than 4 MiB are
rejected.

For the operation catalog, shared-tree policies, rollback behavior, performance
measurements, and compatibility details, read
[Geometry Nodes automation](docs/geometry-nodes.md).

Useful artifacts:

- [Example snapshot](examples/geometry-nodes-snapshot.json)
- [Example patch](examples/geometry-nodes-patch.json)
- [Public JSON schemas](schemas)

## Other capabilities

The server currently exposes 28 MCP tools, including:

- scene and object inspection;
- viewport screenshots;
- arbitrary Blender Python execution;
- object, material, camera, lighting, and scene manipulation through Blender code;
- Poly Haven search, download, and texture application;
- Sketchfab search, preview, and model import;
- Hyper3D Rodin text/image generation and import;
- Hunyuan3D generation and import;
- the six Geometry Nodes tools above.

Example requests:

- “Inspect the scene and frame the camera around the selected object.”
- “Create a low-poly dungeon scene with studio-quality lighting.”
- “Find the Geometry Nodes group used by the selected object and list its inputs.”
- “Export the nodes around Join Geometry and validate a patch that inserts a
  Transform Geometry node.”
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
.\.venv\Scripts\python.exe -m pip install .\blender_mcp-1.7.2-py3-none-any.whl
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./blender_mcp-1.7.2-py3-none-any.whl
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
| `BLENDER_MCP_WORKSPACE` | server working directory | Allowed root for Geometry Nodes JSON files. |
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
| A linked node group cannot be edited | Linked-library trees are intentionally read-only in Geometry Nodes v1. |

Dry-run command:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1))) -DryRun
```

## Security and known limitations

- `execute_blender_code` can run arbitrary Python inside Blender. Save
  the `.blend` file first and review high-impact operations.
- Geometry Nodes v1 covers Geometry Nodes only. Shader, Compositor, Texture, and
  World node trees are outside this contract.
- Linked-library node trees are exportable but read-only.
- Shared node groups are rejected by default unless the caller explicitly
  chooses a single-user copy or accepts shared mutation.
- Claude Desktop always requires final MCPB approval.
- Automatic installation is Windows-only.
- Blender 5.1.2 and 5.2 LTS RC passed the local acceptance suite. Blender 4.2 is
  the manifest minimum but was unavailable for a runtime acceptance test.
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
| [docs/geometry-nodes.md](docs/geometry-nodes.md) | Geometry Nodes protocol guide. |
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
