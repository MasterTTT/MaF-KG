# -*- coding: utf-8 -*-
"""Inventory of remaining review packets (size + what must be judged)."""
import json, glob, os, re, sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')

for dirname in ('review_live01', 'review_guarded02'):
    d = os.path.join(BASE, dirname)
    print('=' * 72)
    print(dirname)
    tot_items = tot_nodes = tot_rels = 0
    for p in sorted(glob.glob(os.path.join(d, 'P*.json'))):
        if p.endswith('_review.json') or not re.match(r'^P\d+\.json$', os.path.basename(p)):
            continue
        label = os.path.basename(p)[:-5]
        pk = json.load(open(p, encoding='utf-8'))
        rpath = os.path.join(d, label + '_review.json')
        if not os.path.exists(rpath):
            print('  %-4s %-8s %-6s items=%-3d ref_nodes=%-2d ref_rels=%-2d  (no review file: failed/no output)' % (
                label, pk.get('section', '?'), 'FAILED', len(pk['items']),
                len(pk['reference']['nodes']), len(pk['reference']['relations'])))
            continue
        rv = json.load(open(rpath, encoding='utf-8'))
        rows = (rv.get('node_coverage') or []) + (rv.get('relation_coverage') or [])
        done = (rv.get('reviewer_provenance') or 'pending') not in ('', 'pending') and \
               all(r.get('status') != 'pending' for r in rows)
        n_items = len(pk['items'])
        n_nodes = len(pk['reference']['nodes'])
        n_rels = len(pk['reference']['relations'])
        tot_items += n_items; tot_nodes += n_nodes; tot_rels += n_rels
        print('  %-4s %-8s %-6s items=%-3d ref_nodes=%-2d ref_rels=%-2d  %s' % (
            label, pk.get('section', '?'), ('DONE' if done else 'TODO'),
            n_items, n_nodes, n_rels, os.path.basename(pk.get('source_file', '') or '')))
    print('  TOTAL items=%d ref_nodes=%d ref_rels=%d' % (tot_items, tot_nodes, tot_rels))
