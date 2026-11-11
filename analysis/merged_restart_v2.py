# -*- coding: utf-8 -*-
"""Restart the merged experiment under frozen-config v2.

Archives the aborted v1 runs (evidence), resets job states, rebinds artifact hashes
for the patched Extractor, re-runs the official prepared-audit with setup() applied,
and appends the v2 record. Budget ledgers are NOT reset: cumulative spend continues.
"""
import json, shutil, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
SNAP = NEW / 'snapshot'
sys.path.insert(0, str(SNAP / 'scripts'))

import run_external_baselines as R  # noqa: E402
import run_direct_extraction_baseline as baseline  # noqa: E402

# 1. archive v1 runs
runs = NEW / 'runs'
arch = NEW / 'runs_v1_aborted'
if runs.exists():
    if arch.exists():
        shutil.rmtree(arch)
    shutil.move(str(runs), str(arch))
    print('archived v1 runs ->', arch.name)
runs.mkdir(parents=True, exist_ok=True)

# 2. reset jobs and rebind artifacts in the master + control manifests
files = sorted((SNAP / 'scripts').glob('*.py')) + sorted((SNAP / 'agents').glob('*.py')) + \
        sorted((SNAP / 'prompts').glob('*.txt')) + [SNAP / 'domain.json', SNAP / 'embedding_lock.json'] + \
        sorted((NEW / 'input').glob('*.md')) + [NEW / 'input' / 'provenance.json', NEW / 'input' / 'sections.json',
                                                NEW / 'work' / 'merged_input.md'] + sorted((NEW / 'plans' / 'merged').glob('*.json'))
artifacts = [baseline.bind(p) for p in files if p.exists()]

targets = [NEW / 'manifest.json'] + [NEW / 'ctl' / m / 'manifest.json' for m in ('maf', 'direct', 'itext2kg', 'edc')]
for p in targets:
    mf = json.loads(p.read_text(encoding='utf-8'))
    mf['common'] = R.CONFIG
    mf['artifacts'] = artifacts
    mf['frozen_config'] = 'v2 (24000->64000 output cap; blank-line evidence endpoints accepted)'
    for job in mf.get('jobs', []):
        job['status'] = 'pending'
        for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
            job.pop(key, None)
    p.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding='utf-8')
print('rebound artifacts:', len(artifacts))

# 3. official audit exactly as execute() performs it
R.setup()
ok = True
for m in ('maf', 'direct', 'itext2kg', 'edc'):
    mf = json.loads((NEW / 'ctl' / m / 'manifest.json').read_text(encoding='utf-8'))
    try:
        baseline.audit_prepared(mf)
        print('%-9s AUDIT OK (v2)' % m)
    except Exception as e:  # noqa: BLE001
        ok = False
        print('%-9s AUDIT FAIL: %s' % (m, e))

# 4. freeze record v2 note
rec = NEW / 'freeze' / 'FREEZE_RECORD.md'
text = rec.read_text(encoding='utf-8')
if 'FROZEN-CONFIG v2' not in text:
    text += """

## 附：FROZEN-CONFIG v2（2026-10-09，用户批准）

**触发**：v1 下 Direct 两轮均硬失败——引用的证据跨度端点落在被"核心证据并集"丢弃的**空行分隔符**上（如 L226），
Extractor 的端点校验要求两端都在可见行集内，遂报 `Evidence endpoints are not visible source lines`。
这是**度量瑕疵**（合并文档 1,094 行中有 16 个空行缺口，随文档变长而更易命中），不是模型能力问题。

**修复（最小）**：端点校验放宽为——端点必须在可见集内，**或**落在 chunk 源跨度内且该行为空行；未见过的**内容行**仍被拒绝。
若起点为空行，其来源结构元数据改由跨度内最近的内容行提供。已在 `snapshot/agents/Extractor.py` 打标 `FROZEN-CONFIG-v2`。

**影响面**：对四个臂统一生效；v1 的部分运行已归档到 `runs_v1_aborted/`，其 token 消耗**保留在账本中不重置**。
"""
    rec.write_text(text, encoding='utf-8')
    print('freeze record updated with v2 note')
print('audit all ok:', ok)
