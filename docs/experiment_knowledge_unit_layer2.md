# 实验记录：第二层 KnowledgeUnit（第一章）

日期：2026-08-15（session 内）
输入：`第一章后五节_合并_semantic_v1_7_0_opencode_flash_high.json`（197 节点 / 301 关系）
脚本：`scripts/build_knowledge_units.py`
产出：`KG/第一章_knowledge_units_v1.json`

## 0. 六项决策（本实验据此执行）

1. 先用第一章（不先碰第二章全量）。
2. 跨层引用走**语义匹配**（成员记录 `(type, name, description)`，不硬编码 content-hash ID）。
3. 先做一次 **layer-1 粒度合并**。
4. `depends_on` 可启用（由跨单元的 `derived_from` 生成）。
5. KnowledgeUnit 粒度保持"每个中心教学目标一个单元"。
6. 先不人工订单元 Gold。

## 1. Layer-1 粒度合并（实验性）

本次用一份**人工策展的小清单**验证机制（非完整目录）：

| canonical | 吸收的碎片 | 结果 |
|---|---|---|
| `函数的增量` | `增量` | merged |
| `复合函数构成条件` | `多个函数顺次构成复合函数的条件` | merged |

合并 = 碎片节点的关系重定向到 canonical + 删除碎片节点。197 → 195 节点。

**发现**：当前图没有 name 级重复（canonical 名去重后 0 重复），碎片是**语义级**的（`整数部分` vs `取整函数`、`局部保号性`(Concept) vs `函数极限的局部保号性`(Claim)、`函数在区间上的有界性` vs `函数的有界性` 等）。要完整识别这类语义碎片，需要一个类似 Verifier 的"语义合并 agent"（embedding + LLM 判定），本次只演示机制、未展开。

## 2. KnowledgeUnit 组织（确定性 MVP）

分组规则：按节点 evidence 的 `heading_path[1]`（节）+ 源文件 `一、二、…` 小节行号范围，每个小节一个单元；无小节标题的节整节一个单元。

结果：**19 个 KnowledgeUnit**（覆盖全部 10 节），`centers_on` = 该小节内有 `discourse_role=="definition"` 的 Concept，`contains` = 其余节点。

示例：
- `一、映射`（centers=6 / contains=10）
- `二、函数`（centers=2 / contains=53）
- `一、数列极限的定义`（centers=1 / contains=2）
- `二、收敛数列的性质`（centers=0 / contains=7）
- `第五节 极限运算法则`（centers=0 / contains=21，整节一个单元）

## 3. depends_on（由跨单元 derived_from 生成）

5 条，方向语义正确，例如：

- `第五节 极限运算法则 → 二、函数极限的性质`（运算法则建立在极限性质上）
- `二、反函数与复合函数的连续性 → 第五节 极限运算法则`
- `一、连续函数的和差积商连续性 → 第五节 极限运算法则`

## 4. 语义引用

每个成员以 `{id, type, name, description}` 记录，未来 layer-1 重抽后可按 `(type, name, description)` 语义重解析，绕开 content-hash ID 漂移问题。

## 5. 结论与局限（决定下一步）

**机制验证通过**：merge / 分组 / centers_on·contains / depends_on / 语义引用 五条链路都跑通，depends_on 方向正确。

**确定性 MVP 的三个明确局限**（这些正是 v1 设计里"单智能体 organizer"要解决的）：

1. **小节粒度不均**：`二、函数` 一个单元吞了 53 个节点，而 `一、映射` 才 16 个——标题边界不等于教学边界。
2. **无小节标题的节无法细分**：第五节（21 节点）、第六节（16）、第七节（8）整节成一个单元，但教材里它们各有多个中心教学目标（四则运算/复合极限/夹逼/单调有界…）。
3. **centers_on=0 的单元偏多**：性质/法则类小节没有"定义性 Concept"，需要按"该单元的核心定理/法则"选中心，而不是只认 definition。

**下一步建议**：把确定性分组换成 v1 设计的 `KnowledgeUnitOrganizer` 单智能体（按节喂第一层节点 + 标题，让它选中心/支撑、允许拆分粗小节、为法则类单元选中心 Claim 作 centers_on），确定性脚本留作 baseline 对照。

（本实验未做：人工单元 Gold、完整碎片目录、LLM organizer——按决策 5、6 暂缓。）
