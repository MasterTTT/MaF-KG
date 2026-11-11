# -*- coding: utf-8 -*-
"""Frozen-reference digest for the merged three-section experiment.

Recomputes the reference-side facts the results document must cite and re-verifies the frozen
hash, so the document never restates a number that drifted. Read-only.

Output: evaluation/external_baselines_merged_2026-10-09/reference/reference_digest.json
"""
import hashlib, json, sys
from collections import Counter, defaultdict
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
FROZEN_SHA = 'f6ad4bc539e721b0500b41c094a7667a3e629aac1af064f26233db07667280a8'
INPUT_SHA = 'c300e342d7b8d3f2a8994414fe73249965ef6c0c5a5a7559442e18babca3912f'
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    raw = (NEW / 'reference' / 'reference_merged_v1.json').read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    ref = json.loads(raw.decode('utf-8'))
    input_raw = (NEW / 'input' / 'merged_ch2s5_ch3s1_ch3s2.md').read_bytes()
    input_sha = hashlib.sha256(input_raw).hexdigest()
    input_text_raw = input_raw.decode('utf-8')
    input_text = input_text_raw.replace('\r\n', '\n')

    nodes = ref['nodes']
    section_of = {}
    for node in nodes:
        section_of[node['id']] = node.get('section', node.get('source_section', 'unknown'))

    def ends(rel):
        for key in ('from', 'source', 'head', 'tail', 'to', 'target'):
            pass
        return rel

    intra = ref['relations']
    cross = ref['cross_section_relations']
    digest = {
        'reference_sha256': sha,
        'reference_sha256_matches_frozen': sha == FROZEN_SHA,
        'input_sha256': input_sha,
        'input_sha256_matches_frozen': input_sha == INPUT_SHA,
        'input_bytes': len(input_raw),
        'input_lines': len(input_text.splitlines()),
        'input_chars': len(input_text),
        'input_line_endings': 'CRLF' if '\r\n' in input_text_raw else 'LF',
        'nodes_total': len(nodes),
        'nodes_required_M': sum(1 for n in nodes if n['status'] == 'M'),
        'nodes_optional_O': sum(1 for n in nodes if n['status'] == 'O'),
        'nodes_by_tier': dict(Counter(n['tier'] for n in nodes if n['status'] == 'M')),
        'nodes_by_type': dict(Counter(n.get('type', 'unknown') for n in nodes)),
        'claims_total': sum(1 for n in nodes if n.get('type') == 'Claim'),
        'claims_required': sum(1 for n in nodes if n.get('type') == 'Claim' and n['status'] == 'M'),
        'nodes_by_section': dict(Counter(section_of.values())),
        'intra_relations': len(intra),
        'cross_section_relations': len(cross),
        'cross_section_relation_ids': [r['id'] for r in cross],
        'must_not_merge_pairs': len(ref['must_not_merge']),
        'boundary_groups': len(ref['boundary_groups']),
        'external_references': len(ref.get('external_references', [])),
        'reference_protocol_frozen': True,
    }
    target = NEW / 'reference' / 'reference_digest.json'
    target.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding='utf-8')
    for key, value in digest.items():
        print('%-38s %s' % (key, value))
    print('wrote ->', target)
    return 0 if (digest['reference_sha256_matches_frozen'] and digest['input_sha256_matches_frozen']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
