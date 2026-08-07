# 第一层语义图迭代记录 08

## 实验目标

本轮检验能否把 v1.5.7 的人工行号 span 改造成可泛化、可审计的教材 teaching-unit 分块，同时保持抽取 Prompt 不变，以隔离“输入组织”这一实验变量。

实验使用 `deepseek-v4-pro`、thinking enabled、reasoning effort medium、max chunk chars 3200、`--no-resume`。Prompt 哈希保持为 `c6559c044d9b15632feb9e27379b6e1c2d903177417d7a784c689a8166c3562f`，Extractor 哈希为 `efd2f25497dee1b539f13b1c6fe675fa5449fad5a7655074c875b1bda0562fc1`。KG JSON 与 cache 只保存在本地，不纳入 Git。

## v1.5.9 实现

新增显式参数：

```text
--chunk-strategy budget|teaching-units
```

默认仍为 `budget`，自动教学单元仅作为实验策略。它与 `--experimental-chunk-spans` 严格互斥，不做隐式降级。

`teaching-units` 只组织教材语篇，不抽取或补造 KG 语义：

- 标题附着到后续实质性单元；
- 连续显式例题形成 audited skipped unit，不发送给模型；
- 定义和解释按标题、例题、核心陈述或字符预算分组；
- “下面的定理说明”“有如下”及句尾“即”等定理引导语附着到后续定理；
- 定理前已完整表达的独立一般结论保留为单独单元；
- 带撇号的加强定理附着到基础定理；
- 推论附着到前一中心定理；
- 小型相邻独立定理最多合并两个，含加强形式或推论的定理族不再吞并下一定理；
- 每个 teaching block 必须恰好进入 processed 或 skipped unit 一次；
- skipped example 的行号不会从 `context_before_text` 或 `context_after_text` 重新进入模型；
- diagnostics 记录 unit kind、grouping reason、anchor block、skipped unit 和覆盖统计。

schema、默认输出和 cache namespace 升级到 v1.5.9。新增 4 项合成测试，完整测试由 32 项增至 `36/36` 通过。

## 静态分块验证

前四节均通过严格 block 覆盖校验。第三、四节自动结果与人工认可边界一致。

第三节发送单元：

```text
1-9,11-55,150-170,200-227,256-271,272-293,294-301
```

跳过例题：

```text
58-149,172-199,229-255
```

第四节发送单元：

```text
1-7,13-54,55-79,112-112,114-151
```

跳过例题：

```text
9-12,81-111
```

其中 L112 的“铅直渐近线”独立陈述与 L114-L151 的倒数关系定理被正确分开。第二节也自然形成“定义讲解、例题跳过、定理 1、定理 2、定理 3+推论、定理 4”的结构，没有章节专属行号或知识名称规则。

## 第三节三轮结果

| Run | Nodes | Relations | Expressions | TP/FP/FN | F1 | Tokens | API latency |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| r1 | 16 | 20 | 6 | 15/1/3 | .8824 | 125459 | 568.8s |
| r2 | 16 | 22 | 11 | 14/2/4 | .8235 | 129624 | 513.7s |
| r3 | 15 | 18 | 10 | 12/3/6 | .7273 | 128187 | 514.4s |

平均 F1 为 `.8111`，总体标准差 `.0639`；低于 v1.5.7 人工中粒度分块两个成功样本的均值 `.8992`。平均 token 为 `127757`，平均 API latency 为 `532.3s`。

三轮节点集合 Jaccard 为 `.3913/.3478/.3478`，仅 7 个节点三轮同名同型稳定，三轮并集为 29 个节点。关系 Jaccard 为 `.0769/.0857/.2903`，仅以下 3 条关系稳定，三轮关系并集达到 48 条：

```text
函数极限的保号性推论 -[derived_from]-> 函数极限的局部保号性
左极限 -[special_case_of]-> 单侧极限
右极限 -[special_case_of]-> 单侧极限
```

`derived_from` 三轮均为 2 条，是局部正向信号；但其它关系与连通分量波动明显。反复漏抽项包括“函数极限与趋近点处的函数值无关”“去心邻域”和“非零函数极限的局部绝对值下界”。部分数学内容虽被抽取，但名称或 Claim kind 与 Gold 不一致，仍按 FP+FN 计分。

## 第四节三轮结果

| Run | Nodes | Relations | Expressions | TP/FP/FN | F1 | Tokens | API latency |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| r1 | 5 | 4 | 4 | 4/1/5 | .5714 | 56983 | 265.4s |
| r2 | 8 | 7 | 5 | 7/1/2 | .8235 | 63644 | 264.8s |
| r3 | 6 | 5 | 2 | 6/0/3 | .8000 | 59034 | 293.5s |

平均 F1 为 `.7317`，总体标准差 `.1137`；低于 v1.5.8 同模型人工 span 三轮均值 `.8375`。平均 token 为 `59887`，平均 API latency 为 `274.6s`。

节点集合 Jaccard 为 `.3000/.3750/.4000`，仅 3 个节点三轮稳定，节点并集为 12 个。关系 Jaccard 为 `.1000/.1250/.3333`，仅“铅直渐近线 defined_in_terms_of 无穷大”三轮稳定，关系并集为 12 条。

自动边界稳定恢复了 L112 的铅直渐近线，但没有稳定恢复“零是唯一的常数无穷小”“无穷大函数不存在有限极限”、正无穷大和负无穷大。倒数关系在 r1 的命名未与 Gold 等价名匹配，仍严格计为 FP+FN。

## 流程可靠性

第三、四节共 6 轮、36 个 API 请求全部一次成功：

- schema 失败 0；
- 网络重试 0；
- CLI 编码失败 0；
- 严重结构问题 0；
- warning 0；
- teaching block 漏失或重复 0；
- 例题局部节点 0。

第三节总计 383270 token、1596.9s API latency；第四节总计 179661 token、823.7s API latency。流程可靠性提升不等于语义质量提升，二者必须分开报告。

## 关系稳定性口径修正（gold-aligned）

迭代 08 初稿直接使用模型生成的节点名称比较关系，错误地把节点命名漂移计入了关系漂移。`scripts/evaluate_kg.py` 现已新增 `relation_stability` 多图评估：先利用现有 Gold 节点匹配结果（type + claim_kind + canonical name，含 accepted_names 与 aliases，排除 X 项，M 优先于 O）把预测关系端点映射到 Gold ID，再计算关系集合 Jaccard 与多轮稳定边；同时保留 `raw_name` 指标，不覆盖历史数据。

两种口径：

- `raw_name`：端点用模型生成的名称原样比较，命名漂移计入关系不稳定；
- `gold_aligned`：端点先映射到 Gold ID，同一 Gold 节点在轮次间的命名差异不再计入关系漂移；端点无法映射到已审查 Gold ID 的关系（`dropped_relation_count`）不进入对齐集合。

第三节对齐结果为：

```text
pairwise Jaccard:      r1-r2  r1-r3  r2-r3
raw_name:               .0769  .0857  .3333
gold_aligned:           .6500  .5000  .4500
```

`gold_aligned` 并集 23 条边，三轮稳定 9 条；`raw_name` 并集 47 条，稳定 3 条（与初稿一致）。三轮稳定对齐边：

```text
左极限 -[defined_in_terms_of]-> 函数在一点处的极限
左极限 -[special_case_of]-> 单侧极限
右极限 -[defined_in_terms_of]-> 函数在一点处的极限
右极限 -[special_case_of]-> 单侧极限
函数极限的唯一性 -[involves]-> 函数在一点处的极限
函数极限的保号性推论 -[derived_from]-> 函数极限的局部保号性
函数极限的保号性推论 -[involves]-> 函数在一点处的极限
函数极限的局部有界性 -[involves]-> 函数在一点处的极限
函数极限的局部保号性 -[involves]-> 函数在一点处的极限
```

第四节对齐结果为：

```text
pairwise Jaccard:      r1-r2   r1-r3  r2-r3
raw_name:               .1000   .1250  .3333
gold_aligned:           .1429   .4000  .3750
```

`gold_aligned` 并集 8 条边，三轮稳定 1 条：「铅直渐近线 -[defined_in_terms_of]-> 无穷大」。

两点口径说明：

1. `raw_name` 的 r2-r3 由初稿的 `.2903`（9/31）变为 `.3333`（10/30）。初稿是一次性脚本，边身份比纯名称集合更细：r2 与 r3 均含「函数极限的保号性推论 -[involves]-> 函数在一点处的极限」，但 evidence 分别为 290-293 与 290-292，按更细身份不匹配。新工具以纯名称集合复现 r1-r2（3/39）与 r1-r3（3/35）与初稿完全一致，r2-r3 的差异源于历史口径而非数据变化；初稿的 `.0769/.0857/.2903` 及 48 条并集作为历史数据保留在本记录上方，不再作为可比口径。
2. 端点无法映射到 Gold ID 的关系（第三节三轮 3/6/5 条，第四节三轮 2/1/0 条）是 pending-unlinked 诊断，只说明该节点尚未在 Gold 中标注，不代表关系本身错误。Gold 关系仍未标注，`relation_metrics` 保持 `null`，对齐 Jaccard 只反映跨轮稳定性，不是关系的 Precision/Recall/F1。
3. 每轮报告新增 `alignment_coverage = aligned_edge_count / relation_count`。`gold_aligned_jaccard` 只衡量对齐子集内部的跨轮稳定性，解读时必须同时看覆盖率：若各轮都只对齐到同一条边，Jaccard 可能为 1.0，但这只是"这一条边稳定"的弱证据。覆盖率低时高 Jaccard 不能作为关系整体稳定的结论。

复现命令：

```powershell
& 'E:\MaF-KG\.venv\Scripts\python.exe' scripts\evaluate_kg.py `
  'KG\第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r1.json' `
  'KG\第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r2.json' `
  'KG\第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r3.json' `
  --gold 'evaluation\gold\semantic_first_three_sections_v2.json' --json
```

第四节把文件换成 `第一章第四节_*` 三个 r1/r2/r3 与 `--gold evaluation\gold\semantic_fourth_section_v1.json` 即可。

## 最终判断

1. 自动 teaching-unit 成功复现人工边界，并将手工行号实验变成了跨章节可运行、可审计的机制；
2. 跳过显式例题、绑定定理变体与推论、过滤 skipped context 是确定的工程收益；
3. 冻结 Gold 上的节点 F1 没有改善，第三、四节平均值均低于对应人工基线；
4. 关系集合稳定性显著不足，不能因 `derived_from` 局部恢复而宣称整体关系质量提升；
5. `teaching-units` 应保留为 opt-in 实验策略，默认继续使用 `budget`；
6. 不应继续增加章节专属分块规则。下一轮更值得研究的是教学单元内部的覆盖检查、候选暂存与延迟确认，或同一单元的独立复核，而不是再次微调行号或正则；
7. 在关系 Gold 完成前，仍不能报告关系 Precision/Recall/F1。

## 本地产物

- `第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r1.json`；
- `第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r2.json`；
- `第一章第三节_semantic_v1_5_9_teaching_units_pro_medium_r3.json`；
- `第一章第四节_semantic_v1_5_9_teaching_units_pro_medium_r1.json`；
- `第一章第四节_semantic_v1_5_9_teaching_units_pro_medium_r2.json`；
- `第一章第四节_semantic_v1_5_9_teaching_units_pro_medium_r3.json`。

以上 JSON 与对应 cache 均仅保存在本地实验工作树。
