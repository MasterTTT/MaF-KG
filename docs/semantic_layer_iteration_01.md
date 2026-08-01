# 第一层语义图迭代记录 01

## 对照设置

- 教材：第一章第一节《映射与函数》
- 模型：`deepseek-v4-pro`
- reasoning effort：`medium`
- 分块：12 个结构感知 chunk
- memory：Top-K 节点、相关一跳关系及节点数学表达
- 两轮均关闭读取缓存，教材内容与模型参数保持一致

## semantic.v1 基线

首轮输出为本地文件 `KG/第一章第一节_语义图_v1.json`。

| 指标 | 结果 |
| --- | ---: |
| 节点 | 64 |
| Concept | 40 |
| Claim | 24 |
| Method | 0 |
| 关系 | 73 |
| MathExpression | 40 |
| create / reuse | 64 / 29 |
| skipped item | 1 |
| isolated node | 3 |
| 超过 20 行的节点证据 | 6 |

结构完整性良好：不存在重复 ID、缺失关系端点或缺失 expression owner；Top-K memory 完成 29 次复用，未出现明显同名重复节点，LaTeX 均由原文 candidate 解析。

主要语义问题：

1. `special_case_of` 承担了 schema 无法表达的定义依赖，29 条中至少 9 条明显不满足子类关系，例如“定义域 -> 函数”“值域 -> 函数”“函数图形 -> 函数”“逆映射 -> 单射”，以及方向相反的“初等函数 -> 基本初等函数”。
2. “复合函数的定义”被错误抽成 Claim，说明“定义不是 Claim”的约束仍可能被“定义语句可判真值”的模型判断覆盖。
3. 章首预告提前生成“极限”和“函数的连续性”，但当前第一节并未实质讲授它们。
4. 6 个节点使用超过 20 行的证据，其中反双曲函数概念的证据跨越约 35 至 70 行，审计定位不够精确。
5. 1 条关系直接使用全局节点 ID 而非当前响应的 `ref_id`，被程序正确拒绝。

## semantic.v1.1 修改

本轮只针对上述高频错误，不改变顺序抽取和 Top-K memory：

- 新增 `defined_in_terms_of: Concept -> Concept`，表达有教材证据的定义依赖，不作为通用相关关系。
- 对 `special_case_of` 使用严格子集测试：每个 source 实例都必须同时是 target 实例，方向为窄概念到宽概念。
- 明确允许节点无边，禁止为了连通而输出关系。
- 将定义规定为本 schema 中的规定性内容，而非 Claim；定义中的条件、量词和等价形式不改变这一边界。
- 章节预告、主题列表和仅有名称的提及不产生节点。
- 节点证据采用最小连续来源区间，超过 20 行应为例外。
- 增加输出审计诊断，自动报告孤立节点与宽证据节点。

## semantic.v1.1 结果

首轮 v1.1 输出为本地文件 `KG/第一章第一节_语义图_v1_1.json`。

| 指标 | v1 | v1.1 |
| --- | ---: | ---: |
| 节点 | 64 | 57 |
| Claim | 24 | 18 |
| Concept | 40 | 39 |
| 关系 | 73 | 73 |
| `special_case_of` | 29 | 9 |
| `defined_in_terms_of` | 0 | 29 |
| MathExpression | 40 | 42 |
| create / reuse | 64 / 29 | 57 / 19 |
| skipped item | 1 | 3 |
| isolated node | 3 | 4 |
| 宽证据节点 | 6 | 5 |

核心修改有效：两个章节预告 chunk 均输出空图；“极限”“函数的连续性”和“复合函数的定义”等错误节点消失；反双曲函数的定义表达由 Claim 转移为 Concept 的 `definition_representation`；9 条 `special_case_of` 中 8 条符合严格子类语义。

仍有四类问题：

1. “函数的有界性”因教材先后给出上下界形式和绝对值形式而生成两个同名 Concept，说明等价定义复用约束不足，同时旧 ID 分配逻辑仍允许同名节点分叉。
2. 模型有 3 次在关系中直接使用 node memory 的全局 ID，未创建 reuse action，程序拒绝后造成“取整函数”“分段函数”等节点孤立。
3. `狄利克雷函数 -> 函数的周期性` 仍被误判为 `special_case_of`；正确建模应是“狄利克雷函数具有周期性”的 Claim 涉及两个 Concept。
4. 反双曲函数 Concept 的 description 混入远处推导公式，使节点证据仍跨越 39 至 76 行。

## semantic.v1.1 r2 调整

- canonical type 与 canonical name 完全相同的 create action 现在直接判为无效，不再生成带来源后缀的第二节点；语义不同的同名对象必须使用可区分名称。
- 明确等价定义和互补定义必须复用同一 Concept，并输出完整更新描述。
- 明确 relation endpoint 只能使用当前响应的 local `ref_id`；引用 memory node 时必须先输出 reuse action。
- 将“命名对象具有某性质”规定为 Claim，而非该对象到性质概念的 `special_case_of`。
- 节点 evidence 只覆盖节点定义，远处公式使用 expression 自身的 evidence，不能反向拉宽节点证据。
- 允许正文实质介绍的数学族形成 umbrella Concept，避免把“忽略预告”错误扩大为忽略正文中的族概念。

## semantic.v1.1 r2 结果

r2 输出为本地文件 `KG/第一章第一节_语义图_v1_1_r2.json`。

| 指标 | v1 | v1.1 | v1.1 r2 |
| --- | ---: | ---: | ---: |
| 节点 | 64 | 57 | 53 |
| Claim / Concept | 24 / 40 | 18 / 39 | 15 / 38 |
| 关系 | 73 | 73 | 72 |
| `special_case_of` | 29 | 9 | 9 |
| `defined_in_terms_of` | 0 | 29 | 37 |
| MathExpression | 40 | 42 | 40 |
| create / reuse | 64 / 29 | 57 / 19 | 53 / 26 |
| skipped item | 1 | 3 | 7 |
| isolated node | 3 | 4 | 4 |
| 宽证据节点 | 6 | 5 | 4 |
| 同类型同名重复 | 0 | 1 | 0 |

r2 保持了预告过滤和定义边界修正，并将同名重复与宽证据继续降低。模型仍尝试创建第二个“函数的有界性”，程序按 canonical invariant 拒绝后，又连带拒绝了依赖该 local ref 的 2 条关系与 1 个 expression。因此严格拒绝能够阻止脏节点进入主图，但不能自动恢复被模型错误身份决策影响的信息。

local ref 提示词没有解决接口问题：模型仍有 3 次直接使用 node memory ID。实验后将端点契约正式改为：新节点使用 local ref；Top-K memory 中未被更新的节点可直接使用其全局 ID；任何未出现在当前 memory 中的全局 ID 仍被拒绝。这是显式接口设计，不是事后兼容或自动补边。本修改已通过本地合并测试，尚未再发起第四轮外部模型调用。

## 结论

本轮最可靠的改进是：

- 章节预告不再产生未来知识点；
- 规定性定义不再被大规模提升为 Claim；
- `special_case_of` 的明显滥用由至少 9 条降到 1 条；
- 精确原文 MathExpression、图完整性和 Top-K 复用保持稳定；
- 同类型同 canonical name 的重复被结构性禁止；
- 输出自动报告孤立节点和宽证据节点。

仍未解决的结构问题是“对象具有某性质”的表达。`狄利克雷函数 -> 函数的周期性` 仍被误写为 `special_case_of`，部分反双曲函数到单调性、奇偶性的边又被写成 `defined_in_terms_of`。这不是继续添加反例提示词即可稳定解决的问题，而是当前 schema 在以下两种方式间尚未作出选择：

1. 始终创建一个 Property Claim，再用 `involves` 连接对象与性质概念；
2. 增加受限的 `has_property: Concept -> Concept`，直接表达教材明确陈述的性质。

在该建模选择确定前，不继续扩充关系类型，以避免用新关系再次掩盖边界问题。
