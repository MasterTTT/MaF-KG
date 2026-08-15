# Semantic Linking (v5)（已归档 / ARCHIVED）

> ⚠️ 本文描述已退役的 `maf-kg.agent.v5` `SemanticLinkingAgent` 设计。该模块已归档至
> `legacy/SemanticLinkingAgent.py`，其依赖 `agents.ConceptFormulaExtractor` 已不存在，
> 当前无法运行。活跃 schema 为 `maf-kg.semantic.v1.6.0`（Concept / Claim / Method）。

The v5 `SemanticLinkingAgent` works on compact published graphs and proposes conservative patch edges.

## Candidate Tasks

- `formula_linking` -> `has_formula`
- `property_linking` -> `has_property`
- `example_linking` -> `example_of`
- `figure_linking` -> `illustrated_by`
- `prerequisite_linking` -> `prerequisite_of`
- `similarity_linking` -> `similar_to`
- `proof_linking` -> `proved_by`

## Design Principles

- no new nodes
- candidate-pair based, not all-pairs exhaustive
- strict type-signature checks
- figure edges require explicit figure-reference support
- direct evidence preferred for accepted edges
- weaker pairs go to `review_edges` or `rejected_pairs`
- theorem/proof linking is conservative and only targets formal proof structure

## Patch Output

The patch keeps:

- `accepted_edges`
- `review_edges`
- `rejected_pairs`
- `coverage_report`
- `diagnostics`
