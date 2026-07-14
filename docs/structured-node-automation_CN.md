# 结构化节点自动化

Blender MCP 1.9 以同一套“按归属对象定位”的 JSON 协议读取几何、着色器和合成器节点树，并进行增量编辑。着色器与合成器使用通用事务工具；几何节点仍由 Geometry Nodes v1 工具负责修改，以保留修改器输入和共享节点树策略等专用能力。

## 为什么采用摊平图结构

快照以名称为键保存节点，并把连接保存为端点记录。它本质上是图，而不是可以递归嵌套的树：Blender 节点会分支、汇合，也会出现 Reroute、Frame 和互相引用的节点组。摊平后可以稳定随机访问、确定性序列化，也便于只审阅很小的 patch。

摊平不代表整张大图对 Transformer 就很便宜。插槽和 RNA 元数据仍会占去大部分篇幅，因此推荐按以下顺序工作：

1. 用 `list_node_trees` 选定准确的归属对象。
2. 用 `get_node_tree_index` 搜索节点名称和类型，并按页读取。
3. 只导出指定节点和少量相邻节点。
4. 仅在需要确认当前版本的插槽或属性时调用 `get_node_type_schema`。
5. 用客户端原有的文件编辑工具修改小型 patch。
6. 先校验，再应用，最后核对实际差异。

## 归属对象

`tree_ref` 是实时节点图的身份。内嵌节点树的显示名称并不可靠，所以协议以拥有它的 Blender ID 定位。

| 节点树 | 归属类型 | 修改方式 |
| --- | --- | --- |
| `GeometryNodeTree` | `NODE_GROUP` | 通用工具读取；Geometry Nodes v1 工具修改 |
| `ShaderNodeTree` | `MATERIAL`、`WORLD`、`LIGHT`、`NODE_GROUP` | 通用的按归属对象事务 |
| `CompositorNodeTree` | `SCENE`、`NODE_GROUP` | 依 Blender 版本选择事务方式 |

请完整保留 `list_node_trees` 返回的 `tree_ref`，不要从界面标签自行拼接。

## 公开工具

| 工具 | 用途 | 修改 Blender |
| --- | --- | --- |
| `list_node_trees` | 列出归属对象、能力、revision、规模、使用者和上限 | 否 |
| `get_node_tree_index` | 搜索并分页读取精简索引 | 否 |
| `export_node_tree` | 返回或原子写入完整图、局部 N 跳子图 | 否 |
| `get_node_type_schema` | 在准确归属环境中查询当前 Blender 的实际节点定义 | 否 |
| `validate_node_tree_patch` | 在隔离副本上检查结构与运行时语义 | 否 |
| `apply_node_tree_patch` | 再次校验，提交已验证副本，并复查或回滚 | 是 |

公开 JSON 约定收录在 [`schemas/`](../schemas)。校验和应用时，必须在内联 `patch` 与工作区内的 `patch_path` 之间二选一。推荐使用文件，因为容易留存、比较和逐步修改。

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
