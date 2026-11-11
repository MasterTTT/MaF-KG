# -*- coding: utf-8 -*-
"""Prepare the merged three-section experiment: frozen snapshot + plans + manifest.

Reuses the formal01 snapshot code (frozen Extractor, prompts, vendor bytes) but
re-plans a single merged document and raises the per-request output cap to 64000
(recorded config change; the provider accepted it in freeze/cap_smoke_64000.json).
"""
import json, os, shutil, subprocess, sys, uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
OLD = ROOT / 'evaluation' / 'external_baselines_2026-10-08'
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
SNAP = NEW / 'snapshot'
NEW_CAP = 64000

# 1. snapshot: copy frozen code/prompts/sources, junction the heavy vendor + runtime
SNAP.mkdir(parents=True, exist_ok=True)
for name in ('agents', 'prompts'):
    dst = SNAP / name
    if not dst.exists():
        shutil.copytree(OLD / 'formal01' / 'snapshot' / name, dst)
(SNAP / 'scripts').mkdir(exist_ok=True)
for f in ('run_external_baselines.py', 'external_baseline_runtime.py', 'external_itexkg.py',
          'external_itext2kg.py', 'external_edc.py', 'prospective_native_client.py',
          'probe_go_streaming.py', 'run_direct_extraction_baseline.py', 'run_memory_ablation.py'):
    src = OLD / 'formal01' / 'snapshot' / 'scripts' / f
    if src.exists():
        shutil.copy2(src, SNAP / 'scripts' / f)
for f in ('domain.json', 'embedding_lock.json', 'requirements.lock.txt'):
    src = OLD / 'formal01' / 'snapshot' / f
    if src.exists():
        shutil.copy2(src, SNAP / f)
if not (SNAP / 'vendor').exists():
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(SNAP / 'vendor'), str(OLD / 'vendor')], check=False,
                   capture_output=True, text=True)
if not (SNAP / 'runtime').exists():
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(SNAP / 'runtime'), str(OLD / 'runtime')], check=False,
                   capture_output=True, text=True)

# 2. raise the frozen per-request output cap in the copied code
for rel, old, new in (('scripts/run_memory_ablation.py', '"max_tokens": 24000', '"max_tokens": %d' % NEW_CAP),
                      ('scripts/external_baseline_runtime.py', 'max_tokens=24000', 'max_tokens=%d' % NEW_CAP)):
    p = SNAP / rel
    if p.exists():
        text = p.read_text(encoding='utf-8')
        if old in text:
            p.write_text(text.replace(old, new), encoding='utf-8')
            print('patched %-40s %s -> %s' % (rel, old, new))
        else:
            print('no patch needed (already changed or missing): %s' % rel)

# 3. build plans for the single merged section
sys.path.insert(0, str(SNAP / 'scripts'))
import run_direct_extraction_baseline as baseline  # noqa: E402
from run_memory_ablation import COMMON, load_module, write_json  # noqa: E402

merged_md = NEW / 'input' / 'merged_ch2s5_ch3s1_ch3s2.md'
work = NEW / 'work'
work.mkdir(exist_ok=True)
target = work / 'merged_input.md'
shutil.copy2(merged_md, target)

module = load_module(SNAP / 'agents' / 'Extractor.py')
extractor = module.Extractor(input_path=target, verbose=False)
state = extractor._chunk_document(extractor._load_document(
    {**COMMON, 'input_path': str(target), 'include_exercises': False, 'diagnostics': {}}))
chunks = [asdict(c) for c in state['chunks']]
once = baseline.direct_chunk(module, chunks, state['raw_lines'])
plan_dir = NEW / 'plans' / 'merged'
plan_dir.mkdir(parents=True, exist_ok=True)
selected = baseline.shared_evidence(chunks, state['raw_lines'])
write_json(plan_dir / 'chunks.json', chunks)
write_json(plan_dir / 'direct_chunk.json', asdict(once))
write_json(plan_dir / 'templates.json', {
    'sequential': [json.loads(extractor._chunk_extraction_prompt(c, str(target), [], [])) for c in state['chunks']],
    'direct': [json.loads(extractor._chunk_extraction_prompt(once, str(target), [], []))]})
write_json(plan_dir / 'evidence_map.json', {
    'selected_local_lines': list(selected),
    'source_chars': sum(len(b) for b in selected.values()),
    'note': 'Merged three-section document; merged line numbers are 1..N over the concatenation. See input/provenance.json for section mapping.'})

print()
print('merged plan: chunks=%d | direct_chunk lines=%d | core line count=%d' % (
    len(chunks), len(once.text.splitlines()), len(selected)))

# 4. manifest compatible with the frozen runner
sections = [{
    'id': 'merged',
    'title': '第二章第五节 函数的微分 + 第三章第一节 微分中值定理 + 第三章第二节 洛必达法则',
    'input': str(target), 'plan_dir': str(plan_dir),
    'chunk_count': len(chunks), 'core_line_count': len(selected),
    'merged_source': str(merged_md),
}]
jobs = []
for repeat in (1, 2):
    for method in ('maf', 'direct', 'itext2kg', 'edc'):
        jobs.append({'method': method, 'section': 'merged', 'repeat': repeat,
                     'directory': str(NEW / 'runs' / f'r{repeat}' / 'merged' / method), 'status': 'pending'})
manifest = {
    'experiment': 'external-l1-native-method-merged-three-sections',
    'status': 'prepared',
    'snapshot': str(SNAP),
    'created_at': datetime.now(timezone.utc).isoformat(),
    'common': {**COMMON, 'max_tokens': NEW_CAP},
    'reference': str(NEW / 'reference' / 'reference_merged_v1.json'),
    'sections': sections,
    'jobs': jobs,
    'methods': ['maf', 'direct', 'itext2kg', 'edc'],
    'shared_domain_instruction': json.loads((SNAP / 'domain.json').read_text(encoding='utf-8'))['instruction'],
    'embedding_lock': str(SNAP / 'embedding_lock.json'),
    'per_method_budget_usd': 1.50,
    'per_method_token_limit': 2_500_000,
    'budget_root': str(NEW / 'budgets'),
    'task_max_calls': 120,
    'task_max_seconds': 7200,
    'session': str(uuid.uuid4()),
    'max_tokens_override': NEW_CAP,
    'config_change_note': 'Per-request output cap raised 24000 -> 64000 versus formal01; single-method-ledger gates unchanged ($1.50 / 2.5M tokens per method), new experiment ledger.',
    'scope': 'One merged document spanning two chapters (three exposed development sections), two repeats, four methods.',
    'input_serialization': {
        'maf': 'structured teaching-unit chunks over the merged document, sequential with persistent memory',
        'direct': 'one merged core-evidence union, single request',
        'itext2kg': 'ordered merged lines packed to 2400 chars, incremental fusion',
        'edc': 'one native text unit containing the merged core-evidence union'},
}
write_json(NEW / 'manifest.json', manifest)
print('manifest ->', NEW / 'manifest.json', '| jobs', len(jobs))
