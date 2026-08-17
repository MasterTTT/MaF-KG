# 实验记录：第二层 KnowledgeUnit（第一章）

日期：2026-08-15（session 内）；2026-08-18 边界修正 v4
脚本：`scripts/build_knowledge_units.py`、`scripts/apply_ku_boundary_fixes.py`、`scripts/build_ku_depends.py`
最终产出：`KG/第一章_knowledge_units_v3.json`（44 个）→ `KG/第一章_knowledge_units_v4.json`（43 个，边界修正版）

## 0. 六项决策（本实验据此执行）

1. 先用第一章。
2. 跨层引用走**语义匹配**（成员记录 `(type, name, description)`，不硬编码 content-hash ID）。
3. 先做一次 layer-1 粒度合并（实验性，见 §1）。
4. `depends_on` 可启用（由跨单元 `derived_from` 生成）。
5. KnowledgeUnit 粒度 = 每个中心教学目标一个单元。
6. 先不人工订单元 Gold。

## 1. 设计机制（纠正版）

第二层**不是按标题切分**，而是按教材的**中心教学目标**语义组织（`docs/knowledge_unit_layer_v1.md`）：标题只是候选边界，organizer 可合并/跳过/拆分。实现为**单智能体 LLM organizer**（`KnowledgeUnitOrganizer`）：

- 每节一次 LLM 调用，喂入该节全部第一层节点（type/name/description/关系，用 n1…别名）+ 小节候选；
- LLM 输出知识单元 + `centers_on`（定义中心目标的节点）/ `contains`（支撑节点）；
- 程序校验：≥1 centers_on、引用落图内、节点最多一个中心、无"其他"单元。

## 2. 工程修复（v3 落地）

| 问题 | 修复 |
|---|---|
| KU ID 顺序编号漂移 | content-hash ID：`ku_<sha1(节\|名)>[:12]` |
| prompt/模型/源文件硬编码 | `--model` / `--src-files` / `--prompt` CLI 化 |
| 输出无运行元数据 | 输出 `run` 段：model、prompt_sha256、endpoints、timestamp、request_count、usage_totals |
| 输入是旧图 | 改用 `第一章_修复版_semantic_v1_7_0.json`（197 节点 / 315 关系，含 14 条 Verifier 补边） |
| 章节归属缺失（一致连续性） | evidence 行号回退到节范围（`section_of`） |
| "单调有界+自然常数e"合并 | prompt 明令拆分，现为两个单元 |
| "其他"单元 | prompt 明令每个节点归入具体单元；0 uncovered |
| KU 无教材证据 | 每个单元带节级证据（源文件 + 行号范围） |

## 2.1 闸门加固（2026-08-18，v5 起）

把"记录型"校验升级为"失败型"（输出仍落盘以便检查，失败可 `--sections` 定点重跑）：

| 隐患 | 修复 |
|---|---|
| 跨节节点归属漂移（幽灵节根因：函数的增量被归入第二章"第一节 导数概念"） | 节归属改为**优先同章 evidence**（`--chapter 第一章`）；dry 模拟确认仅 1 节点归属变化 |
| 跨节重复中心检测失效（`center_seen` 每节重置） | `center_seen` **全局化**，跨节 multi-center 直接 FAIL |
| 无效引用后单元变 0 中心、静默产出空壳 | **0 中心单元 FAIL**（记录单元与节） |
| uncovered 只记录不提醒 | **uncovered > 0 即 FAIL**，提示带 `--sections` 重跑 |
| 边界扫描靠临时命令 | 工具化：`scripts/scan_ku_boundaries.py`（SMALL / SINGLETON / CENTER-HEAVY） |

## 3. 结果（v3）

- **44 个 KnowledgeUnit**（覆盖全部 10 节），校验全绿：uncovered 0、invalid 0、multi-center 0。
- 输入修复版图；成员以 `(id, type, name, description)` 做语义引用。
- 本次运行：98948 token / 11 请求。

示例（中心目标切分合理）：

- 第五节拆成 5 个：四则运算法则 / 无穷小运算性质 / 复合函数极限 / 直接代入与有理分式 / 有理分式在无穷远处的极限。
- 第六节拆成 6 个：夹逼准则 / 单调有界准则 / 柯西准则 / 第一个重要极限 / 自然常数 e 与第二个重要极限 / （单调有界与 e 已分开）。
- 第一节拆成 12 个：映射概念 / 逆映射与复合映射 / 函数概念 / 有界性 / 单调性 / 奇偶性 / 周期性 / 反函数 / 复合函数 / 基本初等函数与初等函数 / 双曲函数 / 反双曲函数。

## 4. depends_on（单独构建，第一版）

脚本 `scripts/build_ku_depends.py`：把跨单元的 `derived_from` 边聚合为 KU 间 `depends_on`。44 个单元产出 **8 条**，语义全部成立：

- `极限的四则运算法则 → 函数极限的局部性质`
- `自然常数 e 与第二个重要极限 → 单调有界准则 / 复合函数的极限运算法则 / 夹逼准则`
- `第一个重要极限 sin x / x → 夹逼准则`（2 条底层边）
- `复合函数的极限与连续性 → 复合函数的极限运算法则`
- `连续函数的和、差、积、商的连续性 → 极限的四则运算法则`
- `初等函数的连续性 → 复合函数的极限与连续性`

确定性第一版仅覆盖 `derived_from` 表达的依赖（稀疏，8 条）；LLM 审核版留作后续。

## 5. 边界扫描（人工审核）

44 个单元中 12 个 ≤2 节点，大多**合理**（单定义/单定理/单性质单元：柯西准则、无穷小定义、函数单调性、函数有界性、周期性、海涅定理、复合函数极限法则、f(x)=A+α 关系、第一个重要极限、连续函数四则运算等 10–11 个）。需人工定夺的 2 处（2026-08-18 用户审核通过）：

1. `函数的增量`（1 节点 0 contains）→ 并入 `函数的连续性`（连续性 `defined_in_terms_of` 函数增量，图谱边本已指向它；并入后与既有 `增量`(一般增量) 节点构成 `special_case_of` 层次，非冗余）。
2. `有理分式函数在无穷远处的极限` contains 里的 `a/x^n 在 x→∞ 时的极限` → drop（例题级局部结论，非独立教学目标，且是有理分式极限公式 m=0 特例）。

## 5.1 边界修正 v4（2026-08-18）

脚本 `scripts/apply_ku_boundary_fixes.py`（确定性、fix 清单硬编码、缺失即报错、每项修正记入输出 `fixes` 数组）：

- FIX-1：`函数的增量` 单元并入 `函数的连续性`（节点移入 contains，原单元删除）→ 44 → **43 个单元**。
- FIX-2：`a/x^n 在 x→∞ 时的极限` 从 contains 移除 → 有理分式单元 2 节点 → 1 节点（纯中心定理）。
- depends_on 重跑（`第一章_ku_depends_v2.json`）仍是 **8 条**：被并入的"函数的增量"无跨单元 `derived_from` 边，依赖结构不变。
- 其余 10 个小单元经用户确认保留原样。

## 6. 已知局限与下一步

1. **depends_on 第一版已出**（§4，v2 随 v4 重跑仍 8 条）；LLM 审核版待做。
2. ~~边界人工审核~~ **已完成**（§5.1，v4 落地）。
3. **layer-1 粒度合并仅演示**（§1 决策 3）：完整语义碎片目录仍需语义合并 agent。
4. **未人工订单元 Gold**：正式冻结前需 Gold。
5. **depends_on 稀疏**：确定性派生只覆盖 `derived_from`；语义级前置依赖（如 连续性→极限定义）需 LLM 审核补全。

## 7. 结论

LLM organizer + 工程硬化 + 边界修正（v4）后，本版可作为**第一章第二层的正式候选基线**（待 depends_on LLM 审核 + 单元 Gold 两件事补完）。
