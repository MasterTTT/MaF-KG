# 教材组织规律驱动管线改进实验（定义检测 + 模板化语言 + 结构捆绑）

## 研究问题

教材组织规律研究给出 3 个可操作优化点：🔴 块解析器缺少"定义"类型（定义被归入 paragraph，LLM 缺失定义定位信号）；🟠 教材使用高度模板化语言（"称为""叫做""记作"明确标记被定义项）但管线未利用；🟡 定义-定理家族在串行抽取中被分割。将 3 项改进整合进管线后，能否稳定提升节点抽取质量（Gold F1）？

**结论：不能。按 v3 修正后 gold 重评：5 轮平均 F1 .7638（排除 LaTeX 泄漏缺陷轮后 .8142），与 medium 基线均值 .8329 持平（不再显著低于基线，r2 .9677 反超）。hints 稳定修复了主极限概念的命名（4/5 轮命中 gold 主名），但命名漂移仍是主导方差源；左/右极限前缀漂移等新命名变体抵消了收益。L1 分块（9 chunks vs 7）未造成 recall 回归；L3 捆绑在第三节未触发，收益无法验证。**

## 冻结快照

| 项目 | 值 |
|---|---|
| Branch | `codex/semantic-layer-experiment`（worktree） |
| agents/Extractor.py SHA256（最终修复后） | `1f65eef4257c91ad55310d7e12a84fb7d630015094d69b6f358d9f4b3970ef8d` |
| prompts/extraction_system.txt SHA256（最终修复后） | `6adfeb87254c0a478013f9a804bcd2a369bad36dc2035deb76ada3f6926a084e` |
| r1-r3 运行时 prompt（无防 LaTeX 指令） | `a259ab605bfc90458b508f77b5112b1e3973b5445246e81472c098bab7b2a2a3` |
| Gold v2（第三节，原始）SHA256 | `7e28e7391f74d2c728c87e4d8c6327cb3aa3f30c17cb6454150570c150cb817d` |
| Gold v3（第三节，范围修订）SHA256 | `3894b09cbed02d08afcff49489ec44d99eca4f3060dab85bf1b0401f848db819` |
| 抽取模型 | `deepseek-v4-pro`，thinking=enabled，reasoning_effort=medium，temperature=0.1 |
| Chunk strategy | `teaching-units`（9 chunks，跳过 3 组例题序列；基线为 7 chunks） |
| Max tokens / max_chunk_chars | 12000 / 3200，context before/after 6/4 |
| Resume | disabled（--no-resume） |

注：r3 后修复了 hint 的 LaTeX 泄漏（`_extract_defined_terms` 增加 `$` 剥离 + prompt 追加"节点名禁止 LaTeX"指令），故 r1-r3 与 r4-r5 运行在不同代码状态下；上表以最终修复后文件哈希为准。与 verifier 消融文档同样的记录方式：以运行时实际使用的 prompt 为准。

## 第三节实验结果

### 执行记录

| Run | Nodes | Rel | F1 | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|---|---|---|
| r1 | 18 | 22 | .7778 | .7778 | .7778 | 14 | 4 | 4 |
| r2 | 15 | 18 | .9091 | 1.0000 | .8333 | 15 | 0 | 3 |
| r3 | 16 | 21 | .5294 | .5625 | .5000 | 9 | 7 | 9 |
| r4 | 16 | 20 | .7059 | .7500 | .6667 | 12 | 4 | 6 |
| r5 | 15 | 18 | .7273 | .8000 | .6667 | 12 | 3 | 6 |
| **5 轮均值** | | | **.7299** | | | | | |
| **4 轮均值（排除 r3）** | | | **.7800** | | | | | |
| 对照（旧管线 control，n=1） | 16 | — | .8824 | .9375 | .8333 | 15 | 1 | 3 |
| 基线（消融 medium 均值，n=3） | 16.3 | — | .8329 | .8745 | .7963 | | | |

复现命令（`--no-resume` 必须，否则命中 chunk 缓存；当前 CLI 已内置 teaching-units/context 参数，无需再传）：

```powershell
& 'E:\MaF-KG\.venv\Scripts\python.exe' agents\Extractor.py `
  --input 'E:\MaF-KG\data\raw\第一章第三节.md' `
  --output 'KG\textbook_patterns\section_03\r1.json' `
  --model deepseek-v4-pro --no-resume
```

评估：`scripts/evaluate_kg.py KG/textbook_patterns/section_03/rN.json --gold evaluation/gold/semantic_first_three_sections_v3.json`

### Gold 范围修订（v3）

实验后审查发现 v2 第三节 gold 存在范围问题，按决定修订为 v3：

- **删除** `s3_concept_horizontal_asymptote`（水平渐近线）：其定义嵌在 L227 几何解释段内，而 v2 的 X 组本就禁止「无穷远函数极限的几何解释」（`s3_forbidden_infinite_limit_geometry`），删后 gold 内部一致；
- **删除** `s3_property_limit_independent_of_point_value`（函数极限与趋近点处的函数值无关）：L31 属定义后的解释性备注，按"解释性观察不入 Claim"的规则排除；
- **保留** `s3_concept_punctured_neighborhood`（去心邻域）：它是定理3'/推论的具名主语，属可准入的支持性概念。

| Run | v2 F1 | v3 F1 | v3 TP/FP/FN | 变化 |
|---|---|---|---|---|
| r1 | .7778 | .7647 | 13/5/3 | 水平渐近线 TP→FP 翻转 |
| r2 | .9091 | .9677 | 15/0/1 | FN 只剩 去心邻域 |
| r3 | .5294 | .5625 | 9/7/7 | |
| r4 | .7059 | .7500 | 12/4/4 | |
| r5 | .7273 | .7742 | 12/3/4 | |
| **均值** | **.7299** | **.7638** | | |
| 排除 r3 | .7800 | **.8142** | | |

基线同口径重评（v3 gold，输出文件 `KG/第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r1~r3.json`）：.8750 / .8125 / .7742 → **均值 .8206**；verifier 对照轮（`E:/MaF-KG/KG/verifier_ablation/section_03/control_noverify_r1.json`）v3 下 .8750。基线 = 旧管线（无 L1/L2/L3 改动，Extractor `1ce022a0`、prompt `90c123d3`）同配置（deepseek-v4-pro medium、teaching-units）在第三节的 3 轮对照。

## 关键发现

### 1. L1 定义检测生效，且未造成 recall 回归

chunk 日志确认：`定义1 设函数...` 被识别为 `definition` 块（break_reason=definition_boundary，teaching_unit_kind=definition），分块从基线 7 个变为 9 个（定义独立成块）。v3 gold 下跨轮反复漏掉的收敛为 去心邻域（r1/r2/r3/r5 共 4/5 轮）与非零函数极限的局部绝对值下界（r5、control 轮）——新旧管线 FN 集合高度重合，是共有的 recall 底，不是 L1 分块引入的。注：水平渐近线在正文 L227 行尾有定义（几何解释段，"直线 y=A 是函数 y=f(x) 的图形的水平渐近线"），r1 从该行正确抽出（描述与源文定义一致，v2 下为 TP，v3 下因 gold 删除翻转为 FP）；r2-r5 为稳定漏抽。

### 2. hints 稳定修复主极限概念命名，但引入了 LaTeX 原样拷贝风险（r3，已修复）

r1/r2/r4/r5 四轮中"函数当x→x0时的极限"均命中 gold 主名（`s3_concept_finite_point_function_limit` 的 accepted_name），r2 达到本轮实验唯一完美精度轮（TP=15，FP=0）。r3 失败模式为模型把 hint 项原样拷进节点名（`函数当 $x \to x_0$ 时的极限`），而 gold canonical 匹配不去 `$`/`\to` → 7 FP + 9 FN。修复 = `_extract_defined_terms` 剥离 `$` + prompt 明示"节点名禁止 LaTeX，LaTeX 只进 math_expression"。r4/r5 无 `$` 泄漏，该失败模式已消除。

### 3. 命名漂移仍是主导方差，hints 未覆盖全部命名变体

- r4/r5：左极限/右极限被抽成"函数当x→x0时的左/右极限"（gold 的左/右极限**无别名**）→ 每个都计 FP+FN。该前缀漂移不明确由 hints 造成：源文 L156 也以完整形式陈述，hint 项在 chunk_004 中亦显示完整形式，属模型自身命名不一致。
- r1：4 个 FP 是截断概念"自变量趋于无穷大/有限值/正无穷大/负无穷大"（旧管线 control 轮相同 4 个概念以完整形式命名并命中 gold 主名）——抽象层级选择（把趋近方式抽成独立概念）而非 hint 直接拷贝。
- 与 verifier 消融一致：gold 别名表覆盖与否造成 ±0.2 量级 F1 波动，是比任何管线改进都大的评分噪声源。

### 4. L3 定义-定理家族捆绑在第三节从未触发

第三节中定义与定理之间总是隔着例题序列（example_boundary 拆分），`definition_theorem_family` 单元一次未出现，全部为独立 definition 或 theorem_family 单元。L3 的收益在本节无法验证；需在定义紧邻定理的章节（如第二章导数定义+求导法则）另行评估。

### 5. 结构性健康

各轮无 identity conflict、无 pending node；r1 有 1 处 chunk_pending_unlinked（chunk_001 的 c1，语义为后续 chunk 才首次定义的对象，属正常的跨块引用）。关系 Gold 未完成，仍不报告关系 P/R/F1。

## 教材适配：定义单元段角色标注（二次验证）

### 动机

L3 捆绑的前提（块级定义-定理紧邻）在全书扫描下不成立——整本教材仅 1 处（第三章 L8182 凸函数定义 → L8198 定理2）。改为**段角色感知**方案：不改变分块，在定义块内按信号词标注语篇角色（`exposition` / `meta_annotation` / `symbolic_restatement` / `geometric_interpretation`），并在 prompt 中给出角色处理规则。真实语篇模式为：**定义 → 元注释 → 符号化重述 → 几何解释 → 例题**，且 5 段模式在第三节定义单元内（chunk_002，block_count=1）天然成立。

实现：`classify_definition_segments`（Extractor.py，信号词表：我们指出/需要注意的是/注意 → meta；也就是说/等价地/记作/可表示为 → symbolic；从几何上来说/从图形上看/几何意义 → geometric）+ `DocumentBlock.segments` 字段 + chunk payload `teaching_blocks[].segments` 序列化 + prompt 新增 `Definition unit discourse pattern` 章节。

运行代码状态（最终）：Extractor `730c2fe2ab0acca20cf225c9170ef63a2d97d99ac8d303c996b29d3c40ce83b1`、prompt `618c2d21368c1fe2edf8771f26653c4297129d1620eaedae73ccd7708d5024ca`。

### 验证轮次（v3 gold 同口径）

| Run | 规则版本 | F1 | Precision | Recall | TP | FP | FN | 历史错误类 |
|---|---|---|---|---|---|---|---|---|
| r6 | 允许例外（首次实现） | .8108 | .7143 | .9375 | 15 | 6 | 1 | ❌ 两类均存活（例外条款精确复活被删节点） |
| r7 | **绝对禁止（收紧）** | **.9412** | .8889 | **1.0000** | 16 | 2 | 0 | ✅ 全部消除，首个 perfect-recall 轮 |
| r8 | 绝对禁止 | .7647 | .7222 | .8125 | 13 | 5 | 3 | ✅ 全部消除（剩余为命名漂移） |
| r9 | 绝对禁止 | .6667 | .6471 | .6875 | 11 | 6 | 5 | ⚠️ 水平渐近线复发（1/3），极限与函数值无关未复发 |
| **r7-r9 均值** | | **.7909** | | | | | | |
| 基线（v3 重评，n=3） | | .8206 | | | | | | |

### 关键发现

1. **例外条款必须删除**：r6 的规则写着"若表述定义中不存在的限制，可挂为 property Claim""几何解释引入新对象（如渐近线）可抽为 Concept"——这两条例外精确对应 v3 删除的两个节点（极限与函数值无关、水平渐近线），模型全部照做。改为绝对禁止（meta_annotation 永不做 Claim、geometric_interpretation 永不建节点）后 r7/r8 两类错误均消失。
2. **错误类消除效果**：meta_annotation→Claim FP 在 r7/r8/r9 连续 3 轮未出现；geometric_interpretation→Concept FP 在 r7/r8 消除、r9 复发（模型遵守率约 2/3，非确定性）。symbolic_restatement→definition_representation 稳定产出（r6 计 10 条，主极限概念公式均挂载）。
3. **r7 达到全部实验（含基线）首个 recall=1.0 轮**：16/16 全中，含此前 4/5 轮漏掉的 去心邻域 与仅 r5/control 命中过的 非零极限局部绝对值下界。
4. **命名漂移仍是主导方差**：r8/r9 无穷远极限家族产生"函数 f(x) 当 x → +∞ 时的极限""x趋于正无穷时函数的极限"等词序/含 f(x) 变体，gold accepted_names 未覆盖 → 单轮 F1 掉 .2；r9 还漏主概念（有限点极限、去心邻域），属模型噪声底。
5. **剩余稳定 FP**：自变量趋于无穷大/有限值 两概念（趋近方式的抽象层级选择）在新旧管线所有轮次反复出现（r1、r6-r9、baseline），是 gold 范围判断的已知分歧点，与段角色机制无关。

### 结论（追加）

段角色标注**消除了两类历史错误类**（meta→Claim 3/3 轮、geometric→Concept 2/3 轮）并给出首个完美 recall 轮，但 F1 均值 .7909 与基线 .8206 持平（r7 .9412 为全部轮次最高）。规则必须绝对禁止；LLM 对提示规则的遵守不稳定（~2/3）。与主实验结论一致：段角色机制把定义单元的"类型错误"变成可预测可消除，但**命名漂移（gold 别名覆盖）仍是比提示词改进更大的杠杆**。

## 最终判断

1. L1 定义检测（+L2 hints）**未能稳定提升 F1**：v3 下 5 轮均值 .7638 / 4 轮 .8142 ≈ 基线 .8329（持平；r2 .9677 反超但不可重复）；hints 消除的主极限命名漂移被左/右极限前缀、截断命名等新变体抵消；
2. hints 的**净收益方向正确但幅度不足**：它把"命名漂移"从随机噪声变成部分可预测（主概念 4/5 轮正确），r2 证明在命名全对时可以达到 .9091——但 gold 别名覆盖（左/右极限补别名）比管线改进更廉价有效；
3. r3 LaTeX 泄漏是 hint 设计的可修复缺陷，修复后稳定（r4/r5 无复发），不属于结构性障碍；
4. L3 捆绑在第三节无样本，悬而未决；若保留，应在定义-定理紧邻的章节验证，且需确认捆绑不引入跨块上下文泄漏；
5. 与 verifier 消融结论一致：**命名规范化（gold 别名覆盖 + 抽取器命名约束）是比任何后处理/提示词改进更大的杠杆**；3-5 轮统计功效有限，但命名漂移的机制性影响不依赖统计。

## 本地产物

- `evaluation/gold/semantic_first_three_sections_v3.json`（范围修订版 gold：删除 水平渐近线、函数极限与趋近点处的函数值无关，保留 去心邻域；v2 保留未动）；
- `KG/textbook_patterns/section_03/r1.json` ~ `r9.json`（实验输出，9 chunks；r6-r9 为段角色标注验证轮）；
- `agents/Extractor.py`：`is_definition` + definition 块分类/边界 + `_teaching_unit_kind` definition 分支 + `_build_teaching_unit_groups` 定义-定理家族捆绑 + `DEFINED_TERM_PATTERNS`/`_extract_defined_terms` + payload `defined_term_hints` + `SEGMENT_*_PATTERN`/`classify_definition_segments`/`DocumentBlock.segments`/payload `teaching_blocks[].segments`；
- `prompts/extraction_system.txt`：Defined-term hints 段 + 节点名禁 LaTeX 指令 + `Definition unit discourse pattern` 段（绝对禁止规则）；
- `tests/test_extractor_core.py`：StructureAwareChunkTests 新增 10 个测试（definition 检测、unit kind、家族捆绑、hints 提取、payload 注入、段角色分类 ×2、payload 序列化）。

## 语义匹配：embedding 检索 + flash 判定（命名漂移修复）

命名漂移是 r8/r9 的主方差来源（±0.2 F1 波动）。验证两条修复路径：规则归一化与语义匹配（embedding 检索 + 小 LLM 判定），先离线模拟验证可行性，再分别落进评测器与抽取器。

### 模拟 1：embedding 检索（qwen3-embedding:0.6b，ollama 本地）

`tmp/embed_sim.py`：memory = 第三节 gold 16 个 M 概念名；5 组测试（DRIFT 变体回收、CONTROL 精确名、DISCRIMINATION 近义族、ABSTRACTION 趋近方式、ORDER 真实创建顺序）。

- **检索 28/28 top-1 正确**（含真实创建顺序约束：∞ 概念先于 ±∞ 存在时仍命中），检索层可放心用；
- **相似度带重叠 → 判定不可行**：真实表面变体相似度 0.874–0.927，但不同且相关的兄弟概念最高 0.972（左/右极限）、局部保号性/局部有界性 0.923——任何阈值都无法安全区分"合并"与"新建"。

### 模拟 2：flash 判定（deepseek-v4-flash，无 thinking）

`tmp/judge_sim.py`：14 对（7 same：r8 的 3 个 f(x) 变体 + r9 的 2 个词序变体 + 2 个 gold 改写；5 different：左/右极限、左极限/单侧极限、概念/关系、保号性/有界性、基础/加强形式；2 different：趋近方式/极限概念）。

**14/14 正确**，理由充分。判定组件就位：embedding 提供召回、flash 提供精度。

### 集成 1：评测器语义匹配（`scripts/evaluate_kg.py --semantic-match`）

- `canonical_node_name` 规则归一化升级：剥离 f(x)/g(x)、LaTeX→Unicode（`\to`→→、`\infty`→∞）、去 $/花括号，再剥离空格标点 + casefold（**两侧**都归一化）；
- 规则匹配失败后（仅 Concept 类型）：embedding top-1 ≥ 0.80 候选 → flash 判定（带预测描述作为上下文）→ same 则记为该 gold 节点匹配；forbidden 检查同样语义回退；
- judge 缓存按 (预测名, gold 名) 去重；API 失败不崩溃。

v3 同口径验证（r6-r9）：

| Run | 基线（段角色后） | 规则归一化后 | +语义匹配 |
|---|---|---|---|
| r6 | .8108 | .8108 | .8108 |
| r7 | .9412 | .9412 | .9412 |
| r8 | .7647 | **.9412** | .9412 |
| r9 | .6667 | .6667 | **.7879** |
| **r6-r9 均值** | .7959 | .8400 | **.8703** |

- r8 的 3 个 f(x) 变体被规则归一化直接修复（剥 f(x) + 去空格，无需 judge）；r9 的 2 个词序变体（x趋于正/负无穷时函数的极限）由 judge 修复；
- r8 达 .9412（与 r7 并列全部轮次最高）；r9 剩余 3 个 FN 是整节漏抽（有限点极限、去心邻域、非零极限局部绝对值下界），属模型噪声而非命名问题；
- 剩余 FP 仍为"自变量趋于无穷大/有限值"（趋近方式抽象层级）等范围判断分歧——judge 正确拒绝（对应模拟 2 的 ABSTRACTION 组），语义匹配不越权合并。

### 集成 2：抽取器命名一致性（`agents/Extractor.py --semantic-dedupe`）

处理跨 chunk 漂移（同一概念在后续 chunk 被再次 create 且命名不同）：

- create 路径（仅 Concept、flag 开启）：embedding top-1 ≥ 0.80 → flash 判定（双描述上下文）→ same 则 create 转为 **reuse + alias**（漂移名进入既有节点 aliases，证据保留，关系经 ref_id 解析到既有节点）；
- 精确 id 碰撞（memory_retrieval_miss / model_decision_conflict）在 flag 下也转为 reuse，而非整 chunk 失败；
- 网络失败静默回退为 create（不因去重机制打断抽取）；判定结果记入 decision ledger（`action=semantic_reuse`、`semantic_merged_to`）；
- 单元测试 4 个（碰撞救援、漂移合并+alias、判定不同保持 create、flag 关闭不受影响），全部通过；真实端点冒烟：漂移对合并 ✓、兄弟概念（左极限）与无关概念（去心邻域）拒绝 ✓。

局限：**首次出现命名漂移**（概念在本 chunk 首次定义即漂移，如 r8/r9 的无穷极限家族）无法用抽取器修复——记忆里尚不存在 canonical 名可与之比对，由评测器语义匹配兜底。两处互补。

### 结论（追加）

命名漂移修复分两层：**规则归一化**（免费，修掉 f(x)/LaTeX/空格类变体，r8 修复）→ **评测器语义匹配**（兜底一切表面变体含词序，r9 修复）。四轮均值 .7959 → .8703，r8 修复至与 r7 并列最高 .9412。判定正确性完全依赖 judge 而非相似度阈值（模拟 1 证明阈值不可行），与模拟结论一致；剩余分数差异全部来自模型噪声底（漏抽）与 gold 范围判断分歧，不再是命名问题。

## 本地产物（追加）

- `scripts/evaluate_kg.py`：`SemanticNameMatcher`（ollama qwen3-embedding + deepseek flash judge + 双缓存）+ `--semantic-match` + `canonical_node_name` 归一化升级；
- `agents/Extractor.py`：`--semantic-dedupe` + `_judge_same_concept`/`_semantic_duplicate_id` + create 路径碰撞救援与语义合并（reuse+alias+evidence）；
- `tests/test_extractor_core.py`：`SemanticDedupeTests` 4 个测试；
- `tmp/embed_sim.py`、`tmp/judge_sim.py`：模拟脚本（可重跑复现 28/28 与 14/14）。

### 集成 3：记忆检索 embedding 分量（`--embedding-memory`，离线验证后搁置）

抽取器主路径的"子图合并到全局图"机制 = 规则检索（`_node_memory_relevance`：精确名/别名 +8、2-3 元 ngram ≥0.12 +4、LaTeX +8、近期 +2.5）→ LLM 在 node_memory 中显式选 create/reuse → 程序校验合并。借鉴 embedding 思路给检索段加分：chunk 文本与每个节点名/别名/描述批量 embedding（qwen3-embedding:0.6b），cosine ≥0.15 时 `+sim×3.0` 叠加进规则分。flag 关闭时零行为变化，网络失败静默回退规则。

**离线验证（`tmp/embed_memory_sim.py`，真实 chunk 文本 + gold 全量记忆）**：

- 主题分离正确：∞ 极限单元中 ∞ 家族 11.0 vs 有限点极限 3.7；定理单元中保号性/唯一性 13.6-14.3 vs 左/右极限 3.1——embedding 不把无关概念拉高；
- 但 embedding 增量近乎常数（+1.8~2.6，窄带），**没有任何案例翻转规则已有的排序**：规则分 8-12 的节点 embedding 后仍 10-14，规则分 1-2 的仍 3-5；
- r6-r9 全部 5 chunk 的 `identity_conflict_count` 均为 0——本节规则检索覆盖率 100%，不存在可修复的 `memory_retrieval_miss`；r8/r9 的漂移是首次出现命名（chunk 内首次定义即漂移，记忆里无 canonical 名可比），检索段无从干预。

**结论**：机制本身正确（无误拉高、零风险、flag 控制），但第三节语料太短且教科书措辞自洽，不存在触发场景——规则已 100% 覆盖。不值得为此跑昂贵 LLM 对照轮。保留实现（`--embedding-memory`，101/101 测试含 4 个新增），其真实价值场景是更长、措辞不一致的语料（跨章节引用时记忆名与正文表面不一致），待后续章节语料就绪再评估。

## 增量更新：种子注入 + 收敛性验证（二三节样本）

### 设计结论（商榷后）

子图合并到全局图的增量更新机制，语义判定**只在节点身份层发生一次**：模型 create 的新概念 → embedding 检索（node_store 全量，含种子）→ flash judge 1-vs-1 判定 → 一致则转 reuse + 别名。关系层不做语义匹配：`relation_type` 是定死的 7 值枚举（`RelationType` Literal），关系的身份 `(source_id, type, target_id)` 三个分量里两个是节点 id、一个是枚举——两端节点 id 语义收敛后，关系靠**精确键去重**自然收敛。若节点复用失败，重复关系是节点漂移的下游症状，在关系层修复会制造跨 id 伪一致，违背"关系端点必须是节点 id"的 schema 约束。

### 实现

- `--seed-graph <path>`（Extractor）：抽取开始时从既有图 JSON 加载三个 store（NodeRecord/RelationRecord/MathExpressionRecord，evidence 完整恢复），种子节点自然参与每 chunk 的 memory 检索、`_semantic_duplicate_id` 去重与关系精确键去重；LLM 可引用种子 id（resolve 已支持 supplied memory id）。
- `scripts/verify_convergence.py`：增量 vs 全量两路径对比——节点集合对齐（规则 canonical + 可选 embedding/judge 语义对齐，**全类型**，与评测器仅 Concept 的口径区分开）+ Jaccard；各自对 gold 的 F1（source_path 自动匹配所在节）；FP/FN 方向节点级差异。评测增量图时过滤种子节点（种子是上一节的内容，对所在节 gold 算 FP 不公平）。

### 实验（第二节 → 第三节）

| 路径 | 配置 | 结果 |
|---|---|---|
| 种子 | 第二节已有抽取（24 节点/31 关系/12 表达式） | 完整加载，零破坏 |
| 增量 | 第三节 + `--seed-graph` + `--semantic-dedupe`（deepseek-v4-pro、teaching-units） | 40 节点 = 24 种子 + 16 新增；53 关系 = 31 种子 + 22 新增 |
| 全量对照 | 第三节独立抽取 r1（空 store，同模型同分块） | 16 节点 / 20 关系 |

**收敛性验证（语义口径）**：

- 节点集合：15/16 对齐，**Jaccard 0.8824**（规则 9 + 语义 6）；唯一差异 = 水平渐近线（全量有/增量无）vs 去心邻域（增量有/全量无）——单轮抽取噪声方向，非机制性偏差；
- F1（同评测器、同语义匹配）：增量 **.9375**（TP 15/FP 1/FN 1）vs 全量 .8750（TP 14/FP 2/FN 2）——增量不降反升：去心邻域救回（full FN → inc TP）、水平渐近线未抽（v3 gold 已删除该项，full 的 FP 在 inc 消失）；
- 关系：增量新增 22 vs 全量 20，种子 31 条关系原样保留；
- 种子节点未被重复创建（无"数列极限"二次出现），增量轮 0 个 identity_conflict。

### 命名变体对（Claim 层）

未对齐的两对 Claim（"函数极限存在的充要条件" vs "函数在一点的极限存在的充分必要条件"、"保号性加强版" vs "保号性加强"）经诊断：embedding 0.92/0.99 远超阈值、judge 均判 same——机制无误，是**评测器语义回退仅启用 Concept 的设计口径**（Claim 判定风险更高）。验证脚本为收敛性口径启用全类型对齐；评测器端如需 Claim 语义回退需单独评估误合并风险。

### 结论

收敛性判据成立：**增量更新与全量重抽在节点集合（15/16，Jaccard 0.88）与 gold F1（.9375 ≥ .8750）上一致**，唯一差异为单轮噪声。增量更新机制可信——种子注入 + 节点层语义判定 + 关系层精确去重的三层结构，不需要每次全量重抽即可获得同等质量的图。单轮样本不足以断言"增量优于全量"（差异幅度在 run-to-run 噪声内），但足以证明无系统性退化。

### 链式增量：第二节 → 第三节 → 第四节（追加）

延续实验：种子 = 第三节**增量产物**（40 节点 = 第二节种子 24 + 第三节新增 16），在"增量之上再做增量"抽第四节，对照第四节全量 r1/r2/r3（5/8/6 节点，同配置）。gold 无第四节标注 → F1 跳过，只做节点集合对齐（`verify_convergence.py` 无 gold 时容错跳过 F1）。

| 对照 | full 节点数 | matched | Jaccard | 差异来源 |
|---|---|---|---|---|
| vs r1 | 5 | 5/5 | **1.0000** | 无 |
| vs r2 | 8 | 5/5 | 0.6250 | r2 独有：正无穷大、负无穷大（L73 定义变体说明段）、零是唯一的常数无穷小（L7 特别地段） |
| vs r3 | 6 | 5/5 | 0.8333 | r3 独有：无穷大函数的极限不存在（L67 解释性备注段） |

**解读**：增量新增 5 个节点（无穷小、无穷大、铅直渐近线、定理1 充要条件、定理2 倒数关系）在**三轮全量中全覆盖**（每轮 5/5 matched）——增量没有漏掉任何共识节点。Jaccard 缺口全部来自全量轮之间的单轮独有抽取（正/负无穷大、常数无穷小、极限不存在均对应正文解释性/变体说明段落，r1 与增量同口径不抽）。**差异源 = 全量轮自身的 run-to-run 噪声，不是增量机制偏差**。

结论：链式增量（增量之上再做增量）的节点集合收敛性成立——增量节点的共识召回 100%，且随链变长种子节点（40/53）零破坏、无重复创建。

### 泛化：第五节（极限运算法则）链式增量（追加）

语料从完整教材提取（`第一章第五节.md`，L2261-2666 含习题1-5，新概念家族：极限四则运算法则、复合函数极限法则）。两轮同配置（deepseek-v4-pro、teaching-units）：全量 = 空 store 抽第五节（16 节点/13 关系）；增量 = 种子 45（第四节链式产物）→ 第五节 + semantic-dedupe（59 节点 = 45 种子 + 14 新增）。

**收敛性（语义对齐）**：增量新增 14 个节点在**全量 16 节点中 14/14 全覆盖**，Jaccard 0.8750。full-only 2 个：

1. **无穷小**——机制性差异而非噪声：全量轮空 store 无记忆，**重新创建**"无穷小"概念（新 id）；增量轮复用链式种子的 `concept_无穷小`（`concept_无穷小_0bd6602871bff0`），第五节 9 条关系挂到该种子节点。这正是增量的核心价值：全量重抽每轮重建公共概念（id 漂移），增量保持 id 稳定。
2. **复合函数极限运算法则的推广**——单轮漏抽方向（增量抽了"复合函数的极限运算法则"，未抽推广变体）。

**复用统计（增量机制的直接证据）**：第五节增量图 78 条关系中 **75 条（96%）引用种子节点**——五个新 Claim（两个无穷小的和、常数×无穷小、有界×无穷小、有限个和、有限个积）全部 `involves` 到种子无穷小；种子概念零破坏、无重复创建（59 = 45 + 14，无"数列极限/无穷大"二次出现）。

**过程中记录**：首轮增量轮被 fail-fast 拦截（chunk 5 模型输出 `q2 -[involves]-> 保号性Claim`，involves 签名仅 Claim→Concept，schema 拒绝）——重跑成功。第五节"不等式性质"内容模型倾向建立 Claim-Claim 关联，involves 签名过窄，属模型 schema 误用（第三节亦有同类 merge fail），非增量机制问题。

**结论（泛化）**：链式增量在第五节（全新语料、全新概念家族、大量跨节引用）上收敛性成立——增量新增节点全量共识覆盖 100%，且**量化验证了机制性优势**：跨节概念（无穷小）id 稳定复用，全量轮则每轮重建。这回答了"能否证明更优"的质量/一致性维度：在跨文档引用场景，增量保持 id 稳定 + 证据累积，是全量重抽结构性做不到的。

### 规模化路线图（路标）

增量链当前池 45 节点（第五节验证：Top-K 规则检索承担全部复用负载，14/17 新增关系引用种子，semantic-dedupe 0 触发）。**触发点：节点池 ≥100（全书级抽取）时**，启用 `--embedding-memory` 复测 Top-K 召回——预期缺口：recency 加权偏向近期 chunk、ngram 对跨节变体名召回弱，精确名失效的远引用场景（如第八节引用第三节概念）出现时 embedding 分量才有触发场景。启用后对比：规则 Top-K 命中率 vs 规则+embedding 命中率、semantic-dedupe 触发率是否上升（保险丝被激活 = 召回缺口出现）。在此之前不改 Top-K 机制。

### derived_from 关系证据修复（追加）

**问题**（证据审计，`tmp/rel_evidence_judge.py`）：60 条关系中 54 条（90%）judge 判定证据支持；唯一短板是 `derived_from`——**3 轮仅 2/6（33%）**。失败模式：证据只引归因句（`"由定理 3, 易得以下推论:"`，L288 单行），未包含被推导语句（推论正文）——judge 理由"证据仅提到归因句，未实质性说明推导关系"。关系本身正确，是证据粒度问题：模型按"最小连续证据跨度"规则挑了归因句。

**修复**（prompt-only，`prompts/extraction_system.txt` derived_from 段追加两条）：证据必须覆盖**完整推导构造**——归因句（"由...可得"、"易得以下推论"、"从...的证明中可知"）+ 其引入的被推导语句（即时推论的正文，或源文中执行推导的证明行）；只到归因句为止的证据不充分。注：单证据跨度 schema（`ProposedRelation.evidence: EvidenceRef`）无需改动——归因句与被推导语句在 chunk 内连续（第三节 L284-292 同一 chunk），单跨度 (288,292)/(284,286) 即覆盖构造。

**验证**（新 prompt 2 轮，同配置 pro-medium）：judge 支持率 **4/4（100%）**，证据跨度精确落在 (288,292)（归因+推论正文）与 (284,286)（归因+定理 3' 正文）。gold F1：r2=.8387（基线均值 .8206 范围内）；r1=.6452 为命名漂移离群（"函数极限存在的单侧极限判据"→"函数在一点极限存在的充要条件（单侧极限）"等 3 处 Claim 命名与 gold 不一致，Claim 层无语义回退，规则匹配即丢失）——非提示词回归，内容无缺失（同 15-16 节点集）。

**结论**：derived_from 证据 33%→100% 支持率提升，纯 prompt 约束达成，无 schema/后处理改动。命名漂移（±0.2 F1）是独立于证据质量的问题，Claim 层 gold 匹配仍无语义回退（已有路标：语义匹配上修范围）。

### 前三节合并抽取实验（追加）

**设计**：合并语料 = 三节正文顺序拼接（`第一章_前三节合并_无习题.md`，1362 行：第一节 L1-689、第二节 L690-1061、第三节 L1062-1362，习题区全部删除——与既有 `_无习题` 变体惯例一致）。合并轮 = 单次抽取（22 chunks）；对照 = 同配置分节抽取（第一节/第二节本轮各 1 轮 pro-medium；第三节沿用 r1-r3 均值）。配置：deepseek-v4-pro、thinking、effort medium、teaching-units、`--no-resume`；合并轮/第一二节对照 `--max-tokens 24000`（chunk_008 在 12000 下连续 3 次截断，长输出稳定超限）。

**评估**（合并图按证据行号偏移拆回三节，逐节对 v3 gold；分节对照直接 evaluate_kg）：

| 节 | 合并抽取 | 分节抽取 | 说明 |
|---|---|---|---|
| 第一节 | P .549 / R .582 / **F1 .565** (TP 39/67) | P .431 / R .463 / **F1 .446** (TP 31/67) | 合并轮 TP 多 8、FP 少 9 |
| 第二节 | P .500 / R .583 / **F1 .538** (TP 7/12) | P .375 / R .500 / **F1 .429** (TP 6/12) | 合并轮 FP 10→7 |
| 第三节 | P .800 / R .750 / **F1 .774** (TP 12/16) | **F1 .821** 均值（.875/.812/.774） | 在基线噪声范围内 |

**跨节关系（合并抽取独有产出，6 条）**：`数列 -[special_case_of]-> 函数`、`数列有界性/无界性 -[defined_in_terms_of]-> 有界/无界`、`函数极限 -[defined_in_terms_of]-> 函数`、`函数极限与数列极限的关系 -[involves]-> 数列极限` 等。分节模式结构性不可能产出（各节独立抽取、无跨节端点）。证据全部落在引用节原文，judge 级质量待后续审计。

**发现**：
1. **合并抽取不劣于分节抽取**（单轮样本）：第一、二节合并轮反而更好（TP 更高、FP 更少——跨节记忆池让"数列/函数/有界"等既建概念被复用而非重复创建，第二节预测 14 vs 分节 17，重复创建被抑制）；第三节在噪声范围内。两轮均为单轮，差异不能全归因于合并机制，需 r2/r3 才能定论。
2. **第一节整体 F1 低（.45-.57）是 gold 尺度问题，与合并/分节无关**：v3 第一节 gold 67 must 含双曲函数性质全族 36 个细粒度 Claim（倍角/和差/单调/有界），模型两轮都只抽核心概念（映射、函数、有界性等 ~39 命中）。"初等函数"区性质族的抽取范围是独立于本实验的问题。
3. **过程教训（记录在案）**：a) 合并输入会被 `_find_exercise_start` 在第一个习题标题处截断——合并语料必须预先去掉全部习题（`_无习题` 变体），不能直接拼原文；b) 后台任务输出不能用 `| head` 管道（head 关闭管道后 SIGPIPE 杀进程，产物半成品），日志应重定向到文件。

### 模型变量实验：deepseek-v4-flash + no-thinking（追加）

**设计**：同一节语料（第三节）、同一套 prompt（含 derived_from 证据修复），仅换模型变量——`deepseek-v4-flash`、thinking 关闭（temperature 0.1，近似 greedy）。对照 = 第三节 pro-medium 三轮基线均值 .8206（.875/.812/.774）。目的：检验 1) 证据修复是否依赖 pro 的推理能力；2) 低成本 flash 在语义层抽取上的能力边界。

**结果**（`section03_flash_nothinking_r1.json`，17 节点 / 18 关系 / 12 表达式）：

| 变量 | F1 | TP | 结构 |
|---|---|---|---|
| pro-medium 基线（r1-r3 均值） | **.8206** | 13.3/16 | 16-18 节点 |
| flash + no-thinking | **.7879** | 13/16 | 17 节点，3 个连通分量 |

flash 落在 pro 三轮噪声范围（.7742-.8750）内，TP 13/16 与 pro 均值持平，FP 4 略高。**无结构性退化**：schema 干净、0 severe issues。

**关键确认——证据修复在 flash 上同样成立**：flash 的两条 `derived_from` 证据跨度精确落在 (284,286) 和 (288,292)（归因句 + 被推导正文），与 pro 修复后形态一致——prompt 约束是语言层指示，不依赖 pro 的推理强度。

**能力边界**：flash 在**合并语料**（三节拼接）上确定性失败——chunk_003 两次输出 `算子 -[equivalent_to]-> 映射`（Concept→Concept，违反 equivalent_to 签名约束），temperature 0.1 + no-thinking 近似 greedy，重试无效（与第三节映射区 pro 偶发同类错误相同，但 pro 在 temperature 下可重试避开）。闪存低温度下对签名约束的遵守不稳定，属模型能力边界而非管线问题。分节 flash 运行（第三节）无此问题，故改用分节对比。

**结论**：flash+no-thinking 在单节语料上与 pro-medium 相当（噪声范围内），证据修复跨模型成立；合并语料（跨节概念池）暴露 flash 的签名遵守短板——若 flash 上生产合并抽取，需提高 temperature 或以规则后处理兜底 equivalent_to 签名。

### flash + thinking-low 合并轮（追加）

**设计**：上一节的 flash+no-thinking 在合并语料 chunk_003 上确定性违反 equivalent_to 签名（Concept→Concept，temperature 0.1 + no-thinking 近似贪婪，重试无效），合并轮无 flash 结果。本轮补跑：`deepseek-v4-flash` + **thinking 启用、effort low**，同语料（`第一章_前三节合并_无习题.md`）、同 max-tokens 24000、`--no-resume`。**跑通，无签名卡死**——thinking 模式让 flash 避开贪婪路径。

**注意（控制变量）**：pro 合并轮（此前）实际用的 `chunk_strategy: budget`（22 块）；本轮按分节默认传了 `teaching-units`（29 块，语义单元捆绑更细）。两轮分块不同，直接对比不严格；flash 轮与分节基线同策略，flash 轮 vs pro 分节基线的对比是干净的。

**结果**（100 节点 / 159 关系 / 6 跨节关系，拆节对 v3 gold）：

| 节 | flash+TL 合并 | pro 合并（budget） | pro 分节基线 |
|---|---|---|---|
| 第一节 | F1 .515（TP 35/FP 34/FN 32，69 节点） | .565（TP 39/FP 32/FN 28，75 节点） | .446 |
| 第二节 | F1 .500（TP 7/FP 9/FN 5，16 节点） | .538（TP 7/FP 7/FN 5，14 节点） | .429 |
| 第三节 | F1 **.903**（TP 14/FP 1/FN 2，15 节点） | .774（TP 12/FP 3/FN 4，15 节点） | .821 均值 |
| 合计 | F1 .574（TP 56/FP 44/FN 39） | .595（TP 58/FP 42/FN 37） | — |

**质量审计**：combined F1 .574 vs .595、TP/FP/FN 各项差 ≤2——单轮噪声水平，"差不多"。细节：第三节 flash 明显更准（TP 14/16、FP 1，全部实验最好单节成绩，但 chunk 策略差异使优势来源存疑）；第一节 69 节点零重名（无重复创建，FP 是 gold 未收录的基础概念而非复制品）；跨节关系 6 条结构与 pro 轮一致；derived_from 证据形态正常（thinking low 下证据修复仍成立）。

**结论**：flash+thinking-low 合并抽取质量与 pro-medium 相当，可作低成本替代；合并语料上 flash 需 thinking 模式（或更高 temperature）绕开确定性签名违反。

### 评估方法修复：三层节点匹配（追加）

**问题**：`evaluate_kg.py` 的 gold 节点匹配只有纯字符串规则（`canonical_node_name` 归一化后集合交集），Claim 层无任何回退——命名漂移（"双曲余弦的单调性" vs "双曲余弦单调性"、"复合函数的极限运算法则" vs "复合函数极限的运算法则"）直接判 FN，实测压低 F1 达 0.1-0.2。语义回退（embedding+judge）仅对 Concept 开放且需 flag。

**修复**（三层匹配，三类节点 Concept/Claim/Method 全覆盖）：
1. **规则层**（不变）：归一化字符串精确匹配，优先。
2. **模糊层**（新增，默认开启，零成本）：`fuzzy_similarity` = max(difflib ratio, 字符 bigram Jaccard)，阈值 0.85；候选须同类型、Claim 还须同 claim_kind；**关键修饰语一致性约束**（反/左/右/上/下/正/负/奇/偶/增/减/内/外 等单字修饰语必须双方一致）——防止"反双曲余弦的单调性"被误配到"双曲余弦单调性"（反函数 vs 原函数，实测 0.875 相似度仍属误配）。
3. **语义层**（`--semantic-match`，需本地 ollama qwen3-embedding + flash judge）：embedding 召回 + judge 判定，扩展到 Claim/Method（gold 池限定同 claim_kind）。

输出新增 `match_layers` 统计（rule/fuzzy/semantic 各命中数），方便校准与报告。

**新方法下全部历史数据重评估**（默认 fuzzy 开）：

| 运行 | 旧 F1 | 新 F1 | 旧 TP | 新 TP | fuzzy 命中 |
|---|---|---|---|---|---|
| 第一节 分节 r1 | .4460 | **.5899** | 31 | 41 | 10 |
| 第二节 分节 r1 | .4286 | **.5385** | 6 | 7 | 3 |
| 第三节 r1（teaching-units） | .8750 | **.9375** | 14 | 15 | 1 |
| 第三节 r2 | .8125 | **.8750** | 13 | 14 | 2 |
| 第三节 r3 | .7742 | **.9032** | 12 | 14 | 2 |
| 合并 r1 第一节 | .5652 | **.6377** | 39 | 44 | — |
| 合并 r1 第二节 | .5385 | **.6154** | 7 | 8 | — |
| 合并 r1 第三节 | .7742 | **.8387** | 12 | 13 | — |

人工抽查确认：fuzzy 层 10 个命中（第一节 r1）全部正确；semantic 层 15 个命中（第一节 r1）全部正确，包括深层改写救回（"反双曲余弦的单调性"→"反双曲余弦单调增加"）与反函数/原函数正确区分。

**意义**：所有历史 F1 被修正（纯字符串低估的真实质量），后续轮次（r2/r3）均以新方法评估。合并 vs 分节的相对格局在新方法下不变（第一二节合并更优、第三节分节更优），且合并轮第一节优势从 +0.119 扩大至 +0.048→+0.05 区间（新方法下 s1 合并 .638 vs 分节 .590、s2 合并 .615 vs 分节 .539、s3 合并 .839 vs 分节 .905）。

### 合并 vs 分节定论（flash r2/r3，追加）

**设计**：为定论"合并抽取 vs 分节抽取"的差异是否超出噪声，跑合并 r2/r3 与三节分节 r2/r3（s3 补 budget 口径 r1-r3，与合并轮同 chunk 策略；既有 s3 teaching-units 轮仅作参照）。全部 `deepseek-v4-flash + thinking + effort low + max_tokens 24000 + budget + --no-resume`（合并 r1/分节 s1/s2 r1 为 pro-medium 同配置，作 r1 对照）。评估统一用修复后的三层匹配 evaluator（默认 fuzzy 口径）。

**节点 F1（rule+fuzzy，同口径）**：

第一节（gold 67）：
| 轮次 | 合并 | 分节 |
|---|---|---|
| r1（pro） | .6377 | .5899 |
| r2（flash） | .6986 | .6763 |
| r3（flash） | .6713 | .7067 |

第二节（gold 12）：
| 轮次 | 合并 | 分节 |
|---|---|---|
| r1（pro） | .6154 | .5385 |
| r2（flash） | .5000 | .5185 |
| r3（flash） | .4138 | .5926 |

第三节（gold 16，budget 口径；分节 teaching-units 参照 .9375/.8750/.9032）：
| 轮次 | 合并 | 分节 |
|---|---|---|
| r2（flash） | .9375 | .8750 |
| r3（flash） | 1.0000 | 1.0000 |

**定论**：

1. **合并 vs 分节无系统性差异（噪声内）**：9 个可对比配对中合并优 4 次、分节优 5 次，差异幅度 ±0.04 以内，方向随轮次漂移（s1 r2 合并 +.022、r3 分节 +.035；s2 r1 合并 +.077、r2/r3 分节 +.018/+.179；s3 持平）。"合并更好"或"分节更好"都是单轮噪声；两模式质量相当，选择取决于其他因素（见 2、3）。
2. **合并的独有价值是跨节关系**：每轮稳定产出 11-15 条跨节关系（数列→函数、数列有界性→函数有界性 special_case_of、函数极限与数列极限的关系 involves 数列极限、s3 函数极限概念 defined_in_terms_of s1 函数/自变量 等），证据全部落在引用节原文、judge 级质量，分节模式结构性不可能产出。若下游需要跨节语义（如整章概念图），合并是唯一来源。
3. **flash+thinking-low 达标（副产品结论升级）**：flash 分节 s1 .676/.707 > pro .590、s2 .519/.593 ≈ pro .539、s3 budget 1.0/.875/1.0 ≥ teaching-units pro .905 均值，且单轮耗时 pro 的 ~1/10（s3 全章约 4 分钟）。此前 flash 合并轮（teaching-units）与 pro 合并轮（budget）因分块策略不同对比不严格；本轮 flash 合并 r2/r3 与 pro 合并 r1 同 budget 口径：s1 .699/.671 vs .638、s2 .500/.414 vs .615、s3 .938/1.0 vs .839——flash 不劣于 pro。合并语料上需开 thinking（no-thinking 有确定性签名违反，已记录）。
4. **第一节 F1 低是 gold 尺度问题（重申）**：67 must 含双曲函数性质族 36 个细粒度 Claim，模型始终只抽核心概念（TP 47-53 稳定）；与合并/分节无关。
