# -*- coding: utf-8 -*-
"""Final sweep: validate every reviewed packet and emit a consolidated table.

Outputs:
  analysis/review_consolidated.json  (per-packet status distributions + provenance)
  stdout: readable table per review directory
"""
import json, os, sys, collections

ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402


def dist(rows, choices=('full', 'partial', 'name_only', 'missing', 'not_applicable')):
    out = collections.Counter(r.get('status') for r in rows)
    return {k: out[k] for k in choices if out.get(k)}


def main() -> int:
    consolidated = {}
    all_ok = True
    for dirname in ('review_live01', 'review_guarded02'):
        d = os.path.join(BASE, dirname)
        mapping = json.load(open(os.path.join(d, 'mapping_private.json'), encoding='utf-8'))
        print('=' * 96)
        print(dirname)
        rows_out = []
        for m in sorted(mapping, key=lambda x: x['label']):
            label = m['label']
            if m['status'] != 'completed':
                print('  %-4s %-9s %-7s r%s  %-9s %s' % (label, m['method'], m['section'], m['repeat'],
                                                         m['status'], m.get('failure_kind', '')))
                rows_out.append({'label': label, 'method': m['method'], 'section': m['section'],
                                 'repeat': m['repeat'], 'status': m['status'],
                                 'failure_kind': m.get('failure_kind')})
                continue
            pk = json.load(open(os.path.join(d, label + '.json'), encoding='utf-8'))
            rv = json.load(open(os.path.join(d, label + '_review.json'), encoding='utf-8'))
            try:
                validate(pk, rv)
                verdict = 'OK'
            except Exception as e:  # noqa: BLE001
                verdict = 'FAIL: %s' % e
                all_ok = False
            recorded_hash = rv.get('prediction_sha256')
            hash_match = None if recorded_hash is None else recorded_hash == pk.get('prediction_sha256')
            if hash_match is False:
                verdict = 'FAIL: recorded prediction hash mismatch'
                all_ok = False
            prov = 'luna' if 'luna' in (rv.get('reviewer_provenance') or '') else 'dsh'
            nd, rd, sd = dist(rv.get('node_coverage', [])), dist(rv.get('relation_coverage', [])), dist(rv.get('prediction_support', []), ('yes', 'partial', 'no', 'undecided'))
            print('  %-4s %-9s %-7s r%s  validate=%-4s prov=%-4s | nodes %-34s | rels %-24s | support %s' % (
                label, m['method'], m['section'], m['repeat'], verdict, prov, nd, rd, sd))
            rows_out.append({'label': label, 'method': m['method'], 'section': m['section'],
                             'repeat': m['repeat'], 'status': 'reviewed', 'validate': verdict,
                             'provenance_class': prov, 'nodes': nd, 'relations': rd, 'support': sd,
                             'prediction_sha256': rv.get('prediction_sha256'),
                             'packet_sha256_match': hash_match,
                             'review_hash_state': 'not_recorded' if recorded_hash is None else 'match' if hash_match else 'mismatch'})
        consolidated[dirname] = rows_out
    out = os.path.join(ROOT, 'analysis', 'review_consolidated.json')
    json.dump(consolidated, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print()
    print('all packets validate:', all_ok)
    print('wrote ->', out)
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
