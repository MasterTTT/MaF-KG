# 第一章第一层抽取——全章可靠性评估（v1.7.0 + Route 1 + reasoning_effort=high）

> 状态：10 节已全部抽取（第四节为旧 low 档，其余 high 档）；**但增量更新缺失，跨节关系断裂，需按 `--seed-graph` 串行链重抽**。本文档先记录当前结果与可靠性结论。

## 1. 执行摘要

- **节点层召回完整**：1-3 节 Gold 节点 recall 全 **1.0**，无漏抽（满射/单射/一一映射、数列有界性、函数极限性质全齐）。
- **节点层精度偏松**：1-3 节 F1 = 0.8842 / 0.9167 / 0.9286，FP 集中在「过度拆分 + 合理但不要求的多抽」，没有错误内容。
- **关系层是主要短板**：①跨节关系断裂（缺增量更新）→ 全章 11 个孤立节点；②`derived_from` 稀疏（每节 1-7 条）；③`equivalent_to`/`establishes` 恒 0。
- **稳定性隐患**：粒度/claim_kind 跨运行漂移；第八节首跑因模型引用未定义 ref_id 而 merge 失败（重跑成功）。

结论：第一层**够用但不够牢靠**——够 layer-2 的 KnowledgeUnit 聚合（按节点组织），不够"推理/依赖图"式的下游消费。牢靠性短板主要在**关系层 + 增量更新**，不在节点抽取。

## 2. 全章抽取统计（high 档，独立抽取）

| 节 | nodes | relations | isolated | 关系类型 |
|---|---:|---:|---:|---|
| 第一节 映射与函数 | 76 | 113 | 2 | involves 50 / special_case_of 32 / defined_in_terms_of 25 / derived_from 4 / uses 2 |
| 第二节 数列的极限 | 13 | 12 | 2 | involves 6 / derived_from 2 / defined_in_terms_of 2 |
| 第三节 函数的极限 | 17 | 20 | 0 | involves 10 / special_case_of 5 / defined_in_terms_of 4 / derived_from 1 |
| 第四节 无穷小与无穷大 | 7* | — | — | （旧 low 档，待重抽） |
| 第五节 极限运算法则 | 24 | 24 | 0 | involves 13 / derived_from 7 / uses 3 |
| 第六节 极限存在准则 | 15 | 14 | 0 | involves 10 / derived_from 4 |
| 第七节 无穷小的比较 | 7 | 4 | **2** | involves 3 / special_case_of 1 |
| 第八节 连续性与间断点 | 16 | 17 | 2 | special_case_of 9 / involves 4 / defined_in_terms_of 4 |
| 第九节 连续函数运算 | 26 | 22 | **3** | involves 13 / derived_from 7 / uses 2 |
| 第十节 闭区间连续函数性质 | 13 | 14 | 0 | involves 12 / derived_from 2 |

孤立节点共 **11** 个，集中在前三节（奇偶性/量词等折叠碎片）和后三节（跨节断裂）。

## 3. 节点层可靠性（Gold 对照，1-3 节）

| 节 | TP/FP/FN | Recall | Precision | F1 |
|---|---:|---:|---:|---:|
| 第一节 | 42/11/0 | 1.0 | 0.7925 | 0.8842 |
| 第二节 | 11/2/0 | 1.0 | 0.8462 | 0.9167 |
| 第三节 | 13/2/0 | 1.0 | 0.8667 | 0.9286 |

15 个 FP 归五类（均可收编，无一为错误数学内容）：
1. 过度拆分：单调增加/减少 vs 单调函数；和差积商 vs 四则运算。
2. 合理多抽：直接函数、函数定义域的确定方法、取整函数跳跃、∀/∃、局部有界性概念。
3. 粒度方差：双曲性质「简单性态 vs 基本性质 vs 原子」跨轮漂移。
4. 重复：复合映射/函数条件、x→∞ 极限 ×2。
5. claim_kind 残余：奇偶分解被分 result（Gold O 为 property）。

## 4. 关系层可靠性（主要短板）

- **第二节关系 Gold 12/12 全中**（recall 1.0）——核心关系的正例是齐的。
- 但关系 Gold 是正例集、无 precision；且只覆盖第二节 12 条。
- **`derived_from` 稀疏**：每节 1-7 条，推导链几乎没建起来（第六节 4 条已是最高）。
- **`equivalent_to` / `establishes` 恒 0**：这两个关系类型在整个第一章一次都没用上，事实上是死的。

## 5. 关键缺陷：增量更新缺失（跨节断裂）

第七节的「k阶无穷小 / 低阶无穷小」、第九节的 3 条「等价无穷小关系式」全部孤立，因为**每节独立抽取、memory 从零开始**——第四节已有的「无穷小」概念不在后节的记忆里，`special_case_of → 无穷小` 根本连不出来。

这不是模型错，是**抽取方式错了**：`Extractor` 原生支持 `--seed-graph` 增量更新（载入前序图为种子，后节可复用/引用种子节点、形成跨节关系），本次漏用。

**修复**：按 `--seed-graph` 串行重抽 1→10（每节以累计图为种子），输出到第十节即为整章连通图。代价约 3+ 小时（串行 10 节 × ~20 分钟）。

## 6. 第二层（KnowledgeUnit）设计复盘

当前设计见 `docs/knowledge_unit_layer_v1.md`：第二层只组织第一层节点为 KnowledgeUnit，`centers_on` / `contains` 跨层引用第一层稳定 ID，`depends_on` 预留。结合本次实测，五处需改进：

1. **ID 稳定性假设不成立（最严重）**：设计写「引用第一层稳定 ID」，但 content-hash ID 随粒度漂移（双曲性质三种粒度 → 三个不同 ID）。layer-2 应改为按 `(type, name, description)` 语义解析匹配到当前第一层图，而非硬编码 ID；或先冻结第一层粒度。
2. **粒度碎片干扰中心选择**：`centers_on` 要选中心，但「单调函数 vs 单调增加/减少」「四则运算 vs 和差积商」碎片并存时，organizer 需一个合并规范（与第一层 canonical-concept 规则对齐）。
3. **关系只作弱提示的 hedge 是对的，且应写死**：实测 `derived_from` 稀疏、`equivalent_to/establishes` 为 0，`centers_on`/`contains` 必须由 evidence + heading 判定，不能依赖第一层关系；`depends_on` 应来自独立抽取或教材结构，不是第一层 `derived_from`。
4. **O 节点语义要进入 layer-2**：本轮把一批 M 降为 O（去心邻域、四则运算、双曲原子性质等）。layer-2 的「中心缺失/coverage gap」判定应只对 M；O 缺了不算 gap。
5. **必须先做增量更新再跑 layer-2**：KnowledgeUnit 的 `centers_on`/`contains` 要引用跨节概念（如第七节单元要挂到第四节「无穷小」），若第一层跨节断裂，layer-2 的单元边界在跨节处必然残缺。

## 7. 待办（下一步）

1. 用户确认增量更新方式 → 按 `--seed-graph` 串行重抽 1→10。
2. 第四节补 high 档重抽。
3. 重抽后重评 1-3 节 F1 + 复验跨节孤立节点归零。
4. 5-10 节目前无 Gold；如需 F1，需补标注（工作量大，建议先用结构/孤立节点/关系完整性做代理指标）。
