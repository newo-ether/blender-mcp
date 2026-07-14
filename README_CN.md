<p align="right">语言：<a href="README.md">English</a> | <strong>简体中文</strong></p>

# Blender MCP — 几何节点自动化 Fork

[![Release](https://img.shields.io/github/v/release/newo-ether/blender-mcp)](https://github.com/newo-ether/blender-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Blender 4.2+](https://img.shields.io/badge/Blender-4.2%2B-orange.svg)](https://www.blender.org/)

这个社区 Fork 将 MCP 客户端连接到 Blender，并为几何节点的读取与编辑提供结构化、修订安全的工作流。

它保留了上游 BlenderMCP 的场景、对象、视口、资产和模型生成工具，同时新增：

- 一等的几何节点发现、导出、验证和事务式编辑能力；
- 包含 Blender 扩展、Python wheel 和 Claude Desktop MCPB，并附带校验和的 GitHub Release；
- 可自动检测客户端和 Blender 的 Windows 一键安装器；
- 面向多版本安装的终端与图形化目标选择器。

> 这是一个第三方社区项目，与 Blender、OpenAI 或 Anthropic 均无隶属关系，也未获得其官方背书。

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

`-Scope Process` 仅对当前 PowerShell 窗口生效，不会永久修改用户或系统的执行策略。脚本源码可直接阅读：[install.ps1](install.ps1)。如需可复现的指定版本安装，请使用：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/v1.7.2/install.ps1))) -ReleaseTag v1.7.2
```

在修改本机之前，安装器会：

1. 检测受支持的 MCP 客户端和本机所有 Blender 安装；
2. 打开终端复选列表；
3. 下载最新稳定版 [GitHub Release](https://github.com/newo-ether/blender-mcp/releases/latest)；
4. 使用 `SHA256SUMS.txt` 校验 wheel、扩展 ZIP 和可选 MCPB；
5. 创建或复用 `%LOCALAPPDATA%\BlenderMCP\venv`；
6. 将服务端和扩展安装到每个选中的 Blender 版本，且不会重置已有 Blender 偏好设置；
7. 为选中的客户端添加或更新规范的 `blender_mcp` 条目。

更新操作是幂等的：完全匹配的 Codex 条目会保持不变；配置不同的 `blender_mcp` Codex 或 Claude Code 用户级条目则会被原位替换。如需保留已有的自定义条目，请使用 `-PreserveExistingMcpEntries`。

### 目标选择器

默认终端界面使用以下按键：

- 上/下方向键：移动
- 空格：切换选中状态
- A：切换所有可用项目
- Enter：开始安装
- Esc 或 Q：取消且不做任何更改

使用 `-Gui` 可打开 WinForms 复选框窗口。

| 目标 | 安装器行为 |
| --- | --- |
| Codex CLI | 添加或更新用户级 `blender_mcp` stdio 服务端。 |
| Codex Desktop（ChatGPT） | 与 CLI 使用同一份 Codex MCP 配置。 |
| Claude Code CLI | 在用户作用域添加或更新 `blender_mcp`；不会移除项目级或本地条目。 |
| Claude Desktop | 打开已经校验的 MCPB；最终确认由 Claude Desktop 完成。 |
| Blender 4.2+ | 可选择一个或多个检测到的版本；默认选中最新版。 |
| 低于 Blender 4.2 | 为便于识别仍会显示，但扩展安装选项处于禁用状态。 |

### 完成设置

1. 打开一个已选中的 Blender 版本。
2. 在 3D 视图中按 N，打开 **BlenderMCP** 标签页。
3. 本地桥接会默认在端口 `9876` 自动启动；偏好设置中的该选项默认开启。
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

从克隆仓库运行时，默认安装可编辑的本地源码。添加 `-UseRelease` 可测试已发布 Release 的安装路径。

| 选项 | 用途 |
| --- | --- |
| `-DryRun` | 输出检测结果、路径、下载项和命令，但不修改系统。 |
| `-Gui` | 使用图形选择器，而不是默认 TUI。 |
| `-NonInteractive` | 跳过两种选择器，使用自动检测到的默认项。 |
| `-BlenderPath <path[]>` | 将 Blender 目标限制为明确指定的可执行文件路径。 |
| `-PythonPath <path>` | 指定用于创建虚拟环境的 Python 3.10+ 解释器。 |
| `-WorkspacePath <path>` | 设置几何节点 JSON 工作区。 |
| `-ReleaseTag <tag>` | 安装指定 Release，而不是最新稳定版。 |
| `-InstallRoot <path>` | 覆盖用户级 Release 安装目录。 |
| `-UseRelease` | 即使从克隆仓库运行脚本，也使用 Release 资产。 |
| `-SkipBlenderExtension` | 仅安装 Python MCP 服务端。 |
| `-SkipCodexRegistration` | 不修改 Codex/ChatGPT 配置。 |
| `-PreserveExistingMcpEntries` | 保留配置不同但同名的 Codex 或 Claude Code 条目，而不是更新它。 |
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

TUI 需要真实的交互式控制台。CI、SSH、重定向的输入/输出以及 `-NonInteractive` 模式会使用检测到的默认项。如果 `Console.ReadKey()` 不可用，安装器会尝试 WinForms，仍不可用时则显示警告并回退到检测到的默认项。

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

MCP 服务端由客户端启动，Blender 扩展负责托管本地桥接。请先启动桥接，再让客户端使用 Blender 工具。

## 几何节点自动化

本 Fork 将节点树视为规范化的图，而不是递归自然语言或单个生成的 `bpy` 脚本。读取结果带有修订号；编辑则使用可在应用前验证的小型语义补丁。

### 专用工具

| 工具 | 用途 | 修改 Blender |
| --- | --- | --- |
| `list_geometry_node_trees` | 列出节点组、使用者、可编辑性、图规模和修订号。 | 否 |
| `get_geometry_node_tree_index` | 搜索并分页读取紧凑节点索引。 | 否 |
| `export_geometry_node_tree` | 返回或写入完整图，或指定节点的 N 跳子图。 | 否 |
| `get_geometry_node_type_schema` | 从当前运行的 Blender 版本探测插槽和可编辑属性。 | 否 |
| `validate_geometry_node_patch` | 验证补丁结构，并在一次性副本上试运行。 | 否 |
| `apply_geometry_node_patch` | 验证、复制、核验、重映射使用者，并报告实际差异。 | 是 |

### 推荐工作流

1. 调用 `list_geometry_node_trees`。
2. 使用 `get_geometry_node_tree_index` 搜索大型图。
3. 仅导出相关节点及其相邻节点。
4. 将返回的 `revision` 写入一个小型补丁 JSON 文件。
5. 使用客户端常规的文件编辑工具修改该文件。
6. 调用 `validate_geometry_node_patch`。
7. 仅应用验证通过的补丁，然后检查 `actual_diff` 和 `new_revision`。

工作区边界由 `BLENDER_MCP_WORKSPACE` 控制。工作区以外的补丁文件、非 JSON 文件以及超过 4 MiB 的补丁文件都会被拒绝。

有关操作目录、共享节点树策略、回滚行为、性能测量和兼容性细节，请阅读[几何节点自动化文档](docs/geometry-nodes.md)。

实用文件：

- [快照示例](examples/geometry-nodes-snapshot.json)
- [补丁示例](examples/geometry-nodes-patch.json)
- [公开 JSON Schema](schemas)

## 其他能力

服务端目前公开 28 个 MCP 工具，包括：

- 场景与对象检查；
- 视口截图；
- 执行任意 Blender Python 代码；
- 通过 Blender 代码操作对象、材质、摄像机、灯光和场景；
- 搜索、下载和应用 Poly Haven 纹理；
- 搜索、预览和导入 Sketchfab 模型；
- 使用 Hyper3D Rodin 进行文本/图像生成并导入；
- 使用 Hunyuan3D 生成并导入；
- 上述六个几何节点工具。

请求示例：

- “检查场景，并让摄像机框选选中的对象。”
- “创建一个低多边形地下城场景，并使用影棚级灯光。”
- “找到选中对象使用的几何节点组，并列出其输入。”
- “导出 Join Geometry 周围的节点，并验证一个插入 Transform Geometry 节点的补丁。”
- “在 Poly Haven 搜索混凝土材质并应用到地面。”

## 手动与跨平台安装

自动引导安装和 Claude Desktop 启动器仅支持 Windows。Python wheel 和 Blender 扩展 ZIP 本身是跨平台的。

从最新 [Releases](https://github.com/newo-ether/blender-mcp/releases/latest) 下载以下资产：

- `blender_mcp-<version>.zip` — Blender 扩展
- `blender_mcp-<version>-py3-none-any.whl` — Python MCP 服务端
- `blender_mcp-<version>.mcpb` — Windows Claude Desktop 软件包
- `SHA256SUMS.txt` — 完整性校验文件

在 Windows 上安装服务端：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\blender_mcp-1.7.2-py3-none-any.whl
```

在 macOS 或 Linux 上：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./blender_mcp-1.7.2-py3-none-any.whl
```

在 Blender 4.2+ 中安装扩展：

1. 打开 **Edit > Preferences > Add-ons**。
2. 选择 **Install from Disk...**。
3. 选择 `blender_mcp-<version>.zip`，无需解压。
4. 启用 **Blender MCP**。

旧版 [addon.py](addon.py) 仍可用于 Blender 3.x，但不支持几何节点 v1 协议，也不对此作兼容性承诺。

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

其他 stdio MCP 客户端可以使用同一可执行文件和环境变量。在 Windows 上使用 Claude Desktop 时，请打开发布的 MCPB，然后在 **Settings > Extensions** 中批准。

## 配置

### MCP 服务端环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BLENDER_HOST` | `localhost` | 运行 Blender 桥接的主机。 |
| `BLENDER_PORT` | `9876` | Blender 扩展公开的 TCP 端口。 |
| `BLENDER_MCP_WORKSPACE` | 服务端工作目录 | 几何节点 JSON 文件允许使用的根目录。 |
| `DISABLE_TELEMETRY` | 未设置 | 设为 `true` 可禁用 MCP 服务端遥测。 |

`BLENDER_MCP_DISABLE_TELEMETRY` 和 `MCP_DISABLE_TELEMETRY` 也可作为完全禁用开关。

### Blender 偏好设置

持久设置位于 **Edit > Preferences > Add-ons > Blender MCP**：

- 遥测同意和自动连接（默认启用）；
- 桥接端口；
- Poly Haven 启用状态；
- Hyper3D 提供商和 API 密钥；
- Sketchfab API 密钥；
- Hunyuan3D 模式、凭据、端点和生成默认值。

无界面环境可以通过以下环境变量提供凭据：

- `BLENDERMCP_SKETCHFAB_API_KEY`
- `BLENDERMCP_HYPER3D_API_KEY`
- `BLENDERMCP_HUNYUAN3D_SECRET_ID`
- `BLENDERMCP_HUNYUAN3D_SECRET_KEY`
- `BLENDERMCP_HUNYUAN3D_API_URL`

在 Blender 中拒绝遥测同意，会移除提示词、代码、截图和其他富元数据，但仍会保留最少量的匿名运行事件。在 MCP 客户端环境中设置 `DISABLE_TELEMETRY=true` 可禁用全部 MCP 服务端遥测。

## 故障排除

| 现象 | 处理方法 |
| --- | --- |
| 未出现复选列表 | 请使用普通 PowerShell 控制台。重定向流、CI、SSH 和 `-NonInteractive` 会按设计使用默认项。需要 WinForms 时使用 `-Gui`。 |
| 某个客户端不可用 | 安装该客户端，重新打开一个 PowerShell 会话，然后再次运行安装器。 |
| 不希望更新自定义 MCP 条目 | 使用 `-PreserveExistingMcpEntries` 重新运行，或使用对应客户端的跳过开关。 |
| Claude Code 仍在使用另一个同名条目 | 运行 `claude mcp get blender_mcp` 并检查其作用域。本地和项目条目优先级更高，安装器有意不移除它们。 |
| 客户端无法连接 Blender | 打开 Blender 并确认自动连接已启用，或手动点击 **Connect to Claude**；两端必须使用相同的主机和端口。 |
| 某个 Blender 中没有扩展 | 重新运行安装器并选中该版本，或通过 `-BlenderPath` 传入其可执行文件。 |
| 几何节点补丁已过期 | 重新生成索引或导出节点树，并使用新的修订号重建补丁。 |
| 无法编辑链接的节点组 | 几何节点 v1 有意将外部库链接节点树设为只读。 |

试运行命令：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create((irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1))) -DryRun
```

## 安全性与已知限制

- `execute_blender_code` 可以在 Blender 内执行任意 Python。请先保存 `.blend` 文件，并检查高影响操作。
- 几何节点 v1 仅涵盖 Geometry Nodes。Shader、Compositor、Texture 和 World 节点树不在该协议范围内。
- 链接的外部库节点树可以导出，但为只读。
- 默认拒绝编辑共享节点组，除非调用方明确选择生成单用户副本或接受共享修改。
- Claude Desktop 始终需要用户最终批准 MCPB。
- 自动安装仅支持 Windows。
- Blender 5.1.2 和 5.2 LTS RC 已通过本地验收测试。Blender 4.2 是清单声明的最低版本，但尚未进行运行时验收测试。
- 可选资产提供商可能会将请求或文件传输到其服务。

## 开发

```powershell
git clone https://github.com/newo-ether/blender-mcp.git
cd blender-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --editable .
```

运行 Schema 测试：

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

构建所有 Release 资产：

```powershell
.\scripts\build_release.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

| 路径 | 用途 |
| --- | --- |
| [addon.py](addon.py) | Blender 插件与扩展源码。 |
| [src/blender_mcp/server.py](src/blender_mcp/server.py) | Python MCP 服务端。 |
| [install.ps1](install.ps1) | Windows Release/引导安装器。 |
| [scripts/build_release.ps1](scripts/build_release.ps1) | Release 资产构建脚本。 |
| [docs/geometry-nodes.md](docs/geometry-nodes.md) | 几何节点协议指南。 |
| [schemas](schemas) | 公开 JSON 协议。 |
| [tests](tests) | 纯 Python 与 Blender 验收脚本。 |

欢迎贡献代码和提交问题报告。

## 上游与致谢

本仓库 Fork 自 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp)，原项目由 [Siddharth Ahuja](https://x.com/sidahuj) 创建。

上游资源：

- [项目网站](https://blendermcp.org/)
- [Discord](https://discord.gg/z5apgR8TFU)
- [原始教程](https://www.youtube.com/watch?v=lCyQ717DuzQ)

## 许可证

[MIT](LICENSE)
