# 第二层 KnowledgeUnit MVP 设计

## 1. 目标

第二层不再抽取新的数学语义，而是按教材的中心教学目标组织第一层 `Concept`、`Claim` 和 `Method`。它回答“这些数学语义共同构成哪个可教学、可复习、可作为路径节点的知识单元”。

本层暂不构建原子多前提推理图，也不生成 `prerequisite_of`、`similar_to` 或能力诊断标签。第一层关系可作为提示信息，但在关系 Gold 完成前不作为决定 KnowledgeUnit 的唯一依据。

## 2. Node Schema

MVP 只有一种新节点：

| 类型 | 必需字段 | 含义 |
| --- | --- | --- |
| `KnowledgeUnit` | `id`, `type`, `name`, `aliases`, `description`, `evidence` | 围绕一个教材中心教学目标组织的知识单元 |

约束：

- 不创建 Chapter、Section 或 Topic 节点；教材位置保存在 evidence 和顶层来源元数据中；
- `description` 描述该单元要理解或掌握的完整数学内容边界，不复制成员节点定义；
- 不在第二层复制 Concept、Claim 或 Method，只引用第一层稳定 ID；
- 一个标题不自动等于一个 KnowledgeUnit，标题只是候选边界；
- 没有足够第一层语义支撑时，不创建空单元，并写入 diagnostics。

## 3. Relation Schema

MVP 只使用两种跨层关系：

| 关系 | 方向 | 含义 |
| --- | --- | --- |
| `centers_on` | KnowledgeUnit -> Concept/Claim/Method | 第一层节点直接定义该单元的中心目标 |
| `contains` | KnowledgeUnit -> Concept/Claim/Method | 第一层节点是该单元明确教授的支撑内容 |

每个 KnowledgeUnit 至少有一个 `centers_on`。同一个第一层节点最多在一个单元中作为中心，但可以被其他单元通过 `contains` 复用。关系保留 evidence，不保存 score、confidence 或 quality。

`depends_on: KnowledgeUnit -> KnowledgeUnit` 预留给后续阶段，只有在第一层关系抽样审核及单元 Gold 完成后才启用。

## 4. 输出结构

```json
{
  "schema_version": "maf-kg.knowledge-unit.v1",
  "source_graphs": ["..."],
  "knowledge_units": [
    {
      "id": "ku_function_limit_finite",
      "type": "KnowledgeUnit",
      "name": "自变量趋于有限值时的函数极限",
      "aliases": [],
      "description": "...",
      "evidence": []
    }
  ],
  "relations": [
    {
      "id": "ku_relation_...",
      "type": "centers_on",
      "source_id": "ku_function_limit_finite",
      "target_id": "concept_...",
      "evidence": []
    }
  ],
  "diagnostics": {}
}
```

## 5. 单智能体流程

```text
load_semantic_graphs
  -> build_source_outline
  -> organize_knowledge_units
  -> validate_cross_layer_refs
  -> save_result
```

只设置一个 `KnowledgeUnitOrganizer`：

1. 每次处理一个教材节，读取该节完整第一层节点及其 evidence；
2. 读取标题层次作为候选教学边界，但允许合并或跳过纯结构标题；
3. 为每个单元选择中心节点和支撑节点；
4. 不修补第一层漏项，不新建第一层节点；
5. 引用不足、中心缺失或边界不确定时写 diagnostics，错误即暴露，不做 fallback；
6. 程序校验引用、关系方向、最小中心数量、重复成员和证据范围。

不增加 NormalizerAgent 或 VerificationAgent。跨节去重只针对 KnowledgeUnit 身份，后续确有重复证据时再引入与第一层相同的顺序 memory。

## 6. 前三节预期单元候选

以下只是待人工审核的候选，不是模型输出：

### 第一节

1. 映射概念；
2. 逆映射与复合映射；
3. 函数概念；
4. 函数的基本性质；
5. 反函数与复合函数；
6. 函数的四则运算；
7. 初等函数。

### 第二节

1. 数列极限的定义；
2. 收敛数列的性质。

### 第三节

1. 自变量趋于有限值时的函数极限；
2. 自变量趋于无穷大时的函数极限；
3. 函数极限的性质。

共 12 个候选 KnowledgeUnit。第一节当前漏抽 `函数的四则运算`，因此第二层程序不得凭标题自行补造该中心节点；应报告该候选单元的 first-layer coverage gap。第三节的去心邻域及两类定义论证法也应以 coverage gap 保留，而不是由第二层重新抽取。

## 7. MVP 验收

- 所有跨层 target ID 均存在于输入第一层图；
- 每个单元至少一个中心节点且无重复关系；
- 不出现 Chapter、Section、例题或证明步骤单元；
- 对前三节 12 个候选完成人工 M/O/Merge 审核后再运行；
- 首轮只评价 KnowledgeUnit 边界和成员归属，不评价单元间先修关系。
