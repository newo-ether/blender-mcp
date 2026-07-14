# Blender MCP — 几何节点自动化社区版

[![Release](https://img.shields.io/github/v/release/newo-ether/blender-mcp)](https://github.com/newo-ether/blender-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Blender 4.2+](https://img.shields.io/badge/Blender-4.2%2B-orange.svg)](https://www.blender.org/)

[English](README.md) | **中文**

这个社区版让 MCP 客户端能够直接操作 Blender，并提供一套结构清晰、带版本校验的几何节点读写方案，避免在过期的节点树上误操作。

在保留上游 BlenderMCP 场景、对象、视口、资产和模型生成工具的基础上，本项目新增了：

- 专为几何节点设计的查找、导出、校验和事务式编辑工具；
- 可按 Blender 版本查询官方手册、Python API、版本说明、当前节点定义和内置 Essentials 资产；
- 可直接发布的 GitHub Release，其中包含 Blender 扩展、Python wheel、Claude Desktop MCPB 和校验文件；
- 可自动检测客户端和 Blender 的 Windows 一键安装器；
- 终端和图形界面两种安装目标选择方式，支持同时安装到多个 Blender 版本。

> 这是第三方社区项目，并非 Blender、OpenAI 或 Anthropic 的官方产品，也未获得这些公司的背书。

## Windows 快速开始

### 环境要求

- Windows PowerShell 5.1 或更高版本
- Python 3.10 或更高版本
- 安装扩展包需要 Blender 4.2 或更高版本
- 至少一个 MCP 客户端，例如 Codex、带 Codex 模式的 ChatGPT、Claude Code 或 Claude Desktop

### 一行命令安装

打开 PowerShell 并运行：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1 | iex
```

`-Scope Process` 只对当前 PowerShell 窗口生效，不会永久修改用户或系统的执行策略。安装脚本是可读的：[install.ps1](install.ps1)。如需固定版本、确保每次安装结果一致，请使用：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/v1.8.2/install.ps1))) -ReleaseTag v1.8.2
```

在修改本机之前，安装器会：

1. 检测本机已安装的受支持 MCP 客户端和所有 Blender 版本；
2. 打开终端复选列表；
3. 下载最新稳定版 [GitHub Release](https://github.com/newo-ether/blender-mcp/releases/latest)；
4. 使用 `SHA256SUMS.txt` 校验 wheel、扩展 ZIP 和可选 MCPB；
5. 安装到 `%LOCALAPPDATA%\BlenderMCP\venv-1.8.2` 这样的版本专用环境；
6. 将服务端和扩展安装到每个选中的 Blender 版本，同时保留原有的 Blender 偏好设置；
7. 为选中的客户端添加或更新名为 `blender_mcp` 的配置项。

安装器可以反复运行，不会重复添加配置。Codex 中完全一致的配置会保持不变；如果用户配置中已有同名但内容不同的 `blender_mcp`，Codex 和 Claude Code 都会直接更新原配置。如需保留自定义设置，请使用 `-PreserveExistingMcpEntries`。

不同版本使用独立环境，因此即使旧服务仍被当前客户端占用，也能先完整安装并验证新版，再切换唯一的客户端配置。当前会话会继续使用旧进程；重启客户端后才会载入新版。安装器不会删除可能仍在运行的旧环境；关闭所有 MCP 客户端后，可以手动清理不再使用的 `venv-<版本>` 目录。

### 选择安装目标

默认终端界面使用以下按键：

- 上/下方向键：移动
- 空格：切换选中状态
- A：切换所有可用项目
- Enter：开始安装
- Esc 或 Q：取消且不做任何更改

使用 `-Gui` 可打开 WinForms 复选框窗口。

| 目标 | 安装器行为 |
| --- | --- |
| Codex CLI | 在用户配置中添加或更新 `blender_mcp` stdio 服务。 |
| Codex Desktop（ChatGPT） | 与 CLI 使用同一份 Codex MCP 配置。 |
| Claude Code CLI | 在用户配置中添加或更新 `blender_mcp`；不会删除项目级或本地配置。 |
| Claude Desktop | 打开校验通过的 MCPB；最后仍需在 Claude Desktop 中确认。 |
| Blender 4.2+ | 可选择一个或多个检测到的版本；默认选中最新版。 |
| 低于 Blender 4.2 | 为便于识别仍会显示，但扩展安装选项处于禁用状态。 |

### 完成设置

1. 打开一个已选中的 Blender 版本。
2. 在 3D 视图中按 N，打开 **BlenderMCP** 标签页。
3. Blender 端的连接服务会在 `9876` 端口自动启动；自动连接默认开启。
4. 重启或重新打开选中的 MCP 客户端。
5. 如果选择了 Claude Desktop，请在 Claude Desktop 内批准 MCPB。

## 安装器参数

通过创建脚本块向远程脚本传递参数：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
$installer = [scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1))
& $installer -Gui
```

从克隆的仓库运行时，可直接调用文件：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

从克隆仓库运行脚本时，默认以可编辑模式安装本地源码。添加 `-UseRelease` 后，则改为测试正式发布包的安装流程。

| 选项 | 用途 |
| --- | --- |
| `-DryRun` | 输出检测结果、路径、下载项和命令，但不修改系统。 |
| `-Gui` | 使用图形界面选择安装目标，而不是默认 TUI。 |
| `-NonInteractive` | 跳过两种选择器，使用自动检测到的默认项。 |
| `-BlenderPath <path[]>` | 只安装到指定的 Blender 可执行文件。 |
| `-PythonPath <path>` | 指定用于创建虚拟环境的 Python 3.10+ 解释器。 |
| `-WorkspacePath <path>` | 设置几何节点 JSON 工作区。 |
| `-ReleaseTag <tag>` | 安装指定 Release，而不是最新稳定版。 |
| `-InstallRoot <path>` | 自定义当前用户的发布版安装目录。 |
| `-UseRelease` | 即使从克隆仓库运行脚本，也改用 Release 中的安装包。 |
| `-SkipBlenderExtension` | 仅安装 Python MCP 服务端。 |
| `-SkipCodexRegistration` | 不修改 Codex/ChatGPT 配置。 |
| `-PreserveExistingMcpEntries` | 保留 Codex 或 Claude Code 中同名但内容不同的现有配置，不自动更新。 |
| `-SkipClaudeCodeRegistration` | 不修改 Claude Code 配置。 |
| `-SkipClaudeDesktop` | 不下载或打开 Claude Desktop MCPB。 |

示例：

```powershell
# 检查 Release 安装路径，但不写入任何状态
& $installer -DryRun

# 安装到两个明确指定的 Blender 版本
& $installer -BlenderPath @(
    "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    "C:\Program Files\Blender Foundation\Blender 5.2 LTS\blender.exe"
)

# 仅安装服务端
& $installer -SkipBlenderExtension -SkipCodexRegistration `
    -SkipClaudeCodeRegistration -SkipClaudeDesktop
```

TUI 只能在可交互的控制台中使用。CI、SSH、输入/输出重定向以及 `-NonInteractive` 模式会直接采用自动检测的默认选项。如果 `Console.ReadKey()` 不可用，安装器会先尝试 WinForms；若 WinForms 也不可用，则给出警告并使用默认选项。

## 架构

```text
Codex / ChatGPT / Claude
          |
          | 基于 stdio 的 MCP
          v
Python MCP 服务端
          |
          | 基于 TCP localhost:9876 的 JSON
          v
Blender 扩展 <-> Blender 场景与节点树
```

MCP 服务端由客户端启动，Blender 扩展则在 Blender 内提供本地连接。请先启动 Blender 端连接，再让客户端调用 Blender 工具。

## Blender 资料查询

服务端既能查阅官方编写的说明，也能核对当前 Blender 版本实际提供的节点。查询结果是有长度上限、注明出处的结构化 JSON，不会把整本手册塞进上下文。

| 工具 | 用途 | 是否需要 Blender |
| --- | --- | --- |
| `get_blender_documentation_context` | 解析当前版本、官方资料频道、语言和替代来源；本身不下载网页。 | 仅 `version="auto"` 时需要 |
| `search_blender_docs` | 在官方手册、Python API 和版本说明中查询，并返回精简的排序结果。 | 仅 `version="auto"` 时需要 |
| `get_blender_doc_page` | 读取查询结果中的单个页面，或其中某个标题对应的章节；页面杂项会被剔除。 | 仅 `version="auto"` 时需要 |
| `search_geometry_node_types` | 查找当前 Blender 版本中确实可用于几何节点的节点类型。 | 是 |
| `get_geometry_node_type_schema` | 读取精简的实时插槽、节点自身属性和动态项目；继承而来的完整 RNA 需明确请求。 | 是 |
| `search_blender_node_assets` | 查看本机 Blender 随附的官方 Essentials 节点资产，不会在工程中留下临时数据。 | 是 |

连接 Blender 时可使用 `version="auto"` 得到与当前构建完全对应的结果；未连接时也能指定 `"5.1"` 之类的版本查询资料。开发版频道、语言切换和英文替代都会如实写入返回数据。联网范围仅限 Blender 官方 HTTPS 站点；下载内容保存在当前用户的缓存中，并明确标注新旧程度和离线替代状态。

查询顺序、版本与语言规则、缓存位置、离线行为和安全边界详见 [Blender 手册与运行时资料查询](docs/blender-knowledge_CN.md)。

## 几何节点自动化

本项目会把节点树整理成结构统一的图数据，而不是层层嵌套的文字描述，也不依赖一次性生成的大段 `bpy` 脚本。每次读取都会附带版本标识；修改时只提交描述差异的小型补丁，并可在真正应用前先行校验。

### 专用工具

| 工具 | 用途 | 修改 Blender |
| --- | --- | --- |
| `list_geometry_node_trees` | 列出节点组、引用位置、可编辑状态、节点与连线数量以及版本标识。 | 否 |
| `get_geometry_node_tree_index` | 搜索节点，并分页返回精简索引。 | 否 |
| `export_geometry_node_tree` | 返回或写入完整节点图，也可只导出指定节点周围的若干层连接。 | 否 |
| `get_geometry_node_type_schema` | 从当前运行的 Blender 中读取精简的节点插槽和可编辑属性。 | 否 |
| `search_geometry_node_types` | 查询当前构建实际可用的几何节点类型。 | 否 |
| `search_blender_node_assets` | 在临时环境中查看本机随附的官方 Essentials 节点资产。 | 否 |
| `validate_geometry_node_patch` | 检查补丁格式，并在临时副本上试运行。 | 否 |
| `apply_geometry_node_patch` | 校验并复制节点树，确认结果后切换引用，同时报告实际改动。 | 是 |

### 推荐工作流

1. 调用 `list_geometry_node_trees`。
2. 使用 `get_geometry_node_tree_index` 在大型节点树中查找目标节点。
3. 仅导出相关节点及其相邻节点。
4. 将返回的 `revision` 写入一个小型 JSON 补丁文件。
5. 使用客户端自带的文件编辑工具修改该文件。
6. 调用 `validate_geometry_node_patch`。
7. 仅应用验证通过的补丁，然后检查 `actual_diff` 和 `new_revision`。

`BLENDER_MCP_WORKSPACE` 决定补丁文件的可读写范围。工作区以外的文件、非 JSON 文件以及超过 4 MiB 的补丁都会被拒绝。

支持哪些补丁操作、如何处理共享节点树、怎样回滚，以及性能和兼容性说明，详见[几何节点自动化文档](docs/geometry-nodes.md)。

实用文件：

- [快照示例](examples/geometry-nodes-snapshot.json)
- [补丁示例](examples/geometry-nodes-patch.json)
- [公开 JSON Schema](schemas)

## 其他能力

服务端目前提供 33 个 MCP 工具，包括：

- 场景与对象检查；
- 视口截图；
- 执行任意 Blender Python 代码；
- 通过 Blender 代码操作对象、材质、摄像机、灯光和场景；
- 搜索、下载和应用 Poly Haven 纹理；
- 搜索、预览和导入 Sketchfab 模型；
- 使用文字或图片通过 Hyper3D Rodin 生成模型并导入；
- 使用 Hunyuan3D 生成并导入；
- 三个官方资料查询工具；
- 八个几何节点读取、检索、校验与应用工具。

请求示例：

- “检查场景，并让摄像机框选选中的对象。”
- “创建一个低多边形地下城场景，并使用影棚级灯光。”
- “找到选中对象使用的几何节点组，并列出其输入。”
- “根据当前 Blender 版本，查找 XPBD Solver 的官方说明和实际节点定义。”
- “导出 Join Geometry 周围的节点，并验证一个插入 Transform Geometry 节点的补丁。”
- “在 Poly Haven 搜索混凝土材质并应用到地面。”

## 手动与跨平台安装

一键安装脚本和 Claude Desktop 启动器目前只支持 Windows；Python wheel 和 Blender 扩展 ZIP 本身可以跨平台使用。

从最新 [Releases](https://github.com/newo-ether/blender-mcp/releases/latest) 下载以下资产：

- `blender_mcp-<version>.zip` — Blender 扩展
- `blender_mcp-<version>-py3-none-any.whl` — Python MCP 服务端
- `blender_mcp-<version>.mcpb` — Windows Claude Desktop 软件包
- `SHA256SUMS.txt` — 文件校验值

在 Windows 上安装服务端：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\blender_mcp-1.8.2-py3-none-any.whl
```

在 macOS 或 Linux 上：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./blender_mcp-1.8.2-py3-none-any.whl
```

在 Blender 4.2+ 中安装扩展：

1. 打开 **Edit > Preferences > Add-ons**。
2. 选择 **Install from Disk...**。
3. 选择 `blender_mcp-<version>.zip`，无需解压。
4. 启用 **Blender MCP**。

旧版 [addon.py](addon.py) 仍可用于 Blender 3.x，但不支持几何节点 v1 协议，本项目也不保证这部分功能与新版一致。

### 手动注册客户端

请使用已安装的 `blender-mcp` 可执行文件绝对路径。

Codex CLI 和带 Codex 模式的 ChatGPT：

```powershell
codex mcp add blender_mcp `
  --env BLENDER_HOST=localhost `
  --env BLENDER_PORT=9876 `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

Claude Code：

```powershell
claude mcp add --scope user blender_mcp `
  --env BLENDER_HOST=localhost `
  --env BLENDER_PORT=9876 `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

其他基于 stdio 的 MCP 客户端也可以使用同一个可执行文件和同一组环境变量。在 Windows 上使用 Claude Desktop 时，请打开发布的 MCPB，并在 **Settings > Extensions** 中确认安装。

## 配置

### MCP 服务端环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BLENDER_HOST` | `localhost` | Blender 端连接服务所在的主机。 |
| `BLENDER_PORT` | `9876` | Blender 扩展监听的 TCP 端口。 |
| `BLENDER_MCP_WORKSPACE` | 服务端工作目录 | 几何节点 JSON 文件可以读写的根目录。 |
| `BLENDER_MCP_CACHE_DIR` | 系统的当前用户缓存 | 可选；指定 Blender 资料缓存的上级目录。 |
| `DISABLE_TELEMETRY` | 未设置 | 设为 `true` 可禁用 MCP 服务端遥测。 |

设置 `BLENDER_MCP_DISABLE_TELEMETRY` 或 `MCP_DISABLE_TELEMETRY` 也可以彻底关闭遥测。

### Blender 偏好设置

以下设置会保存在 **Edit > Preferences > Add-ons > Blender MCP** 中：

- 是否允许遥测，以及是否自动连接（自动连接默认开启）；
- 桥接端口；
- 是否启用 Poly Haven；
- Hyper3D 提供商和 API 密钥；
- Sketchfab API 密钥；
- Hunyuan3D 模式、凭据、端点和生成默认值。

在无头运行环境中，可以通过以下环境变量提供凭据：

- `BLENDERMCP_SKETCHFAB_API_KEY`
- `BLENDERMCP_HYPER3D_API_KEY`
- `BLENDERMCP_HUNYUAN3D_SECRET_ID`
- `BLENDERMCP_HUNYUAN3D_SECRET_KEY`
- `BLENDERMCP_HUNYUAN3D_API_URL`

在 Blender 中关闭遥测许可后，提示词、代码、截图等详细信息都不会被发送，但仍会保留最少量的匿名运行事件。如需彻底关闭 MCP 服务端遥测，请在客户端环境中设置 `DISABLE_TELEMETRY=true`。

## 故障排除

| 现象 | 处理方法 |
| --- | --- |
| 未出现复选列表 | 请使用普通 PowerShell 控制台。使用重定向、CI、SSH 或 `-NonInteractive` 时，安装器会直接采用默认选项，这是预期行为。如需 WinForms，请添加 `-Gui`。 |
| 未检测到某个客户端 | 安装该客户端，重新打开 PowerShell，然后再次运行安装器。 |
| 不希望更新自定义 MCP 配置 | 使用 `-PreserveExistingMcpEntries` 重新运行，或使用对应客户端的跳过开关。 |
| Claude Code 仍在使用另一个同名配置 | 运行 `claude mcp get blender_mcp` 并检查配置范围。本地配置和项目配置优先级更高，安装器不会主动删除它们。 |
| 客户端无法连接 Blender | 打开 Blender 并确认自动连接已启用，或手动点击 **Connect to Claude**；两端必须使用相同的主机和端口。 |
| 某个 Blender 中没有扩展 | 重新运行安装器并选中该版本，或通过 `-BlenderPath` 传入其可执行文件。 |
| 几何节点补丁已过期 | 重新读取索引或导出节点树，再使用新的版本标识重建补丁。 |
| 无法编辑外部链接的节点组 | 几何节点 v1 会将外部库中的节点树设为只读。 |
| 离线时无法查询资料 | 需要先在相同来源、版本和语言下完成一次联网查询。只有明确标为过期的缓存才会在网络或服务器故障时替代使用；404 不会使用旧内容。 |
| 候选版手册中找不到某个页面 | 先查看返回数据中的替代来源说明，再查询当前版本的节点类型、实时定义和 Essentials 资产。 |
| 更新后仍保留旧版环境 | 关闭所有 MCP 客户端，再删除 `%LOCALAPPDATA%\BlenderMCP\venv-<旧版本>`。请保留 `current-server.txt` 指向的目录。 |

试运行命令：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1))) -DryRun
```

## 安全性与已知限制

- `execute_blender_code` 可以在 Blender 内执行任意 Python。进行大范围修改前，请先保存 `.blend` 文件并检查代码。
- 几何节点 v1 只支持 Geometry Nodes，不包括 Shader、Compositor、Texture 和 World 节点树。
- 候选版资料和手册译文可能尚未补齐。所有替代来源都会明确标注；涉及当前构建的插槽与属性时，应以实时节点定义为准。
- 链接的外部库节点树可以导出，但为只读。
- 默认不会编辑多个对象共用的节点组；客户端必须明确选择创建单独副本，或确认允许同时影响所有引用位置。
- Claude Desktop 始终需要用户最终批准 MCPB。
- 自动安装仅支持 Windows。
- Blender 4.2.22 LTS、5.1.2 和 5.2 LTS RC 均已通过本地实时定义、事务修改、外部链接只读和规模测试。
- 可选资产提供商可能会将请求或文件传输到其服务。

## 开发

```powershell
git clone https://github.com/newo-ether/blender-mcp.git
cd blender-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --editable .
```

运行 JSON Schema 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

运行 Blender 偏好设置保留测试：

```powershell
.\tests\test_installer_preferences.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

构建 Blender 扩展：

```powershell
.\.venv\Scripts\python.exe scripts\build_blender_extension.py `
  --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

构建完整的 Release 文件：

```powershell
.\scripts\build_release.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

| 路径 | 用途 |
| --- | --- |
| [addon.py](addon.py) | Blender 插件与扩展源码。 |
| [src/blender_mcp/server.py](src/blender_mcp/server.py) | Python MCP 服务端。 |
| [install.ps1](install.ps1) | Windows 一键安装脚本。 |
| [scripts/build_release.ps1](scripts/build_release.ps1) | Release 文件构建脚本。 |
| [docs/blender-knowledge_CN.md](docs/blender-knowledge_CN.md) | 官方资料与实时节点定义查询指南。 |
| [docs/geometry-nodes.md](docs/geometry-nodes.md) | 几何节点协议指南。 |
| [schemas](schemas) | 公开的 JSON Schema。 |
| [tests](tests) | 纯 Python 与 Blender 验收脚本。 |

欢迎贡献代码和提交问题报告。

## 上游与致谢

本仓库基于 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) 开发，原项目由 [Siddharth Ahuja](https://x.com/sidahuj) 创建。

上游资源：

- [项目网站](https://blendermcp.org/)
- [Discord](https://discord.gg/z5apgR8TFU)
- [原始教程](https://www.youtube.com/watch?v=lCyQ717DuzQ)

## 许可证

[MIT](LICENSE)
