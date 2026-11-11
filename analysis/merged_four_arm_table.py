# -*- coding: utf-8 -*-
"""Assemble the four-arm comparison table for the merged three-section experiment.

The two comparison rounds are scored separately (round v2: Direct and MaF; round v3: the
parse-compatible EDC and the symbol-compatible iText2KG). This script joins them into one table,
keeping the three metric families in their own universes and never pooling an arm with the variant
it replaces.

Cost is reported from the ledgers: the v2 portion for Direct/MaF, the v3 portion for the two fixed
variants, so sunk v1 spend and the superseded v2 arms are not charged to the comparison.

Output: evaluation/external_baselines_merged_2026-10-09/four_arm_table.json (+ a markdown rendering)
"""
import json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')

ARMS = [
    ('direct', 'Direct（单次直读）', NEW / 'semantic_scores.json', 'v2', None),
    ('maf', 'MaF（顺序 + 记忆）', NEW / 'semantic_scores.json', 'v2', None),
    ('edc-parse-compatible', 'EDC（解析兼容）', NEW / 'semantic_scores_v3.json', 'v3', 'edc_v3'),
    # v4 replaces the v3 iText arm: it passes the same review and delivers every entity and relation name
    # verbatim (0 of 52 and 0 of 27 normalised, against 5/26 and 3/23 in v3). v3 stays on disk as boundary
    # evidence of what the pinned normalisation cost.
    ('itext-symbol-native', 'iText2KG（符号原生）', NEW / 'semantic_scores_v4.json', 'v4', 'itext_v4'),
]


def v3_spend(method, marker):
    ledger = json.loads((NEW / 'budgets' / (method + '.json')).read_text(encoding='utf-8'))
    entries = [r for r in ledger['requests'] if marker in str(r.get('label', ''))]
    tokens = sum((r.get('usage') or {}).get('total_tokens') or r.get('bound_tokens') or 0 for r in entries)
    usd = sum(r.get('estimated_usd') if r.get('estimated_usd') is not None else (r.get('bound_usd') or 0)
              for r in entries)
    return {'requests': len(entries), 'tokens': tokens, 'usd': round(usd, 6),
            'basis': 'ledger entries whose label contains %s' % marker}


def v2_spend(method):
    """v2 portion = entries after the first repeated label (the v1/v2 boundary)."""
    ledger = json.loads((NEW / 'budgets' / (method + '.json')).read_text(encoding='utf-8'))
    entries = ledger['requests']
    seen, split = set(), None
    for index, entry in enumerate(entries):
        label = str(entry.get('label', ''))
        if label in seen:
            split = index
            break
        seen.add(label)
    portion = entries[split:] if split is not None else entries
    if method == 'itext2kg':
        portion = [r for r in portion if 'edc_v3' not in str(r.get('label', '')) and 'itext_v3' not in str(r.get('label', ''))]
    tokens = sum((r.get('usage') or {}).get('total_tokens') or r.get('bound_tokens') or 0 for r in portion)
    usd = sum(r.get('estimated_usd') if r.get('estimated_usd') is not None else (r.get('bound_usd') or 0)
              for r in portion)
    return {'requests': len(portion), 'tokens': tokens, 'usd': round(usd, 6),
            'basis': 'v2 portion of the ledger (excludes v1 sunk spend and any later variant)'}


def arm_row(key, label, scores_path, round_tag, ledger_marker=None):
    if not scores_path.exists():
        return {'arm': key, 'label': label, 'status': 'scores_pending'}
    scores = json.loads(scores_path.read_text(encoding='utf-8'))
    info = scores['by_arm'].get(key)
    if info is None:
        return {'arm': key, 'label': label, 'status': 'arm_absent_in_scores'}
    metrics = info['pooled_for_reviewed_outputs_only']
    spend = v2_spend('maf' if key == 'maf' else 'direct') if round_tag == 'v2' else \
        v3_spend('edc' if key.startswith('edc') else 'itext2kg',
                 ledger_marker or ('edc_v3' if key.startswith('edc') else 'itext_v3'))
    return {
        'arm': key, 'label': label, 'status': 'scored', 'round': round_tag,
        'reviewed_repeats': info['reviewed_repeats'], 'planned_repeats': info['planned_repeats'],
        'noncompleted': info['noncompleted'],
        'full_required_recall': metrics['full_required_recall'],
        'recognized_required_recall': metrics['recognized_required_recall'],
        'full_core_recall': metrics['full_core_recall'],
        'full_detail_recall': metrics['full_detail_recall'],
        'full_relation_recall': metrics['full_relation_recall'],
        'recognized_relation_recall': metrics['recognized_relation_recall'],
        'full_cross_relation_recall': metrics['full_cross_relation_recall'],
        'recognized_cross_relation_recall': metrics['recognized_cross_relation_recall'],
        'claim_conditions_complete_ratio': metrics['claim_conditions_complete_ratio'],
        'native_support_yes_ratio': metrics['native_support_yes_ratio'],
        'duplicate_repetitions': metrics['pooled_duplicate_groups'],
        'duplicate_splits': metrics['pooled_duplicate_splits'],
        'false_merge_events': metrics['pooled_false_merge_events'],
        'per_repeat_full_required_recall': info['per_repeat_full_required_recall'],
        'per_repeat_cross_relation': info['per_repeat_cross_relation'],
        'spend': spend,
        'tokens_per_output': (spend['tokens'] / info['reviewed_repeats']) if info['reviewed_repeats'] else None,
    }


def fmt(value, digits=3):
    return 'n/a' if value is None else ('%.*f' % (digits, value))


def main() -> int:
    rows = [arm_row(*arm) for arm in ARMS]
    scored = [r for r in rows if r['status'] == 'scored']
    payload = {
        'experiment': 'merged three-section input (ch2_s5 + ch3_s1 + ch3_s2)',
        'rounds': {'v2': 'Direct and MaF', 'v3': 'parse-compatible EDC and symbol-compatible iText2KG'},
        'arms': rows,
        'complete': len(scored) == len(ARMS),
        'universes': {
            'content': 'required-content recall over 35 required reference items; full and recognized reported separately',
            'conditions': 'required Claims whose material conditions are stated in the output itself',
            'relations': 'intra relations (45) and cross-section relations (2) scored in separate universes',
            'duplicates': 'genuine repetition only; legitimate decomposition reported as splits',
        },
        'excluded': {
            'edc': 'v2 EDC (parser-compatibility failure: definitions dropped, alignment never ran)',
            'itext2kg-progress-bound-3': 'v2 iText2KG (pinned ASCII sanitiser destroyed relation predicates)',
            'itext2kg': 'unguarded v2 iText2KG (120-call ceiling failure)',
        },
        'no_pooling': 'no arm is pooled with the variant it replaces; repeats are not independent samples',
    }
    target = NEW / 'four_arm_table.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    lines = ['| 指标 | ' + ' | '.join(r['label'] for r in rows) + ' |',
             '|---|' + '---|' * len(rows)]
    metric_rows = [
        ('必答内容 full 召回', 'full_required_recall'),
        ('必答内容 识别到', 'recognized_required_recall'),
        ('core / detail full', None),
        ('节内关系 full', 'full_relation_recall'),
        ('节内关系 识别到', 'recognized_relation_recall'),
        ('跨节关系 full（4 槽）', 'full_cross_relation_recall'),
        ('必答 Claim 条件完整', 'claim_conditions_complete_ratio'),
        ('来源支持 yes', 'native_support_yes_ratio'),
        ('重复 / 拆分', None),
        ('误合并', 'false_merge_events'),
        ('两轮 full 召回', None),
        ('本轮实际 token（请求数）', None),
    ]
    for title, key in metric_rows:
        cells = []
        for row in rows:
            if row['status'] != 'scored':
                cells.append('待评分')
                continue
            if key:
                cells.append(fmt(row[key]))
            elif title.startswith('core'):
                cells.append('%s / %s' % (fmt(row['full_core_recall']), fmt(row['full_detail_recall'])))
            elif title.startswith('重复'):
                cells.append('%d / %d' % (row['duplicate_repetitions'], row['duplicate_splits']))
            elif title.startswith('两轮'):
                cells.append(' / '.join(fmt(x) for x in row['per_repeat_full_required_recall']))
            else:
                cells.append('%s (%d req)' % (f'{row["spend"]["tokens"]:,}', row['spend']['requests']))
        lines.append('| %s | %s |' % (title, ' | '.join(cells)))
    markdown = '\n'.join(lines)
    (NEW / 'four_arm_table.md').write_text(markdown + '\n', encoding='utf-8')

    print(markdown)
    print()
    print('complete:', payload['complete'])
    for row in rows:
        if row['status'] != 'scored':
            print('  %-28s %s' % (row['arm'], row['status']))
    print('wrote ->', target, 'and four_arm_table.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
