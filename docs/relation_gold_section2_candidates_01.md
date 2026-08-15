# 第一章第二节关系 Gold 候选审查稿

## 1. 标注范围

- 教材：`data/raw/第一章第二节.md`
- 节点基准：`evaluation/gold/semantic_first_three_sections_v4.json` 中第二节节点
- 层级：第一层教材语义图，不包含 KU 之间的教学组织关系，也不展开证明内部的原子推理链
- 证据行号：均对应 `第一章第二节.md` 的本节局部行号
- 当前状态：已审查；正式正例集见 `evaluation/gold/relations_section2_v1.json`

只标注教材明确支持、可独立复用的关系。例题结果、证明中间不等式、图示解释和习题均不作为关系端点。

## 2. 关系方向约定

| 关系 | 方向 | 本稿采用的含义 |
| --- | --- | --- |
| `involves` | Claim -> Concept | 结论直接讨论该核心概念 |
| `uses` | Method -> Concept/Claim/Method | 方法以该语义为明确依据或对象 |
| `establishes` | Method -> Claim | 教材明确用该方法证明该一般结论 |
| `derived_from` | Claim -> Claim | 派生结论指向其依据；方向是“结论 -> 根据” |
| `equivalent_to` | Claim <-> Claim | 教材明确支持严格双向等价 |
| `special_case_of` | 特殊 Concept -> 一般 Concept | 严格概念包含关系 |
| `defined_in_terms_of` | 被定义 Concept -> 支撑 Concept | 当前定义实质依赖另一个概念 |

## 3. 建议纳入的候选关系

### 3.1 概念定义与包含关系

| ID | Start | Relation | End | 原文行 | 判定理由 |
| --- | --- | --- | --- | --- | --- |
| R01 | `s2_concept_sequence_limit` 数列的极限 | `defined_in_terms_of` | `s2_concept_sequence` 数列 | L100-L118 | 极限定义以给定数列及其项为定义对象。 |
| R02 | `s2_concept_sequence_boundedness` 数列有界性 | `defined_in_terms_of` | `s2_concept_sequence` 数列 | L269-L277 | 有界性由数列全部项满足统一界来定义。 |
| R03 | `s2_concept_subsequence` 子数列 | `special_case_of` | `s2_concept_sequence` 数列 | L343-L353 | 原文明确说从原数列抽取后“得到一个数列”，满足严格包含关系。 |

R03 不同时标为 `defined_in_terms_of`，避免同一概念对同时承担定义依赖和严格包含两种关系；这里优先保留语义更强的 `special_case_of`。

### 3.2 Claim 与核心概念

| ID | Start | Relation | End | 原文行 | 判定理由 |
| --- | --- | --- | --- | --- | --- |
| R04 | `s2_property_limit_uniqueness` 收敛数列极限唯一 | `involves` | `s2_concept_sequence_limit` 数列的极限 | L247-L249 | 定理直接陈述收敛数列的极限唯一性。 |
| R05 | `s2_property_convergent_sequence_bounded` 收敛数列必有界 | `involves` | `s2_concept_sequence_limit` 数列的极限 | L291-L311 | 前件是数列收敛。 |
| R06 | `s2_property_convergent_sequence_bounded` 收敛数列必有界 | `involves` | `s2_concept_sequence_boundedness` 数列有界性 | L291-L311 | 后件是数列有界。 |
| R07 | `s2_property_sign_preservation` 收敛数列保号性 | `involves` | `s2_concept_sequence_limit` 数列的极限 | L323-L334 | 定理由极限符号推出数列项最终同号。 |
| R09 | `s2_property_subsequence_limit` 收敛数列的任一子数列收敛于同一极限 | `involves` | `s2_concept_sequence_limit` 数列的极限 | L357-L363 | 定理比较原数列与子数列的极限。 |
| R10 | `s2_property_subsequence_limit` 收敛数列的任一子数列收敛于同一极限 | `involves` | `s2_concept_subsequence` 子数列 | L357-L363 | 子数列是定理的核心对象。 |

派生 Claim 不重复添加原 R08、R11-R14 这类 `involves` 边。它们已通过
`derived_from` 接入基础结论，再逐词连接 Claim 描述中的概念只会增加图密度，
不会增加新的逻辑信息。

### 3.3 教材明确给出的派生关系

| ID | Start | Relation | End | 原文行 | 判定理由 |
| --- | --- | --- | --- | --- | --- |
| R16 | `s2_property_limit_sign_corollary` 项最终非负或非正时极限保号的推论 | `derived_from` | `s2_property_sign_preservation` 收敛数列保号性 | L337-L339 | detail；推论证明明确调用定理3。 |
| R17 | `s2_criterion_distinct_subsequence_limits` 两个子数列收敛到不同极限则原数列发散 | `derived_from` | `s2_property_subsequence_limit` 收敛数列的任一子数列收敛于同一极限 | L357-L365 | detail；原文明确写“由定理4可知”。 |

原 R15 不纳入。定理2只推出“有界是收敛的必要条件”，完整的“必要非充分”
还依赖后续反例，单独指向定理2不能支持整个 Claim。

## 4. 需要校准的 Method 关系

以下关系在数学语义上成立，但证据强度弱于前三组。教材使用的是“根据数列极限的定义”，而 `ε-N论证法` 是 Gold 对多段证明实践的归纳命名，不是教材在四个定理前逐次使用的显式方法名。

| ID | Start | Relation | End | 原文行 | 风险 |
| --- | --- | --- | --- | --- | --- |
| C01 | `s2_method_epsilon_n` ε-N论证法 | `uses` | `s2_concept_sequence_limit` 数列的极限 | L100-L120, L205 | 强候选。原文明确把利用数列极限定义进行论证概括为一种证明做法。 |
| C02 | `s2_method_epsilon_n` ε-N论证法 | `establishes` | `s2_property_limit_uniqueness` 收敛数列极限唯一 | L251-L263 | 证明使用 ε-N 定义，但同时依赖反证法；若标注，会把“主要证明方法”和“定义展开”混在一起。 |
| C03 | `s2_method_epsilon_n` ε-N论证法 | `establishes` | `s2_property_convergent_sequence_bounded` 收敛数列必有界 | L293-L311 | 证明直接令 ε=1 展开定义，证据较强。 |
| C04 | `s2_method_epsilon_n` ε-N论证法 | `establishes` | `s2_property_sign_preservation` 收敛数列保号性 | L325-L334 | 证明直接令 ε=a/2 展开定义，证据较强。 |
| C05 | `s2_method_epsilon_n` ε-N论证法 | `establishes` | `s2_property_subsequence_limit` 收敛数列的任一子数列收敛于同一极限 | L359-L363 | 证明直接展开定义，但核心还包括 `n_k >= k`。 |

建议先将 C01 纳入正式 Gold；C02-C05 暂不纳入。原因不是它们错误，而是当前 `establishes` 尚未明确表示“教材的主要证明方法”还是“证明过程中使用过的方法”。在这个语义未统一前写入会降低跨章节一致性。

## 5. 明确不纳入的边

| 候选 | 不纳入原因 |
| --- | --- |
| 数列的极限 `special_case_of` 数列 | 极限不是一种数列，不满足概念包含。 |
| 数列有界性 `special_case_of` 数列 | 有界性是数列的性质，不是一类数列实体；节点已按性质概念建模。 |
| 子数列同时 `defined_in_terms_of` 数列 | 与 R03 重复表达，优先保留严格包含关系。 |
| 收敛数列极限唯一 `derived_from` 数列的极限 | `derived_from` 只允许 Claim -> Claim，概念定义不是 Claim。 |
| 保号性推论 `equivalent_to` 收敛数列保号性 | 两者方向相反但不是同一命题的简单双向等价，条件结构不同。 |
| 有界必要非充分判据 `equivalent_to` 收敛数列必有界 | 判据还包含“不充分”，语义严格强于单个定理。 |
| 两个子数列极限不同判据 `equivalent_to` 子数列收敛定理 | 前者是后者的逆否式应用，但不是与完整定理等价的同一节点表述。 |
| 派生判据重复 `involves` 其描述中的全部概念 | 已有 `derived_from` 提供逻辑接入，重复概念边不增加信息。 |
| 有界必要非充分判据 `derived_from` 收敛数列必有界 | 目标定理只支持必要性，不能单独支持“不充分”。 |

本节没有足够证据支持任何 `equivalent_to` 关系。

## 6. 建议的首版正式 Gold

- 直接纳入：R01-R07、R09-R10、R16-R17，共 11 条，其中 R16-R17 为 detail。
- 建议纳入：C01，共 1 条。
- 暂缓：C02-C05，共 4 条。
- 首版正例总数：12 条。

这 12 条可以评估关系召回、类型正确率和方向正确率，但不能直接给出严格的 precision/F1。要计算 precision/F1，需要先声明一个经过完整人工审核的候选全集，未列入的关系才能合法视为负例。建议在第二节语义校准后，再汇集三轮模型输出的关系并集，形成待人工判定的封闭候选池。
