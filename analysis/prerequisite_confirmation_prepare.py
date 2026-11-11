# -*- coding: utf-8 -*-
"""P1-B 5.2 step 3: freeze the proposal and review payloads for the confirmation round.

Two review arms, both on the same 28 frozen pairs, differing only in context:

  ku   the two endpoints as catalogue entries (name, description, chapter, Card spans)
  ctx  the same, plus the real text of the endpoints' Cards (examples excluded, capped)

The context arm is locked to `ctx` because P1-B 5.1 showed arm B (real source text) is never worse on
necessity and identical on directness; that decision is recorded here, not re-taken per target.

A separate proposal payload asks the model, for each target, which catalogue units are its
prerequisites. That is the end-to-end universe; the review of the frozen pool is the fixed-pool
universe. They are scored apart and never pooled.

Usage: python analysis/prerequisite_confirmation_prepare.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
FROZEN = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'frozen_inputs01'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
BATCH = 4
PASSAGE_LIMIT = 2500
sys.stdout.reconfigure(encoding='utf-8')


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def endpoint(unit):
    return {'id': unit['id'], 'name': unit['name'], 'chapter': unit.get('chapter'),
            'description': unit.get('description', ''),
            'cards': [{'source': Path(c['source_path']).name, 'start_line': c['start_line'],
                       'end_line': c['end_line'], 'kind': c.get('teaching_unit_kind')}
                      for c in unit.get('cards', [])]}


def passages(unit, limit=PASSAGE_LIMIT):
    result, remaining = [], limit
    for card in unit.get('cards', []):
        if card.get('teaching_unit_kind') == 'example':
            continue
        path = Path(card['source_path'])
        if not path.is_file() or remaining <= 0:
            continue
        lines = path.read_text(encoding='utf-8').splitlines()
        text = '\n'.join(lines[card['start_line'] - 1:card['end_line']])
        if not text:
            continue
        clipped = text[:remaining]
        remaining -= len(clipped)
        result.append({'source': path.name, 'start_line': card['start_line'], 'end_line': card['end_line'],
                       'kind': card.get('teaching_unit_kind'), 'text': clipped,
                       'truncated': len(clipped) < len(text)})
    return result


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> int:
    if (OUT / 'arm_ku').exists():
        raise SystemExit('arms already built')
    reference = json.loads((OUT / 'reference_v2.json').read_text(encoding='utf-8'))
    selection = json.loads((OUT / 'selection.json').read_text(encoding='utf-8'))
    units = {u['id']: u for u in json.loads((FROZEN / 'catalog.json').read_text(encoding='utf-8'))['knowledge_units']}
    target_ids = [row['unit_id'] for row in selection['targets']]

    for arm in ('ku', 'ctx'):
        rows = []
        for index, pair in enumerate(reference['pairs']):
            row = {'index': index, 'pair_id': pair['id'],
                   'edge': '%s depends_on %s' % (pair['target_name'], pair['prerequisite_name']),
                   'target': endpoint(units[pair['target_ku']]),
                   'prerequisite': endpoint(units[pair['prerequisite_ku']])}
            if arm == 'ctx':
                row['target_passages'] = passages(units[pair['target_ku']])
                row['prerequisite_passages'] = passages(units[pair['prerequisite_ku']])
            rows.append(row)
        folder = OUT / ('arm_%s' % arm)
        for start in range(0, len(rows), BATCH):
            write(folder / ('batch_%d.json' % (start // BATCH + 1)), rows[start:start + BATCH])
        write(folder / 'payload_manifest.json', {
            'arm': arm, 'pairs': len(rows), 'batches': (len(rows) + BATCH - 1) // BATCH,
            'context': 'catalogue entries only' if arm == 'ku' else 'catalogue entries + real Card text',
            'bindings': [{'path': str(p), 'sha256': sha(p)} for p in sorted(folder.glob('batch_*.json'))],
        })
        chars = sum(len(p['text']) for row in rows for key in ('target_passages', 'prerequisite_passages')
                    for p in row.get(key, []))
        print('arm %-4s pairs=%d batches=%d passage_chars=%d' % (arm, len(rows),
                                                                 (len(rows) + BATCH - 1) // BATCH, chars))

    proposal = []
    for target_id in target_ids:
        target = units[target_id]
        candidates = [{'id': u['id'], 'name': u['name'], 'chapter': u.get('chapter'),
                       'description': (u.get('description') or '')[:160]}
                      for uid, u in units.items() if uid != target_id]
        proposal.append({'target': endpoint(target), 'candidate_units': candidates, 'max_proposals': 8})
    write(OUT / 'proposal_payload.json', {'tasks': proposal,
                                          'rule': ('propose only units that are genuine prerequisites of the target, '
                                                   'at most 8 per target, with role and reason')})
    print('proposal payload: %d targets, %d candidates each'
          % (len(proposal), len(proposal[0]['candidate_units'])))
    print('locked arm: ctx (from 5.1: source text never worse on necessity, identical on directness)')
    print('wrote ->', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
