# Thinking Effort Ablation: DeepSeek V4 Pro medium vs max

## 研究问题

在 DeepSeek V4 Pro 上，将 `reasoning_effort` 从 `medium` 提高到 `max`，能否稳定且实质性地改善节点抽取质量（以 Gold F1 为主要指标）。

**结论：不能。Max 未通过阶段门槛（F1 差 -0.02 < 0.05 阈值，成功率 67% < 100%），不推荐默认使用 Max。**

## 冻结快照

| 项目 | 值 |
|---|---|
| Branch | `codex/semantic-layer-experiment` |
| Base commit | `3cfb57ac04cc3cf60e39d87f3c4c4199f2b83771` |
| agents/Extractor.py SHA256 | `1ce022a09c4b4862676a8c02ae2d210af02bba27f801470721a19013e389af06` |
| prompts/extraction_system.txt SHA256 | `90c123d3c57d0cb7dfb6b3262ad9139fb8e3759f46ca90b8e9ca161fd7e5b6cc` |
| Gold v2 (第三节) SHA256 | `7e28e7391f74d2c728c87e4d8c6327cb3aa3f30c17cb6454150570c150cb817d` |
| 实测模型 | `deepseek-v4-pro` |
| Chunk strategy | `teaching-units` |
| Max tokens | 12000 |
| Max retries | 4 |
| Request delay | 8s |
| Memory node bytes | 12000 |
| Memory relation bytes | 6000 |
| Thinking | enabled |
| Proxy | disabled |
| Resume | disabled |

唯一变量：`--reasoning-effort medium | max`

## 第三节实验结果

### 执行记录

| Run | Group | Status | Nodes | Concept | Claim | Method | Rel | Expr | F1 | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| r1 | medium | ✅ | 17 | 10 | 7 | 0 | 25 | 7 | .9143 | .9412 | .8889 | 16 | 1 | 2 |
| r2 | medium | ✅ | 15 | 8 | 7 | 0 | 17 | 7 | .7273 | .8000 | .6667 | 12 | 3 | 6 |
| r3 | medium | ✅ | 17 | 9 | 8 | 0 | 24 | 7 | .8571 | .8824 | .8333 | 15 | 2 | 3 |
| r1 | max | ✅ | 17 | 9 | 8 | 0 | 19 | 5 | .6286 | .6471 | .6111 | 11 | 6 | 7 |
| r2 | max | ✅ | 17 | 9 | 8 | 0 | 20 | 11 | .9143 | .9412 | .8889 | 16 | 1 | 2 |
| r3 | max | ❌ truncation | — | — | — | — | — | — | — | — | — | — | — | — |

### 汇总统计

#### Medium (n=3)

| 指标 | Mean | Std | Min | Max |
|---|---|---|---|---|
| Precision | .8745 | .0580 | .8000 | .9412 |
| Recall | .7963 | .0950 | .6667 | .8889 |
| **F1** | **.8329** | .0824 | .7273 | .9143 |
| Node count | 16.3 | 0.9 | 15 | 17 |
| Success rate | **3/3 (100%)** |

#### Max (n=2, r3 failed)

| 指标 | Mean | Std | Min | Max |
|---|---|---|---|---|
| Precision | .7941 | .1471 | .6471 | .9412 |
| Recall | .7500 | .1389 | .6111 | .8889 |
| **F1** | **.7714** | .1429 | .6286 | .9143 |
| Node count | 17.0 | 0.0 | 17 | 17 |
| Success rate | **2/3 (67%)** |

### Token 成本

| Group | Mean Total Tokens | Mean Prompt | Mean Completion | Mean Reasoning | Reasoning Ratio |
|---|---|---|---|---|---|
| medium | 83,719 | 53,424 | 30,858 | 25,571 | 82.9% |
| max | 105,859 | 53,972 | 51,887 | 47,209 | 91.0% |

**Max 多消耗 26.4% total token，其中推理 token 多 84.6%。有效输出 token（completion - reasoning）medium 平均 5,287 vs max 平均 4,678。**

### 人工检查

- **隐式节点**：两组均未产生教材未表达的隐式推断节点。
- **过细节点**：max_r1 出现"绝对值不等式与区间不等式等价"作为独立 Claim，属于过细拆分；medium 各轮均无此问题。
- **例题污染**：两组均未将例题内容抽成知识节点（teaching-units 分块正确过滤了例题块）。
- **遗漏**：max_r1 遗漏了"函数极限的局部保号性"（Gold Claim），这是 max 中最低 recall 的主要原因。
- **Max 是否只增加节点而不增加正确节点**：max 各组节点数一致（17），但 max_r1 的 FP=6 远高于 medium 平均 2.3——增加的节点偏误而非偏对。

### 阶段门槛判定

| 条件 | 要求 | 实际 | 通过 |
|---|---|---|---|
| max avg F1 - medium avg F1 ≥ 0.05 | ≥ .05 | -.062 | ❌ |
| max avg precision 下降 ≤ 0.03 | ≥ .845 | .794 | ❌ |
| max 成功率 ≥ 2/3 | ≥ 2 | 2 | ✅ |
| max 失败率 ≤ medium 失败率 | ≤ 0% | 33% | ❌ |
| max 无明显增加过细/隐式节点 | — | max_r1 有过细 | ❌ |

**5 项条件中仅 1 项通过。Max 未达到阶段门槛。**

## 第四节

不执行。Max 在第三节未展示稳定收益。

## 客观结论

1. **Max 不能稳定改善节点抽取质量**：2/3 成功率，F1 方差是 medium 的 2.5 倍（σ=0.14 vs 0.06）。
2. **Max 的成本增量没有对应回报**：+30% token（+93% 推理 token），但有效输出 token 不变，F1 反而略低于 medium。
3. **Max 的高方差是工程风险**：max_r1 F1=0.63 明显差于最差的 medium 轮次（0.73），说明 max 可能产生反效果。
4. **DeepSeek V4 Pro 的 reasoning_effort=medium 已饱和**：增加推理链长度并不转化为更准确的节点识别。提示工程（system prompt + evidence 约束）可能是更有效的改进方向。

## 局限性

- 仅测试一个章节（第三节，398 行），结论可能不泛化。
- max_r3 因 truncation 失败（12K token 不足），可能低估了 max 的成功率（如果提高 max_tokens）。
- 仅一个 provider/model 组合，不同后端可能有不同行为。
- 样本量小（n=2-3），无法做统计显著检验。

## 输出路径

```
E:\MaF-KG\KG\thinking_effort_ablation\section_03\
  medium_r1.json  (F1=.9143)
  medium_r2.json  (F1=.7273)
  medium_r3.json  (F1=.8571)
  max_r1.json     (F1=.6286)
  max_r2.json     (F1=.9143)
  max_r3: truncation 失败，无输出文件
```
