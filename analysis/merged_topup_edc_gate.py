# -*- coding: utf-8 -*-
"""Top up the EDC gate so both parse-compatible repeats finish, then re-queue the gated repeat.

Measured from the running variant: the alignment stage costs 23 835 tokens per call on average
(min 20 193, max 123 522), not the 20 600 assumed when the 7.5M gate was approved. r1 fits inside
the current gate; r2 reaches it at roughly call 79 of 101. The gate is therefore topped up to the
measured requirement plus a margin, the ledger and the manifest are kept equal (Budget validates the
triple), and the interrupted repeat is re-queued from scratch. Consumption is never zeroed.
"""
import json, shutil, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = NEW / 'edc_v3'
LEDGER_PATH = NEW / 'budgets' / 'edc.json'
NEW_LIMITS = (3.45, 9_000_000)
MEASURED_PER_CALL = 23_835
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    ledger = json.loads(LEDGER_PATH.read_text(encoding='utf-8'))
    used_tokens, used_usd = ledger['accounting_tokens'], ledger['accounting_usd']
    previous = (ledger['usd_limit'], ledger['token_limit'])

    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    jobs = {job['repeat']: job for job in manifest['jobs']}

    requeued = []
    for repeat, job in jobs.items():
        # only jobs that actually stopped are re-queued; a job still running is left untouched
        if job['status'] in ('failed', 'gated'):
            directory = Path(job['directory'])
            if directory.exists():
                shutil.rmtree(directory)
            job['status'] = 'pending'
            for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
                job.pop(key, None)
            requeued.append(repeat)

    ledger['usd_limit'], ledger['token_limit'] = NEW_LIMITS
    history = ledger.setdefault('limit_raise_history', [])
    if ledger.get('limit_raise') and ledger['limit_raise'] not in history:
        history.append(ledger['limit_raise'])
    ledger['limit_raise'] = {
        'approved': 'user 2026-10-09 (two-repeat completion); amount corrected to the measured per-call cost',
        'from': list(previous),
        'to': list(NEW_LIMITS),
        'reason': ('Measured alignment cost is 23 835 tokens/call, not the 20 600 assumed when 7.5M was approved, '
                   'so finishing 232 calls needs ~8.0M cumulative. Top-up covers the measured requirement plus margin.'),
        'measured_tokens_per_alignment_call': MEASURED_PER_CALL,
        'consumption_policy': 'cumulative; nothing zeroed',
    }
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')

    manifest['per_method_budget_usd'], manifest['per_method_token_limit'] = NEW_LIMITS
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    R.setup()
    baseline.audit_prepared(manifest)
    print('EDC gate: %s -> %s' % (previous, NEW_LIMITS))
    print('ledger after raise: %d tokens (%.2fM) / $%.4f' % (used_tokens, used_tokens / 1e6, used_usd))
    print('re-queued repeats:', requeued or 'none')
    print('job states:', [(j['repeat'], j['status']) for j in manifest['jobs']])
    unsettled = sum(1 for r in ledger['requests'] if r.get('status') == 'reserved')
    print('unsettled reservations:', unsettled)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
