# legacy/ — 已归档的历史代码与文档

本目录存放**已退役的 `maf-kg.agent.v5` 实体图谱家族**的历史产物，仅作参考，不再是仓库的活跃 schema。

活跃 schema 是 `maf-kg.semantic.v1.6.0`（节点 `Concept` / `Claim` / `Method`），由
`agents/Extractor.py` 产出；其设计见 `docs/semantic_layer_v1.md`。

## 内容

| 文件 | 说明 | 归档原因 |
| --- | --- | --- |
| `SemanticLinkingAgent.py` | v5 语义连边 Agent | 依赖 `agents.ConceptFormulaExtractor`，该模块已删除，无法 import |
| `schema_v3.md` | v5 实体图谱 schema 与多 Agent 管线 | 描述的 NodeResolver / RelationAgent / NormalizerAgent / VerificationAgent 管线已不存在 |
| `semantic_linking.md` | v5 SemanticLinkingAgent 设计 | 对应代码已归档，描述已过时 |

## 说明

- v5 家族节点类型为 `Concept / Formula / Property / Theorem / Example / Proof / Figure`，
  关系为 `has_formula / has_property / example_of / illustrated_by / prerequisite_of / similar_to / proved_by`，
  与当前 `Concept / Claim / Method` 语义层是两个不同的 schema，切勿混用。
- 相关历史实验记录（如 `docs/experiment_topk_memory_v7.md`、`docs/textbook_patterns_experiment_01.md`）
  仍保留在 `docs/` 下作为实验日志，不属本目录归档范围。
