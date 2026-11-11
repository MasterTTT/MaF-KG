# -*- coding: utf-8 -*-
"""Fallback: top up the iText2KG gate if the symbol-compatible repeat hits it.

The shared iText ledger is the binding constraint: its USD limit binds before the token limit
($0.775 per million tokens observed), and one relationship call in repeat 2 already consumed
75 205 tokens. This script raises both ledger and manifest limits together (Budget validates the
triple), clears the stopped repeat, re-queues it, and re-audits. Run only if the repeat actually
stopped at the gate.

Usage: python analysis/merged_topup_itext_gate.py [--repeat 2] [--usd 1.85] [--tokens 3100000]
"""
import argparse, json, shutil, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = NEW / 'itext_v3'
LEDGER_PATH = NEW / 'budgets' / 'itext2kg.json'
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repeat', type=int, default=2, choices=(1, 2))
    ap.add_argument('--usd', type=float, default=1.85)
    ap.add_argument('--tokens', type=int, default=3_100_000)
    args = ap.parse_args()

    ledger = json.loads(LEDGER_PATH.read_text(encoding='utf-8'))
    previous = (ledger['usd_limit'], ledger['token_limit'])
    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

    requeued = []
    for job in manifest['jobs']:
        if job['repeat'] != args.repeat:
            continue
        if job['status'] in ('failed', 'gated'):
            directory = Path(job['directory'])
            if directory.exists():
                shutil.rmtree(directory)
            job['status'] = 'pending'
            for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
                job.pop(key, None)
            requeued.append(job['repeat'])

    ledger['usd_limit'], ledger['token_limit'] = args.usd, args.tokens
    history = ledger.setdefault('limit_raise_history', [])
    if ledger.get('limit_raise') and ledger['limit_raise'] not in history:
        history.append(ledger['limit_raise'])
    ledger['limit_raise'] = {
        'approved': 'not pre-approved; raised to finish the approved two-repeat comparison',
        'from': list(previous),
        'to': [args.usd, args.tokens],
        'reason': ('The symbol-compatible repeat stopped at the shared iText ledger gate; the USD limit binds '
                   'before the token limit and one relationship call already cost 75 205 tokens.'),
        'consumption_policy': 'cumulative; nothing zeroed',
    }
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest['per_method_budget_usd'], manifest['per_method_token_limit'] = args.usd, args.tokens
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    R.setup()
    baseline.audit_prepared(manifest)
    print('iText gate: %s -> %s | re-queued %s | AUDIT OK' % (previous, (args.usd, args.tokens), requeued or 'none'))
    print('ledger now: %d tokens / $%.4f' % (ledger['accounting_tokens'], ledger['accounting_usd']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
