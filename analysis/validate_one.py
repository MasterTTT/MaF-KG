# -*- coding: utf-8 -*-
"""Validate one external-baseline review packet with the official validate().

Usage:
  python analysis/validate_one.py <review_dir_name> <LABEL>
  e.g. python analysis/validate_one.py review_live01 P09
       python analysis/validate_one.py review_guarded02 P02
"""
import json, os, sys

ROOT = r'E:\MaF-KG\.worktrees\stable-main'
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    dirname, label = sys.argv[1], sys.argv[2]
    base = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08', dirname)
    packet = json.load(open(os.path.join(base, label + '.json'), encoding='utf-8'))
    review = json.load(open(os.path.join(base, label + '_review.json'), encoding='utf-8'))
    try:
        validate(packet, review)
    except Exception as e:  # noqa: BLE001
        print('VALIDATE FAIL [%s/%s]: %s' % (dirname, label, e))
        return 1
    nodes = review.get('node_coverage', [])
    rels = review.get('relation_coverage', [])
    sup = review.get('prediction_support', [])
    def dist(rows, key='status'):
        out = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return out
    print('VALIDATE OK [%s/%s]  nodes=%d %s | relations=%d %s | support=%d %s' % (
        dirname, label, len(nodes), dist(nodes), len(rels), dist(rels), len(sup), dist(sup)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
