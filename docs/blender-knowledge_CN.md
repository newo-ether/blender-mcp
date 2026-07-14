# Blender 手册与运行时资料查询

[English](blender-knowledge.md) | **中文**

Blender MCP 会把官方编写的资料与当前 Blender 构建实际具备的功能结合起来。模型既能查阅概念和用法，也能核对实验性节点、当前版本的插槽定义和本机随附的 Essentials 资产，而不必把整本手册塞进上下文。

## 工具一览

| 工具 | 适用场景 | 是否需要 Blender |
| --- | --- | --- |
| `get_blender_documentation_context` | 确定当前构建、资料频道、语言和替代来源。 | 仅 `version="auto"` 时需要 |
| `search_blender_docs` | 在官方手册、Python API 和版本说明中进行有限范围的查询。 | 仅 `version="auto"` 时需要 |
| `get_blender_doc_page` | 读取单个页面，或其中某个标题对应的章节。 | 仅 `version="auto"` 时需要 |
| `search_geometry_node_types` | 查找当前构建中确实可用于几何节点的节点类型。 | 是 |
| `get_geometry_node_type_schema` | 查看当前节点类型的精简插槽、节点自身属性和动态项目。 | 是 |
| `search_blender_node_assets` | 查看本机 Blender 随附的官方 Essentials 节点资产。 | 是 |

前三个工具只读访问网络。运行时和 Essentials 工具同样不会修改工程；它们会在返回前清理临时节点树和载入的数据块，并核对清理结果。

## 推荐查询顺序

1. 先查手册，了解概念和操作流程。
2. 需要 RNA 标识符或脚本细节时，再查 Python API。
3. 功能刚刚加入或行为发生变化时，补查版本说明。
4. 使用 `search_geometry_node_types` 确认当前 Blender 构建接受的节点类型。
5. 使用 `get_geometry_node_type_schema` 获取精简的实时插槽和属性定义。只有确实需要继承而来的 RNA 信息时，才使用 `detail="full"`。
6. 搜索本机 Essentials，了解官方提供的高层节点组和示例。

以 Blender 5.2 候选版中的 XPBD 为例，可以组合调用：

```text
search_blender_docs(
  query="XPBD Solver",
  version="auto",
  sources=["manual", "python_api", "release_notes"]
)
search_geometry_node_types(query="XPBD")
get_geometry_node_type_schema(
  node_type="GeometryNodeXPBDSolver",
  detail="compact"
)
search_blender_node_assets(
  library="geometry_nodes_dynamics_assets.blend",
  detail="summary"
)
```

官方资料负责解释用途，当前构建返回的实时定义则是插槽和属性的最终依据。

## 版本与替代来源

`version` 支持以下形式：

- `auto`：读取当前连接的 Blender 构建；未连接时明确报错，不会自行猜测；
- `major.minor` 或 `major.minor.patch`：无需连接 Blender，按指定版本查询；
- `current`：使用当前稳定版资料频道；
- `dev`：使用开发版资料频道。

手册、Python API 和版本说明会分别选择频道。例如，候选版可以使用开发版手册和 API，同时仍按具体版本号查询版本说明。任何频道切换或语言替代都会写入结构化的 `fallback` 字段，不会悄悄发生。

手册支持 `en`、`zh-hans`、`zh-hant` 等规范化语言代码；Python API 和版本说明只有英文版。如果某个版本的中文索引或页面尚未发布，工具可以在同一频道改查英文，并同时保留请求语言、实际语言和替代原因。

候选版资料有时晚于程序本身发布。查不到页面并不代表当前 Blender 中没有相应节点；这时应以实时节点查询为准。

## 查询范围与出处

一次查询最多返回 20 条结果。页面读取只接受查询结果中的相对路径，不接受任意网址；每次最多返回 50,000 个字符。脚本、样式、导航、表单等网页杂项会被剔除，返回数据仍保留官方规范链接，便于人工核对。

联网范围严格限制在以下 HTTPS 站点：

- `docs.blender.org`：官方手册和 Python API；
- `developer.blender.org`：版本说明。

工具会拒绝任意外部网址、嵌入凭据、非标准端口、目录穿越、跳往非官方站点的重定向、异常内容类型、超大响应和无限重定向。

## 缓存与离线使用

索引和页面保存在当前用户的缓存目录中，不会写入仓库或 `.blend` 文件。默认位置如下：

- Windows：`%LOCALAPPDATA%\BlenderMCP\Cache\docs-v1`；
- macOS：`~/Library/Caches/blender-mcp/docs-v1`；
- Linux：`$XDG_CACHE_HOME/blender-mcp/docs-v1`，未设置时为 `~/.cache/blender-mcp/docs-v1`。

可以通过 `BLENDER_MCP_CACHE_DIR` 指定上级缓存目录。缓存的有效期为 24 小时，支持 ETag 和 Last-Modified 复核，总容量上限为 128 MiB。原子写入、内容哈希和带版本的键值可以避免使用半写入、损坏或版本不兼容的数据。

遇到超时、连接失败或 HTTP 5xx 时，已过期的缓存可以作为 `stale_fallback` 返回。结果会明确给出缓存状态、存放时间、内容年龄和触发替代的错误。HTTP 404 等客户端错误不会使用旧缓存。

如果缓存目录没有写入权限，本次请求会标为 `cache_unavailable`；只要网络正常，仍可返回实时内容。

## 隐私与工程安全

资料查询只访问官方索引与页面，不会上传 Blender 场景。实时定义查询会临时建立节点树，Essentials 查询会短暂载入 Blender 随附的资产；两者都会在返回前检查清理结果，也不会保存工程。

`execute_blender_code` 与几何节点补丁属于另一类可修改操作，安全边界和回滚规则见[几何节点自动化](geometry-nodes.md)。

## 兼容性

只要客户端加载了这个 MCP 服务端，就能使用全部查询工具，不依赖额外的客户端 Skill。当前已在 Blender 4.2.22 LTS、Blender 5.1.2 和 Blender 5.2 LTS RC 上完成实时验收。
