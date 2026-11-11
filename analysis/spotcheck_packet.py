# -*- coding: utf-8 -*-
"""Main-agent spot check: does a packet's native output really contain/omit keywords?

Usage:
  python analysis/spotcheck_packet.py <review_dir> <LABEL> <kw1> [<kw2> ...]
"""
import json, os, sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    dirname, label, kws = sys.argv[1], sys.argv[2], sys.argv[3:]
    pk = json.load(open(os.path.join(BASE, dirname, label + '.json'), encoding='utf-8'))
    print('packet %s | section %s | items %d | ref nodes %d | ref rels %d' % (
        label, pk.get('section'), len(pk['items']),
        len(pk['reference']['nodes']), len(pk['reference']['relations'])))
    hits = {k: [] for k in kws}
    for it in pk['items']:
        blob = json.dumps(it, ensure_ascii=False)
        for k in kws:
            if k in blob:
                hits[k].append(it['item_id'])
    for k in kws:
        print('  kw %-14s -> %s' % (k, hits[k] if hits[k] else 'NOT FOUND in any item'))
    print()
    print('--- item name/description digest ---')
    for it in pk['items']:
        nat = it.get('native', {})
        name = nat.get('name') or nat.get('startNode') or '?'
        if it.get('kind') == 'relation':
            name = '%s -[%s]-> %s' % (nat.get('startNode', '?'), nat.get('type', '?'), nat.get('endNode', '?'))
        desc = (nat.get('description') or nat.get('logical_form') or '')
        print('  %-8s %s' % (it['item_id'], str(name)[:70]))
        if desc:
            print('           %s' % str(desc)[:150].replace('\n', ' '))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
