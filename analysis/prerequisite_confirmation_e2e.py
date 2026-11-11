# -*- coding: utf-8 -*-
"""P1-B 5.2 step 6: score the end-to-end candidate generation universe.

Only proposals labelled `required` count as positive predictions, because the reference's positive class
is `required`; a support or principle-support proposal is not a required claim and is scored as a miss,
with the role recorded so the reason is visible. The fixed-pool review in `comparison.json` is a
different universe and is never pooled with this one.

Usage: python analysis/prerequisite_confirmation_e2e.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    reference = json.loads((OUT / 'reference_v2.json').read_text(encoding='utf-8'))
    selection = json.loads((OUT / 'selection.json').read_text(encoding='utf-8'))
    targets = {row['unit_id']: row['name'] for row in selection['targets']}

    gold_required = {}
    for pair in reference['pairs']:
        if pair['role'] == 'required':
            gold_required.setdefault(pair['target_ku'], set()).add(pair['prerequisite_ku'])

    rows, tp, fp, fn = [], 0, 0, 0
    for path in sorted((OUT / 'proposals').glob('target_*.json')):
        proposal = json.loads(path.read_text(encoding='utf-8'))
        target_id = proposal['target_id']
        gold = gold_required.get(target_id, set())
        proposed_required = {p['unit_id'] for p in proposal['proposals'] if p['role'] == 'required'}
        proposed_other = {p['unit_id']: p['role'] for p in proposal['proposals'] if p['role'] != 'required'}
        hit = proposed_required & gold
        missed = gold - proposed_required
        extra = proposed_required - gold
        down_labelled = sorted(missed & set(proposed_other))
        tp += len(hit)
        fp += len(extra)
        fn += len(missed)
        rows.append({
            'target_id': target_id, 'target_name': targets.get(target_id, proposal['target_name']),
            'gold_required': len(gold), 'proposed_total': len(proposal['proposals']),
            'proposed_required': len(proposed_required),
            'hit': len(hit), 'missed': len(missed), 'extra': len(extra),
            'missed_but_proposed_as_support': [{'unit_id': u, 'role': proposed_other[u]} for u in down_labelled],
            'missed_entirely': sorted(missed - set(proposed_other)),
            'extra_units': sorted(extra),
        })
        print('%-14s gold=%d proposed=%d(required %d) hit=%d missed=%d extra=%d | support-labelled misses %d'
              % (rows[-1]['target_name'], len(gold), len(proposal['proposals']), len(proposed_required),
                 len(hit), len(missed), len(extra), len(down_labelled)))

    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    payload = {
        'experiment': 'prerequisite-new-target-confirmation-v2',
        'universe': 'end_to_end_candidate_generation',
        'metric_note': ('Positive prediction = a proposal labelled `required`. A pair proposed only as '
                        'proof_support or principle_support is a miss for the required class and is recorded '
                        'with its role, so the failure mode is visible rather than hidden.'),
        'per_target': rows,
        'overall': {'tp': tp, 'fp': fp, 'fn': fn, 'precision': precision, 'recall': recall, 'f1': f1},
        'not_pooled_with': 'the fixed-pool review in comparison.json',
    }
    (OUT / 'end_to_end.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('end-to-end: tp=%d fp=%d fn=%d | P=%s R=%s F1=%s'
          % (tp, fp, fn, round(precision, 4) if precision else None,
             round(recall, 4) if recall else None, round(f1, 4) if f1 else None))
    print('wrote ->', OUT / 'end_to_end.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
