# -*- coding: utf-8 -*-
"""Assemble the three section candidates into one merged-coordinate reference proposal."""
import hashlib, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
CAND = NEW / 'reference_candidates'
OUT = NEW / 'reference'
OUT.mkdir(parents=True, exist_ok=True)

prov = json.loads((NEW / 'input' / 'provenance.json').read_text(encoding='utf-8'))
src2merged = {}
for row in prov['mapping']:
    src2merged[(row['section'], row['source_line'])] = row['merged_line']

SECTIONS = [('ch2_s5', 'D-'), ('ch3_s1', 'M-'), ('ch3_s2', 'L-')]
EXTERNAL = {  # out-of-scope targets referenced by the sources; not scored
    'EXT-ch1_s4-thm1': '第一章第四节 定理1（罗尔定理的前置：最值/费马型结论）',
    'EXT-ch1_s7-thm1': '第一章第七节 定理1（等价无穷小相关）',
    'EXT-ch1_s9-ex7': '第一章第九节 例7（例题，按规程排除）',
    'EXT-ch2_s1-geom': '第二章第一节 三、导数的几何意义',
    'EXT-ch2_s2-basic-deriv': '第二章第二节 四、基本初等函数导数公式',
    'EXT-ch2_s2-sum-deriv': '第二章第二节 一、和差积商求导法则',
    'EXT-ch2_s2-comp-deriv': '第二章第二节 三、复合函数求导法则',
    'EXT-ch1_extremum': '第一章 闭区间上连续函数的最大值最小值定理（ch3_s1 证明罗尔时引用）',
}

items, relations, must_not_merge, groups, uncertainties, external_refs = [], [], [], [], [], []
for sid, prefix in SECTIONS:
    cand = json.loads((CAND / (sid + '.json')).read_text(encoding='utf-8'))
    for it in cand.get('items', []):
        a, b = it['evidence']
        ma, mb = src2merged.get((sid, a)), src2merged.get((sid, b))
        if ma is None or mb is None:
            raise SystemExit('evidence line outside provenance: %s %s %s' % (sid, it['id'], it['evidence']))
        row = dict(it)
        row.update({'section': sid, 'source_evidence': [a, b], 'merged_evidence': [ma, mb]})
        items.append(row)
    for rel in cand.get('relations', []):
        end = rel.get('end', '')
        if isinstance(end, str) and end.startswith('EXT-'):
            external_refs.append({'produced_by': sid, 'relation_id': rel.get('id'), 'target': end,
                                  'target_meaning': EXTERNAL.get(end, '(unmapped external target)'),
                                  'note': 'out of the merged three-section scope; excluded from scoring'})
            continue
        relations.append({'id': rel['id'], 'section': sid, 'start': rel['start'], 'type': rel['type'],
                          'end': end, 'justification': rel.get('justification', ''), 'evidence': rel.get('evidence')})
    must_not_merge += [dict(p, section=sid) for p in cand.get('must_not_merge', [])]
    groups += [dict(g, section=sid) for g in cand.get('boundary_groups', [])]
    uncertainties += ['[%s] %s' % (sid, u) for u in cand.get('uncertainties', [])]

def mm(sid, line):
    v = src2merged.get((sid, line))
    if v is None:
        raise SystemExit('cross-section evidence line unmapped: %s %s' % (sid, line))
    return v

# resolved cross-section / cross-chapter relations (temporary names from candidates)
cross = [
    {'id': 'X-R1', 'start': 'L-N2', 'type': 'derived_from', 'end': 'M-7', 'scope': 'cross-section (ch3_s2 <- ch3_s1)',
     'justification': '教材第371行明示洛必达法则根据柯西中值定理推出，第391行给出该证明。',
     'evidence': [mm('ch3_s2', 371), mm('ch3_s2', 391)]},
    {'id': 'X-R2', 'start': 'M-5', 'type': 'involves', 'end': 'D-N3', 'scope': 'cross-chapter (ch3_s1 <- ch2_s5)',
     'justification': '教材第175行以 dy=f\'(x)Δx 是 Δy 的近似表达式、误差仅在 Δx→0 时趋零，作为引出有限增量公式（精确等式）的对照。',
     'evidence': [mm('ch3_s1', 175)]},
]

ref = {
    'version': 'merged-three-sections.reference.v1',
    'status': 'proposal — pending user review and freeze; NOT yet frozen',
    'date': '2026-10-09',
    'approval_basis': 'Three per-section candidate sets produced independently from source only (analysis/merged_reference_protocol.md); '
                      'merged here with id alignment, merged-coordinate evidence, and resolved cross-section relations. '
                      'Relation subset remains assistant-provisional.',
    'input': {
        'merged_file': 'input/merged_ch2s5_ch3s1_ch3s2.md',
        'merged_sha256': prov['merged_sha256'],
        'merged_lines': prov['merged_lines'],
        'sections': [
            {'id': 'ch2_s5', 'title': '第二章第五节 函数的微分', 'merged_range': [1, 574]},
            {'id': 'ch3_s1', 'title': '第三章第一节 微分中值定理', 'merged_range': [575, 936]},
            {'id': 'ch3_s2', 'title': '第三章第二节 洛必达法则', 'merged_range': [937, 1094]},
        ],
    },
    'scoring_policy': {
        'alignment': 'one-to-one semantic alignment with explicit mathematical-condition review; evidence lines are merged-document coordinates',
        'statuses': ['full', 'partial', 'name_only', 'missing', 'not_applicable'],
        'conditions': 'Claim judged full must have conditions_complete=true with the conditions written in the output itself',
        'no_total_f1': 'required recall, relation coverage, duplicate/false-merge counts and cross-section relation coverage are separate universes',
        'duplicates_false_merges': 'duplicate_groups = one reference item covered by several output items or one output item repeated; '
                                   'false_merges = a must_not_merge pair collapsed into one output item',
        'external_targets': 'references to chapters outside the merged scope are recorded but never scored',
    },
    'nodes': items,
    'relations': relations,
    'cross_section_relations': cross,
    'external_references': external_refs,
    'must_not_merge': must_not_merge,
    'boundary_groups': groups,
    'uncertainties': uncertainties,
    'counts': {'items': len(items),
               'required': sum(1 for i in items if i.get('status') == 'M'),
               'optional': sum(1 for i in items if i.get('status') == 'O'),
               'relations_intra': len(relations),
               'relations_cross': len(cross),
               'must_not_merge': len(must_not_merge),
               'boundary_groups': len(groups)},
}
path = OUT / 'reference_merged_v1.json'
path.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding='utf-8')
sha = hashlib.sha256(path.read_bytes()).hexdigest().upper()
print('wrote ->', path)
print('sha256:', sha)
print()
print('counts:', json.dumps(ref['counts'], ensure_ascii=False))
print()
print('items by section:')
for sid, prefix in SECTIONS:
    sub = [i for i in items if i['section'] == sid]
    print('  %-7s items=%-3d M=%-3d O=%-3d relations=%-3d must_not_merge=%d' % (
        sid, len(sub), sum(1 for i in sub if i['status'] == 'M'), sum(1 for i in sub if i['status'] == 'O'),
        len([r for r in relations if r['section'] == sid]), len([p for p in must_not_merge if p.get('section') == sid])))
print()
print('cross-section relations:')
for c in cross:
    print('  %-5s %s -[%s]-> %s  (%s) evidence merged lines %s' % (
        c['id'], c['start'], c['type'], c['end'], c['scope'], c['evidence']))
print()
print('external (out-of-scope) references recorded:', len(external_refs))
