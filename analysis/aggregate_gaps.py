# -*- coding: utf-8 -*-
"""Aggregate per-reference coverage across reviewed packets -> systematic gap list."""
import json, glob, os, re, sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')
ORDER = {'full': 0, 'partial': 1, 'name_only': 2, 'missing': 3, 'not_applicable': 4}


def is_done(rv):
    prov = (rv.get('reviewer_provenance') or 'pending')
    rows = (rv.get('node_coverage') or []) + (rv.get('relation_coverage') or [])
    return prov not in ('', 'pending') and rows and all(r.get('status') != 'pending' for r in rows)


def main() -> int:
    dirname = sys.argv[1] if len(sys.argv) > 1 else 'review_live01'
    d = os.path.join(BASE, dirname)
    mapping = {m['label']: m for m in json.load(open(os.path.join(d, 'mapping_private.json'), encoding='utf-8'))}
    node_status = {}   # ref_id -> {label: status}
    rel_status = {}
    ref_name = {}
    done, pending = [], []
    for p in sorted(glob.glob(os.path.join(d, 'P*.json'))):
        if p.endswith('_review.json') or not re.match(r'^P\d+\.json$', os.path.basename(p)):
            continue
        label = os.path.basename(p)[:-5]
        rp = os.path.join(d, label + '_review.json')
        if not os.path.exists(rp):
            pending.append(label + '(no output)')
            continue
        pk = json.load(open(p, encoding='utf-8'))
        rv = json.load(open(rp, encoding='utf-8'))
        if not is_done(rv):
            pending.append(label)
            continue
        done.append(label)
        for n in pk['reference']['nodes']:
            ref_name[n['id']] = '%s [%s]' % (n.get('name', '?'), n.get('type', '?'))
        for r in pk['reference']['relations']:
            ref_name[r['id']] = 'REL %s' % (r.get('name') or r.get('meaning', '')[:36])
        for row in rv.get('node_coverage', []):
            node_status.setdefault(row['reference_id'], {})[label] = row['status']
        for row in rv.get('relation_coverage', []):
            rel_status.setdefault(row['reference_id'], {})[label] = row['status']

    print('=' * 100)
    print('%s | reviewed %d: %s' % (dirname, len(done), ' '.join(done)))
    print('pending: %s' % (' '.join(pending) or 'none'))
    print()
    for title, table in (('REFERENCE NODES', node_status), ('REFERENCE RELATIONS', rel_status)):
        print('--- %s (per-reference status across reviewed packets) ---' % title)
        for ref in sorted(table, key=lambda x: (len(x), x)):
            dist = {}
            for st in table[ref].values():
                dist[st] = dist.get(st, 0) + 1
            worst = max(table[ref].values(), key=lambda s: ORDER.get(s, 9))
            weak = sorted(l for l, s in table[ref].items() if s in ('missing', 'name_only'))
            tag = '  <== SYSTEMATIC GAP' if dist.get('missing', 0) + dist.get('name_only', 0) >= 2 else ''
            print('  %-6s %-42s %s%s' % (ref, ref_name.get(ref, '?')[:42], dist, tag))
            if weak:
                print('         weak in: %s' % ' '.join(weak))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
