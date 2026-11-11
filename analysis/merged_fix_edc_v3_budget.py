# -*- coding: utf-8 -*-
"""Repair the edc_v3 launch: align manifest budget limits with the raised ledger, reset the jobs.

The first launch failed before any API call: prospective_native_client.Budget requires the ledger's
(model, usd_limit, token_limit) to equal the manifest's values, and the manifest still carried the
pre-raise limits. This script sets them to the approved values, clears the failed run directories
(the adapter refuses to overwrite an existing audit), resets the job states, and re-audits.
"""
import json, shutil, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = NEW / 'edc_v3'
sys.stdout.reconfigure(encoding='utf-8')

LEDGER = json.loads((NEW / 'budgets' / 'edc.json').read_text(encoding='utf-8'))
manifest_path = OUT / 'manifest.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

before = (manifest['per_method_budget_usd'], manifest['per_method_token_limit'])
manifest['per_method_budget_usd'] = LEDGER['usd_limit']
manifest['per_method_token_limit'] = LEDGER['token_limit']
manifest['budget_alignment_note'] = ('Manifest limits must equal the ledger limits (Budget validates the triple); '
                                     'the first launch failed before any API call because they diverged.')
for job in manifest['jobs']:
    directory = Path(job['directory'])
    if directory.exists():
        shutil.rmtree(directory)
        print('cleared failed run dir ->', directory.name)
    job['status'] = 'pending'
    for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
        job.pop(key, None)
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print('manifest limits: %s -> %s (ledger %s)' % (before, (manifest['per_method_budget_usd'],
                                                          manifest['per_method_token_limit']),
                                                 (LEDGER['usd_limit'], LEDGER['token_limit'])))

sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
import run_external_baselines as R  # noqa: E402
import run_direct_extraction_baseline as baseline  # noqa: E402
R.setup()
baseline.audit_prepared(manifest)
print('AUDIT OK | jobs', [(j['repeat'], j['status']) for j in manifest['jobs']])
print('ledger untouched: %.4f usd / %d tokens | unsettled reservations: %d' % (
    LEDGER['accounting_usd'], LEDGER['accounting_tokens'],
    sum(1 for r in LEDGER['requests'] if r.get('status') == 'reserved')))
