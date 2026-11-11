# -*- coding: utf-8 -*-
"""Validate one merged-experiment review packet with the official validate().

Usage:
  python analysis/validate_merged_one.py <LABEL>
  e.g. python analysis/validate_merged_one.py P03

Reads evaluation/external_baselines_merged_2026-10-09/review_merged/<LABEL>.json and
<LABEL>_review.json, applies the frozen official validate(), then additionally checks the
merged-only blocks (cross_relation_coverage, duplicate_groups, false_merges).
"""
import json, os, sys

ROOT = r'E:\MaF-KG\.worktrees\stable-main'
REVIEW_DIR = os.path.join(ROOT, 'evaluation', 'external_baselines_merged_2026-10-09', 'review_merged')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402


def check_merged_blocks(packet, review):
    references = packet['reference'].get('cross_section_relations', [])
    rows = review.get('cross_relation_coverage')
    if not isinstance(rows, list) or len(rows) != len(references) or \
            {r['reference_id'] for r in rows} != {r['id'] for r in references}:
        raise ValueError('Cross-section relation judgments are missing or repeated')
    known = {i['item_id'] for i in packet['items']}
    for row in rows:
        if row['status'] not in {'full', 'partial', 'name_only', 'missing', 'not_applicable'}:
            raise ValueError('Unfinished cross-section semantic judgment')
        if set(row['item_ids']) - known or (row['status'] in {'full', 'partial', 'name_only'} and not row['item_ids']):
            raise ValueError('Cross-section judgment does not link actual output')
        if not row['reason'].strip():
            raise ValueError('Cross-section rationale required')
    groups = review.get('duplicate_groups', [])
    merges = review.get('false_merges', [])
    if not isinstance(groups, list) or not isinstance(merges, list):
        raise ValueError('duplicate_groups / false_merges must be lists')
    pairs = {tuple(sorted(entry['pair'])) for entry in packet['reference'].get('must_not_merge', [])}
    for entry in merges:
        pair = entry.get('pair') if isinstance(entry, dict) else None
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('false_merges entries need a two-element pair')
        if not entry.get('reason', '').strip() or not entry.get('item_ids'):
            raise ValueError('false_merges entries need item_ids and a reason')
        if tuple(sorted(pair)) not in pairs:
            raise ValueError('false_merges pair is not in the frozen must_not_merge list: %s' % pair)
    for entry in groups:
        if not isinstance(entry, dict) or not entry.get('reference_id') or not entry.get('item_ids') or \
                not entry.get('reason', '').strip():
            raise ValueError('duplicate_groups entries need reference_id, item_ids and a reason')


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2
    label = sys.argv[1]
    review_dir = sys.argv[2] if len(sys.argv) == 3 else REVIEW_DIR
    packet = json.load(open(os.path.join(review_dir, label + '.json'), encoding='utf-8'))
    review = json.load(open(os.path.join(review_dir, label + '_review.json'), encoding='utf-8'))
    try:
        validate(packet, review)
        check_merged_blocks(packet, review)
    except Exception as e:  # noqa: BLE001
        print('VALIDATE FAIL [%s/%s]: %s' % (os.path.basename(review_dir), label, e))
        return 1

    def dist(rows):
        out = {}
        for r in rows:
            out[r['status']] = out.get(r['status'], 0) + 1
        return out

    nodes = review['node_coverage']
    rels = review['relation_coverage']
    cross = review['cross_relation_coverage']
    sup = review['prediction_support']
    print('VALIDATE OK [%s/%s] nodes=%d %s | relations=%d %s | cross=%d %s | support=%d %s | dup=%d false_merge=%d' % (
        os.path.basename(review_dir), label, len(nodes), dist(nodes), len(rels), dist(rels), len(cross), dist(cross),
        len(sup), dist(sup), len(review.get('duplicate_groups', [])), len(review.get('false_merges', []))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
