# 第一层第二节节点与关系收敛实验 01

## 1. 实验目标

将第一章第二节的第一层图谱从过细、过密的抽取结果收敛到教材可独立引用的数学语义，同时保持例题跳过策略。

目标节点为 11 个：

- Concept：数列、数列的极限、数列有界性、子数列
- Method：ε-N 论证法
- Claim：极限唯一性、收敛数列必有界、保号性、保号性推论、子数列极限性质
- Detail Claim：两个子数列极限不同则原数列发散

以下内容不独立建点：状态类语法变体、数列的项/一般项、定义内部辅助量词与邻域、解释性表示观点、重复逆否结论，以及例题局部观察。

## 2. 固定实验条件

- 输入：`data/raw/第一章第二节.md`，不含习题
- Provider：OpenCode Go
- Model：`deepseek-v4-flash`
- Thinking：enabled
- Reasoning effort：low
- Chunk：`teaching-units`
- Resume：disabled
- 例题：显式 example sequence 整块跳过
- 例题后独立总结：重新进入 exposition chunk
- Schema：`maf-kg.semantic.v1.7.0`

## 3. 实施内容

1. 为 Claim 增加可选 `logical_form`，支持 `implies`、`iff`、AND 前提和 `converse_status`。
2. 将必要非充分信息收敛到原 Claim，例如收敛数列必有界使用 `converse_status=disproved`。
3. 增加严格、可选的缺失 logical_form 定向复查；只允许更新既有候选 Claim，不能创建节点。
4. 修复例题边界：L155-L204、L207-L244、L265-L290 仍跳过，L205 方法总结独立保留。
5. 增加直接 `derived_from` 支配下的冗余 `involves` 约简。
6. 收紧通用准入规则：辅助记号、定义内部状态、组件标签、解释观点和例题观察不升格。
7. OpenCode 默认密钥环境变量统一为 `OPENCODE_API_KEY`。
8. 更新第二节节点 Gold，并新增关系正例 Gold：`evaluation/gold/relations_section2_v1.json`。

## 4. 实验结果

| 轮次 | 节点 | 关系 | 主要现象 |
| --- | ---: | ---: | --- |
| 原基线 | 20 | 25 | 状态、项/一般项、解释性 Claim 和重复逆否大量过抽 |
| 初次约简 | 18 | 16 | 关系明显变稀，但仍有 7 个过细节点 |
| 准入收紧 | 12 | 13 | 核心集合基本正确，仍保留一个例题观察；Method 名称偏口语化 |
| 严格失败样本 | 未完成 | 未完成 | 模型将 `logical_form.conclusion` 返回为数组，Pydantic 按设计直接拒绝 |
| 最终有效轮 | 11 | 12 | 4 Concept + 1 Method + 6 Claim；零孤立点 |

最终有效输出：

`E:/MaF-KG/KG/第一章第二节_semantic_v1_7_0_opencode_flash_low_final_v2.json`

节点 Gold 结果：

- Precision：1.0000
- Recall：1.0000
- F1：1.0000
- Core recall：9/9
- Detail recall：2/2
- 结构错误：0
- 孤立节点：0

关系正例人工对齐：

- 12 条审定正例中命中 11 条，正例召回 91.7%
- 唯一错误：`子数列 -> 数列` 输出为 `defined_in_terms_of`，Gold 要求 `special_case_of`
- 其余方向、类型和端点符合关系正例集
- Gold 不是封闭负例集，因此不报告严格关系 precision/F1

## 5. logical_form 结果

六个 Claim 均已在首轮抽取中获得 logical_form，最终定向复查无候选：

- 收敛数列必有界正确记录 `converse_status=disproved`
- 保号性推论使用两个 AND 前提
- 子数列判据与其依据通过 `derived_from` 连接
- 不再创建“有界是收敛的必要非充分条件”和“无界数列必发散”两个重复 Claim

## 6. 仍需注意

1. `子数列 special_case_of 数列` 在不同无缓存运行间存在关系类型波动，说明 Prompt 已明确但模型执行不完全稳定。
2. 有界性 Claim 的证据 L291-L321 跨度较宽，但这是为了同时支撑定理方向和反例给出的 converse failure，不是例题污染。
3. 数列极限定义证据 L100-L120 跨 21 行，属于完整定义和必要注释，保留合理。
4. 一次严格 schema 失败证明 fail-fast 生效；不应添加静默类型修复或 fallback。

## 7. 复现实验

```powershell
.venv\Scripts\python.exe -X utf8 -u agents\Extractor.py `
  --input "E:\MaF-KG\data\raw\第一章第二节.md" `
  --output "E:\MaF-KG\KG\第一章第二节_semantic_v1_7_0_opencode_flash_low.json" `
  --provider opencode-go `
  --model deepseek-v4-flash `
  --thinking enabled `
  --reasoning-effort low `
  --chunk-strategy teaching-units `
  --logical-form-review `
  --no-resume `
  --no-proxy `
  --max-retries 4 `
  --request-delay 3
```

```powershell
.venv\Scripts\python.exe -X utf8 scripts\evaluate_kg.py `
  "E:\MaF-KG\KG\第一章第二节_semantic_v1_7_0_opencode_flash_low.json" `
  --gold evaluation\gold\semantic_first_three_sections_v4.json `
  --gold-source "第一章第二节.md"
```
