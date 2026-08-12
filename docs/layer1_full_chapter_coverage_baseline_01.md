# 第一层全章覆盖基线实验 01

## 研究问题

在不修改第一层 Schema、抽取 Prompt、Top-K Memory 与第二层代码的前提下，评估以下问题：

1. `teaching-units` 结构化分块在第一章全章连续抽取时是否可靠；
2. 第一层主干语义与细节语义的召回是否存在系统差异；
3. 基于结构解析的覆盖账本能否发现真实遗漏；
4. 同一配置重复运行时，节点与关系的语义粒度是否稳定。

**结论：管线结构可靠，但语义粒度仍不稳定。核心语义召回通常高于细节语义召回；覆盖复查实验没有改善 Gold 指标，其 3 个触发项中 2 个是“定理引用”解析误报，另 1 个是节点类型约束过严。因此，本轮不启用覆盖复查作为默认流程。**

## 冻结设计

### 第一层边界

第一层节点表示教材明确陈述、可以独立引用和复用的数学语义，保留现有 `Concept / Claim / Method` Schema。例题步骤、证明中间过程与解释性碎片不作为独立节点。

### Gold 诊断层级

Gold v4 将必抽节点分为两层；这只是评价分层，不改变图谱 Schema，也不降低细节节点的抽取要求。

| 层级 | 含义 | 指标 |
|---|---|---|
| `core` | 章节主干概念、中心可复用结论、必要方法，直接支撑 KnowledgeUnit 中心 | 单独报告召回率 |
| `detail` | 教材明确讲授、可复用，但用于展开主干的性质、恒等式、推论、加强形式或族成员 | 单独报告召回率 |

不报告分层 precision/F1，因为一个预测节点无法自然归入某个 Gold 层级；整体 precision/recall/F1 保持原定义。

### 全章作用域

同一份全章输出按 Gold 声明的原文区间分别评价：

| Gold 单节 | 全章原文范围 |
|---|---:|
| 第一章第一节 | L424-L1259 |
| 第一章第二节 | L1260-L1685 |
| 第一章第三节 | L1686-L2083 |

节点必须具有范围内 evidence 才计入该节。关系稳定性还要求两个端点和关系 evidence 都落在该范围内，避免把全局复用边重复计入多个章节。

## 实验配置

| 项目 | 值 |
|---|---|
| Base commit | `93630fca` (`main`) |
| 运行时 Extractor SHA256 | `b71a3c6d85a595e211fab28ed0bb83c1134ce6c1de30d8bc7d0692366641bef1` |
| 运行时 Prompt SHA256（strip 后） | `adba7d01b7d6d09efe650cd1c59d223437da0d88a050aab060932f711816ae1f` |
| 输入 | `第一章_全章_无习题_保留行号.md` |
| Provider / model | OpenCode Go / `deepseek-v4-flash` |
| Thinking / effort | enabled / low |
| Chunk strategy | `teaching-units` |
| Chunks | 63 |
| Max tokens | 64000 |
| Max chunk chars | 3200 |
| Context before / after | 6 / 4 lines |
| Top-K Memory | nodes 12000 bytes / relations 6000 bytes |
| Max retries / request delay | 4 / 3 s |
| Resume | disabled for the 3 controls |
| Coverage review | disabled for the 3 controls |

曾试跑 `max_tokens=12000` 与 `24000`，均在高密度 chunk 上截断，未作为正式基线。正式对照使用 `64000`，并不表示每次响应都会消耗该上限。

旧控制输出的 `diagnostics.run_config.memory_node_bytes` 与 `memory_relation_bytes` 为 `null`，原因是这两个字段当时未声明在 LangGraph 的 `ExtractionState` 中，状态传递时被丢弃；抽取逻辑仍通过 `state.get(..., 12000/6000)` 使用默认预算。代码已补齐状态字段与回归测试，后续输出会准确记录实际预算。这个修复只影响遥测，不改变本次控制结果。

## 三轮对照结果

### 图规模与运行可靠性

| Run | Nodes | Relations | Expressions | Severe | Warnings | API request failures | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|
| r1 | 171 | 288 | 96 | 0 | 5 | 2（重试后成功） | 0 |
| r2 | 181 | 281 | 99 | 0 | 18 | 1（重试后成功） | 0 |
| r3 | 194 | 320 | 107 | 0 | 9 | 0 | 0 |

三轮均完成，结构审计均无 severe error。节点数量为 `171 / 181 / 194`，说明相同输入与配置下仍存在明显粒度方差。

### Gold 节点评价

| Section | Run | Precision | Recall | F1 | Core recall | Detail recall |
|---|---:|---:|---:|---:|---:|---:|
| 第一节 | r1 | .6406 | .6119 | .6260 | .6667 | .5676 |
| 第一节 | r2 | .6087 | .6269 | .6176 | .7333 | .5405 |
| 第一节 | r3 | .5500 | .6567 | .5986 | .7333 | .5946 |
| 第二节 | r1 | .5455 | .5000 | .5217 | .6667 | .0000 |
| 第二节 | r2 | .5333 | .6667 | .5926 | .7778 | .3333 |
| 第二节 | r3 | .4286 | .5000 | .4615 | .6667 | .0000 |
| 第三节 | r1 | .8667 | .8125 | .8387 | 1.0000 | .2500 |
| 第三节 | r2 | .7500 | .7500 | .7500 | .8333 | .5000 |
| 第三节 | r3 | .7857 | .6875 | .7333 | .9167 | .0000 |

关键观察：

- 三节中，`core` recall 每轮都高于 `detail` recall；
- 第二节细节召回尤其不稳定，三轮为 `.0000 / .3333 / .0000`；
- 第一节节点规模最大，整体 precision 随节点增多而下降，说明模型倾向在密集教材段落中改变拆分粒度；
- 第三节主干覆盖最好，但细节节点仍随运行显著波动。

### 关系稳定性

Gold 尚未标注关系，因此这里只报告重复运行的集合稳定性，不把 Jaccard 解释为关系正确率。`gold_aligned` 先将关系端点映射到经审核的 Gold 节点，从而降低纯命名漂移的影响。

| Section | Raw-name Jaccard mean | Gold-aligned Jaccard mean | Alignment coverage mean | 3-run stable / union |
|---|---:|---:|---:|---:|
| 第一节 | .2419 | .5947 | .4536 | 34 / 74 |
| 第二节 | .0850 | .3611 | .2991 | 3 / 12 |
| 第三节 | .0932 | .3431 | .4348 | 3 / 18 |

端点对齐后 Jaccard 明显提高，说明一部分“关系不稳定”其实来自节点命名漂移。但第二、三节的 Gold-aligned Jaccard 仍低，关系选择本身也不稳定。由于对齐覆盖率只有约 30%-45%，这些数值只能作为诊断，不能替代人工关系 Gold。

## 覆盖账本与复查实验

### 机制

结构解析器记录定义、定理、推论、性质和核心陈述块是否已有节点 evidence。规则只报告潜在遗漏；可选 `--coverage-review` 会让同一个 Extractor 针对遗漏块再读一次，规则本身不生成数学事实。

### 配对结果

在 r1 上开启复查时，63 个首轮 chunk 全部命中缓存，只额外调用模型 3 次：

| 指标 | r1 control | r1 + coverage review |
|---|---:|---:|
| Nodes | 171 | 171 |
| Relations | 288 | 289 |
| Expressions | 96 | 96 |
| Coverage gaps | 3 | 3 |
| Gold node metrics | 不变 | 不变 |

因此，覆盖复查未证明有效。新增的 1 条关系没有改变节点 Gold 指标，也没有关闭任何 gap。

### 误报根因

1. L2599：`定理6表示……` 是对既有定理的应用说明，被旧解析器误识别为新定理；
2. L3146：`定理2表明……` 是对既有定理的总结，被旧解析器误识别为新定理；
3. L2195：教材陈述铅直渐近线的一般规律，r1 已有 `Concept` evidence，但旧账本强制要求 `Claim`。

代码已针对诊断根因修正：`定理 X 表示/表明/说明/给出……` 会结束前一个 theorem block，并从引用句起归为 exposition；真正的 `定理6（……）设……` 仍归为 theorem。`core_statement` 只要求存在合法 Node evidence，不再预设节点必须是 Claim。用当前解析器重建三轮历史输出的覆盖账本后，L2599 与 L3146 不再形成 gap，L2195 在已有节点 evidence 的 r1/r2 中也不再误报；r3 因确实没有该行节点 evidence，仍保留为遗漏候选。该修复提高了诊断准确性，不代表抽取模型质量已提升。

## 客观结论

1. `teaching-units` 可支撑 63 个 chunk 的全章连续抽取，结构层面可靠；
2. 第一层主干召回相对高于细节召回，但尚未达到稳定、完整覆盖；
3. 最大问题不是“有没有运行成功”，而是同一语义的命名、聚合与拆分尺度不稳定；
4. 覆盖规则适合作为遗漏诊断器，但当前复查策略未显示收益，不应默认启用；
5. 在关系 Gold 缺失前，关系 Jaccard 只能回答“是否重复出现”，不能回答“是否正确”；
6. 下一轮应优先研究语义粒度稳定性，例如对高方差教学单元做定向误差分类，而不是继续增加 Agent。

## 后续复验命令

以下命令使用已修复“定理引用”块边界的当前解析器，因此用于后续复验，不应被解释为对历史三轮控制输出的逐字复现。

全章抽取：

```powershell
& 'E:\MaF-KG\.venv\Scripts\python.exe' -X utf8 agents\Extractor.py `
  --input 'E:\MaF-KG\data\raw\第一章_全章_无习题_保留行号.md' `
  --output 'E:\MaF-KG\KG\layer1_coverage_baseline\full_chapter_control_r4.json' `
  --provider opencode-go `
  --model deepseek-v4-flash `
  --thinking enabled `
  --reasoning-effort low `
  --chunk-strategy teaching-units `
  --max-tokens 64000 `
  --max-retries 4 `
  --request-delay 3 `
  --no-resume
```

三轮节点与关系稳定性评价：

```powershell
& 'E:\MaF-KG\.venv\Scripts\python.exe' -X utf8 scripts\evaluate_kg.py `
  'E:\MaF-KG\KG\layer1_coverage_baseline\full_chapter_control_r1.json' `
  'E:\MaF-KG\KG\layer1_coverage_baseline\full_chapter_control_r2.json' `
  'E:\MaF-KG\KG\layer1_coverage_baseline\full_chapter_control_r3.json' `
  --gold evaluation\gold\semantic_first_three_sections_v4.json
```

实验 JSON、日志和缓存均保留在本机 `KG/` 与 `node_cache/`，受 `.gitignore` 排除，不提交到仓库。
