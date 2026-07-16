# 结构化节点自动化

Blender MCP 1.9 以同一套“按归属对象定位”的 JSON 协议读取几何、着色器和合成器节点树，并进行增量编辑。着色器与合成器使用通用事务工具；几何节点仍由 Geometry Nodes v1 工具负责修改，以保留修改器输入和共享节点树策略等专用能力。

## 为什么采用摊平图结构

快照以名称为键保存节点，并把连接保存为端点记录。它本质上是图，而不是可以递归嵌套的树：Blender 节点会分支、汇合，也会出现 Reroute、Frame 和互相引用的节点组。摊平后可以稳定随机访问、确定性序列化，也便于只审阅很小的 patch。

摊平不代表整张大图对 Transformer 就很便宜。插槽和 RNA 元数据仍会占去大部分篇幅，因此推荐按以下顺序工作：

1. 当请求指向“当前/打开的节点编辑器”时，先用 `get_node_editor_context`；只有状态唯一确定一个编辑器时才继续。
2. 没有可用界面上下文，或请求直接指定数据块时，用 `list_node_trees` 选定准确的归属对象。
3. 用 `get_node_tree_index` 搜索节点名称和类型，并按页读取。
4. 用 `query_node_graph` 查询指定字段、连接、路径、Named Attribute 或有界方向切片。
5. 只需公式、插槽值和连接时，用 `view="operations"` 导出指定节点和少量相邻节点。
6. 仅在需要确认当前版本的插槽或属性时调用 `get_node_type_schema`。
7. 用客户端原有的文件编辑工具修改小型 patch。
8. 先校验，再应用，最后核对实际差异。

## 归属对象

`tree_ref` 是实时节点图的身份。内嵌节点树的显示名称并不可靠，所以协议以拥有它的 Blender ID 定位。

| 节点树 | 归属类型 | 修改方式 |
| --- | --- | --- |
| `GeometryNodeTree` | `NODE_GROUP` | 通用工具读取；Geometry Nodes v1 工具修改 |
| `ShaderNodeTree` | `MATERIAL`、`WORLD`、`LIGHT`、`NODE_GROUP` | 通用的按归属对象事务 |
| `CompositorNodeTree` | `SCENE`、`NODE_GROUP` | 依 Blender 版本选择事务方式 |

请完整保留 `list_node_trees` 返回的 `tree_ref`，不要从界面标签自行拼接。

修改工具必须按节点树领域选择，不能只使用最先发现的 validator：

```text
GeometryNodeTree / NODE_GROUP
  -> validate_geometry_node_patch
  -> apply_geometry_node_patch

ShaderNodeTree 或 CompositorNodeTree
  -> validate_node_tree_patch
  -> apply_node_tree_patch
```

本地 Geometry 节点树的通用能力会返回 `validate=false`、`apply=false` 和
`mutation_reason="geometry_uses_v1_mutation_tools"`。链接或只读目标的两项能力都为 false；本地 library override 可以使用通用 validator 做 dry-run，但不能应用。v1 不增加 `recommended_tools` 能力字段，因为严格 schema 会拒绝额外 capability 属性；一等工具名称应留给有版本的 v2 envelope。

## 公开工具

| 工具 | 用途 | 修改 Blender |
| --- | --- | --- |
| `get_node_editor_context` | 解析可见节点编辑器及当前归属节点树，不按焦点或顺序猜测 | 否 |
| `list_node_trees` | 列出归属对象、能力、revision、规模、使用者和上限 | 否 |
| `ensure_scene_compositor_tree` | 检查 Scene，或显式初始化缺失的合成树 | 仅 `create_if_missing=true` 时 |
| `get_node_tree_index` | 搜索并分页读取精简索引 | 否 |
| `query_node_graph` | 投影字段，或查询连接、Named Attribute、路径和有界切片 | 否 |
| `export_node_tree` | 返回或原子写入完整图、局部 N 跳子图 | 否 |
| `get_node_type_schema` | 在准确归属环境中查询当前 Blender 的实际节点定义 | 否 |
| `validate_node_tree_patch` | 在隔离副本上检查结构与运行时语义 | 否 |
| `apply_node_tree_patch` | 再次校验，提交已验证副本，并复查或回滚 | 是 |

## 当前节点编辑器上下文

`get_node_editor_context` 返回 `blender-node-editor-context/1`，包含仅在当前文件会话有效的 `context_id`、确定性的 `context_revision`、固定状态、当前/选中节点、导航路径以及可解析的 `tree_ref`。状态机不隐含猜测：

| 状态 | 含义 | 后续动作 |
| --- | --- | --- |
| `NO_EDITOR` | 没有打开节点编辑器 | 请用户打开，或用 `list_node_trees` 按归属对象选择 |
| `UNIQUE_EDITOR` | 仅有一个未固定编辑器 | 使用 `selected_context_id` 和对应 `tree_ref` |
| `PINNED_EDITOR` | 仅有一个固定编辑器 | 尊重固定目标 |
| `MULTIPLE_EDITORS` | 存在多个编辑器 | 要求明确选择；不能根据焦点、顺序或最近使用时间推断 |
| `STALE_CONTEXT` | 预期文件会话或上下文 revision 已改变 | 刷新后再使用任何界面目标 |

后续操作依赖同一界面目标时，应回传上一次的 `file_session_id` 和 `context_revision`。歧义或过期状态的 `selected_context_id` 始终为 null。

公开 JSON 约定收录在 [`schemas/`](../schemas)。校验和应用时，必须在内联 `patch` 与工作区内的 `patch_path` 之间二选一。推荐使用文件，因为容易留存、比较和逐步修改。

`operations` 导出视图省略继承 RNA 元数据，但保留节点运算枚举、非默认可写标量、启用或已连接插槽的默认值、接口和连接。它与其他视图共用同一个完整图 revision。

## 有界图查询

`query_node_graph` 返回 `blender-node-graph-query/1`，并与各导出视图使用同一个完整图 revision。支持：

| 需求 | 查询类型 | 必需参数 |
| --- | --- | --- |
| 指定精简字段 | `fields` | 可选 `node_names`；可选白名单 `fields` |
| 节点相关或精确插槽连接 | `socket_links` | 可选 `node_names`；使用 `socket_id` 时必须准确给一个节点名 |
| Named Attribute 读写节点 | `named_attributes` | 可选 `node_names` 和精确 `attribute_name` |
| 一条最短路径 | `shortest_path` | `from_node`、`to_node`；可选 `direction` |
| 上游或下游可达节点 | `upstream`、`downstream` | `node_names` |
| 有界双向或单向切片 | `slice` | `node_names`；可选 `direction` |

按以下规则选择工具：

```text
字段、路径、连接       -> query_node_graph
局部公式与接线         -> operations 导出
准确插槽或 RNA 约定    -> semantic 导出或节点类型 schema
排版                   -> layout 导出
```

`fields` 只投影精简节点记录；当问题同时需要相关连接和插槽默认值时，仍应使用定向 `operations` 导出。未知字段或无效参数组合会返回针对具体参数的错误，不会静默生成残缺记录。

## 空 Scene 的合成树初始化

空 Scene 可能没有合成树。Blender 5.1 起，仅设置 `Scene.use_nodes` 不会创建 `Scene.compositing_node_group`。先使用默认的 `create_if_missing=false` 调用 `ensure_scene_compositor_tree` 检查状态；只有调用方明确需要时，才以 `true` 再调用一次。

Blender 5.1 起，创建过程会新建独立 `CompositorNodeTree`，添加 Image 输出接口和 Group Output，只赋给所选 Scene，并核对规范 `tree_ref`。任一步失败都会恢复 Scene 指针并删除新树。已有节点树时返回 `ready` 且不修改；链接或 override Scene 会被拒绝。

## Patch 内容

`blender-node-tree-patch/1` 包含准确的 `tree_ref`、完整图的 `base_revision`、所需能力和不超过 500 项的操作。[`examples/`](../examples) 中全为零的 revision 只是占位符，实际使用时必须换成当前工程导出的 revision。

通用操作包括：

- 图结构：`add_node`、`remove_node`、`rename_node`、`add_link`、`remove_link`；
- 数值：`set_node_property`、`set_socket_default`；
- 排版：`set_node_layout`、`set_annotation`；
- 节点组接口：`add_interface_socket`、`remove_interface_socket`；
- 动态数据：`set_color_ramp`、`set_curve_mapping`。

新增节点先使用 patch 内的临时 `id`；同一 patch 的后续操作可以引用它，应用结果会给出最终 Blender 节点名称。插槽选择器采用导出的 `input:<序号>:<名称>` 与 `output:<序号>:<名称>`，序号可区分重名插槽，不应根据界面文字猜测。

带类型的值可引用支持的 Blender ID 或 View Layer，无须在 patch 中夹带 Python。ID 不存在、归属不符、当前版本没有该节点、属性只读、连接无效或 revision 过期时，都会返回结构化诊断。

## 校验与提交

校验先在 MCP 进程检查 JSON 结构，再由 Blender 解析归属对象、核对 revision 和安全上限。所有操作都会在隔离副本上执行，并重新导出候选结果。成功时应同时满足 `valid: true`、`stage: "runtime"` 和 `will_mutate: false`。

应用前会立即再校验一次，随后按下表选择提交方式，并把提交后的图与已验证候选结果逐项核对。默认会把原对象保留为带 fake user 的备份。

| 归属对象 | 提交方式 |
| --- | --- |
| 材质、世界、灯光 | 复制归属对象及其内嵌着色器树，再重定向原有使用者 |
| 着色器或合成器节点组 | 复制 NodeTree，再重定向使用者 |
| Blender 4.2 的场景 | 复制带内嵌合成树的 Scene，再重定向使用者 |
| Blender 5.1 起的场景 | 复制合成节点树，只替换所选 Scene 的指针 |

Blender 5.1 起，即使多个场景共用旧合成节点组，也只会修改指定场景。提交或复查任一步失败时，事务会恢复归属指针、名称、fake-user 状态和原图身份。若返回 `rollback_failed`，应由人工检查工程。

## 安全边界与上限

- 外部链接库中的归属对象只读，不允许修改。
- Library Override 可以读取和试运行，但正式应用会被拒绝。
- Python 或附加组件提供的自定义节点可以导出，通用协议不会修改。
- 旧式 Texture 节点树不在本协议范围内。
- `ShaderNodeScript`、`CompositorNodeOutputFile` 以及路径、脚本等有外部副作用的属性默认拒绝修改。
- 校验不会渲染、写输出文件、烘焙，也不会遗留临时数据块。
- patch 文件上限为 2 MiB、500 项操作。
- 运行时修改上限为 10,000 个节点，校验限时 30 秒。
- 完整响应上限为 8 MiB；更大的节点树应改用索引和局部导出。
- 当本地 Object Info 来源被禁止渲染、`As Instance` 是固定真值，且其几何可到达 Group Output 时，几何导出与 patch 试运行会发出警告。可让原型在相机外保持可渲染、关闭实例输出，或在图内实现/创建原型。

`BLENDER_MCP_WORKSPACE` 是所有快照与 patch 文件的路径边界；越界路径和非 JSON 文件会被拒绝。

## 性能验收

2,048 节点的验收用例已在 Blender 4.2.22、5.1.2 和 5.2 LTS RC 通过。Blender 5.2 中，完整着色器快照为 1,502,614 字节，索引加局部导出只占 0.224%；完整合成器快照为 4,973,998 字节，对应局部流程只占 0.103%。4.2 与 5.1 的结果相近。

同一套验收还覆盖查找、解释、新增、重新连接、调参、注释和回滚。这些比例只衡量协议载荷，不代表特定模型的准确率，也不是按某一种 tokenizer 计算的 token 数。

## 兼容性

| Blender | 着色器 | 合成器 | 说明 |
| --- | --- | --- | --- |
| 4.2.22 LTS | 通过 | 通过 | 使用场景内嵌合成树事务 |
| 5.1.2 | 通过 | 通过 | 使用 `Scene.compositing_node_group` |
| 5.2 LTS RC | 通过 | 通过 | 实时节点定义可反映新版本功能 |

项目不会用一份写死的跨版本目录猜测节点和插槽。请针对已连接的 Blender 调用 `get_node_type_schema`；若要理解节点行为而不仅是 RNA 外形，再配合官方手册查询工具。
