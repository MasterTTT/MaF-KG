# -*- coding: utf-8 -*-
"""Precise review-packet state + official validate on reviewed packets."""
import json, glob, os, sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from score_external_baseline_reviews import validate  # noqa: E402

for dirname in ('review_live01', 'review_guarded02'):
    d = os.path.join(BASE, dirname)
    print('=' * 64)
    print(dirname)
    mapping = {}
    mp = os.path.join(d, 'mapping_private.json')
    if os.path.exists(mp):
        for m in json.load(open(mp, encoding='utf-8')):
            mapping[m['label']] = m
    done = []
    pend = []
    for p in sorted(glob.glob(os.path.join(d, 'P*_review.json'))):
        label = os.path.basename(p).replace('_review.json', '')
        obj = json.load(open(p, encoding='utf-8'))
        prov = (obj.get('reviewer_provenance') or '').strip()
        rows = (obj.get('node_coverage') or []) + (obj.get('relation_coverage') or [])
        pending_rows = sum(1 for r in rows if r.get('status') == 'pending')
        is_done = prov not in ('', 'pending') and pending_rows == 0 and rows
        m = mapping.get(label, {})
        tag = '%s %s r%s' % (m.get('method', '?'), m.get('section', '?'), m.get('repeat', '?'))
        if is_done:
            done.append(label)
            verdict = 'validate: OK'
            try:
                validate(json.load(open(os.path.join(d, label + '.json'), encoding='utf-8')), obj)
            except Exception as e:  # noqa: BLE001
                verdict = 'validate: FAIL -> %s' % e
            print('  %-5s DONE   %-26s rows=%2d  %s' % (label, tag, len(rows), verdict))
        else:
            pend.append(label)
            print('  %-5s PENDING %-26s rows=%2d pending_rows=%d' % (label, tag, len(rows), pending_rows))
    print('  -> done %d %s | pending %d' % (len(done), done, len(pend)))
