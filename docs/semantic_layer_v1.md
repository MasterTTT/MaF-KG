# 第一层数学语义图实验（semantic.v1）

> 本文记录 2026-08-02 首轮基线设计。当前实验分支已迭代到
> `maf-kg.semantic.v1.1`；首轮产物保存在本地，不纳入 Git。

## 1. 实验目标

本分支验证一个比原始教材实体图更紧凑的第一层图谱：从教材中抽取可复用的数学语义，而不把教材的定义、定理、证明、例题和图示结构直接复制成图节点。

该层只回答三个问题：教材讲了什么数学对象、明确陈述了什么一般结论、明确教授了什么可复用方法。第二层再按教材的中心教学目标将这些语义组织成 `KnowledgeUnit`；原子化的多前提推理图暂不在本实验中构建。

## 2. Schema

### Nodes

| 类型 | 边界 |
| --- | --- |
| `Concept` | 可跨局部段落复用的数学概念、命名对象或类别；定义浓缩进 `description` |
| `Claim` | 教材明确陈述、可判真假的一般性数学结论，包括定理、性质、推论、判据和恒等规律 |
| `Method` | 面向一类问题、具有操作过程和预期结果的可复用方法 |

定义、定理、性质、推论、证明、例题、习题、图示、章和节均视为教材话语角色，不是本层节点类型。

### Relations

| 关系 | 方向 | 含义 |
| --- | --- | --- |
| `involves` | Claim -> Concept | 结论明确涉及的核心概念 |
| `uses` | Method -> Concept/Claim/Method | 方法明确使用的概念、结论或子方法 |
| `establishes` | Method -> Claim | 方法明确用于建立的结论 |
| `derived_from` | Claim -> Claim | 前一结论由后一结论明确导出 |
| `equivalent_to` | Claim <-> Claim | 教材支持的严格逻辑等价 |
| `special_case_of` | Concept -> Concept | 前一概念是后一概念的明确特例 |

本层不抽取 `prerequisite_of`、`similar_to` 或通用 `related_to`，也不自动补传递边。

### MathExpression

`Formula` 不再是语义节点。LaTeX 作为语义节点拥有的原文辅助记录保存：

| role | owner |
| --- | --- |
| `definition_representation` | Concept |
| `claim_representation` | Claim |
| `method_template` | Method |

LaTeX 只能引用程序从 Markdown 中识别出的原文 candidate，不允许模型重写。例题、习题和证明中的局部表达式不进入 `math_expressions`。

## 3. 输出结构

```json
{
  "schema_version": "maf-kg.semantic.v1",
  "source_path": "...",
  "title": "...",
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "nodes": [],
  "relations": [],
  "math_expressions": [],
  "diagnostics": {}
}
```

节点保留 `id/type/name/aliases/description/evidence`；关系保留 `id/type/source_id/target_id/evidence`。不输出 `score`、`confidence`、`quality` 或类型专属 `content`。

## 4. 执行流程

LangGraph 工作流保持为单一顺序抽取器：

```text
load_document -> chunk_document -> extract_sequentially -> save_result
```

每个 chunk 从当前全局图检索 Top-K 节点及其一跳关系和数学表达，模型对当前实体显式选择 `create` 或 `reuse`，随后程序校验引用、关系签名、证据行与 LaTeX candidate，并把子图合并回全局图。缓存使用独立的 `semantic_layer_v1` 命名空间，不会读取旧 schema 的 chunk 结果。

## 5. 实验原则

- 有明确教材依据才抽取，没有就保持为空。
- Top-K memory 用于身份判断，不构成事实证据。
- 关系必须由当前 chunk 明确支持。
- 不从例题或证明局部过程提升一般性节点。
- 不把第一层提前拆成原子命题或多前提推理结构。

## 6. 第一节测试命令

在 `E:\MaF-KG\.worktrees\semantic-layer-experiment` 中运行：

```powershell
E:\MaF-KG\.venv\Scripts\python.exe -X utf8 -u agents\Extractor.py `
  --input "E:\MaF-KG\data\raw\第一章第一节_映射与函数.md" `
  --output "E:\MaF-KG\KG\第一章第一节_语义图_v1.json" `
  --provider deepseek `
  --model deepseek-v4-pro `
  --reasoning-effort medium `
  --thinking disabled `
  --no-proxy `
  --max-retries 4 `
  --request-delay 8 `
  --no-resume
```

首次对新 schema 做对照实验时使用 `--no-resume`。后续仅在输入、提示词、模型参数和 memory 快照完全一致时使用缓存重跑。
