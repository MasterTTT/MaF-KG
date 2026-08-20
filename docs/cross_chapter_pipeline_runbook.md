# 跨章管线 Runbook（第三~七章增量，2026-08-21 起）

每章严格按序执行（下一章 seed = 上一章 repaired 图）。全部用 venv python
（`E:\MaF-KG\.venv\Scripts\python.exe`），API key 先注入进程：

```powershell
$env:OPENCODE_API_KEY = [Environment]::GetEnvironmentVariable('OPENCODE_API_KEY','User')
$env:DEEPSEEK_API_KEY  = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')
```

## 0. 切章（已完成 4-7）

```powershell
$book='E:\MaF-KG\data\raw\高等数学上册第七版(同济大学数学系).md'
# 第四章 不定积分 L9218-11082 | 第五章 定积分 L11083-13442
# 第六章 定积分的应用 L13443-14439 | 第七章 微分方程 L14440-19005
python scripts\build_chapter_noex.py $book <start> <end> --out "E:\MaF-KG\data\raw\第X章_全章_无习题.md"
```

## 1. L1 增量抽取（seed=上一章 repaired 图）

```powershell
python -X utf8 agents\Extractor.py `
  --input 'E:\MaF-KG\data\raw\第X章_全章_无习题.md' `
  --output 'E:\MaF-KG\KG\第X章_全章_semantic_v1_7_0_deepseek_flash_high.json' `
  --provider deepseek --model deepseek-v4-flash `
  --chunk-strategy teaching-units --max-chunk-chars 3200 `
  --chunk-context-before-lines 6 --chunk-context-after-lines 4 `
  --memory-node-bytes 12000 --memory-relation-bytes 6000 `
  --max-tokens 64000 --max-retries 4 --request-delay 3 `
  --seed-graph '<上一章 repaired 图>' 2>&1 | Tee-Object "E:\MaF-KG\KG\_log_chX_extract.txt"
```

> seed 链：第三章 repaired(418/703) → 第四章 → 第五章 → 第六章 → 第七章。
> 抽取完成后 output 文件是"该章+历史 seed"的累计全局图（Ch3 后 418 = 326 seed + 92 新增）。

## 2. verifier 修复

```powershell
python -X utf8 scripts\verify_graph.py <第X章 抽取图> --seed <上一章 repaired> --judge `
  --apply "E:\MaF-KG\KG\第X章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json"
```

## 3. 第二层 KU 组织（同一 Organizer，无章节特例）

```powershell
python -X utf8 scripts\build_knowledge_units.py <第X章 repaired 图> `
  --out "E:\MaF-KG\KG\第X章_knowledge_units_v1.json" `
  --model deepseek-v4-flash --chapter 第X章 `
  --src-files "E:\MaF-KG\data\raw\第X章_全章_无习题.md"
```

## 4. depends（确定性 v1，与第三章同模式）

```powershell
python -X utf8 scripts\build_ku_depends.py <第X章 KU> <第X章 repaired 图> `
  --out "E:\MaF-KG\KG\第X章_ku_depends_v1.json"
```

## 5. 累计合并（merge_ku_layers.py）

```powershell
python -X utf8 scripts\merge_ku_layers.py `
  --ku1 "E:\MaF-KG\KG\<前N章>_knowledge_units_merged.json" `
  --ku2 "E:\MaF-KG\KG\第X章_knowledge_units_v1.json" `
  --out-ku "E:\MaF-KG\KG\<前N+1章>_knowledge_units_merged.json" `
  --deps1 "E:\MaF-KG\KG\<前N章>_ku_depends_merged.json" `
  --deps2 "E:\MaF-KG\KG\第X章_ku_depends_v1.json" `
  --out-deps "E:\MaF-KG\KG\<前N+1章>_ku_depends_merged.json"
```

（三章状态：`第一二三章_knowledge_units_merged.json` = 89 KU / 95 depends）

## 6. Neo4j 全量导入（里程碑 --clear）

```powershell
$env:NEO4J_PASSWORD='<用户提供>'
python -X utf8 scripts\import_kg_to_neo4j.py `
  --json <最新累计 repaired 图> `
  --ku-json <最新 merged KU> `
  --depends-json <最新 merged depends> --clear
```

## 注意

- opencode 端点 403（观测中），全链路 --provider deepseek（官方 API，慢但稳）。
- 抽取后台长任务不要阻塞等待（会被打断连带杀掉），靠完成通知 + 轻量 job_list。
- 每章完成后立即更新 `docs/experiment_knowledge_unit_layer2.md` 并 commit。
