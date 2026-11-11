# -*- coding: utf-8 -*-
"""P1-B 5.2 step 4: score the two confirmation arms against the frozen reference.

Uses the same frozen scorer as 5.1 and the original prerequisite experiment
(`score_ku_prerequisite_evaluation.score_evaluation`), so the numbers are comparable. Two arms
differ only in context; the reference was frozen before any prediction.

Usage: python analysis/prerequisite_confirmation_score.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
DEPS = ROOT / 'evaluation' / 'prerequisite_candidate_retrieval_2026-10-07' / 'run01' / 'runtime_dependencies'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
FROZEN = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'frozen_inputs01'
sys.path.insert(0, str(DEPS))
sys.stdout.reconfigure(encoding='utf-8')

from score_ku_prerequisite_evaluation import score_evaluation  # noqa: E402


def main() -> int:
    reference = json.loads((OUT / 'reference_v2.json').read_text(encoding='utf-8'))
    pairs = reference['pairs']
    units = {u['id']: u for u in json.loads((FROZEN / 'catalog.json').read_text(encoding='utf-8'))['knowledge_units']}

    annotations = [{'id': p['id'], 'target_ku': p['target_ku'], 'prerequisite_ku': p['prerequisite_ku'],
                    'target_name': p['target_name'], 'prerequisite_name': p['prerequisite_name'],
                    'role': p['role'], 'directness': p['directness'], 'reason': p['reason'],
                    'provenance': p['provenance']} for p in pairs]
    candidates = [{'source_ku': p['target_ku'], 'target_ku': p['prerequisite_ku'],
                   'source_name': p['target_name'], 'target_name': p['prerequisite_name'],
                   'signals': [], 'chain_via': []} for p in pairs]
    frozen = {'endpoints': list(units.values()), 'candidates': candidates, 'reference': annotations}

    summary = {}
    for arm in ('ku', 'ctx'):
        path = OUT / ('arm_%s' % arm) / 'predictions.json'
        if not path.is_file():
            print('arm %-4s : no predictions yet' % arm)
            continue
        raw = json.loads(path.read_text(encoding='utf-8'))
        verdicts = []
        for row in raw['verdicts']:
            pair = pairs[row['index']]
            verdict = {'source_ku': pair['target_ku'], 'target_ku': pair['prerequisite_ku'],
                       'label': row['label'], 'reason': row['reason']}
            if row['label'] == 'required':
                verdict['directness'] = row['directness']
            verdicts.append(verdict)
        scores = score_evaluation({**frozen, 'predictions': {'verdicts': verdicts}})
        (OUT / ('scores_arm_%s.json' % arm)).write_text(
            json.dumps(scores, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        pool = scores['candidate_pool_review']
        summary[arm] = {
            'verdicts': len(verdicts),
            'requiredness': {k: pool['requiredness'][k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')},
            'directness': {k: pool['directness'][k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')},
            'indirect_false_accepts': scores['indirect_false_accepts']['count'],
            'indirect_false_accept_denominator': scores['indirect_false_accepts']['denominator'],
            'support_false_accepts': scores['support_false_accepts']['count'],
            'label_counts': {label: sum(1 for v in verdicts if v['label'] == label)
                             for label in sorted({v['label'] for v in verdicts})},
            'unparsable_batches': raw.get('unparsable_batches', []),
        }
        row = summary[arm]
        print('arm %-4s (%d verdicts): necessity P=%.3f R=%.3f F1=%.3f | directness P=%.3f R=%.3f F1=%.3f | '
              'indirect-as-direct %d/%d | labels %s'
              % (arm, row['verdicts'], row['requiredness']['precision'], row['requiredness']['recall'],
                 row['requiredness']['f1'], row['directness']['precision'], row['directness']['recall'],
                 row['directness']['f1'], row['indirect_false_accepts'],
                 row['indirect_false_accept_denominator'], row['label_counts']))

    if len(summary) == 2:
        left = json.loads((OUT / 'arm_ctx' / 'predictions.json').read_text(encoding='utf-8'))['verdicts']
        right = json.loads((OUT / 'arm_ku' / 'predictions.json').read_text(encoding='utf-8'))['verdicts']
        by_index = {row['index']: row for row in right}
        same_label = sum(1 for row in left if by_index[row['index']]['label'] == row['label'])
        both_required = [row for row in left if by_index[row['index']]['label'] == 'required'
                         and row['label'] == 'required']
        same_directness = sum(1 for row in both_required
                              if by_index[row['index']].get('directness') == row.get('directness'))
        summary['agreement_ctx_vs_ku'] = {'pairs': len(left), 'same_label': same_label,
                                          'both_required': len(both_required),
                                          'same_directness_among_both_required': same_directness}
        print('agreement ctx vs ku: %s' % summary['agreement_ctx_vs_ku'])

    payload = {
        'experiment': 'prerequisite-new-target-confirmation-v2',
        'reference': 'reference_v2.json (frozen before prediction)',
        'scorer': 'score_ku_prerequisite_evaluation.score_evaluation (frozen, reused)',
        'arms': summary,
        'universes': {'fixed_pool_review': 'this file', 'end_to_end_candidate_generation': 'reported separately'},
        'note': ('Four targets that took no part in the earlier tuning, 28 frozen pairs, two context arms, one '
                 'round. Negative pairs include six same-family pairs and three reversed-direction pairs, which is '
                 'where a prerequisite graph usually goes wrong.'),
    }
    (OUT / 'comparison.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('wrote ->', OUT / 'comparison.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
