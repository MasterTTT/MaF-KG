# -*- coding: utf-8 -*-
"""P1-B 5.2 step 1: freeze the four NEW query targets before anything is predicted.

The plan requires that the new targets did not take part in the earlier review tuning. That is checked
mechanically against the frozen reference (the four targets it used) and against the pairs where a
candidate appears as a prerequisite. The selection is written down with its rule *before* any
candidate generation or judgement, so it cannot be adjusted after seeing results.

Goal-type coverage is the selection axis, taken from the target's chapter and its role in the
catalogue: definition (definition/concept), theorem (statement), method (procedure), application.

Usage: python analysis/prerequisite_new_targets_v2_select.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
FROZEN = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'frozen_inputs01'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
sys.stdout.reconfigure(encoding='utf-8')

# Chosen by goal type, one per row, from the catalogue units that no earlier round used as a target.
CHOSEN = [
    ('ku_fb8130a0dde2', 'definition', '第四章 g1',
     '定义型：原函数与不定积分的概念。原选 函数极限的定义 已换掉——它在目录里没有任何先修单元，'
     '作为先修查询目标会退化为只有负例；本项在目录内有真实先修，且仍是"理解定义"类目标'),
    ('ku_da56c3fdccf2', 'theorem_proof', '第三章 g1', '定理型：罗尔定理是证明型目标，且是后续中值定理的先修'),
    ('ku_e9f8c9c2740e', 'method_application', '第四章 g1', '方法型：分部积分法的应用，考察"应用方法"这一类目标'),
    ('ku_99bdf15cb98e', 'theorem_application', '第五章 g3', '定理应用型：牛顿-莱布尼茨公式的使用'),
]


def main() -> int:
    units = {u['id']: u for u in json.loads((FROZEN / 'catalog.json').read_text(encoding='utf-8'))['knowledge_units']}
    reference = json.loads((FROZEN / 'reference.json').read_text(encoding='utf-8'))['annotations']
    used_targets = sorted({row['target_ku'] for row in reference})
    appears_as_prerequisite = {row['prerequisite_ku'] for row in reference}

    rows, problems = [], []
    for unit_id, goal_type, position, why in CHOSEN:
        if unit_id not in units:
            problems.append('%s: not in the frozen catalogue' % unit_id)
            continue
        if unit_id in used_targets:
            problems.append('%s: already used as a query target' % unit_id)
        rows.append({
            'unit_id': unit_id, 'name': units[unit_id]['name'], 'goal_type': goal_type,
            'catalogue_position': position, 'why': why,
            'used_as_target_in_earlier_round': unit_id in used_targets,
            'appears_as_prerequisite_in_earlier_reference': unit_id in appears_as_prerequisite,
            'earlier_pairs_where_it_is_a_prerequisite': sum(1 for row in reference
                                                            if row['prerequisite_ku'] == unit_id),
        })
    if problems:
        raise SystemExit('selection invalid: %s' % problems)

    payload = {
        'experiment': 'prerequisite-new-target-confirmation-v2',
        'status': 'targets_frozen_pairs_pending',
        'purpose': ('Second, bounded prerequisite confirmation on four query targets that took no part in the '
                    'earlier review tuning. The reference pairs will be built and frozen before any candidate '
                    'generation or judgement runs.'),
        'history_check': {
            'source': str(FROZEN / 'reference.json'),
            'earlier_used_targets': used_targets,
            'earlier_used_target_names': [units[u]['name'] for u in used_targets],
            'rule': ('a new target must not appear as a target in the earlier reference; appearing as a '
                     'prerequisite there is allowed and is recorded, because that is prior knowledge, not a '
                     'tuned query'),
        },
        'targets': rows,
        'goal_types_covered': sorted({row['goal_type'] for row in rows}),
        'next_steps': [
            'build 24-32 directed reference pairs over these four targets with roles and directness, from the '
            'frozen source, and freeze them before prediction',
            'lock the context arm from P1-B 5.1 (arm B >= arm A on necessity, equal on directness)',
            'run candidate generation and the review separately, and never pool the fixed-pool review with the '
            'end-to-end candidate numbers',
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'selection.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('earlier used targets: %s' % [units[u]['name'] for u in used_targets])
    for row in rows:
        print('  %-18s %-20s %-22s prereq-in-earlier-ref=%s (%d pairs)'
              % (row['unit_id'], row['name'], row['goal_type'],
                 row['appears_as_prerequisite_in_earlier_reference'],
                 row['earlier_pairs_where_it_is_a_prerequisite']))
    print('wrote ->', OUT / 'selection.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
