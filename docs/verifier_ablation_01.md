# Verifier 复核通道消融实验

## 研究问题

在抽取完成后增加一个"批评家"阶段（借鉴 VoG ICLR 2026 / Graph Counselor ACL 2025 的验证-修正闭环）：用便宜的 flash 模型逐节点校验 evidence 是否支持抽取内容，输出 0/1 信号，对被标记 chunk 用 pro 模型 + guidance 重抽修正。能否稳定提升节点抽取质量（Gold F1）？

**结论：不能。verify 轮 3 轮平均 F1 .6252，低于 medium 基线 .8329 约 -0.21。原因不是 verifier 检查本身（它准确标记了真实错误），而是修正路径的设计缺陷：被标记节点从不被删除，修正只会新增重复节点。**

## 冻结快照

| 项目 | 值 |
|---|---|
| Branch | `codex/semantic-layer-experiment`（worktree） |
| agents/Extractor.py SHA256 | `7b4e0ee4bffb8bc524fe50ba58d301b088c14092e5ad13e9974c42d6630142aa` |
| prompts/extraction_system.txt SHA256（运行时记录） | `c6559c044d9b15632feb9e27379b6e1c2d903177417d7a784c689a8166c3562f` |
| prompts/extraction_verify.txt SHA256 | `f6587f98c693aab6814624a6bacb2d9a87f1df5279b1775639459126771cc803` |
| Gold v2（第三节）SHA256 | `7e28e7391f74d2c728c87e4d8c6327cb3aa3f30c17cb6454150570c150cb817d` |
| 抽取模型 | `deepseek-v4-pro`，thinking=enabled，reasoning_effort=medium |
| Verify 模型 | `deepseek-v4-flash`，thinking=disabled，temperature=0.1 |
| Chunk strategy | `teaching-units`（7 chunks，跳过 3 组例题序列） |
| Max tokens | 12000 / verify 4000 |
| Resume | disabled |

注意：消融文档 `thinking_effort_ablation_01.md` 中冻结的 prompt 哈希 `90c123d3` 与各轮输出的 `run_config.system_prompt_sha256`（`c6559c04`）不符，属记录错误；以运行时记录为准，消融与本次实验 prompt 一致。

## 第三节实验结果

### 执行记录

| Run | Verify 标记 | 修正 chunk | Nodes | Rel | F1 | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|---|---|---|---|---|
| r1 | 0 / 42 | 0 | 17 | 25 | .6286 | .6471 | .6111 | 11 | 6 | 7 |
| r2 | 4 / 43 | 3 (002/004/005) | 21 | 24 | .6154 | .5714 | .6667 | 12 | 9 | 6 |
| r3 | 2 / 43 | 1 (005) | 20 | 23 | .6316 | .6000 | .6667 | 12 | 8 | 6 |
| **verify 均值** | | | | | **.6252** | | | | | |
| 对照（无 verify，同 extractor） | — | — | 16 | — | .8824 | .9375 | .8333 | 15 | 1 | 3 |
| 基线（消融 medium 均值，n=3） | — | — | 16.3 | — | .8329 | .8745 | .7963 | | | |

复现命令（对照轮与 verify 轮均需 `--no-resume`，否则会命中 chunk 缓存）：

```powershell
& 'E:\MaF-KG\.venv\Scripts\python.exe' agents\Extractor.py `
  --input 'E:\MaF-KG\data\raw\第一章第三节.md' `
  --output 'KG\verifier_ablation\section_03\verify_r1.json' `
  --model deepseek-v4-pro --chunk-strategy teaching-units `
  --chunk-context-before-lines 6 --chunk-context-after-lines 4 --verify --no-resume
```

## 三个关键发现

### 1. Verifier 检查本身准确（诊断价值成立）

flash 标记的 4 类问题全部属实：
- `concept_函数极限的唯一性` / `concept_函数极限的局部有界性`：证据是定理（Claim），却抽成了 Concept——**类型错误**，且 r2/r3 独立标记了**完全相同的节点 ID**（名称哈希一致），说明是模型稳定复发的错误模式；
- `claim_自变量趋于无穷大时函数极限的_X定义`：证据是定义，抽成了 Claim——定义属于 Concept description，不该是独立 Claim；
- `concept_定义`（"ε-δ 定义"）：自命名，教材表述为"定义1"。

与教材组织规律研究的预测一致：定理节（"定理1/2/3"句式）产出 Claim 类内容，定义节（"就称为"句式）产出 Concept 类内容，模型混淆了这两类。

### 2. 修正路径无法修复类型错误（核心缺陷）

`_correct_result` 只把重抽结果 merge 进 stores，**从不删除被标记节点**。类型错误类标记（Concept ↔ Claim）重抽后得到不同 identity 的新节点，旧节点原样保留：

- r2：4/4 被标记节点留在最终结果；新增 `claim_函数极限的唯一性定理` 与 `concept_函数极限的唯一性` **并存**；
- r3：2/2 同样保留并重复。

结果：修正把 FP 从 6 推到 9（r2）、8（r3），精度被重复节点拖低。修正路径需要"删除被标记节点及其孤立关系后再 merge"的 replace 语义才能生效。

### 3. r1 低分与 verify 无关（抽样噪声 + 命名漂移）

r1 标记 0 项、无修正，但 F1 仍只有 .6286。同 extractor 同 prompt 的对照轮 .8824，证明 extractor 版本变化（消融 1ce022a0 → 本次 7b4e0ee4，含 verify 代码）对抽取路径行为中性。r1 的低分来自：
- 命名漂移：抽成"函数在点x₀处的极限"，不在 gold `accepted_names` 内（gold 收录的是"函数当x→x₀时的极限"等 8 个别名）→ 按 FN+FP 严格计分；
- 漏抽 去心邻域 / 水平渐近线 / 函数极限与趋近点处的函数值无关。

gold 匹配基于 curated 别名表，同名概念的不同表述（教材"记号变体"模式：也可记作/也可写为）会造成 ±0.2 量级的 F1 波动——这是比 verify 更大的评分噪声源。

## 关系稳定性

pairwise gold_aligned Jaccard：r1 vs r2 = 0.0，r1 vs r3 = 0.0，r2 vs r3 = 1.0。与迭代 08 结论一致：关系集合跨轮不稳定，r1 与 r2/r3 完全不对齐。关系 Gold 未完成前仍不能报告关系 P/R/F1。

## 最终判断

1. flash verifier 是可靠的**类型/证据检查器**（标记全部属实、跨轮稳定），作为诊断前置（人工审查前先看 flagged 列表）有明确价值；
2. 作为修正器，当前实现**明确有害**：被标记节点不删除、只新增重复 → FP 上升 → F1 下降 -0.2；
3. 修正路径必须改为 replace 语义（删除 flagged 节点 + 孤立关系后合并重抽结果）才有希望；
4. 命名规范化（gold 别名覆盖 / 抽取器 canonical 命名）是比 verifier 更大的杠杆；
5. 3 轮 vs 3 轮基线统计功效有限（同消融实验限制），但 r2/r3 的 FP 上升机制是确定性的，不依赖统计。

## 本地产物

- `KG/verifier_ablation/section_03/verify_r1.json` / `verify_r2.json` / `verify_r3.json`（--verify）；
- `KG/verifier_ablation/section_03/control_noverify_r1.json`（对照，无 --verify）；
- `prompts/extraction_verify.txt`（verifier 系统提示词）；
- `tests/test_extractor_core.py::VerifierTests`（9 个测试）。
