# -*- coding: utf-8 -*-
"""P1-B 5.1: build the two context arms (B: real source text, C: B + local KU context) offline.

Arm A already exists: `run02/diagnostics01/judge_all_pairs/batch_1..4.json` judged all 27 frozen pairs
with KU + member descriptions only. The user review asked for two further arms that change *only* the
context, with the same review prompt and the same 27 pairs:

  B  same KU/member payload + the real Card source text (passages) for both endpoints, so the
     reviewer can see the sentences that would make a prerequisite necessary;
  C  B + a program-constructed local context: other knowledge units whose Card evidence lies between
     the two endpoints in the same source file, with their evidence ranges. It shows *possible*
     intermediate units and never the reference role or directness.

Nothing is annotated here and no API call is made; the packets are frozen before any verdict exists.

Usage: python analysis/prerequisite_context_arms.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
SRC = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02'
DEPS = ROOT / 'evaluation' / 'prerequisite_candidate_retrieval_2026-10-07' / 'run01' / 'runtime_dependencies'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_context'
BATCH = 4
MAX_INTERMEDIATES = 12
# Passage budget per endpoint, per pair. The approved P1-B 5.1 gate is 0.3 M tokens for eight calls, and
# both arms together must fit inside it: the KU/member payload is fixed by arm A, so the source text is
# what has to be bounded. Measured at 2500 characters per endpoint the two arms plus their verdict output
# stay inside the gate; the full Cards remain in the frozen index if a larger budget is approved later.
# Arm B runs first and its real usage decides whether arm C is started at all.
PASSAGE_LIMIT = 2500
sys.path.insert(0, str(DEPS))
sys.stdout.reconfigure(encoding='utf-8')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def passages(unit, limit=PASSAGE_LIMIT):
    """Real source text for a unit's Cards, examples excluded, capped like the earlier arm."""
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
        result.append({'passage_id': card['card_id'], 'source': path.name,
                       'start_line': card['start_line'], 'end_line': card['end_line'],
                       'roles': card.get('discourse_roles', []), 'kind': card.get('teaching_unit_kind'),
                       'text': clipped, 'truncated': len(clipped) < len(text)})
    return result


def spans(unit):
    return [(Path(c['source_path']).name, c['start_line'], c['end_line']) for c in unit.get('cards', [])]


def local_context(units, source_id, target_id):
    """Units whose Card evidence lies between the two endpoints in the same source file.

    An overlap-with-an-endpoint rule finds nothing here (the endpoints' own Cards do not touch other
    units), so the rule is interval-based: for every file the two endpoints appear in, take the span
    from the earliest endpoint Card to the latest one and list the other units that fall inside it.
    These are possible intermediates, not asserted bridges.
    """
    source, target = units[source_id], units[target_id]
    endpoint_spans = spans(source) + spans(target)
    intervals = {}
    for name, start, end in endpoint_spans:
        lo, hi = intervals.get(name, (start, end))
        intervals[name] = (min(lo, start), max(hi, end))
    candidates = {}
    for unit_id, unit in units.items():
        if unit_id in (source_id, target_id):
            continue
        inside = []
        for name, start, end in spans(unit):
            if name in intervals and start >= intervals[name][0] and end <= intervals[name][1]:
                inside.append({'source': name, 'start_line': start, 'end_line': end})
        if inside:
            candidates[unit_id] = {'id': unit_id, 'name': unit['name'], 'chapter': unit.get('chapter'),
                                   'evidence_ranges': inside[:4],
                                   'why': 'Card evidence lies between the two endpoints in the same source file'}
    ordered = sorted(candidates.values(),
                     key=lambda c: (c['evidence_ranges'][0]['source'], c['evidence_ranges'][0]['start_line']))
    return {'rule': ('program-constructed: for each file the two endpoints appear in, take the span from their '
                     'earliest to their latest Card and list the other units inside it; this lists possible '
                     'intermediates, not a claim that any of them is a bridge'),
            'intervals': {name: {'start_line': lo, 'end_line': hi} for name, (lo, hi) in sorted(intervals.items())},
            'candidates_total': len(ordered),
            'candidates_shown': ordered[:MAX_INTERMEDIATES]}


def main() -> int:
    if OUT.exists():
        raise SystemExit('exists: %s' % OUT)
    frozen = SRC / 'frozen_inputs01'
    for name in ('catalog.json', 'reference.json'):
        if not (frozen / name).is_file():
            raise SystemExit('missing frozen input: %s' % (frozen / name))
    units = {u['id']: u for u in json.loads((frozen / 'catalog.json').read_text(encoding='utf-8'))['knowledge_units']}
    reference = json.loads((frozen / 'reference.json').read_text(encoding='utf-8'))['annotations']
    system_path = Path(json.loads((SRC / 'manifest.json').read_text(encoding='utf-8'))['snapshot']) / 'review_system.txt'

    # Arm A's frozen batches already carry the exact KU/member payload for these 27 pairs, in reference
    # order. Reusing them verbatim (rather than rebuilding the endpoint blocks) guarantees that arms A, B
    # and C differ only in the added context.
    a_folder = SRC / 'diagnostics01' / 'judge_all_pairs'
    a_rows = []
    for path in sorted(a_folder.glob('batch_*.json')):
        a_rows += json.loads(path.read_text(encoding='utf-8'))
    if len(a_rows) != len(reference):
        raise SystemExit('arm A batches hold %d rows but the reference has %d' % (len(a_rows), len(reference)))
    for index, (row, annotation) in enumerate(zip(a_rows, reference)):
        if row['source']['id'] != annotation['target_ku'] or row['target']['id'] != annotation['prerequisite_ku']:
            raise SystemExit('arm A row %d does not line up with the frozen reference order' % index)
    print('arm A payload verified against the frozen reference order (%d rows)' % len(a_rows))

    arms = {}
    for arm in ('B', 'C'):
        rows = []
        for index, base in enumerate(a_rows):
            row = json.loads(json.dumps(base, ensure_ascii=False))
            row['index'] = index
            source_id = reference[index]['target_ku']
            target_id = reference[index]['prerequisite_ku']
            row['source_passages'] = passages(units[source_id])
            row['target_passages'] = passages(units[target_id])
            if arm == 'C':
                row['local_context'] = local_context(units, source_id, target_id)
            rows.append(row)
        folder = OUT / ('arm%s' % arm)
        folder.mkdir(parents=True)
        for start in range(0, len(rows), BATCH):
            (folder / ('batch_%d.json' % (start // BATCH + 1))).write_text(
                json.dumps(rows[start:start + BATCH], ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'payload_manifest.json').write_text(json.dumps({
            'arm': arm, 'system_sha256': sha(system_path), 'pairs': len(rows), 'batches': (len(rows) + BATCH - 1) // BATCH,
            'context': ('KU + members + real Card source text for both endpoints' if arm == 'B'
                        else 'arm B context + program-constructed local KU context'),
            'bindings': [{'path': str(p), 'sha256': sha(p)} for p in sorted(folder.glob('batch_*.json'))],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        arms[arm] = {'pairs': len(rows), 'batches': (len(rows) + BATCH - 1) // BATCH,
                     'source_passage_chars': sum(len(p['text']) for row in rows for p in row['source_passages']),
                     'target_passage_chars': sum(len(p['text']) for row in rows for p in row['target_passages']),
                     'local_context_candidates': sum(row.get('local_context', {}).get('candidates_total', 0) for row in rows)}
        print('arm %s: pairs=%d batches=%d passages src=%d tgt=%d chars | local candidates=%d'
              % (arm, arms[arm]['pairs'], arms[arm]['batches'], arms[arm]['source_passage_chars'],
                 arms[arm]['target_passage_chars'], arms[arm]['local_context_candidates']))

    a_folder = SRC / 'diagnostics01' / 'judge_all_pairs'
    manifest = {        'experiment': 'prerequisite-context-arms-b-c',
        'status': 'prepared',
        'purpose': ('Context ablation on the frozen 27 prerequisite pairs: does giving the reviewer the real source '
                    'text (B), and additionally a program-constructed local KU context (C), change the necessity and '
                    'directness verdicts that arm A produced from KU + members alone?'),
        'reference': str(frozen / 'reference.json'), 'reference_sha256': sha(frozen / 'reference.json'),
        'catalog': str(frozen / 'catalog.json'), 'catalog_sha256': sha(frozen / 'catalog.json'),
        'review_system': str(system_path), 'review_system_sha256': sha(system_path),
        'arm_A_reused': {'folder': str(a_folder), 'pairs': len(a_rows),
                         'bindings': [{'path': str(p), 'sha256': sha(p)}
                                      for p in sorted(a_folder.glob('batch_*.json'))]},
        'arms': arms,
        'frozen_before_verdicts': True,
        'budget_gate': {'usd': 0.20, 'tokens': 300_000,
                        'note': ('Approved P1-B 5.1 gate (0.3 M tokens / $0.20). Eight review calls, one round. '
                                 'Arm B runs first; its measured usage decides whether arm C is started, so the '
                                 'gate can never be overshot by starting both arms blindly.')},
        'passage_budget': {'limit_chars_per_endpoint': PASSAGE_LIMIT,
                           'why': ('the KU/member payload is fixed by arm A, so the source text is the only knob; '
                                   'the cap keeps both arms plus verdict output inside the approved gate'),
                           'limitation': ('arm B/C see the leading Cards of each endpoint up to the cap, not the '
                                          'complete Card set; a no-effect result must be read with that truncation '
                                          'in mind, and the full Cards stay available in the frozen index')},
        'no_answer_leakage': ('Neither arm contains reference roles, directness, reasons or bridge paths; arm C lists '
                              'possible intermediates by evidence overlap only, and the rule is frozen here.'),
        'stop_condition': ('One round only. If directness stays unreliable, the prerequisite part is reported as an '
                           'evaluation protocol plus failure diagnosis, not as a high-accuracy learning-path system.'),
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('manifest ->', OUT / 'manifest.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
