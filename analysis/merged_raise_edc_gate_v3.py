# -*- coding: utf-8 -*-
"""Raise the EDC ledger gate for the parse-compatible re-run (user approved 2026-10-09).

The fixed run must execute the alignment stage that never ran: one call per extracted triplet
(r1 131 + r2 101 = 232 calls at ~20.6k tokens each, measured from the v2 ledger) plus the two
replayed Extract/Define stages, which cost nothing. Cumulative consumption is never zeroed; the
previous raise is kept in a history list.
"""
import json, sys
from pathlib import Path

LEDGER = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09\budgets\edc.json')
NEW_LIMITS = (2.90, 7_500_000)
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding='utf-8'))
    previous = ledger.get('limit_raise')
    history = ledger.setdefault('limit_raise_history', [])
    if previous and previous not in history:
        history.append(previous)
    ledger['usd_limit'], ledger['token_limit'] = NEW_LIMITS
    ledger['limit_raise'] = {
        'approved': 'user 2026-10-09',
        'from': [previous['to'][0], previous['to'][1]] if previous else [1.5, 2500000],
        'to': list(NEW_LIMITS),
        'reason': ('The v2 EDC failure was a parser-compatibility fault, not a capability result: the pinned '
                   'Define parser requires ASCII ":" and dropped all 52/27 definitions, so the alignment stage '
                   'never executed (2 calls per repeat). The fixed run replays the frozen Extract/Define '
                   'responses and performs 232 alignment calls (one per triplet), which needs ~4.9M tokens.'),
        'reused_responses': True,
        'consumption_policy': 'cumulative; nothing zeroed',
    }
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    print('EDC gate: usd %.2f -> %.2f | tokens %d -> %d' % (
        previous['to'][0] if previous else 1.5, NEW_LIMITS[0],
        previous['to'][1] if previous else 2500000, NEW_LIMITS[1]))
    print('accounting unchanged: %.4f usd / %d tokens' % (ledger['accounting_usd'], ledger['accounting_tokens']))
    print('raise history entries:', len(history))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
