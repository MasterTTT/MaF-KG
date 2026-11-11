# -*- coding: utf-8 -*-
"""Validate every reviewable packet in a review round with both gates.

Runs the frozen official validate() plus the merged-only checks (cross-section relations,
duplicate_groups shape, false_merges restricted to the frozen must_not_merge pairs) over all packets
listed in the round's mapping_private.json, so the reviewers' self-checks are re-verified centrally.

Usage:
  python analysis/merged_validate_round.py [--review-dir DIR]
"""
import argparse, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'analysis'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402
from validate_merged_one import check_merged_blocks  # noqa: E402

DEFAULT = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09' / 'review_merged'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--review-dir', default=str(DEFAULT))
    args = ap.parse_args()
    review_dir = Path(args.review_dir)
    mapping = json.loads((review_dir / 'mapping_private.json').read_text(encoding='utf-8'))

    failures, reviewable, skipped = [], 0, 0
    for job in mapping:
        label = job['label']
        packet_path = review_dir / (label + '.json')
        if not packet_path.exists():
            failures.append((label, 'packet file missing'))
            continue
        packet = json.loads(packet_path.read_text(encoding='utf-8'))
        if packet.get('status') and packet.get('status') != 'reviewable':
            skipped += 1
            print('%-4s %-28s r%s  SKIP (%s, not a zero score)' % (label, job['arm'], job['repeat'], packet['status']))
            continue
        review_path = review_dir / (label + '_review.json')
        if not review_path.exists():
            failures.append((label, 'review file missing'))
            continue
        review = json.loads(review_path.read_text(encoding='utf-8'))
        try:
            validate(packet, review)
            check_merged_blocks(packet, review)
        except Exception as exc:  # noqa: BLE001
            failures.append((label, '%s: %s' % (type(exc).__name__, exc)))
            print('%-4s %-28s r%s  FAIL %s' % (label, job['arm'], job['repeat'], exc))
            continue
        reviewable += 1
        nodes = Counter(r['status'] for r in review['node_coverage'])
        rels = Counter(r['status'] for r in review['relation_coverage'])
        cross = Counter(r['status'] for r in review['cross_relation_coverage'])
        print('%-4s %-28s r%s  OK  nodes=%s rel=%s cross=%s dup=%d false_merge=%d' % (
            label, job['arm'], job['repeat'], dict(nodes), dict(rels), dict(cross),
            len(review.get('duplicate_groups') or []), len(review.get('false_merges') or [])))
    print()
    print('round %s: %d reviewed and valid, %d skipped (no-output), %d failed' % (
        review_dir.name, reviewable, skipped, len(failures)))
    for label, reason in failures:
        print('  FAILED %s: %s' % (label, reason))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
