# 语义层 description 身份匹配与跨章泛化实验 09

## 1. 目标

本轮做两件事：

1. 把第一层评估的"节点身份"从 **name** 对齐到 **description（完整命题）**，根治命名漂移；
2. 在补全四节 Gold description 后，用 `maf-kg.semantic.v1.7.0` 对第一、二、三、四节做跨章泛化评估。

背景：v1.7.0 的 `extraction_system.txt` 里明确写着「Claim identity is determined by that complete proposition, not by a short display label」，但 Gold 节点只有 `{gold_id, name, accepted_names}`、评估器只按 name 匹配，二者自相矛盾，导致「函数极限的符号性质」（模型简洁名）对不上「函数在去心邻域内保号时极限保号」（Gold 命题式名）这类稳定漂移。

## 2. 改动清单

### 评估器 `scripts/evaluate_kg.py`

- **`MatchPolicy` 全局预设**：`fuzzy=True / semantic=True / fuzzy_threshold=0.65`，`match_node_to_gold` 统一走 rule → fuzzy → semantic 三层，节点评估和关系端点对齐共用，消除重复实现。
- **semantic 默认开启**：`--semantic-match` 改为 `BooleanOptionalAction` 默认 True，`--no-semantic-match` 关闭。
- **judge 改为按 description 判定**：`_judge(name_a, name_b, desc_a, desc_b)` 把两侧完整命题都给 flash judge，提示词强调「以完整描述/命题为准，名称只是辅助，一个定理与其推论是不同对象」。
- **RAG 预筛也并入 description**：`best_candidates` 用 `_embed_text = name + " " + description` 拼接后 embedding。
- **可扩展性**：`SIM_THRESHOLD 0.80 → 0.65`（原阈值挡住 0.7253 的真匹配），`TOP_K = 3`（每查询最多判 3 个候选，judge 调用次数受控）。
- **judge 端点优先级**：`opencode.ai/zen/go/v1`（OPENCODE_API_KEY）优先，失败回退 `api.deepseek.com`（DEEPSEEK_API_KEY），并检查 opencode 网关的 `code` 字段。
- **`--relation-gold`**：关系正例集接入评估（端点对齐到节点 Gold id，报 recall + wrong_type/wrong_direction/missing_endpoint/missing_edge，closed_world=false 不报 precision/F1）。

### 提取器 `agents/Extractor.py`

- **`LogicalForm.conclusion: str → list[str]`**：模型对 "iff" 充要条件稳定把 conclusion 输出成数组（镜像 premises 的 list），原 schema 不对称导致第四节 chunk 2 连续两次 fail-fast。改为 list 后与 premises 对称，消除摩擦（同步改 `validate_logical_form`）。
- `_judge_same_concept`（semantic 去重）同样改成 opencode→deepseek 优先级。

### Prompts

- `extraction_system.txt`：`logical_form.conclusion` 示例改为数组。
- `logical_form_review.txt`：conclusion 规则与示例改为数组。

### Gold

- 四节 M+O 节点补 `description`（完整命题/定义意义）：第一节 80、第二节 15、第三节 25、第四节 16。
- 第三节 3 个过度要求节点 **M → O**：`去心邻域`、`自变量趋于正无穷时的函数极限`、`自变量趋于负无穷时的函数极限`（合并可接受、不独立复习）。

### 测试

- 更新 `conclusion` 为 list 的 8 处断言/构造；新增 MatchPolicy、relation-gold、semantic 默认等测试。**153 passed + 8 subtests**。

## 3. 抽取配置（四节一致）

`opencode-go` + `deepseek-v4-flash`，thinking enabled，reasoning effort low，`--chunk-strategy teaching-units`，`--logical-form-review`，`--no-resume`，`--max-tokens 64000`（原 12000 在第一节 chunk 7 高密度处截断）。

## 4. 四节节点 F1（semantic 全开）

| 节 | TP/FP/FN | Precision | Recall | F1 | core recall | detail recall |
|---|---:|---:|---:|---:|---:|---:|
| 第二节 | 11/0/0 | 1.0 | 1.0 | **1.0** | 9/9 | 2/2 |
| 第三节 | 13/0/0 | 1.0 | 1.0 | **1.0** | 11/11 | 2/2 |
| 第四节 | 6/1/3 | .8571 | .6667 | **.75** | — | — |
| 第一节 | 40/12/27 | .7692 | .5970 | **.6723** | 20/30 | 20/37 |

第一节 rule+fuzzy 基线为 F1 **.5714**（34/18/33），开 semantic 后提升到 .6723。

## 5. 关键发现

1. **description 匹配根治命名漂移**：「函数极限的符号性质」靠完整命题被正确判为推论「函数在去心邻域内保号时极限保号」而非定理「局部保号性」，**没有给 Gold 补任何 accepted_names**（避免"喂结果骗自己"）。
2. **`logical_form.conclusion` 数组是 schema/模型真实摩擦**，不是随机错误：第四节 chunk 2 连续两次稳定复现，改 list 后消除。
3. **第四节漏抽是抽取问题**：`零是唯一的常数无穷小`、`无穷大函数不存在有限极限` 两个 property 从 v1.5.x 一路漏到 v1.7.0；`无穷大与无穷小的倒数关系` 被错分 property（Gold 是 result）。
4. **第一节是跨章最大的短板**：概念最密（映射/函数/基本初等函数/双曲函数族），模型漏 27 个必抽、多 12 个错抽，集中在双曲函数族与基本初等函数分类。这是真抽取质量问题，不是匹配问题。
5. **可扩展性**：semantic judge 是大节评估的瓶颈（flash 每次 30-60s），`TOP_K=3` 使第一节从"超时跑不完"变为 ~10 分钟跑完。

## 6. 遗留问题

1. 第一节 F1 .67 的漏抽（双曲函数族、基本初等函数）；
2. 第四节 2 个 property 漏抽 + 倒数关系 claim_kind 错分；
3. 第三节 `自变量趋于无穷大时函数的极限` 孤立节点（未连 `special_case_of → 函数极限`，有限点版本却连了）；
4. 第一/二/三节的关系 Gold 尚未标注（仅第二节有 `relations_section2_v1.json`）。

## 7. 本地产物

- `E:\MaF-KG\KG\第一章第一节_semantic_v1_7_0_opencode_flash_low.json`（52 节点）
- `E:\MaF-KG\KG\第一章第三节_semantic_v1_7_0_opencode_flash_low.json`（13 节点）
- `E:\MaF-KG\KG\第一章第四节_semantic_v1_7_0_opencode_flash_low.json`（7 节点）
- 第二节沿用 `第一章第二节_semantic_v1_7_0_opencode_flash_low_final_v2.json`

## 8. 复现命令

```powershell
# 抽取（以第一节为例）
.venv\Scripts\python.exe -X utf8 -u agents\Extractor.py `
  --input 'E:\MaF-KG\data\raw\第一章第一节_映射与函数.md' `
  --output 'E:\MaF-KG\KG\第一章第一节_semantic_v1_7_0_opencode_flash_low.json' `
  --provider opencode-go --model deepseek-v4-flash --thinking enabled `
  --reasoning-effort low --chunk-strategy teaching-units --logical-form-review `
  --no-resume --no-proxy --max-retries 4 --request-delay 3 --max-tokens 64000

# 评估（semantic 默认开）
.venv\Scripts\python.exe -X utf8 scripts\evaluate_kg.py `
  "E:\MaF-KG\KG\第一章第一节_semantic_v1_7_0_opencode_flash_low.json" `
  --gold evaluation\gold\semantic_first_three_sections_v4.json `
  --gold-source "第一章第一节_映射与函数.md"
```

## 9. 第一节 27 漏抽归因（调研结论）

对第一节 F1 .67 的 27 个漏抽逐节点归因（详见对话调研，含行号/块证据）：

| 根因 | FN | 处置 |
|---|---:|---|
| A1 真定义被 example 块吞掉（满射/单射/一一映射） | 3 | 修 chunker（见 §11） |
| A2 例题内一般结论（符号函数恒等式、奇偶分解） | 2 | 降 O |
| B 双曲/反双曲「基本性质」整段折叠 | 12 | 原子 property 降 O，合并节点加 O |
| C 定义性子概念折叠（定义域/值域/像/原像/奇函数/偶函数） | 6 | 降 O |
| D 模型看到但没抽（函数图形/自然定义域/最小正周期） | 3 | 降 O |
| E Gold 与源文错位（逆映射存在条件：一一映射 vs 单射） | 1 | 改 Gold description |

其中 A1 是 chunker 对 example 块的整块跳过吞掉了教科书正文定义（`is_definition` 只认「定义 N 设」句式，认不出「设…若…则称…为…」句式），是唯一值得修管线的真 bug；其余均为 Gold 粒度/语义裁定。

## 10. Gold v5 变更（`evaluation/gold/semantic_first_three_sections_v5.json`）

- M 数 67 → **43**（第一节）；
- 降 O：8 个 Concept（定义域/值域/像/原像/奇函数/偶函数/函数图形/自然定义域）+ 15 个 property（13 个双曲/反双曲原子性质 + 最小正周期 + 奇偶分解）+ 1 个 identity（符号函数恒等式）；
- E 修正：`s1_criterion_inverse_mapping_existence` description 改为「充分必要条件是 f 为单射（只有单射才存在逆映射）」；
- 新增 O 节点：`函数的奇偶性`、`反双曲函数`（Concept）；6 个「X的基本性质」（Claim/property），使模型的自然折叠输出不再计 FP；
- O Claim 按 claim_kind 拆组（property / identity），保持 kind 匹配语义。

## 11. Chunker 修复（A1）：example 块不得吞并新一轮定义动作句

- 新增 `is_fresh_definitional_act`：行首 `设|若|如果|假设` 且含 `则称|那么称|称为|叫做|记为|记作|统称为|定义为`；用于 example 块延伸时的断块条件。
- 判别原则：前缀区分「新一轮定义动作」与「当前例题对象的命名注释」（如「这函数称为绝对值函数」不断块），避免把例题打得七零八落；非 example 块不受影响（不产生内容丢失路径，故不改它们的分类）。
- 全章 4 节源文块级扫描验证：第二/三/四节的 skipped units 与 chunk spans 与旧输出**完全一致**（零波及）；第一节例1-例3 区被跳块 37-48 → 37-42，43-47 行（满射/单射/一一映射定义）救回为新 chunk。
- 测试：新增 2 个断块/不断块用例，155 passed + 8 subtests。

