# -*- coding: utf-8 -*-
"""Adjudicate every reported duplicate_group against the tightened criterion (user review 2026-10-09).

Tightened criterion
-------------------
A reference item covered by several output items is NOT automatically a duplicate. Only
`repetition` counts in the metric: the same content expressed redundantly in two or more output
items, or the same output item repeated. A `split` -- a reference item decomposed into output
items that each carry a different part or a different condition -- is informational only.

The decisions below are made against the output items' native content and the reviewers' own
reasons; each entry keeps the reason so the call can be re-checked.

Output: evaluation/external_baselines_merged_2026-10-09/review_merged/duplicate_adjudication.json
"""
import argparse, json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
REVIEW = NEW / 'review_merged'
sys.stdout.reconfigure(encoding='utf-8')

# (label, 1-based group index in that review) -> ('repetition' | 'split', reason)
DECISIONS = {
    # itext2KG progress-bounded r2 (damaged configuration, adjudicated for completeness)
    ('P04', 1): ('split', 'D-N10 被拆成 15 条各自不同的公式，逐条承载表内不同行，属分解而非重复'),
    ('P04', 2): ('split', 'D-N19 拆出的两条分别是绝对误差限与相对误差限，内容不同'),
    ('P04', 3): ('split', 'D-N8 拆出的两条分别是几何意义与局部线性化，承载不同部分'),
    ('P04', 4): ('split', 'D-N4 的两半（线性主部结论、近似式）分别落在两个条目，内容不同'),
    ('P04', 5): ('repetition', 'R029 与 R034 起点、终点、类型完全相同，是同一断言重复输出两次'),
    # MaF r2
    ('P05', 1): ('repetition', 'N036 已含乘积法则，N001 又把同一乘积法则单独抽成一条，同一内容重复表达'),
    ('P05', 2): ('repetition', 'N008 与 N039 的条件与结论几乎同文，差别只在原生本体类型，属同一内容重复表达'),
    # MaF r1
    ('P06', 1): ('split', 'N029（可微）与 N030（微分）承载参考节点的两个不同部分'),
    ('P06', 2): ('split', 'N037 写出 dx=Δx；N030 的 math_expressions 是 dy=f\'(x)dx，是另一条公式，并非同一内容重复'),
    ('P06', 3): ('split', 'N014（法则表达式）与 N031（形式不变性）承载不同部分'),
    ('P06', 4): ('split', 'N018 承载三式、N042 承载近似计算的实质，内容不同'),
    ('P06', 5): ('repetition', 'N011 与 N041 都写出 f(x)≈f(0)+f\'(0)x，同一内容重复表达'),
    ('P06', 6): ('split', '五条工程近似公式各自不同，属分解'),
    ('P06', 7): ('split', '绝对误差与相对误差是两项不同内容'),
    ('P06', 8): ('split', '绝对误差限与相对误差限是两项不同内容'),
    ('P06', 9): ('split', '(5-8) 与 (5-9) 是两条不同公式'),
    ('P06', 10): ('split', 'N040（转化方法）与 N027（其他型清单）承载不同部分'),
    # itext2KG progress-bounded r1 (damaged configuration)
    ('P07', 1): ('repetition', 'R008/R010/R012 起点终点完全相同，同一断言重复三次'),
    ('P07', 2): ('repetition', 'R023 与 R028 起点终点方向完全相同，同一断言重复两次'),
    # Direct r2
    ('P10', 1): ('split', 'N020（可微）与 N021（微分）承载参考节点的两个不同部分'),
    ('P10', 2): ('repetition', 'N028 独立写出 dx=Δx，N003 的 description 又复述同一句，属同一内容重复表达'),
    ('P10', 3): ('split', '绝对误差与相对误差是两项不同内容'),
    ('P10', 4): ('split', '绝对误差限与相对误差限是两项不同内容'),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--review-dir', default=str(REVIEW))
    ap.add_argument('--extra-decisions', help='JSON file mapping "LABEL:index" -> {"kind": ..., "reason": ...}')
    args = ap.parse_args()
    review_dir = Path(args.review_dir)
    decisions = dict(DECISIONS)
    if args.extra_decisions:
        extra = json.loads(Path(args.extra_decisions).read_text(encoding='utf-8'))
        for key, value in extra.items():
            label, _, index = key.rpartition(':')
            decisions[(label, int(index))] = (value['kind'], value['reason'])
    mapping = json.loads((review_dir / 'mapping_private.json').read_text(encoding='utf-8'))
    entries, problems = [], []
    for job in mapping:
        label = job['label']
        path = review_dir / (label + '_review.json')
        if not path.exists():
            continue
        review = json.loads(path.read_text(encoding='utf-8'))
        groups = review.get('duplicate_groups') or []
        for index, group in enumerate(groups, 1):
            key = (label, index)
            if key not in decisions:
                problems.append({'label': label, 'index': index, 'reference_id': group.get('reference_id'),
                                 'status': 'unadjudicated'})
                continue
            kind, reason = decisions[key]
            entries.append({
                'label': label, 'arm': job['arm'], 'repeat': job['repeat'], 'index': index,
                'reference_id': group.get('reference_id'), 'item_ids': group.get('item_ids'),
                'kind': kind, 'adjudication_reason': reason,
                'reviewer_reason': (group.get('reason') or '')[:400],
            })
    labels_in_round = {job['label'] for job in mapping}
    unused = [k for k in decisions if k[0] in labels_in_round
              and k not in {(e['label'], e['index']) for e in entries}]
    payload = {
        'criterion': ('repetition = the same content expressed redundantly in two or more output items, or the same '
                      'output item repeated (counted). split = a reference item decomposed into output items carrying '
                      'different parts or conditions (informational, not counted).'),
        'adjudicated_at': '2026-10-09',
        'adjudicator': 'main agent, applying the user review of 2026-10-09',
        'basis': "the output items' native content plus each reviewer's own reason",
        'counts_by_arm': {},
        'entries': entries,
        'unadjudicated': problems,
        'decisions_without_matching_group': unused,
    }
    for entry in entries:
        arm = payload['counts_by_arm'].setdefault(entry['arm'], {'repetition': 0, 'split': 0})
        arm[entry['kind']] += 1
    target = review_dir / 'duplicate_adjudication.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for arm, counts in sorted(payload['counts_by_arm'].items()):
        print('%-27s repetition=%d split=%d' % (arm, counts['repetition'], counts['split']))
    if problems:
        print('UNADJUDICATED:', problems)
    if unused:
        print('DECISIONS WITHOUT MATCHING GROUP:', unused)
    print('wrote ->', target)
    return 0 if not problems and not unused else 1


if __name__ == '__main__':
    raise SystemExit(main())
