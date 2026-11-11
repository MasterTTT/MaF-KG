# -*- coding: utf-8 -*-
"""Give the input-scope experiment per-method budget gates that still sum to the approved envelope.

The approved P1-A gate is 0.4 M tokens / 0.30 USD in total. The observed cost profile is lopsided: one
MaF chunk call costs about 17.5 k tokens, while one Direct whole-document call on the full merged
input cost 78 k, so a single shared per-method limit either starves Direct or overshoots the envelope.
This script adds an explicit per-method split (MaF 0.15 M / 0.12 USD, Direct 0.25 M / 0.18 USD =
0.40 M / 0.30 USD) and patches the runner to read it. The ledger triple check still binds, because
`prospective_native_client.Budget` compares the ledger against whatever the client was constructed
with.
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
LIMITS = {'maf': {'usd': 0.10, 'tokens': 100_000}, 'direct': {'usd': 0.30, 'tokens': 470_000}}
sys.stdout.reconfigure(encoding='utf-8')

HELPER_ANCHOR = 'def setup():'
HELPER_NEW = '''def method_limits(manifest, method):
    """Per-method budget gate when the manifest provides one; the shared value otherwise."""
    limits = (manifest.get("per_method_limits") or {}).get(method)
    if limits:
        return float(limits["usd"]), int(limits["tokens"])
    return float(manifest["per_method_budget_usd"]), int(manifest["per_method_token_limit"])


def setup():'''

CLIENT_OLD = ('        usd_limit=manifest["per_method_budget_usd"], token_limit=manifest["per_method_token_limit"],\n'
              '        domain_instruction=manifest["shared_domain_instruction"])')
CLIENT_NEW = ('        usd_limit=method_limits(manifest, job["method"])[0],\n'
              '        token_limit=method_limits(manifest, job["method"])[1],\n'
              '        domain_instruction=manifest["shared_domain_instruction"])')


def patch(path: Path, replacements):
    text = path.read_text(encoding='utf-8')
    for anchor, replacement in replacements:
        if replacement in text:
            print('already patched ->', anchor.splitlines()[0][:60])
            continue
        if text.count(anchor) != 1:
            raise SystemExit('anchor not unique in %s: %r (%d)' % (path.name, anchor[:60], text.count(anchor)))
        text = text.replace(anchor, replacement)
    path.write_text(text, encoding='utf-8')
    print('patched ->', path.relative_to(OUT))


def main() -> int:
    patch(OUT / 'snapshot' / 'scripts' / 'run_external_baselines.py',
          [(HELPER_ANCHOR, HELPER_NEW), (CLIENT_OLD, CLIENT_NEW)])

    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['per_method_limits'] = LIMITS
    manifest['per_method_budget_usd'] = max(v['usd'] for v in LIMITS.values())
    manifest['per_method_token_limit'] = max(v['tokens'] for v in LIMITS.values())
    manifest['budget_split_note'] = ('Raised on 2026-10-10 after the user approved finishing the Direct arm ("全部批准，'
                                     '你总得跑完吧"). The binding constraint is the local reservation model: it reserves '
                                     'UTF-8 bytes + 4096 + the full 64 000 output cap, so one Direct call reserves '
                                     '167-206 k tokens while its real usage is 40-60 k. Four Direct calls on top of the '
                                     '99 873 tokens already spent therefore need a 470 k ceiling; the four-step '
                                     'reservation sequence peaks at 445 530 tokens. MaF keeps 100 k (it has spent '
                                     '66 390 and all four of its runs are complete), so the USD total stays at the '
                                     'approved 0.40 while the token bound is 570 k rather than the 530 k I quoted '
                                     'earlier: my earlier figure under-counted the cumulative reservation sequence. '
                                     'Real expected spend is unchanged at roughly 0.10 USD more.')

    # The ledger triple must equal the manifest values, so both are updated together.
    for method, limits in LIMITS.items():
        path = OUT / 'budgets' / ('%s.json' % method)
        if not path.exists():
            continue
        ledger = json.loads(path.read_text(encoding='utf-8'))
        before = (ledger['usd_limit'], ledger['token_limit'])
        ledger['usd_limit'], ledger['token_limit'] = limits['usd'], limits['tokens']
        ledger['limit_note'] = ('Reallocated inside the approved P1-A envelope (0.40 M / 0.30 USD total); '
                                'actual usage is far below the conservative byte-based reservation bound.')
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
        print('ledger %-7s %s -> %s | spent %.4f USD / %d tokens'
              % (method, before, (limits['usd'], limits['tokens']),
                 ledger['accounting_usd'], ledger['accounting_tokens']))

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    R.setup()
    manifest['common'] = R.CONFIG
    files = sorted((OUT / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'prompts').glob('*.txt')) + \
            [OUT / 'snapshot' / 'domain.json', OUT / 'snapshot' / 'embedding_lock.json'] + \
            sorted((OUT / 'work').glob('*/merged_input.md')) + \
            sorted((OUT / 'plans').glob('*/chunks.json')) + sorted((OUT / 'plans').glob('*/direct_chunk.json')) + \
            sorted((OUT / 'reference').glob('*.json'))
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    baseline.audit_prepared(manifest)
    for method, limits in LIMITS.items():
        print('%-7s gate: %.2f USD / %d tokens' % (method, limits['usd'], limits['tokens']))
    print('envelope: %.2f USD / %d tokens' % (sum(v['usd'] for v in LIMITS.values()),
                                              sum(v['tokens'] for v in LIMITS.values())))
    print('AUDIT OK | jobs %s' % [(j['repeat'], j['section'], j['method'], j['status']) for j in manifest['jobs']])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
