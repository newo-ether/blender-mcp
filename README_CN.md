# Blender MCP — 结构化节点自动化社区版

[![Release](https://img.shields.io/github/v/release/newo-ether/blender-mcp)](https://github.com/newo-ether/blender-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Blender 4.2+](https://img.shields.io/badge/Blender-4.2%2B-orange.svg)](https://www.blender.org/)

[English](README.md) | **中文**

这个社区版让 MCP 客户端能够直接操作 Blender，并提供一套结构清晰、带版本校验的几何、着色器与合成器节点读写方案，避免在过期的节点树上误操作。

在保留上游 BlenderMCP 场景、对象、视口、资产和模型生成工具的基础上，本项目新增了：

- 按材质、世界、灯光、场景和节点组准确定归属的查找、导出、校验和事务式编辑工具；
- 可按 Blender 版本查询官方手册、Python API、版本说明、当前节点定义和内置 Essentials 资产；
- 可直接发布的 GitHub Release，其中包含 Blender 扩展、Python wheel、Claude Desktop MCPB、可移植 Blender MCP Agent Skill 和校验文件；
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
Set-ExecutionPolicy Bypass -Scope Process -Force; irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/bootstrap.ps1 | iex
```

`-Scope Process` 只对当前 PowerShell 窗口生效，不会永久修改用户或系统的执行策略。纯 ASCII 的 [bootstrap.ps1](bootstrap.ps1) 只负责取得并启动可读的完整脚本 [install.ps1](install.ps1)。如需固定版本、确保每次安装结果一致，请使用：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create(([string](irm https://raw.githubusercontent.com/newo-ether/blender-mcp/v1.11.2/install.ps1)).TrimStart([char]0xFEFF))) -ReleaseTag v1.11.2
```

完整安装器为了让 Windows PowerShell 5.1 从本地文件读取中文而保留 UTF-8 BOM；经 HTTP 直接建立脚本块时，需先移除这个开头字符。bootstrap 会自动完成这一步，上面的固定版本命令则明确写出同一项处理。

在修改本机之前，安装器会：

1. 检测本机已安装的受支持 MCP 客户端和所有 Blender 版本；
2. 打开终端复选列表；
3. 下载最新稳定版 [GitHub Release](https://github.com/newo-ether/blender-mcp/releases/latest)；
4. 使用 `SHA256SUMS.txt` 校验 wheel、扩展 ZIP、可移植 Skill ZIP 和可选的备用 MCPB；
5. 安装到 `%LOCALAPPDATA%\BlenderMCP\venv-1.11.2` 这样的版本专用环境；
6. 将服务端和扩展安装到每个选中的 Blender 版本，同时保留原有的 Blender 偏好设置；
7. 为选中的客户端添加或更新名为 `blender_mcp` 的配置项；
8. 为选中的 Codex 和 Claude Code 安装同一份可移植 Skill，并为 Claude Desktop 准备已校验的上传 ZIP；安装结束时明确提醒仍需手动上传，不会把它报告为已安装。

安装器可以反复运行，不会重复添加配置。Codex 中完全一致的配置会保持不变；如果用户配置中已有同名但内容不同的 `blender_mcp`，Codex、Claude Code 和 Claude Desktop 都会直接更新原配置。如需保留自定义设置，请使用 `-PreserveExistingMcpEntries`。

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
| Codex / ChatGPT | 合并为一个选项，在二者共用的用户配置中添加或更新 `blender_mcp` stdio 服务，并在 `~/.agents/skills` 安装一份共用 Skill。 |
| Claude Code CLI | 在用户配置中添加或更新 `blender_mcp`，并在 `~/.claude/skills` 安装 Skill；不会删除项目级或本地 MCP 配置。 |
| Claude Desktop | 将 `blender_mcp` 安全合并到 `%APPDATA%\Claude\claude_desktop_config.json`，保留其他设置，并在替换前备份；同时准备已校验的 Skill ZIP 供用户明确上传。若 JSON 无效或不可写，才改用已校验的 MCPB，并交由 Claude 在应用内确认。 |
| Blender 4.2+ | 默认选中检测到的所有受支持版本；不希望更新的版本可以手动取消。 |
| 低于 Blender 4.2 | 为便于识别仍会显示，但扩展安装选项处于禁用状态。 |

### 完成设置

1. 打开一个已选中的 Blender 版本。
2. 在 3D 视图中按 N，打开 **BlenderMCP** 标签页。
3. Blender 会自动注册当前实例；连接端点由内部自动分配，不需要配置端口。
4. 新建任务或重启所选 MCP 客户端，让客户端发现服务端和文件系统 Skill。
5. Claude Desktop 通常只需重启。只有安装器报告改用 MCPB 时，才需要在 **Settings > Extensions > Advanced settings > Install Extension...** 中确认；此路径不依赖 Windows 的 `.mcpb` 文件关联。
6. Claude Desktop 的 Skill 需要在 **Customize > Skills > Create skill > Upload a skill** 中明确上传已校验的 `blender-mcp-skill-<version>.zip`。Claude Desktop 与 Claude Code 之间不会自动同步 Skill。

## 安装器参数

通过创建脚本块向远程脚本传递参数：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
$installer = [scriptblock]::Create(([string](irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1)).TrimStart([char]0xFEFF))
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
| `-Language <Auto\|en-US\|zh-CN>` | 指定安装器语言。`Auto` 在 Windows 界面语言为 `zh-CN`/`zh-Hans` 时使用中文，其余情况使用英文。 |
| `-BlenderPath <path[]>` | 只安装到指定的 Blender 可执行文件。 |
| `-PythonPath <path>` | 指定用于创建虚拟环境的 Python 3.10+ 解释器。 |
| `-WorkspacePath <path>` | 设置结构化节点树 JSON 工作区。 |
| `-ReleaseTag <tag>` | 安装指定 Release，而不是最新稳定版。 |
| `-InstallRoot <path>` | 自定义当前用户的发布版安装目录。 |
| `-UseRelease` | 即使从克隆仓库运行脚本，也改用 Release 中的安装包。 |
| `-SkipBlenderExtension` | 仅安装 Python MCP 服务端。 |
| `-SkipCodexRegistration` | 不修改 Codex/ChatGPT 配置。 |
| `-PreserveExistingMcpEntries` | 保留 Codex、Claude Code 或 Claude Desktop 中同名但内容不同的现有配置，不自动更新。 |
| `-SkipClaudeCodeRegistration` | 不修改 Claude Code 配置。 |
| `-SkipClaudeDesktop` | 不修改 Claude Desktop，也不下载备用 MCPB。 |
| `-SkipSkillInstallation` | 只注册所选 MCP 客户端，不安装或准备 Agent Skill。 |
| `-SkillScope <User\|Project>` | 将 Codex 和 Claude Code 文件系统 Skill 安装到用户范围或项目范围。 |
| `-SkillProjectPath <path>` | 为 `-SkillScope Project` 指定项目根目录；默认使用当前目录。 |
| `-ForceSkillUpdate` | 替换本地已修改或不受安装器管理的同名 Skill；默认会保留本地修改。 |

示例：

```powershell
# 检查 Release 安装路径，但不写入任何状态
& $installer -DryRun

# 安装到两个明确指定的 Blender 版本
& $installer -BlenderPath @(
    "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)

# 仅安装服务端
& $installer -SkipBlenderExtension -SkipCodexRegistration `
    -SkipClaudeCodeRegistration -SkipClaudeDesktop
```

TUI 只能在可交互的控制台中使用。CI、SSH、输入/输出重定向以及 `-NonInteractive` 模式会直接采用自动检测的默认选项，其中包括所有受支持的 Blender。如果 `Console.ReadKey()` 不可用，安装器会先尝试 WinForms；若 WinForms 也不可用，则给出警告并使用默认选项。

## 架构

```text
Codex / ChatGPT / Claude
          |
          | 基于 stdio 的 MCP
          v
Python MCP 服务端
          |
          | 本地发现 + 单个已占用的回环连接
          v
一个已选中的 Blender 扩展 <-> 场景与节点树
```

MCP 服务端由客户端启动，Blender 扩展则在 Blender 内提供本地连接。安装器只安装扩展，不会启动 Blender。请从已登录用户的交互式桌面启动 Blender，再开启 Blender 端连接；后台进程或 Windows Session 0 进程不会产生可见窗口。

### 多 Blender 实例

每个打开的 Blender 进程都会自动注册本地身份。一个 MCP 服务端可以发现多个实例，但同一时间只操作一个：

1. `list_blender_instances` 返回打开文件、活动场景、Blender 版本、未保存状态、可用性和占用状态。
2. 只有一个可用实例时可以自动选择；有多个实例时必须用明确的 `instance_id` 调用 `claim_blender_instance`，不会根据前台窗口或端口顺序猜测。
3. 修改操作必须持有插件端认可的占用。`get_active_blender_instance` 查看当前目标，任务结束后用 `release_blender_instance` 释放。
4. 关闭 **Allow AI control** 可把某个窗口保留给人工操作。每个 3D 视图内出现青色空心边框时，表示该 Blender 进程正被 AI 占用；边框不会拦截鼠标和键盘输入。

## Blender 资料查询

服务端既能查阅官方编写的说明，也能核对当前 Blender 版本实际提供的节点。查询结果是有长度上限、注明出处的结构化 JSON，不会把整本手册塞进上下文。

| 工具 | 用途 | 是否需要 Blender |
| --- | --- | --- |
| `get_blender_documentation_context` | 解析当前版本、官方资料频道、语言和替代来源；本身不下载网页。 | 仅 `version="auto"` 时需要 |
| `search_blender_docs` | 在官方手册、Python API 和版本说明中查询，并返回精简的排序结果。 | 仅 `version="auto"` 时需要 |
| `get_blender_doc_page` | 读取查询结果中的单个页面，或其中某个标题对应的章节；页面杂项会被剔除。 | 仅 `version="auto"` 时需要 |
| `search_geometry_node_types` | 查找当前 Blender 版本中确实可用于几何节点的节点类型。 | 是 |
| `get_geometry_node_type_schema` | 读取精简的实时插槽、节点自身属性和动态项目；继承而来的完整 RNA 需明确请求。 | 是 |
| `get_node_type_schema` | 按几何、着色器或合成器的实际归属环境查询节点定义。 | 是 |
| `get_runtime_automation_context` | 实时探测渲染引擎/输出、分层 Action、合成器与 Object Info 兼容性，不保留探针数据。 | 是 |
| `search_blender_node_assets` | 查看官方 Essentials 或 Blender 已配置的用户节点资产，不在工程中留下检查数据。 | 是 |
| `export_blender_node_asset` | 用一次性加载只读导出准确资产的内部节点图，检查后不保留 datablock。 | 是 |
| `import_blender_node_asset` | 重新核对查询结果的来源身份后，把一个准确节点资产 append 为本地副本。 | 是 |
| `audit_external_dependencies` | 只读列出缺失的 Library、Image、Cache、字体及其他外部文件。 | 是 |
| `plan_external_dependency_relinks` / `apply_external_dependency_relinks` | 先生成有边界且保留歧义的重链接计划，再显式应用同一 revision；失败时回滚路径。 | 是 |
| `inspect_evaluated_mesh` | 返回有边界的求值拓扑、连通分量、边长统计、包围盒和 Named Attribute。 | 是 |
| `get_simulation_status` | 检查 Geometry Nodes 仿真区及 Bake 能力与状态。 | 是 |
| `clear_simulation_cache` / `reset_simulation` / `bake_simulation` | 精确定位修改器与 bake ID；当前同步 Bake 会如实返回 `cancellable=false`。 | 是 |

连接 Blender 时可使用 `version="auto"` 得到与当前构建完全对应的结果；未连接时也能指定 `"5.1"` 之类的版本查询资料。开发版频道、语言切换和英文替代都会如实写入返回数据。联网范围仅限 Blender 官方 HTTPS 站点；下载内容保存在当前用户的缓存中，并明确标注新旧程度和离线替代状态。

查询顺序、版本与语言规则、缓存位置、离线行为和安全边界详见 [Blender 手册与运行时资料查询](docs/blender-knowledge_CN.md)。

## 结构化节点自动化

节点树统一表示为摊平的图数据：`nodes{}`、`links[]` 和接口记录。连接关系不会递归嵌套，也不需要让模型重写一整段临时 `bpy` 脚本。完整导出和局部子图共用同一个 revision；过期 patch 会在修改前被拒绝。

| 领域 | 可定位的归属对象 | 安全提交方式 |
| --- | --- | --- |
| 着色器 | 材质、世界、灯光、着色器节点组 | 复制归属对象或节点树，校验后重定向原有使用者 |
| 合成器 | 场景、合成器节点组 | Blender 4.2 采用场景副本；5.1 起只切换选定场景的节点树 |
| 几何 | 几何节点组 | 通用工具负责读取；修改仍由兼容的 Geometry v1 工具负责 |

### 通用工具

| 工具 | 用途 | 修改 Blender |
| --- | --- | --- |
| `list_node_trees` | 按归属对象列出几何、着色器与合成器节点树，包括使用者、能力、上限和 revision。 | 否 |
| `ensure_scene_compositor_tree` | 检查准确 Scene；只有显式传入 `create_if_missing=true` 时才创建、验证缺失的合成树，并支持回滚。 | 显式创建时 |
| `get_node_tree_index` | 搜索并分页读取精简索引，不把整张图塞进上下文。 | 否 |
| `export_node_tree` | 返回或写入完整摊平图，也可只取指定节点周边的 N 跳子图。 | 否 |
| `get_node_type_schema` | 在当前 Blender 和准确归属环境中读取插槽、属性、动态结构和限制。 | 否 |
| `validate_node_tree_patch` | 在隔离副本上检查结构、revision、引用、运行时语义和安全上限。 | 否 |
| `apply_node_tree_patch` | 再次校验后按归属类型提交，重新导出复查；任一阶段失败都精确回滚。 | 是 |
| `modify_verify_save` | 校验受支持的节点 Patch、检查候选图断言、事务提交并回读；只有显式 `save_policy` 才保存。 | 是 |

原有的 8 个 `*_geometry_node_*` 工具仍保留，继续支持修改器输入和显式的共享树策略。资产查询还支持 Blender 已配置的用户库；导入是另一个必须显式调用的事务。

### 推荐流程

1. Scene 尚无合成树时，先只读调用 `ensure_scene_compositor_tree`；确实需要时再传 `create_if_missing=true`。
2. 调用 `list_node_trees`，保留完整的 `tree_ref`。
3. 大树先用 `get_node_tree_index` 定位节点。
4. 只导出相关节点和少量邻居；默认保留 `view="auto"`：完整图使用 `operations`，定向子图使用 `semantic`。
5. 把 `tree_ref` 和返回的 `revision` 写入小型 patch JSON。
6. 用客户端现有的文件编辑工具修改 patch。
7. 先调用 `validate_node_tree_patch`。
8. 只应用通过校验的 patch，再检查 `actual_diff`、`new_revision`、使用者和备份去向。

需要一次完成时，可用 `modify_verify_save` 组合副本校验、节点/连线/接口数量断言、事务提交和 revision 回读。默认 `save_policy="never"`；`on_success` 与 `required` 都属于显式保存请求。

通用协议支持常规节点与布局操作、Frame 注释、节点组接口、Color Ramp、Curve Mapping，以及带类型的 Blender ID 和 View Layer 引用。`BLENDER_MCP_WORKSPACE` 是文件边界；越界路径、非 JSON、超过 2 MiB 或 500 个操作的 patch 都会被拒绝。完整响应上限为 8 MiB；超出时会明确引导使用索引和局部导出。

事务方式、版本差异、安全边界和性能数据见[结构化节点自动化说明](docs/structured-node-automation_CN.md)；几何修改器和资产特性仍见[几何节点说明](docs/geometry-nodes.md)。

示例与公开协议：

- [着色器快照](examples/shader-node-tree-snapshot.json) 和 [patch](examples/shader-node-tree-patch.json)
- [合成器快照](examples/compositor-node-tree-snapshot.json) 和 [patch](examples/compositor-node-tree-patch.json)
- [几何快照](examples/geometry-nodes-snapshot.json) 和 [patch](examples/geometry-nodes-patch.json)
- [公开 JSON Schema](schemas)

## 其他能力

服务端提供的 MCP 工具包括：

- 场景与对象检查；
- 视口截图；
- 执行任意 Blender Python 代码；
- 通过 Blender 代码操作对象、材质、摄像机、灯光和场景；
- 搜索、下载和应用 Poly Haven 纹理；
- 搜索、预览和导入 Sketchfab 模型；
- 使用文字或图片通过 Hyper3D Rodin 生成模型并导入；
- 使用 Hunyuan3D 生成并导入；
- 三个官方资料查询工具；
- 按归属对象工作的通用节点工具、运行时兼容性探测、用户节点资产导入，以及 Geometry Nodes v1 兼容工具。

请求示例：

- “检查场景，并让摄像机框选选中的对象。”
- “创建一个低多边形地下城场景，并使用影棚级灯光。”
- “找到选中对象使用的几何节点组，并列出其输入。”
- “根据当前 Blender 版本，查找 XPBD Solver 的官方说明和实际节点定义。”
- “导出 Join Geometry 周围的节点，并验证一个插入 Transform Geometry 节点的补丁。”
- “先索引这个材质的节点树，只导出 Principled BSDF 周边，再验证一个带可读 Frame 注释的补丁。”
- “在当前场景的合成器最终输出前插入 RGB Curves、Denoise 和 Glare，不要渲染，也不要设置 File Output。”
- “在 Poly Haven 搜索混凝土材质并应用到地面。”

## 手动与跨平台安装

一键安装脚本和 Claude Desktop 启动器目前只支持 Windows；Python wheel 和 Blender 扩展 ZIP 本身可以跨平台使用。

从最新 [Releases](https://github.com/newo-ether/blender-mcp/releases/latest) 下载以下资产：

- `blender_mcp-<version>.zip` — Blender 扩展
- `blender_mcp-<version>-py3-none-any.whl` — Python MCP 服务端
- `blender_mcp-<version>.mcpb` — Windows Claude Desktop 软件包
- `blender-mcp-skill-<version>.zip` — 用于 Claude Desktop 上传和文件系统安装的可移植 Agent Skill
- `SHA256SUMS.txt` — 文件校验值

在 Windows 上安装服务端：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\blender_mcp-1.11.2-py3-none-any.whl
```

在 macOS 或 Linux 上：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./blender_mcp-1.11.2-py3-none-any.whl
```

在 Blender 4.2+ 中安装扩展：

1. 打开 **Edit > Preferences > Add-ons**。
2. 选择 **Install from Disk...**。
3. 选择 `blender_mcp-<version>.zip`，无需解压。
4. 启用 **Blender MCP**。

插件仅以 Blender 4.2+ Extension ZIP 作为正式分发方式。旧的 Blender 3.x
单文件源码安装已经移除，使 Blender 运行时代码可以按经过测试的领域模块维护，
不再生成一个巨型插件文件。

### 手动安装 Agent Skill

Skill 用来补充 MCP 注册后的工作流指导，本身不会启动或注册 MCP 服务。所有客户端都使用
[skills/blender-mcp](skills/blender-mcp) 中的同一份 canonical 文件夹：

- Codex Desktop 与 Codex CLI：复制到 `~/.agents/skills/blender-mcp`；项目范围则复制到 `<project>/.agents/skills/blender-mcp`。
- Claude Code：复制到 `~/.claude/skills/blender-mcp`；项目范围则复制到 `<project>/.claude/skills/blender-mcp`。
- Claude Desktop：在 **Customize > Skills** 上传 `blender-mcp-skill-<version>.zip`；ZIP 根目录是 `blender-mcp` 文件夹。

Codex、Claude Code 和 Claude Desktop 分别管理 Skill，不会跨产品自动同步。Windows
安装器会在受管理的文件系统安装旁记录哈希：内容完全一致时跳过，安装器管理且未修改的副本可自动更新；本地修改默认保留，只有使用
`-ForceSkillUpdate` 才会替换。

### 手动注册客户端

请使用已安装的 `blender-mcp` 可执行文件绝对路径。

Codex CLI 和带 Codex 模式的 ChatGPT：

```powershell
codex mcp add blender_mcp `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

Claude Code：

```powershell
claude mcp add --scope user blender_mcp `
  --env BLENDER_MCP_WORKSPACE=C:\path\to\workspace `
  -- C:\path\to\venv\Scripts\blender-mcp.exe
```

其他基于 stdio 的 MCP 客户端也可以使用同一个可执行文件和同一组环境变量。在 Windows 上，安装器只会添加或更新 `%APPDATA%\Claude\claude_desktop_config.json` 中的 `mcpServers.blender_mcp`，其余字段照原样保留；已有文件会先备份。写入的均为绝对路径，因此不再要求 Claude 展开 `${HOME}`。如果 JSON 已损坏或无法写入，安装器会保持原文件不变，再改用发布的 MCPB；此时请在 **Settings > Extensions > Advanced settings > Install Extension...** 中确认安装。

## 配置

### MCP 服务端环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BLENDER_MCP_RUNTIME_DIR` | 当前用户的平台状态目录 | 可选；仅用于测试或高级本地部署的实例注册目录覆盖。 |
| `BLENDER_HOST` / `BLENDER_PORT` | `localhost` / `9876` | 仅在没有支持自动发现的新版插件时使用的旧版回退。 |
| `BLENDER_MCP_WORKSPACE` | 服务端工作目录 | 结构化节点树 JSON 文件可以读写的根目录。 |
| `BLENDER_MCP_CACHE_DIR` | 系统的当前用户缓存 | 可选；指定 Blender 资料缓存的上级目录。 |
| `DISABLE_TELEMETRY` | 未设置 | 设为 `true` 可禁用 MCP 服务端遥测。 |

设置 `BLENDER_MCP_DISABLE_TELEMETRY` 或 `MCP_DISABLE_TELEMETRY` 也可以彻底关闭遥测。

### Blender 偏好设置

以下设置会保存在 **Edit > Preferences > Add-ons > Blender MCP** 中：

- 是否允许遥测、是否自动连接，以及 **Allow AI control**（默认开启）；
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
| Claude Desktop 中没有 Blender MCP | 重启 Claude Desktop，再检查 `%APPDATA%\Claude\claude_desktop_config.json` 和 `%APPDATA%\Claude\logs`。若原 JSON 无效，安装器不会覆盖，而会明确提示改用 MCPB。 |
| Claude Code 仍在使用另一个同名配置 | 运行 `claude mcp get blender_mcp` 并检查配置范围。本地配置和项目配置优先级更高，安装器不会主动删除它们。 |
| 客户端找不到 Blender | 打开 Blender 并确认自动连接已启用，或点击 **Start MCP connection**，再调用 `list_blender_instances`；实例端点会自动注册。 |
| 同时打开了多个 Blender | 根据文件和场景摘要选择准确实例，再调用 `claim_blender_instance`；不要根据窗口焦点或端点顺序猜测。 |
| 某个 Blender 窗口需要保留给人工操作 | 在该窗口关闭 **Allow AI control**；若已占用，点击 **Release AI control**，空心边框会随即消失。 |
| Blender 进程存在但看不到窗口 | 安装器不会启动 Blender。请从已登录用户的桌面启动；后台或 Windows Session 0 进程不是可见 GUI 启动。 |
| 仍看到旧的 `blender: uvx blender-mcp` | 重新运行安装器。除非使用 `-PreserveExistingMcpEntries`，它只删除语义明确的旧 Blender 条目，并保留无关 `uvx` 服务。 |
| 某个 Blender 中没有扩展 | 重新运行安装器并选中该版本，或通过 `-BlenderPath` 传入其可执行文件。 |
| Windows 不知道如何打开 `.mcpb` | 这只影响备用流程。在 Claude Desktop 中进入 **Settings > Extensions > Advanced settings > Install Extension...** 并选择已下载的 MCPB；安装器也会尝试直接调用检测到的 Claude，并可在资源管理器中选中该文件。 |
| 几何节点补丁已过期 | 重新读取索引或导出节点树，再使用新的版本标识重建补丁。 |
| 着色器或合成器补丁已过期 | 重新导出准确的 `tree_ref`；不要把补丁用在显示树名相同的另一个归属对象上。 |
| 无法应用外部链接或 Library Override | 链接数据只读；本地 override 可查看和预演，但为避免破坏库关系，不允许提交。 |
| 完整节点树超过响应上限 | 先用 `get_node_tree_index`，再向 `export_node_tree` 传入 `view="operations"`、指定的 `node_names` 和较小的 `neighbor_depth`。 |
| 离线时无法查询资料 | 需要先在相同来源、版本和语言下完成一次联网查询。只有明确标为过期的缓存才会在网络或服务器故障时替代使用；404 不会使用旧内容。 |
| 候选版手册中找不到某个页面 | 先查看返回数据中的替代来源说明，再查询当前版本的节点类型、实时定义和 Essentials 资产。 |
| 更新后仍保留旧版环境 | 关闭所有 MCP 客户端，再删除 `%LOCALAPPDATA%\BlenderMCP\venv-<旧版本>`。请保留 `current-server.txt` 指向的目录。 |

试运行命令：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; & ([scriptblock]::Create(([string](irm https://raw.githubusercontent.com/newo-ether/blender-mcp/main/install.ps1)).TrimStart([char]0xFEFF))) -DryRun
```

## 安全性与已知限制

- `execute_blender_code` 可以在 Blender 内执行任意 Python。进行大范围修改前，请先保存 `.blend` 文件并检查代码。
- 通用修改协议覆盖本地着色器和合成器归属对象。Texture Nodes 以及未经明确适配的插件/自定义节点只读。
- 候选版资料和手册译文可能尚未补齐。所有替代来源都会明确标注；涉及当前构建的插槽与属性时，应以实时节点定义为准。
- 链接的外部库节点树可以导出，但为只读；本地 Library Override 不是可提交目标。
- `ShaderNodeScript`、`CompositorNodeOutputFile`、脚本/路径/输出 slot 设置，以及渲染、合成执行、烘焙、模拟和保存图像，都不在通用修改协议内。
- 着色器归属对象和节点组提交时会重定向其原有使用者。Blender 5.1 起的场景合成器修改只切换所选场景，其他共享原树的场景保持不变。
- 当本地 Object Info 目标被禁止渲染、`As Instance` 是固定真值，且其几何可到达 Group Output 时，结构化导出和 patch 校验都会给出警告。
- Claude Desktop 的自动注册使用官方文档公开的本地服务 JSON 配置；只有备用 MCPB 流程需要用户在应用内确认。
- 自动安装仅支持 Windows。
- Blender 4.2.22 LTS、5.1.2 和 5.2 LTS RC 均已通过本地实时定义、事务回滚、链接/override、2,048 节点效率和 corner-case 测试。
- 可选资产提供商可能会将请求或文件传输到其服务。

## 开发

```powershell
git clone https://github.com/newo-ether/blender-mcp.git
cd blender-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --editable ".[test]"
```

运行 Python 单元与结构测试：

```powershell
.\.venv\Scripts\ruff.exe check blender_extension src scripts tests --select F401,F821,F822,F823
.\.venv\Scripts\python.exe -m pytest
```

运行 Blender 偏好设置保留测试：

```powershell
.\tests\test_installer_preferences.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

运行 MCP 客户端注册安装器测试：

```powershell
.\tests\test_installer_client_registration.ps1
```

构建 Blender 扩展：

```powershell
.\.venv\Scripts\python.exe scripts\build_blender_extension.py `
  --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

构建完整的 Release 文件：

```powershell
.\scripts\build_release.ps1 `
  -BlenderPath "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
  -PythonPath .\.venv\Scripts\python.exe
```

| 路径 | 用途 |
| --- | --- |
| [blender_extension](blender_extension) | Blender 4.2+ Extension 源码、manifest、桥接、节点、供应商和 UI 模块。 |
| [src/blender_mcp/app.py](src/blender_mcp/app.py) | Python MCP stdio 应用组合入口。 |
| [src/blender_mcp/tools](src/blender_mcp/tools) | 按实例、场景、文档、节点和供应商分组的 MCP 工具。 |
| [src/blender_mcp/transport](src/blender_mcp/transport) | 本地 Blender Socket 传输与实例路由。 |
| [src/blender_mcp/protocol](src/blender_mcp/protocol) | 纯 Python 错误类型与结构化节点协议。 |
| [bootstrap.ps1](bootstrap.ps1) | 纯 ASCII 的一行安装入口，用于取得本地化安装器。 |
| [install.ps1](install.ps1) | 可直接阅读的 Windows Release 安装器。 |
| [scripts/build_release.ps1](scripts/build_release.ps1) | Release 文件构建脚本。 |
| [docs/blender-knowledge_CN.md](docs/blender-knowledge_CN.md) | 官方资料与实时节点定义查询指南。 |
| [docs/geometry-nodes.md](docs/geometry-nodes.md) | 几何节点协议指南。 |
| [docs/structured-node-automation_CN.md](docs/structured-node-automation_CN.md) | 着色器/合成器通用协议与安全边界。 |
| [schemas](schemas) | 公开的 JSON Schema。 |
| [tests](tests) | 纯 Python 与 Blender 验收脚本。 |

欢迎贡献代码和提交问题报告。

## 上游与致谢

本项目最初基于 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp)，原项目由 [Siddharth Ahuja](https://x.com/sidahuj) 创建。当前 Extension、MCP 主机、安装器、协议与发布结构均独立维护，构建和运行时不依赖上游仓库。

上游资源：

- [项目网站](https://blendermcp.org/)
- [Discord](https://discord.gg/z5apgR8TFU)
- [原始教程](https://www.youtube.com/watch?v=lCyQ717DuzQ)

## 许可证

[MIT](LICENSE)
