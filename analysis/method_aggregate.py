# -*- coding: utf-8 -*-
"""Per-method aggregation over reviewed packets (provisional or final).

Only includes packets whose review is complete and validate-clean; reports how
many planned runs are still missing so partial views cannot be mistaken for final.
"""
import json, os, sys, collections

ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402

STATUSES = ('full', 'partial', 'name_only', 'missing', 'not_applicable')


def main() -> int:
    dirname = sys.argv[1] if len(sys.argv) > 1 else 'review_live01'
    d = os.path.join(BASE, dirname)
    mapping = json.load(open(os.path.join(d, 'mapping_private.json'), encoding='utf-8'))
    per_method = collections.defaultdict(lambda: {'packets': 0, 'nodes': collections.Counter(),
                                                  'rels': collections.Counter(), 'support': collections.Counter(),
                                                  'scope': collections.Counter(), 'cond': collections.Counter(),
                                                  'labels': []})
    missing = collections.defaultdict(list)
    for m in sorted(mapping, key=lambda x: (x['method'], x['section'], x['repeat'])):
        if m['status'] != 'completed':
            missing[m['method']].append('%s(%s)' % (m['label'], m.get('failure_kind', m['status'])))
            continue
        rp = os.path.join(d, m['label'] + '_review.json')
        if not os.path.exists(rp):
            missing[m['method']].append('%s(no review)' % m['label'])
            continue
        pk = json.load(open(os.path.join(d, m['label'] + '.json'), encoding='utf-8'))
        rv = json.load(open(rp, encoding='utf-8'))
        rows = (rv.get('node_coverage') or []) + (rv.get('relation_coverage') or [])
        if not rows or any(r.get('status') == 'pending' for r in rows):
            missing[m['method']].append('%s(pending)' % m['label'])
            continue
        try:
            validate(pk, rv)
        except Exception as e:  # noqa: BLE001
            missing[m['method']].append('%s(INVALID:%s)' % (m['label'], e))
            continue
        agg = per_method[m['method']]
        agg['packets'] += 1
        agg['labels'].append(m['label'])
        for r in rv['node_coverage']:
            agg['nodes'][r['status']] += 1
            if r.get('conditions_complete') is not None:
                agg['cond']['complete' if r['conditions_complete'] else 'incomplete'] += 1
        for r in rv['relation_coverage']:
            agg['rels'][r['status']] += 1
        for r in rv['prediction_support']:
            agg['support'][r['status']] += 1
            agg['scope']['admitted' if r['scope_admitted'] is True else
                        'excluded' if r['scope_admitted'] is False else 'unresolved'] += 1

    print('=' * 100)
    print('%s | per-method aggregation (reviewed packets only)' % dirname)
    for method in sorted(per_method):
        a = per_method[method]
        n = a['nodes']
        tot = sum(n.values())
        req = n['full'] + n['partial'] + n['name_only'] + n['missing']
        rel = a['rels']
        rt = sum(rel.values())
        sup = a['support']
        st = sum(sup.values())
        print()
        print('  %s : %d reviewed packets %s' % (method, a['packets'], a['labels']))
        print('    reference nodes %-3d  full %-3d partial %-3d name_only %-3d missing %-3d not_appl %-3d' % (
            tot, n['full'], n['partial'], n['name_only'], n['missing'], n['not_applicable']))
        print('      -> full-rate %.3f | recognized(full+partial+name_only) %.3f' % (
            n['full'] / tot if tot else 0, (n['full'] + n['partial'] + n['name_only']) / tot if tot else 0))
        print('    reference relations %-3d full %-3d partial %-3d name_only %-3d missing %-3d' % (
            rt, rel['full'], rel['partial'], rel['name_only'], rel['missing']))
        print('    claim conditions: %s' % dict(a['cond']))
        print('    prediction support %-3d %s | scope %s' % (st, dict(sup), dict(a['scope'])))
        if missing.get(method):
            print('    NOT INCLUDED (planned but unavailable): %s' % ' '.join(missing[method]))
    print()
    print('  methods with unavailable planned runs:', {k: v for k, v in missing.items() if v})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
