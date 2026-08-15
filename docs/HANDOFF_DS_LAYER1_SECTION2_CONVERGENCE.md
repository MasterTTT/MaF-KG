# Handoff：第一层第二节语义图收敛

## 当前工作区

- Worktree：`E:\MaF-KG\.worktrees\stable-main`
- Branch：`codex/layer1-coverage-baseline`
- 最新稳定基线提交：`816d602c`
- 当前改动尚未提交
- KG 实验输出不进入 Git

请不要在脏的 legacy root 上开发，也不要回滚与本任务无关的迁移和脚本改动。

## 当前结论

第二节节点收敛目标已经实现。最佳无缓存运行得到：

- 11 nodes
- 12 relations
- 3 definition representations
- 0 isolated nodes
- Node Gold P/R/F1 = 1.0/1.0/1.0
- 关系正例 11/12，唯一关系类型错误见下文

最佳输出仅作实验审查：

`E:\MaF-KG\KG\第一章第二节_semantic_v1_7_0_opencode_flash_low_final_v2.json`

完整实验记录：

`docs/layer1_section2_convergence_01.md`

## 已完成的代码

### `agents/Extractor.py`

- Schema 升级到 `maf-kg.semantic.v1.7.0`
- Claim-only `logical_form`
- logical_form 贯穿 memory、create/reuse、seed、final serialization 和审计
- `--logical-form-review` 定向复查
- `_prune_dominated_involves` 稀疏关系约简
- 例题后 exposition restart 边界修复
- OpenCode API key 默认读取 `OPENCODE_API_KEY`
- cache 目录升级为 `semantic_layer_v1_7_0`

### Prompts

- `prompts/extraction_system.txt`：收紧节点准入、状态聚合、组件词、例题观察、重复逆否和方法命名
- `prompts/logical_form_review.txt`：独立英文复查提示词
- `prompts/extraction_verify.txt`：支持 logical_form 审核

### Gold

- `evaluation/gold/semantic_first_three_sections_v4.json`：第二节节点契约已按 11 节点目标更新
- `evaluation/gold/relations_section2_v1.json`：12 条 reviewed positive relations，`closed_world=false`
- `docs/relation_gold_section2_candidates_01.md`：关系判定依据与排除项

## 不得回退的设计约束

1. 例题保持跳过；不得抽例题步骤、局部公式、局部结论和“这个例子说明”的观察。
2. 例题结束后的独立“注意/一般地/方法总结”必须恢复为 exposition，不能随例题一起吞掉。
3. 不增加 NormalizerAgent、VerificationAgent 或 SemanticLinkingAgent 来掩盖上游问题。
4. logical_form 复查是 Extractor 内部小步骤，只能更新既有 Claim。
5. 不添加 schema fallback、自动类型矫正或静默容错；模型 JSON 错误必须 fail-fast。
6. 不硬编码第二节节点名黑名单；规则必须跨章节泛化。
7. Definition 不是节点；定义内容进入 Concept description。
8. 必要非充分信息进入原 Claim 的 logical_form，不生成重复 Claim。
9. 派生 Claim 已通过 `derived_from` 获得语义上下文时，删除继承性的重复 `involves`。
10. 当前主线使用 OpenCode Go + `deepseek-v4-flash` + thinking low。

## 首要待办

### P0：稳定 `special_case_of` 与 `defined_in_terms_of` 边界

最佳运行把：

```text
子数列 --defined_in_terms_of--> 数列
```

抽成了错误类型。Gold 是：

```text
子数列 --special_case_of--> 数列
```

原因：子数列本身仍是一种数列，满足严格 subsumption；`defined_in_terms_of` 只用于定义依赖但不是子类型的情况。

Prompt 已写明“真子类型优先 special_case_of”，所以不要继续堆同义句。下一步应先做跨节错误样本审计，判断这是模型随机波动，还是某类“从原对象抽取得到新对象”的稳定混淆。不要写“子数列”专用规则。

### P1：为关系正例 Gold 增加 evaluator 支持

当前 `scripts/evaluate_kg.py` 的 `relation_metrics` 固定为 null。建议增加可选 `--relation-gold`：

- 先用节点 Gold 把模型节点对齐到 gold_id
- 计算 reviewed positive relation recall
- 单独报告 wrong_type、wrong_direction、missing_endpoint
- 因 `closed_world=false`，不要计算严格 precision/F1

### P2：跨章节泛化验证

第二节已经收敛，但 Prompt 规则尚未在第一、三、四节重新验证。保持模型、thinking、chunk、memory 和 no-resume 不变，依次检查：

- 辅助术语是否被过度压制
- Method 是否稳定使用数学术语而非口语动作名
- 状态 Concept 合并是否误伤独立教学主题
- derived_from 约简是否保留派生 Claim 新引入的核心 Concept

## 已知失败样本

一次运行在 chunk 5 返回：

```json
"conclusion": ["数列 {x_n} 的极限唯一"]
```

而 schema 要求字符串。Pydantic 正确拒绝并终止，debug 文件位于 worktree 的 `KG/_debug`。这是模型格式错误，不是代码 bug，不要添加 list-to-string fallback。

## 测试状态

```text
144 passed, 8 subtests passed
```

继续工作前先运行：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q
```

## 审查建议

后续 DS 完成修改后，应把以下内容交回 Codex 审查：

1. `git diff --stat` 和关键 diff
2. 全量测试输出
3. 无缓存运行命令和输出文件路径
4. 节点逐项列表、关系逐项列表
5. 节点 Gold 指标
6. 关系正例 recall 与 wrong_type/wrong_direction 明细
7. 是否出现新 fallback、硬编码或额外 Agent
