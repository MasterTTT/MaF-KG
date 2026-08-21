# 实验记录：第二层 KnowledgeUnit（第一章）

日期：2026-08-15（session 内）；2026-08-18 边界修正 v4；2026-08-18 depends_on LLM 语义审核版 v4；2026-08-18 KU Gold v1 + 边界修正 v7
脚本：`scripts/build_knowledge_units.py`、`scripts/apply_ku_boundary_fixes.py`、`scripts/build_ku_depends.py`、
`scripts/build_ku_depends_candidates.py`、`scripts/review_ku_depends_llm.py`、`scripts/finalize_ku_depends.py`、
`scripts/check_ku_gold.py`
最终产出：`KG/第一章_knowledge_units_v3.json`（44 个）→ `KG/第一章_knowledge_units_v4.json`（43 个，边界修正版）
→ `KG/第一章_knowledge_units_v6.json`（46 个）→ **`KG/第一章_knowledge_units_v7.json`（46 个，Gold 对齐版，当前基线）**；
`KG/第一章_ku_depends_v4.json`（45 条，LLM 语义审核版，v7 下仍全部有效）

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

## 3. 结果（v5，闸门加固后重排）

- **46 个 KnowledgeUnit**（覆盖全部 10 节，10 请求 / 95109 token），校验全绿：uncovered 0、invalid 0、multi-center 0、zero-center 0。
- 幽灵节消失（10 请求 = 10 节）；`函数的增量` 已**天然**落回 `函数的连续性` contains（此前靠人工 FIX-1 修进去）。
- v3（44）→ v5（46）与 v3→v4 的"修正"不同：v5 是**在修复后代码上的全新生成**，第一节多出 `函数的四则运算`、`分段函数与常见特殊函数` 等更细的单元；第四节把 `无穷小定义` 与 `f(x)=A+α` 合并为一个两中心单元（同为"无穷小与极限关系"目标）。

## 4. depends_on（v3 确定性版 → v4 LLM 语义审核版）

### 4.1 v3：确定性 derived_from 聚合（历史）

脚本 `scripts/build_ku_depends.py`：把跨单元的 `derived_from` 边聚合为 KU 间 `depends_on`。v6（46 单元）产出 **5 条**：

- `极限的四则运算法则 → 函数极限的基本性质`
- `初等函数在其定义区间内的连续性 → 极限的四则运算法则`
- `初等函数在其定义区间内的连续性 → 复合函数的连续性与极限换序`
- `复合函数的连续性与极限换序 → 复合函数的极限运算法则`
- `自然常数e与第二个重要极限 → 复合函数的极限运算法则`

v2（8 条，v4 单元对齐）→ v3（5 条）：v5 单元重组后部分跨单元 `derived_from` 边落入同单元，故聚合边收敛。
**注意：v3 的 5 条中 2 条是 node→KU 映射 bug 的假边**（见 §4.3），v4 起不再以 v3 文件为基线，改为从图上重算。

### 4.2 v4：LLM 语义审核版（P2 流程，2026-08-18 会话）

三阶段全脚本化，对应交接文档 §12 P2：

1. **候选生成** `scripts/build_ku_depends_candidates.py`：
   - 强信号：跨 KU 的 `derived_from` / `defined_in_terms_of` / `uses`；
   - 弱信号：`involves` 仅在被涉及节点是目标 KU 的 center、且目标非基础桶（映射、函数的基本概念）时保留；
   - 两跳链候选：A→B→C 全跨 KU（derived_from/defined_in_terms_of/uses），仅当该对无直接信号，
     用于覆盖「连续性→(单侧极限)→函数极限定义」这类图上只有间接路的先修；
   - `special_case_of` 不参与（从属关系 ≠ 先修）。产出 **56 候选**。
2. **LLM 审核** `scripts/review_ku_depends_llm.py` + `prompts/ku_depends_review.txt`：
   deepseek-v4-flash 分批（15/批 → 4 请求，56378 tokens）逐候选输出 accept/strength/reason。
   判定纪律：中心教学目标层面的先修语义；拒绝「仅内容相关 / 被讨论对象 / 教材章节顺序 /
   方向倒置的 involves / 通用基础锚 / special_case 投影」。
   → **45 接受 / 11 拒绝**；11 条拒绝人工复核全部合理（含 2 处方向倒置、3 处"被讨论对象"、
   3 处伪锚「无穷小→数列」链断裂、2 处反例/主题相关）。
3. **人工复核 + 终版** `scripts/finalize_ku_depends.py`：
   - 基线改为**从图上按修复后映射重算**跨 KU `derived_from`（9 条，全部被 LLM 接受）；
   - 解析真实 L1 关系 id 写入 `derived_from_edges`（9 条全为合法 `relation_*` id）；
   - 终版 `KG/第一章_ku_depends_v4.json`：**45 条**，Neo4j 兼容（`source_ku/target_ku/count/derived_from_edges`），
     全部边源/目标引用落在 46 个单元内，无 count=0；逐边 audit（basis/strength/reason/evidence/chain）在 `audit.edges`。

新增的有价值依赖（v3 无）：**函数的连续性 → 函数极限的定义**（目标案例）、函数的间断点及其分类 → 极限定义/单侧极限、
单侧极限 → 函数极限定义、自然常数e → 单调有界/夹逼准则、第一个重要极限 → 夹逼准则、
连续函数的和差积商的连续性 → 极限四则（v3 假边的正确形态）、初等函数 → 基本初等函数、
反双曲→双曲→基本初等函数、函数的基本概念 → 映射 等。

### 4.3 本会话工程修正（三个 bug）

1. **node→KU 映射中心优先**：一个节点可在一个 KU 做 center 又做多个 KU 的 contains 成员；
   旧实现 contains 后写覆盖 center 导致归属漂移。修复为 center 先写、contains 只 `setdefault`。
   直接影响候选正确性（如 `复合函数的极限运算法则→初等函数` 实为 `→复合函数`）。
2. **v3 基线 2 条映射假边**（旧 `build_ku_depends.py` 受同一 bug 影响）：
   - `初等函数连续性→极限的四则运算法则` 实应为 `连续函数的和、差、积、商的连续性→极限的四则运算法则`
     （底层边 `连续函数的和、差、积、商仍在同一点连续` 的归属被覆盖）；
   - `初等函数连续性→复合函数的连续性与极限换序` 的底层边在修复后映射下为**同 KU 内部边**，本不应成 KU 依赖。
   两处均由 v4 的按图重算基线自动纠正。
3. **审核批次 index 按批重置**：批次内 `enumerate` 从 0 起导致 verdicts 与候选错位（v1 次运行作废）；
   修复为全局下标并重跑（v2 审核文件 `KG/第一章_ku_depends_review_v2.json`）。

### 4.4 与 v3 对照

| 版本 | 边数 | 说明 |
|---|---|---|
| v1 | 8 | v4 单元对齐（历史，确定性） |
| v2 | 8 | v4 单元对齐（历史，确定性） |
| v3 | 5 | v6 单元对齐，确定性 derived_from 聚合（含 2 条映射假边） |
| **v4** | **45** | 修复映射后按图重算基线（9）+ LLM 语义审核新增（36），逐边有 evidence 与判据 |

## 5. 边界扫描（人工审核）

v3 的 44 个单元中 12 个 ≤2 节点，大多**合理**（单定义/单定理/单性质单元：柯西准则、无穷小定义、函数单调性、函数有界性、周期性、海涅定理、复合函数极限法则、f(x)=A+α 关系、第一个重要极限、连续函数四则运算等 10–11 个）。需人工定夺的 2 处（2026-08-18 用户审核通过）：

1. `函数的增量`（1 节点 0 contains）→ 并入 `函数的连续性`（连续性 `defined_in_terms_of` 函数增量，图谱边本已指向它；并入后与既有 `增量`(一般增量) 节点构成 `special_case_of` 层次，非冗余）。
2. `有理分式函数在无穷远处的极限` contains 里的 `a/x^n 在 x→∞ 时的极限` → drop（例题级局部结论，非独立教学目标，且是有理分式极限公式 m=0 特例）。

## 5.1 边界修正（v4 人工修正 → v6 代码天然归位）

脚本 `scripts/apply_ku_boundary_fixes.py`（确定性、fix 清单硬编码或 `--fix-list` 外置、缺失即报错、每项修正记入输出 `fixes` 数组）：

- **v4（v3 + FIX-1/FIX-2）**：`函数的增量` 并入 `函数的连续性`（44→43）；`a/x^n` 从 contains 移除。
- **v6（v5 + FIX-2-v5）**：闸门修复后 `函数的增量` 无需再修（天然归位）；`a/x^n` 仍被 organizer 放进 contains，按同一理由再次 drop（46 单元不变，fix-list 见 `scripts/fixes_ch1_v5.json`）。
- v5 边界扫描（`scan_ku_boundaries.py`）：10 个 SMALL 全部合理，4 个 SINGLETON（柯西准则 / 函数的四则运算 / 连续函数四则运算 / 归结原则）为正当单目标单元，保留。

## 5.2 KU Gold v1 与边界修正 v7（2026-08-18）

**Gold**：`evaluation/gold/ku_gold_ch1_v1.json`（46 单元：名称 / 中心目标 / expected centers / 必含节点 / 
禁止合并对 14 组，全部带教材出处行号）；检查器 `scripts/check_ku_gold.py`（Gold FAIL = 期望中心缺失 / 必含缺失 / 
禁止合并对缺失；WARN = 额外中心，供人工确认）。

**复核重点项**（交接文档 §12 P1）：单调有界准则/e、数列极限性质、等价无穷小、函数极限保序性全部核对——
保序性确系第五节定理5（前五节 L2025-2028），当前归 `极限的四则运算法则` contains **正确**；
`收敛数列的性质` 的中心缺"收敛数列必有界"（发现 F1）；`等价无穷小及其代换` 缺"常用的等价无穷小关系式"（发现 F4）。

**v6→v7 修正**（fix-list `scripts/fixes_ch1_v6.json`，应用 `apply_ku_boundary_fixes.py` 新增 `move_member` / `add_member` 动作）：

| Fix | 动作 | 内容 | 依据 |
|---|---|---|---|
| FIX-3 | move_member(promote) | `收敛数列必有界` → `收敛数列的性质`.centers | 第二节定理2（前五节 L1131-1151）；证据被锚在第六节引用处（L312） |
| FIX-4 | move_member | `收敛数列的有界性` → `收敛数列的性质`.contains | 同上（与 FIX-3 语义重复，L1 级去重留待合并目录） |
| FIX-5 | add_member | `常用的等价无穷小关系式` → `等价无穷小及其代换`.contains | 第七节例2（后五节 L428-432）；原组织进 [31]（证据锚第九节 L836），现多成员复用 |

**v7 验证**：数量 46、闸门全绿（zero-center 0 / multi-center 0）；**Gold 检查 FAIL 0 / WARN 0**；
边界扫描 11 SMALL（10 与 v5 相同 + 收敛数列的性质 4 centers 转 CENTER-HEAVY，系定理1-4 全面覆盖，可辩护）；
**depends v4 在 v7 下全部有效**（候选对集合不变，新增对恰为已拒绝的 11 对，无未审核新对）。

**Gold findings F1–F5**（随 Gold 文件保存）：
- F1 [high] 收敛数列必有界/有界性 归属漂移（已由 FIX-3/4 修复）；
- F2 [medium] L1 重复抽取：收敛数列必有界 vs 收敛数列的有界性 同结论两节点 → 属 L1 合并目录（剩余工作 #3）；
- F3 [info] 夹逼准则 contains 复用两个重要极限命题节点（合规）；
- F4 [medium] 常用的等价无穷小关系式 归属漂移（已由 FIX-5 修复）；
- F5 [info] 换元法求复合函数的极限 保持单一归属 [29]：多成员复用会引入跨 KU uses 边需重审 depends，暂缓。

## 6. Neo4j 导入（2026-08-18）

`scripts/import_kg_to_neo4j.py`（扩展支持 KU 层）：L1 修复版图 + KU v6 + depends v3 全量导入 Docker 内 `neo4j:5`（bolt://localhost:7687，凭证来自环境变量）：

| 内容 | 计数 |
|---|---|
| KGNode（Concept/Claim/Method 标签） | 197 |
| L1 关系（7 种） | 315 |
| KnowledgeUnit | 46 |
| CENTERS_ON / CONTAINS | 67 / 150（共 217） |
| DEPENDS_ON | 5 |

验证查询通过：`函数的增量` 归位 `函数的连续性 [CONTAINS]`；依赖边 5 条全部落在真实单元间。

## 6.1 跨章泛化（P3，第二章，2026-08-18）

同一 Organizer（`build_knowledge_units.py --chapter 第二章`，不加章节特例）跑第二章全章图
`第二章_全章_semantic_v1_7_0_opencode_flash_high_global_repaired.json`（326 节点 / 520 关系），
`--sections` 限定第二章 5 节：

- **第二章 KU v1：22 单元**（第一章 46）；全闸门绿（uncovered 0 / invalid 0 / multi-center 0 / zero-center 0）；
  5 请求 / 43116 tokens。单元粒度章节内更细相关（如 导数公式按 幂指对 / 正余弦 分单元、微分公式一表一单元）。
- 边界扫描：3 个 SINGLETON（函数可导性与连续性的关系 / 微分的几何意义 / 相关变化率）均正当单目标；0 CENTER-HEAVY。
- 已知问题定位（均为 L1 层，非 KU 层）：`隐函数求导法` Method **确实缺失**（§8.2）；`隐函数的显化` 仍为 Claim 类型；
  `微分形式不变性` 已正确成为 基本初等函数微分法则 的中心。
- **跨章 depends（两章合并 82 条 = v4 45 全保留 + 新增 37）**：LLM 审核 52 条未定案新对 → 37 接受 / 15 拒绝（拒绝全部合理，
  正确拒掉"连续性是可导前提"等伪先修——可导性定义用极限，连续性是其推论）。见 §6.2。

## 6.2 合并与两章 depends（2026-08-18）

- 合并工具 `scripts/merge_chapter_artifacts.py`：两章 L1（KG 326 节点 / 520 关系 / 245 表达式，节点 id 两章全兼容）、
  KU（68 = 46 + 22）、depends 合并为单一导入文件（`KG/两章合并_*`）。
- 候选/审核：`build_ku_depends_candidates.py`（两章 108 候选）→ `filter_undecided_candidates.py` 过滤出 v4 未定案 52 条
  → `review_ku_depends_llm.py`（3 请求 / 42842 tokens）→ `finalize_ku_depends.py --prior-depends v4` 产出
  **`KG/两章_ku_depends_v1.json`（82 条）**。
- **跨章依赖边 7 条**（ch2 KU → ch1 KU 先修）：求导法则→函数四则运算、单侧导数→单侧极限、
  单侧导数→函数极限定义、幂函数导数公式→自然常数e、复合函数求导法则→复合函数、
  可导性与连续性关系→连续性、反函数求导法则→反函数。
- 依赖枢纽：`导数的定义` 入度 15（两章最大前置中枢，合理）、`函数的连续性` 9、`数列极限的定义` 7。

## 6.3 第三章增量全链路（2026-08-20）

- **准备**：`scripts/build_chapter_noex.py` 从 `高等数学上册第七版.md`（原书行 7008-9959）切出
  `第三章_全章_无习题.md`（2420 行 / 8 节 / 剔除 9 个习题块），与第二章做法一致。
- **L1 增量抽取**（`Extractor.py --provider deepseek`，因 opencode 403）：
  seed = `第二章_全章_semantic_v1_7_0_opencode_flash_high_global_repaired.json`（326 节点 / 520 关系）；
  `--seed-graph` 增量，输出 **`第三章_全章_semantic_v1_7_0_deepseek_flash_high.json`** = **418 节点
  （326 复用 seed + 92 新增）/ 688 关系 / 299 表达式**；67 chunk 全成功，1.44M token（70 分钟纯模型时间）。
- **Verifier**（`verify_graph.py --seed <Ch2图> --judge --apply`）：44 命中（新节点 10）→ LLM 判定 →
  **应用 15 条补边**（曲率/无穷小/未定式→极限、弧微分→微分、符号/绝对值/取整→函数、根的隔离→隔离区间、a/x^n→x→∞极限 等）+
  3 条人工确认项（光的折射定律 drop / 微分形式不变性→Claim / 隐函数的显化→Method），产出
  **`第三章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json`（418 节点 / 703 关系）**。
- **L2 组织**（同一 Organizer，`--chapter 第三章 --sections` 限 8 节，无章节特例）：
  **第三章 KU v1：21 单元**，全闸门绿；8 请求 / 37987 tokens。边界扫描仅 2 SMALL（柯西中值定理单例、
  函数图形的描绘）均正当，**0 修正**。
- **depends（第三章 v1：13 条）**：中值定理链语义漂亮——柯西→罗尔、拉格朗日→罗尔/柯西/泰勒2、
  泰勒2→柯西、单调性判定→拉格朗日、极值→罗尔、麦克劳林→泰勒、割线法→切线法。
- 注：第三章 11 个节点 evidence 只有单元素 heading_path（`['定理1 设']`，因第二/六节无 `一、`小节标题），
  靠 `section_from_line` 行号回退归节，uncovered 0 验证无恙。

## 6.4 三章集成 + Neo4j 最终态（2026-08-20）

- 合并（`scripts/merge_ku_layers.py`）：`两章合并_ku.json`(68) + 第三章 v1(21) = **89 KU**；
  `两章_ku_depends_v1.json`(82) + 第三章 v1(13) = **95 depends**（唯一）。成员 id 在 418 图内零失配。
- Neo4j（Docker `neo4j:5`，bolt://localhost:7687）：三者与容器曾 Exited(137) 需重启。
  `import_kg_to_neo4j.py --clear` 全量导入 L1 修复图 + 89 KU + 95 depends，核对通过：

| 内容 | 计数 |
|---|---|
| KGNode（Concept/Claim/Method） | 418 |
| L1 关系（7 种） | 703 |
| KnowledgeUnit（一+二+三章） | 89 |
| CENTERS_ON / CONTAINS | 140 / 302（共 442） |
| DEPENDS_ON（含跨章 7+ 条） | 95 |

- 跨章依赖抽查正确：求导法则→函数四则运算/导数的定义、反函数求导法则→反函数等。

## 6.5 第四章增量全链路（2026-08-20 夜间）

- **切片**：`build_chapter_noex.py` 从 `高等数学上册第七版(同济大学数学系).md`（**完整 7 章版**，20639 行）切
  `第四章_全章_无习题.md`（1423 行 / 5 节 / 去 6 习题块）。注：`高等数学上册第七版.md`（410KB）只到第四章开头，
  四章起必须用完整版 903KB 文件。
- **L1 抽取**（`--provider deepseek`，seed=第三章 repaired 485/804）：**485 节点（418 seed + 67 新增）/ 790 关系 / 358 表达式**，49 chunk。
  两次抽取器固化（见下）后才跑通。
- **抽取器新固化（本章踩坑，均已提交）**：
  1. 证据端点允许落在 chunk 上下文窗口（`_sanitize_evidence`；模型引用 context 行"倒代换 L867"不再硬失败，打印 warning）——commit `3847bc7c`；
  2. 同名 create 自动转 reuse 并记入 identity_conflicts 审计（模型重排列积分表行，如 q2 余切函数的积分公式、q6/q9 x²−a² 同名）——commit `310db043`；
  3. reuse 的 claim_kind 标签差异改为保留 canonical + 记日志（两处：phase1 校验与 phase2 `_merge_reuse_action`）——commit `8c23df72`、`a71d251b`。
- **Verifier**（`--seed 第三章 repaired --judge --apply`）：44 命中（新 14）→ LLM 判定 → **应用 14 条补边**
  （涉及 10 / special_case_of 2 / defined_in_terms_of 1 / uses 1；如 积分曲线→原函数、万能代换→三角函数、
  递推公式→不定积分等）。**修复一个 apply 解析 bug**：judge 的 detail 是冒号式 `involves: 不定积分`，而 apply
  只认 `->`，首轮 12 条全被 skip（repaired==raw）；已兼容两种格式（commit `3ff8ea07`）。产出
  **`第四章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json`（485 节点 / 804 关系）**。
- **L2 组织**：**必须 `--sections` 限定本章节**（输入是累计图，不带会把前三章 seed 节点也组织一遍——首次误出
  105 单元含旧章，重跑后 10 单元，全闸门绿）。**第四章 KU v1：10 单元**（原函数与不定积分概念 / 基本积分表 /
  不定积分性质 / 第一类换元法 / 第二类换元法 / 分部积分法 / 有理函数的积分 / 万能代换 / 简单根式代换 / 积分表的使用），
  5 请求 / 24343 tokens。
- **depends（第四章 v1）**：确定性 `build_ku_depends.py` **0 条**（无跨单元 derived_from 边；本章主要靠
  uses/defined_in_terms_of，语义 depends 需 LLM 审核版）。
- **合并**：`merge_ku_layers.py` → `第一二三四章_knowledge_units_merged.json`（**99 KU**）/ `第一二三四章_ku_depends_merged.json`（95，Ch4 未增）。
- **遗留**：第四章 depends LLM 审核版待做；第四章 KU 无 Gold；`光的折射定律` verifier 判 involves 正弦函数（与旧建议 drop 冲突，待人工复核）。

## 6.6 第五章增量全链路（2026-08-20 夜间）

- **切片**：`第五章_全章_无习题.md`（1552 行 / 4 节 / 去 6 习题块）。
- **L1 抽取**（seed=第四章 repaired 485/804）：**546 节点（+61 新增）/ 892 关系 / 408 表达式**，44 chunk。
  途中踩 2 个模型关系类型笔误（chunk8 `involves` Claim→Claim、chunk24 `special_case_of` Concept→Claim），
  重试即越（模型自纠正）；抽取器 claim_kind 容错补齐第二处（`a71d251b`）后 chunk23 才过。
- **Verifier**（`--seed 第四章 repaired --judge --apply`）：39 命中（新 14）→ **LLM 全部判 keep、0 补边**
  （多数理由：图内无独立"极限"节点，不臆造 `defined_in_terms_of→极限`；或已有 special_case_of/继承 involves）。
  2 项 retag（微分形式不变性→Claim、隐函数的显化→Method）仍归人工待定。Ch5 repaired == raw（546/892）。
- **L2**：`--sections` 限定 4 节 → **第五章 KU v1：12 单元**（定积分的定义 / 基本性质 / 中值定理 /
  积分上限函数 / 牛顿-莱布尼茨公式 / 换元法 / 分部积分法 / 两种反常积分 / 近似计算等），全闸门绿，4 请求。
- **depends（第五章 v1：3 条）**：`无界函数的反常积分→牛顿-莱布尼茨公式`、`无穷限反常积分→牛顿-莱布尼茨公式`、
  `牛顿-莱布尼茨公式→积分上限的函数及其导数`。
- **合并**：→ `第一二三四五章_knowledge_units_merged.json`（**111 KU**）/ `第一二三四五章_ku_depends_merged.json`（98）。

## 6.7 第六章增量全链路（2026-08-20 夜间）

- **切片**：`第六章_全章_无习题.md`（782 行 / 3 节 / 去 3 习题块）。
- **L1 抽取**（seed=第五章 repaired 546/892）：**574 节点（+28 新增）/ 920 关系 / 425 表达式**，22 chunk，identity_conflict 0。
- **Verifier**（`--seed 第五章 repaired --judge --apply`）：命中多判 keep（"极限节点不存在/已 defined_in_terms_of"），
  **5 条补边**（光滑曲线弧→可求长曲线弧/导数、曲边梯形→连续函数、极坐标面积元素→微分、极坐标曲边扇形面积公式→定积分）；
  3 项人工待定（等速直线运动的路程公式、微分形式不变性、隐函数的显化）。
- **L2**（`--sections` 3 节）→ **第六章 KU v1：9 单元**（定积分的元素法 / 直角与极坐标平面图形面积 /
  旋转体体积 / 平行截面立体体积 / 平面曲线弧长 / 变力作功 / 水压力 / 引力），全闸门绿，3 请求。
- **depends（第六章 v1）**：确定性 0 条（应用公式多靠 uses/defined_in_terms_of，语义 depends 待 LLM 审核版）。
- **合并**：→ `第一二三四五六章_knowledge_units_merged.json`（**120 KU**）/ `第一二三四五六章_ku_depends_merged.json`（98）。

## 6.8 第七章增量全链路（2026-08-20 夜间）+ 全书七章完成

- **切片**：`第七章_全章_无习题.md`（3197 行 / 8 节 / 去 12 习题块）。
- **L1 抽取**（seed=第六章 repaired 574/920）：**681 节点（+107 新增）/ 1076 关系 / 504 表达式**，48 chunk。
  途中踩多个坎（均处理并记录）：
  - chunk8/31 `uses` Concept→Concept 关系错误 → 重试自纠正一个；chunk31 复发两次，根因是**前面 chunk 把
    "常数变易法"误建为 Concept**（真为 Method），缓存定向修正（Concept→Method + 把 chunk12 里它的
    `defined_in_terms_of` 边修为 `uses`）后通过；
  - chunk13 表达式 LaTeX 候选超出上下文证据行 → 表达式校验与证据宽松对齐（可见行集）——commit `6be2dea1`。
- **Verifier**（`--seed 第六章 repaired --judge --apply`）→ **3 条补边**（微分方程的阶→微分方程、微分算子→导数、
  高阶微分方程→微分方程）+ 4 项人工待定（待定系数法、微小量分析法、微分形式不变性、隐函数的显化）。
  产出 `第七章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json`（681 节点 / 1079 关系）。
- **L2**（`--sections` 8 节）→ **第七章 KU v1：22 单元**（微分方程定义与阶 / 可分离变量 / 齐次 / 一阶线性 /
  伯努利 / 可降阶三类 / 高阶线性结构与朗斯基 / 通解结构 / 常数变易法 / 常系数齐次与非齐次特解两类 / 振动），
  全闸门绿，8 请求。
- **depends（第七章 v1）**：确定性 0 条（语义依赖待 LLM 审核版）。
- **全书合并**：`全书七章_knowledge_units_merged.json`（**142 KU** = 46+22+21+10+12+9+22）/ `全书七章_ku_depends_merged.json`（98）。
- **Neo4j 最终态**（`--clear` 全量导入）：KGNode **681** / L1 关系 **1079** / KnowledgeUnit **142** /
  CENTERS_ON 219 + CONTAINS 542 = 761 / **DEPENDS_ON 98**；跨章依赖抽查正确。

## 6.9 全书七章总结（2026-08-20 夜间一次性跑通）

| 章 | L1 新增 | 累计节点 | KU | depends | 备注 |
|---|---|---|---|---|---|
| 第一章（历史） | — | 197 | 46 | 45(LLM v4) | v7 基线 |
| 第二章（历史） | +129 | 326 | 22 | — | 与一章合并 82 |
| 第三章（历史） | +92 | 418 | 21 | 13 | 三章合并 95 |
| 第四章 | +67 | 485 | 10 | 0(确定性) | 本章起用完整版教材 903KB |
| 第五章 | +61 | 546 | 12 | 3 | verifier 0 补边（keep-all） |
| 第六章 | +28 | 574 | 9 | 0 | verifier 5 补边 |
| 第七章 | +107 | 681 | 22 | 0 | 3 补边 + 缓存类型修正 |
| **全书** | — | **681** | **142** | **98** | Neo4j 已全量导入 |

- 全程 deepseek（opencode 403 中）；抽取器共做 6 处修复（本夜 5 处 + 历史 1 处），详见各章小节与 runbook。
- **清洗记录**：night 抽取器修复 commit——`3847bc7c`（证据上下文窗）、`310db043`（同名 create 自动复用）、
  `8c23df72`+`a71d251b`（reuse claim_kind 容错两处）、`6be2dea1`（LaTeX 候选可见行集）；`3ff8ea07`（verify apply 冒号 detail）。
- 遗留（见 §7）：四~七章 depends 均为确定性 v1（0-3 条，需 LLM 审核版补语义边）；九处 verifier 人工项待定；
  四~七章 KU 无 Gold；层一粒度合并目录仍是独立步骤。

## 7. 已知局限与下一步

1. ~~depends_on 确定性稀疏版~~ **已完成 LLM 语义审核版**（v4：45 条，§4.2；两章合并 v1：82 条，§6.2；
   三章集成 **95 条**，§6.4）；仍可在 Neo4j 内复核全图，按需对依赖边补人工 Gold（`audit.edges` 带逐边证据与判据）。
2. ~~边界人工审核~~ **已完成**（v4/v7 修正；第三章 21 单元扫描 0 修正）。
3. **第三章残留人工项**（verifier 标出 3 项）：`光的折射定律`（外部物理，建议 drop）、
   `微分形式不变性`（Concept→Claim）、`隐函数的显化`（Concept→Method）——`retag/drop` 需人工确认。
4. **layer-1 粒度合并仅演示**（§1 决策 3）：完整语义碎片目录仍需语义合并 agent（Gold F2 已给首例）。
5. **跨章扩容**：第三章（8 节）单次 organizer 正常；第四章（不定积分，积分）节更大，若单节超
   ~200 节点需分层/重叠分块。
6. opencode 端点当前 403（限流/鉴权），全链路走 deepseek 兜底；恢复后无代码改动。
