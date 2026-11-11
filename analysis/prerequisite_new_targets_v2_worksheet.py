# -*- coding: utf-8 -*-
"""P1-B 5.2 worksheet: everything needed to annotate prerequisite pairs for the four frozen targets.

Prints, for each target, its own catalogue entry and the entries of every other unit, with the source
spans its Cards cover, so the pairs can be written from the frozen text rather than from memory. The
output is also written to `worksheet.md` for the record.

Usage: python analysis/prerequisite_new_targets_v2_worksheet.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
FROZEN = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'frozen_inputs01'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
TARGETS = ['ku_09206fe29015', 'ku_da56c3fdccf2', 'ku_e9f8c9c2740e', 'ku_99bdf15cb98e']
sys.stdout.reconfigure(encoding='utf-8')


def spans(unit):
    out = []
    for card in unit.get('cards', []):
        name = Path(card['source_path']).name
        out.append('%s:%d-%d(%s)' % (name, card['start_line'], card['end_line'],
                                     card.get('teaching_unit_kind', '?')))
    return out


def main() -> int:
    units = {u['id']: u for u in json.loads((FROZEN / 'catalog.json').read_text(encoding='utf-8'))['knowledge_units']}
    lines = ['# 5.2 先修参考标注工作表', '',
             '四个新目标与全部候选单元（含 Cards 覆盖的源文行段），用于从冻结源文写参考对。', '']
    for target_id in TARGETS:
        target = units[target_id]
        lines += ['## 目标 %s · %s' % (target_id, target['name']),
                  '- 章节/目标位：%s / %s' % (target.get('chapter'), target.get('goal_id')),
                  '- 描述：%s' % target.get('description', ''),
                  '- Cards：%s' % '; '.join(spans(target)), '']
    lines += ['## 其余单元（候选先修）', '']
    for unit_id, unit in sorted(units.items(), key=lambda kv: (kv[1].get('chapter') or '', kv[0])):
        mark = ' **[目标]**' if unit_id in TARGETS else ''
        lines.append('- `%s` %s%s | %s / %s' % (unit_id, unit['name'], mark, unit.get('chapter'), unit.get('goal_id')))
        lines.append('  - 描述：%s' % (unit.get('description') or '')[:220])
        lines.append('  - Cards：%s' % '; '.join(spans(unit))[:400])
    text = '\n'.join(lines) + '\n'
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'worksheet.md').write_text(text, encoding='utf-8')
    print(text)
    print('wrote ->', OUT / 'worksheet.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
