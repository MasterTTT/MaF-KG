# -*- coding: utf-8 -*-
"""P1-B 5.1 scoring: compare the context arms (A, B, and C when it runs) on the same 27 frozen pairs.

Arm A judged the 27 pairs from KU + member descriptions; arm B added the real Card source text; arm C
would add the program-constructed local context. All three are scored with the *frozen* scorer used by
the original experiment (`score_ku_prerequisite_evaluation.score_evaluation`) on the same frozen
`score_input.json`, so the numbers are directly comparable.

Usage: python analysis/prerequisite_context_score.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
SRC = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'diagnostics01' / 'judge_all_pairs'
DEPS = ROOT / 'evaluation' / 'prerequisite_candidate_retrieval_2026-10-07' / 'run01' / 'runtime_dependencies'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_context'
sys.path.insert(0, str(DEPS))
sys.stdout.reconfigure(encoding='utf-8')

from score_ku_prerequisite_evaluation import score_evaluation  # noqa: E402


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> int:
    frozen = json.loads((SRC / 'score_input.json').read_text(encoding='utf-8'))
    reference = frozen['reference']

    arms = {'A': json.loads((SRC / 'predictions.json').read_text(encoding='utf-8'))['verdicts']}
    for arm in ('B', 'C'):
        path = OUT / ('arm%s' % arm) / 'predictions.json'
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding='utf-8'))
        # The runner stores readable names; the scorer needs the frozen KU ids. The batch rows carry the
        # frozen order, so index maps straight back onto the reference annotations.
        verdicts = []
        for row in raw['verdicts']:
            annotation = reference[row['index']]
            verdict = {'source_ku': annotation['target_ku'], 'target_ku': annotation['prerequisite_ku'],
                       'label': row['label'], 'reason': row['reason']}
            if row['label'] == 'required':
                verdict['directness'] = row['directness']
            verdicts.append(verdict)
        arms[arm] = verdicts

    summary = {}
    for arm, verdicts in arms.items():
        data = {**frozen, 'predictions': {'verdicts': verdicts}}
        scores = score_evaluation(data)
        write(OUT / ('scores_arm%s.json' % arm), scores)
        pool = scores['candidate_pool_review']
        summary[arm] = {
            'verdicts': len(verdicts),
            'requiredness': {k: pool['requiredness'][k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')},
            'directness': {k: pool['directness'][k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')},
            'indirect_false_accepts': scores['indirect_false_accepts']['count'],
            'indirect_false_accept_denominator': scores['indirect_false_accepts']['denominator'],
            'support_false_accepts': scores['support_false_accepts']['count'],
            'support_false_accept_denominator': scores['support_false_accepts']['denominator'],
            'label_counts': {label: sum(1 for v in verdicts if v['label'] == label)
                             for label in sorted({v['label'] for v in verdicts})},
            'graph_is_dag': scores['graph_diagnostics']['predicted_required_graph']['is_dag'],
        }
        print('arm %s (%d verdicts): requiredness P=%.3f R=%.3f F1=%.3f | directness P=%.3f R=%.3f F1=%.3f | '
              'indirect-as-direct %d/%d | labels %s'
              % (arm, len(verdicts), summary[arm]['requiredness']['precision'],
                 summary[arm]['requiredness']['recall'], summary[arm]['requiredness']['f1'],
                 summary[arm]['directness']['precision'], summary[arm]['directness']['recall'],
                 summary[arm]['directness']['f1'], summary[arm]['indirect_false_accepts'],
                 summary[arm]['indirect_false_accept_denominator'], summary[arm]['label_counts']))

    # Arm-to-arm agreement on the same 27 pairs, field by field.
    agreement = {}
    for other in ('B', 'C'):
        if other not in arms:
            continue
        left = {v['index'] if 'index' in v else i: v for i, v in enumerate(arms['A'])}
        right = {}
        raw = json.loads((OUT / ('arm%s' % other) / 'predictions.json').read_text(encoding='utf-8'))['verdicts']
        for row in raw:
            right[row['index']] = row
        same_label = sum(1 for i, v in left.items() if i in right and v['label'] == right[i]['label'])
        both_required = [i for i, v in left.items() if i in right and v['label'] == 'required'
                         and right[i]['label'] == 'required']
        same_directness = sum(1 for i in both_required
                              if v_directness(left[i]) == right[i].get('directness'))
        agreement['A_vs_%s' % other] = {
            'pairs': len(right), 'same_label': same_label,
            'both_required': len(both_required), 'same_directness_among_both_required': same_directness,
            'directness_changed_to_indirect': sum(1 for i in both_required
                                                  if left[i].get('directness') == 'direct'
                                                  and right[i].get('directness') == 'indirect'),
            'directness_changed_to_direct': sum(1 for i in both_required
                                                if left[i].get('directness') == 'indirect'
                                                and right[i].get('directness') == 'direct'),
        }
        print('agreement A vs %s: %s' % (other, agreement['A_vs_%s' % other]))

    write(OUT / 'comparison.json', {
        'purpose': ('Do the necessity and directness verdicts change when the reviewer is given the real source '
                    'text (B) and a program-constructed local unit context (C), with the same prompt, the same 27 '
                    'frozen pairs and the same scorer?'),
        'scorer': 'score_ku_prerequisite_evaluation.score_evaluation (frozen, reused)',
        'arms': summary, 'agreement': agreement,
        'note': ('Arm A is the reused frozen judgement round; arm B/C verdicts come from the new context packets. '
                 'One round only: this is a context diagnostic, not a blind improvement claim.'),
    })
    print('wrote ->', OUT / 'comparison.json')
    return 0


def v_directness(verdict):
    return verdict.get('directness')


if __name__ == '__main__':
    raise SystemExit(main())
