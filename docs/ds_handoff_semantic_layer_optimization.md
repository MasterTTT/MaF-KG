# MaF-KG 开发 Handoff

## 工作环境

- 仓库：`E:\MaF-KG`
- 工作树：`E:\MaF-KG\.worktrees\semantic-layer-experiment`
- 分支：`codex/semantic-layer-experiment`
- 当前存在未提交修改，禁止 reset、checkout 或覆盖用户已有改动。
- 本轮不要提交或推送 Git。
- KG JSON、缓存和实验产物不要加入版本控制。

## 当前状态

项目正在实验教材语义层知识图谱抽取。当前节点主要为 `Concept / Claim / Method`，使用顺序 chunk 抽取、Top-K 图记忆和全局子图合并。

刚完成可选分块策略：

```text
--chunk-strategy budget|teaching-units
```

现有验证结果：

- 36/36 单元测试通过。
- `py_compile` 通过。
- `git diff --check` 通过。
- 自动 teaching-unit 分块提高了可审计性，但没有超过人工 span 的语义质量基线，因此必须继续保持 opt-in。

## 本轮目标

不要增加 Agent，不要修改分块策略，不要改 extraction system prompt，不要处理 Neo4j。

本轮只解决以下三个问题。

### 1. 修正关系稳定性评估

当前实验报告直接使用模型生成的节点名称比较关系，错误地把节点命名漂移计入关系漂移。

Gold 对齐端点后的关系 Jaccard 为：

```text
第三节：.65 / .50 / .45
第四节：.1429 / .40 / .375
```

原始名称口径为：

```text
第三节：.0769 / .0857 / .2903
第四节：.1000 / .1250 / .3333
```

要求：

- 在 `scripts/evaluate_kg.py` 中先利用现有 Gold 节点匹配结果，将预测关系端点映射到 Gold ID。
- 再计算关系集合 Jaccard 和多轮稳定边。
- 同时保留 `raw_name` 与 `gold_aligned` 两种指标，不能偷偷覆盖原始指标。
- Gold 尚未标注关系时，`relation_metrics` 必须继续为 `null`，不能把稳定性冒充 precision/recall。
- 在 `docs/semantic_layer_iteration_08.md` 中添加明确的口径修正说明，不删除历史原始数据。
- 添加对应单元测试。

相关位置：

- `scripts/evaluate_kg.py:682`
- `docs/semantic_layer_iteration_08.md:75`

### 2. 将 merge 改为严格、事务式合并

当前 `_merge_chunk_response` 遇到非法节点、关系或表达式时，会写入 `skipped_items` 后继续执行。这属于静默丢弃模型错误，与项目原则冲突。

要求：

- 默认情况下，任何模型输出的非法 node action、非法 reuse、identity conflict、非法关系端点、非法关系 signature、非法 evidence 或非法 expression 都必须使当前 chunk 失败。
- 禁止 fallback、忽略、自动修复和静默跳过。
- 合并必须是事务式的：先在临时 store 或 staging 数据中完整验证，全部合法后再一次性提交。
- 验证失败不能让 `node_store/relation_store/expression_store` 留下部分修改。
- 错误信息必须包含 `chunk_id`、item 类型、引用和具体原因。
- 保留原始响应及调试信息，便于复现。
- 不需要保留默认宽松模式；如确有审计需求，只能设计成显式 opt-in，且不能用于正式实验。
- evaluator 应把 merge failure、identity conflict 和 pending-unlinked diagnostics 明确呈现，不能将存在静默损失的结果称为 `is_structurally_clean=true`。
- 添加事务回滚及各种非法输出的测试。

相关位置：

- `agents/Extractor.py:2350`
- `agents/Extractor.py:2373`
- `agents/Extractor.py:2453`
- `agents/Extractor.py:2479`
- `agents/Extractor.py:2554`
- `scripts/evaluate_kg.py:431`

### 3. 精简 Top-K memory

当前发送给模型的 memory 包含完整 evidence、绝对路径、block 元数据、检索分数、检索原因和关系 evidence，负载过大。

要求模型可见的紧凑 memory DTO 只保留：

```text
NodeMemory:
- id
- type
- name
- aliases
- claim_kind
- short_description

RelationMemory:
- source_id
- relation_type
- target_id
```

其他要求：

- Retrieval score/reasons 可用于程序内部排序，但不要发送给模型。
- 不发送 occurrence/evidence、source path、heading path 和 relation evidence。
- 不再只依赖固定 `max_nodes=40/max_relations=60`。
- 增加序列化字符预算或可靠的 token 预算，在预算内按相关性选择节点和关系。
- 被选中关系的两个端点必须同时存在于 node memory。
- 输出顺序必须确定，保证相同输入产生相同 memory 和缓存指纹。
- 删除 user payload 中与 system prompt 重复且不需要动态变化的 schema 信息，但不要修改 system prompt。
- 更新缓存指纹和单元测试。

相关位置：

- `agents/Extractor.py:122`
- `agents/Extractor.py:2154`
- `agents/Extractor.py:2272`
- `agents/Extractor.py:2297`
- `agents/Extractor.py:2987`

## 明确不做

- 不增加 NormalizerAgent、VerificationAgent 或其他 Agent。
- 不修改节点或关系 schema。
- 不修改 `prompts/extraction_system.txt`。
- 不继续优化 teaching-unit 分块。
- 不实现跨文档 base graph。
- 不重构节点 ID。
- 不运行付费 API 实验。
- 不导入 Neo4j。
- 不写兼容性补丁或 fallback。

## 工程约束

- 使用 `apply_patch` 修改文件。
- 保持 UTF-8、无 BOM、CRLF。
- 不破坏中文内容。
- 不重写无关文件。
- 先阅读当前 diff，再基于已有未提交修改继续工作。

## 验收命令

```powershell
& 'E:\MaF-KG\.venv\Scripts\python.exe' -m unittest tests.test_extractor_core tests.test_evaluate_kg

& 'E:\MaF-KG\.venv\Scripts\python.exe' -m py_compile agents\Extractor.py scripts\evaluate_kg.py tests\test_extractor_core.py tests\test_evaluate_kg.py

git diff --check
```

## 最终汇报要求

完成后请报告：

1. 修改了哪些文件。
2. 关系稳定性的新旧口径及区别。
3. merge 如何保证原子性。
4. memory 精简前后的序列化字符规模。
5. 新增了哪些测试。
6. 所有验证命令的真实结果。
7. 尚未解决的风险。

不要提交 Git，等待人工审查。
