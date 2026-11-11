# -*- coding: utf-8 -*-
"""Separate frozen-config v1 sunk spend from v2 spend inside the merged-experiment ledgers.

The v2 restart deliberately did NOT reset the budget ledgers, and v1 ran from the same
`runs/<repeat>/merged/<arm>/...` paths as v2. Ledger labels therefore cannot distinguish the two
attempts, but entries are appended in chronological order and v2 re-runs every arm from its first
job, so the first repeated label marks the v1/v2 boundary. This script records that boundary and
both portions, so the cost table can report sunk spend without inventing a number.

Output: evaluation/external_baselines_merged_2026-10-09/budgets/ledger_split.json
"""
import json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')


def portion(entries):
    tokens, usd, bound_only = 0, 0.0, 0
    for entry in entries:
        usage = entry.get('usage') or {}
        if usage.get('total_tokens') is None:
            bound_only += 1
        tokens += usage.get('total_tokens') or entry.get('bound_tokens') or 0
        usd += entry.get('estimated_usd') if entry.get('estimated_usd') is not None else (entry.get('bound_usd') or 0)
    return {'requests': len(entries), 'tokens': tokens, 'usd': round(usd, 6),
            'entries_without_reported_usage': bound_only}


def main() -> int:
    out = {}
    for path in sorted((NEW / 'budgets').glob('*.json')):
        if path.stem == 'ledger_split':
            continue
        ledger = json.loads(path.read_text(encoding='utf-8'))
        entries = ledger.get('requests', [])
        seen, split = set(), None
        for index, entry in enumerate(entries):
            label = str(entry.get('label', ''))
            if label in seen:
                split = index
                break
            seen.add(label)
        v1 = entries[:split] if split is not None else []
        v2 = entries[split:] if split is not None else entries
        out[path.stem] = {
            'ledger_path': str(path),
            'ledger_accounting_tokens': ledger.get('accounting_tokens'),
            'ledger_accounting_usd': ledger.get('accounting_usd'),
            'v1_sunk': portion(v1),
            'v2': portion(v2),
            'boundary_index': split,
            'split_basis': 'first repeated job label in append order marks the v2 restart',
        }
    payload = {
        'note': ('Ledgers were not reset across the v1 abort, so the accounting totals include v1 sunk spend. '
                 'The split below is derived from append order, not from a reset record; entry labels are identical '
                 'for both attempts because v1 used the same run paths.'),
        'v1_sunk_total_tokens': sum(v['v1_sunk']['tokens'] for v in out.values()),
        'v1_sunk_total_usd': round(sum(v['v1_sunk']['usd'] for v in out.values()), 6),
        'ledger_total_tokens': sum(v['ledger_accounting_tokens'] or 0 for v in out.values()),
        'ledger_total_usd': round(sum(v['ledger_accounting_usd'] or 0 for v in out.values()), 6),
        'arms': out,
    }
    target = NEW / 'budgets' / 'ledger_split.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for arm, info in out.items():
        print('%-9s v1 %s | v2 %s | ledger %s tok / %s usd' % (
            arm, info['v1_sunk'], info['v2'], info['ledger_accounting_tokens'], info['ledger_accounting_usd']))
    print('v1 sunk total: %d tokens / %.4f usd' % (payload['v1_sunk_total_tokens'], payload['v1_sunk_total_usd']))
    print('ledger totals: %d tokens / %.4f usd' % (payload['ledger_total_tokens'], payload['ledger_total_usd']))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
