# -*- coding: utf-8 -*-
"""P1-A primary endpoint: the same early content (ch2_s5) judged in every context scope.

Per-scope full-recall numbers are not comparable on their own, because S1's 19 required items are a
subset of S2's 26 and S3's 35: a decline could be composition, not context. This script restricts every
review to the frozen `reference_common_ch2s5.json` item set and reports, scope by scope:

  * required-node coverage (full / partial / name-only / missing) on the common nodes,
  * `conditions_complete` on the common required Claims,
  * relation coverage on the common intra relations,
  * duplicates and false merges as recorded (they are per-output-item, so they are reported as-is),

for every repeat that has a review. S1 and S2 come from this round's packets; S3 is read from the
already-completed merged reviews, so no new judgement is needed for the reference point.

Usage: python analysis/input_scope_common_subset.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
SCOPE = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
sys.stdout.reconfigure(encoding='utf-8')

SOURCES = {
    ('maf', 's1', 1): SCOPE / 'review_scope' / 'R01_review.json',
    ('maf', 's1', 2): SCOPE / 'review_scope' / 'R03_review.json',
    ('maf', 's2', 1): SCOPE / 'review_scope' / 'R02_review.json',
    ('maf', 's2', 2): SCOPE / 'review_scope' / 'R04_review.json',
    ('maf', 's3', 1): MERGED / 'review_merged' / 'P06_review.json',
    ('maf', 's3', 2): MERGED / 'review_merged' / 'P05_review.json',
    ('direct', 's1', 2): SCOPE / 'review_scope' / 'R07_review.json',
    ('direct', 's2', 1): SCOPE / 'review_scope' / 'R06_review.json',
    ('direct', 's2', 2): SCOPE / 'review_scope' / 'R05_review.json',
    ('direct', 's3', 1): MERGED / 'review_merged' / 'P08_review.json',
    ('direct', 's3', 2): MERGED / 'review_merged' / 'P10_review.json',
}
COVERAGE = ('full', 'partial', 'name_only', 'missing', 'not_applicable')


def compact(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def main() -> int:
    common = json.loads((SCOPE / 'reference' / 'reference_common_ch2s5.json').read_text(encoding='utf-8'))
    node_ids = {n['id'] for n in common['nodes']}
    required_ids = {n['id'] for n in common['nodes'] if n['status'] == 'M'}
    claim_ids = {n['id'] for n in common['nodes'] if n['type'] == 'Claim' and n['status'] == 'M'}
    relation_ids = {r['id'] for r in common['relations']}
    print('common subset: %d nodes (%d required, %d required Claims) | %d intra relations'
          % (len(node_ids), len(required_ids), len(claim_ids), len(relation_ids)))

    rows = []
    for (arm, scope, repeat), path in sorted(SOURCES.items()):
        if not path.is_file():
            print('%-6s %-3s r%d : review missing (%s)' % (arm, scope, repeat, path.name))
            continue
        review = json.loads(path.read_text(encoding='utf-8'))
        nodes = {row['reference_id']: row for row in review['node_coverage']}
        rels = {row['reference_id']: row for row in review.get('relation_coverage', [])}
        missing_ids = node_ids - set(nodes)
        if missing_ids:
            print('%-6s %-3s r%d : %d common nodes are not in this review (%s)'
                  % (arm, scope, repeat, len(missing_ids), sorted(missing_ids)[:5]))
        counts = {key: 0 for key in COVERAGE}
        for node_id in node_ids & set(nodes):
            counts[nodes[node_id]['status']] = counts.get(nodes[node_id]['status'], 0) + 1
        required_full = sum(1 for i in required_ids & set(nodes) if nodes[i]['status'] == 'full')
        conditions_true = sum(1 for i in claim_ids & set(nodes) if nodes[i].get('conditions_complete') is True)
        conditions_false = sum(1 for i in claim_ids & set(nodes) if nodes[i].get('conditions_complete') is False)
        rel_counts = {}
        for rel_id in relation_ids & set(rels):
            rel_counts[rels[rel_id]['status']] = rel_counts.get(rels[rel_id]['status'], 0) + 1
        # Duplicate counts follow the adjudication layer when it exists: only `repetition` is counted, and
        # `split` (one reference item carried by output items in different roles) is reported separately.
        adjudication = {}
        adj_path = path.parent / 'duplicate_adjudication.json'
        if adj_path.is_file():
            for entry in json.loads(adj_path.read_text(encoding='utf-8')).get('entries', []):
                adjudication[(entry['label'], entry['index'])] = entry['kind']
        label = path.name.split('_')[0]
        repetitions = splits = 0
        for index in range(1, len(review.get('duplicate_groups', [])) + 1):
            kind = adjudication.get((label, index))
            if kind == 'split':
                splits += 1
            elif kind == 'repetition':
                repetitions += 1
            else:
                repetitions += 1     # no adjudication on file: fall back to the reviewer's own count
        row = {
            'arm': arm, 'scope': scope, 'repeat': repeat, 'review': str(path.relative_to(ROOT)),
            'common_nodes_judged': len(node_ids & set(nodes)),
            'coverage': counts,
            'required_full': required_full, 'required_total': len(required_ids),
            'required_full_recall': round(required_full / len(required_ids), 4),
            'conditions_complete_true': conditions_true, 'conditions_complete_false': conditions_false,
            'conditions_total': len(claim_ids),
            'relations': rel_counts,
            'relation_full': rel_counts.get('full', 0), 'relations_total': len(relation_ids),
            'duplicate_repetitions': repetitions, 'duplicate_splits': splits,
            'duplicate_groups_raw': len(review.get('duplicate_groups', [])),
            'false_merges': len(review.get('false_merges', [])),
            'missing_from_review': sorted(missing_ids),
        }
        rows.append(row)
        print('%-6s %-3s r%d : full %d/%d (%.3f) | conditions true %d / false %d of %d | relations full %d/%d %s | dup %d(+%d split) fm %d'
              % (arm, scope, repeat, required_full, len(required_ids), row['required_full_recall'],
                 conditions_true, conditions_false, len(claim_ids),
                 row['relation_full'], len(relation_ids), rel_counts, repetitions, splits,
                 row['false_merges']))

    payload = {
        'purpose': ('Does the same early content stay completely expressed when the context is extended? '
                    'Every number here is restricted to the pre-frozen ch2_s5 item set, so the scopes share '
                    'one denominator and the comparison is paired.'),
        'common_subset': {'nodes': len(node_ids), 'required': len(required_ids), 'required_claims': len(claim_ids),
                          'intra_relations': len(relation_ids), 'source': 'reference_common_ch2s5.json'},
        'rows': rows,
        'notes': [
            'S1 = merged lines 1-574, S2 = 1-936, S3 = 1-1094 (S3 read from the completed merged reviews).',
            'S1 and S2 here have one repeat each so far; S3 has two. Repeat counts must accompany the table.',
            'Duplicates and false merges are per output item, not per reference item, so they are not restricted '
            'to the common subset.',
            'Both scopes changed the amount of content and the task scope at once, so this is a paired '
            'context-extension diagnostic, not a pure character-length causal test.',
        ],
    }
    target = SCOPE / 'common_subset_retention.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
