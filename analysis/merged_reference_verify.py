# -*- coding: utf-8 -*-
"""Verify the assembled reference: every relation endpoint must be a real item id.

Dangling endpoints (temporary cross-section names from the candidates) are moved
to external_references so the frozen relation set only contains resolvable pairs.
"""
import hashlib, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
P = NEW / 'reference' / 'reference_merged_v1.json'
ref = json.loads(P.read_text(encoding='utf-8'))
ids = {i['id'] for i in ref['nodes']}
print('items:', len(ids))

kept, moved = [], []
for rel in ref['relations']:
    if rel['start'] in ids and rel['end'] in ids:
        kept.append(rel)
    else:
        moved.append(rel)
print('relations resolvable:', len(kept), '| dangling moved out:', len(moved))
for m in moved:
    print('   %-6s %-6s -[%s]-> %s   (%s)' % (m.get('id'), m['start'], m['type'], str(m['end'])[:46], m.get('section')))

for m in moved:
    ref['external_references'].append({
        'produced_by': m.get('section'), 'relation_id': m.get('id'),
        'raw_start': m['start'], 'raw_end': m['end'], 'type': m['type'],
        'justification': m.get('justification', ''),
        'note': 'target lies outside the merged three-section scope; recorded, never scored',
    })

# cross-section relations must also resolve
for c in ref['cross_section_relations']:
    assert c['start'] in ids and c['end'] in ids, c

ref['relations'] = kept
ref['counts'].update({'relations_intra': len(kept), 'external_references': len(ref['external_references'])})
ref['verification'] = {
    'endpoint_check': 'all relation endpoints resolve to reference item ids',
    'moved_to_external': [m.get('id') for m in moved],
    'duplicate_ids': len(ids) != len(ref['nodes']),
}
P.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding='utf-8')
print()
print('final counts:', json.dumps(ref['counts'], ensure_ascii=False))
print('final sha256:', hashlib.sha256(P.read_bytes()).hexdigest().upper())
print()
print('must_not_merge pairs (%d):' % len(ref['must_not_merge']))
for p in ref['must_not_merge']:
    pair = p.get('pair') or p.get('items') or p
    print('   %-22s %s' % (str(pair)[:44], str(p.get('reason', ''))[:60]))
