# -*- coding: utf-8 -*-
"""Adjudicate the input-scope duplicate groups under the user's 2026-10-10 ruling.

The user's ruling: a Method item restating a formula that a Claim item already states is **not** a
repetition (不算). Seven of the nine recorded groups are exactly that shape; they are reclassified as
`split` (informational, never counted). The two remaining groups in R06 are a different shape and stay
counted: two Concept items sharing one definition sentence, and two native edges with the same predicate
and the same target node. The reviewers' own files are untouched; this table is the adjudication layer.

Usage: python analysis/input_scope_adjudicate_duplicates.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
REVIEW = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope' / 'review_scope'
sys.stdout.reconfigure(encoding='utf-8')

SPLIT = {
    ('R03', 1): 'Claim N008 与 Method N029 承载同一复合函数微分公式：Method 把它作为操作模板复述，属不同角色的合理拆分，按用户 2026-10-10 裁决不计重复',
    ('R03', 2): 'Claim N001 只给出乘积法则等式，Method N026 在法则清单中复述该式；同一公式由结论项与操作项分别承载，属合理拆分',
    ('R04', 1): '同上形态：Claim N001 与 Method N031 承载同一乘积法则等式，属合理拆分',
    ('R05', 1): 'Claim N010 与 Method N025 承载同一组微分近似公式与前提，Method 复述 Claim，属合理拆分',
    ('R05', 2): 'Claim N009 与 Method N024 承载同一组误差限公式与前提，Method 复述 Claim，属合理拆分',
    ('R07', 1): 'Claim N028 与 Method N042 承载同一组近似等式，Method 复述 Claim，属合理拆分',
    ('R07', 2): 'Claim N031 与 Method N040 承载同一组误差限等式，Method 复述 Claim，属合理拆分',
}
REPETITION = {
    ('R06', 1): '两个 Concept 项（N025 可微 / N026 微分）各自完整复述同一句定义原文；虽然分别定义不同对象，但同一句被逐字表达两次，按收紧后的判据仍计重复（替代读法：若视两个对象为不同内容，应降为拆分）',
    ('R06', 2): 'R011 与 R012 的谓语与目标节点完全相同、仅主语不同，是同一条关联被两条边重复表达，计重复',
}


def main() -> int:
    mapping = {row['label']: row for row in json.loads((REVIEW / 'mapping_private.json').read_text(encoding='utf-8'))}
    entries, counts = [], {}
    for path in sorted(REVIEW.glob('R0*_review.json')):
        label = path.name.split('_')[0]
        review = json.loads(path.read_text(encoding='utf-8'))
        for index, group in enumerate(review.get('duplicate_groups', []), 1):
            kind = 'split' if (label, index) in SPLIT else ('repetition' if (label, index) in REPETITION else None)
            if kind is None:
                raise SystemExit('unadjudicated group %s #%d' % (label, index))
            entry = {'label': label, 'arm': mapping[label]['arm'], 'section': mapping[label]['section'],
                     'repeat': mapping[label]['repeat'], 'index': index,
                     'reference_id': group.get('reference_id'), 'item_ids': group.get('item_ids'),
                     'kind': kind,
                     'adjudication_reason': SPLIT.get((label, index)) or REPETITION[(label, index)],
                     'reviewer_reason': group.get('reason', '')}
            entries.append(entry)
            key = (mapping[label]['arm'], kind)
            counts[key] = counts.get(key, 0) + 1

    payload = {
        'criterion': ('repetition = the same content expressed redundantly in two or more output items, or the '
                      'same output item repeated (counted). split = one reference item carried by output items in '
                      'different roles (informational, not counted).'),
        'ruling': ('User, 2026-10-10: a Method item restating a formula that a Claim item already states is NOT a '
                   'repetition. Seven groups fall under that ruling and become `split`; the two R06 groups are a '
                   'different shape and stay counted.'),
        'adjudicated_at': '2026-10-10',
        'adjudicator': 'main agent, applying the user ruling of 2026-10-10',
        'basis': "the output items' native content plus each reviewer's own reason",
        'counts_by_arm': {('%s|%s' % key): value for key, value in sorted(counts.items())},
        'entries': entries,
        'unadjudicated': [],
        'decisions_without_matching_group': [],
    }
    (REVIEW / 'duplicate_adjudication.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for entry in entries:
        print('%-4s #%d %-6s %-10s ref=%-6s items=%s' % (entry['label'], entry['index'], entry['kind'],
                                                         entry['arm'], entry['reference_id'], entry['item_ids']))
    print('counts:', payload['counts_by_arm'])
    print('wrote ->', REVIEW / 'duplicate_adjudication.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
