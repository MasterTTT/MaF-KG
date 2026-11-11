# -*- coding: utf-8 -*-
"""Build the S1/S2 review packets for the input-scope experiment (P1-A), arm-blind.

Reuses the merged experiment's packet helpers so the two rounds are judged with the same schema and
the same policy wording, differing only in the reference universe: each prefix is scored against the
subset that was frozen from the reference by merged line range *before* any prefix prediction existed.

S3 needs no new packet: the existing merged reviews (P05/P06 for MaF, P08/P10 for Direct) already
cover all 42 reference items, so the common early subset can be read straight out of them.

Usage: python analysis/input_scope_prepare_review.py
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
REVIEW = OUT / 'review_scope'
SEED = 20261010
sys.path.insert(0, str(ROOT / 'analysis'))
sys.stdout.reconfigure(encoding='utf-8')

from merged_prepare_review import compact, native_items, normalise_evidence, section_offsets  # noqa: E402


def scope_policy(last_line):
    return ('Judge only actual native semantic output. Source verifies support but cannot fill absent conditions. '
            'Cite evidence with `evidence_merged`, which is in merged-document coordinates (L1-L%d; '
            'ch2_s5 L1-574, ch3_s1 L575-936). `evidence_source_chapter`, when present, is the original '
            'chapter-file coordinate and must NOT be cited as a merged line number. '
            'The reference for this run contains only items whose merged evidence lies inside L1-%d; items '
            'outside that range are not part of this comparison and must not be reported as missing. '
            'External references are recorded but never scored.' % (last_line, last_line))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--append', action='store_true',
                    help='add packets for jobs that completed after the first build (labels continue the sequence)')
    args = ap.parse_args()
    REVIEW.mkdir(parents=True, exist_ok=True) if args.append else None
    if REVIEW.exists() and not args.append:
        raise SystemExit('refusing to overwrite existing packets: %s' % REVIEW)
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    offsets = section_offsets()
    existing = json.loads((REVIEW / 'mapping_private.json').read_text(encoding='utf-8')) if args.append else []
    done = {(row['section'], row['method'], row['repeat']) for row in existing}
    jobs = []
    for job in manifest['jobs']:
        if job['status'] != 'completed':
            continue
        section = next(s for s in manifest['sections'] if s['id'] == job['section'])
        if (section['scope'], job['method'], job['repeat']) in done:
            continue
        jobs.append({'method': job['method'], 'arm': job['method'], 'section': section['scope'],
                     'section_id': job['section'], 'repeat': job['repeat'], 'directory': job['directory'],
                     'status': job['status'], 'graph_sha256': None,
                     'reference': section['reference']})
    random.Random(SEED).shuffle(jobs)

    if not args.append:
        REVIEW.mkdir(parents=True)
    mapping, prepared = list(existing), 0
    for index, job in enumerate(jobs, len(existing) + 1):
        label = 'R%02d' % index
        job['label'] = label
        mapping.append({k: job[k] for k in ('label', 'arm', 'method', 'section', 'section_id', 'repeat',
                                            'directory', 'status')})
        graph_path = Path(job['directory']) / 'graph.json'
        if not graph_path.exists():
            (REVIEW / (label + '.json')).write_text(json.dumps(
                {'label': label, 'status': 'no_graph', 'not_a_zero_score': True}, ensure_ascii=False, indent=2),
                encoding='utf-8')
            print('%-4s %-6s %-4s r%s  no_graph' % (label, job['method'], job['section'], job['repeat']))
            continue
        graph = json.loads(graph_path.read_text(encoding='utf-8'))
        items, dropped = native_items(graph)
        job['graph_sha256'] = json.loads((Path(job['directory']) / 'completion.json').read_text(encoding='utf-8'))['graph_sha256']
        if not items:
            (REVIEW / (label + '.json')).write_text(json.dumps(
                {'label': label, 'status': 'empty_output', 'not_a_zero_score': True,
                 'native_null_slots': len(dropped)}, ensure_ascii=False, indent=2), encoding='utf-8')
            print('%-4s %-6s %-4s r%s  empty_output' % (label, job['method'], job['section'], job['repeat']))
            continue
        subset = json.loads(Path(job['reference']).read_text(encoding='utf-8'))
        job['line_range'] = subset['line_range']   # carried by the frozen subset, not by the manifest
        reference = normalise_evidence({
            'nodes': subset['nodes'],
            'relations': subset['relations'],
            'cross_section_relations': subset['cross_section_relations'],
            'must_not_merge': subset['must_not_merge'],
            'boundary_groups': subset['boundary_groups'],
        }, offsets)
        reference['external_references'] = subset.get('external_references', [])
        packet = {
            'label': label, 'section': job['section'], 'items': items, 'native_nulls': dropped,
            'reference': reference,
            'policy': scope_policy(job['line_range'][1]),
            'blinding_limit': 'Method labels masked; representation may reveal method. Not independent human annotation.',
            'prediction_sha256': job['graph_sha256'],
            'scope_note': ('Prefix run: the document ends at merged line %d, so the reference universe is the '
                           'pre-frozen subset for that range.' % job['line_range'][1]),
        }
        (REVIEW / (label + '.json')).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
        (REVIEW / (label + '_review.json')).write_text(json.dumps({
            'label': label, 'reviewer_provenance': 'pending',
            'node_coverage': [{'reference_id': n['id'], 'status': 'pending', 'item_ids': [],
                               'conditions_complete': None, 'reason': ''} for n in subset['nodes']],
            'relation_coverage': [{'reference_id': r['id'], 'status': 'pending', 'item_ids': [], 'reason': ''}
                                  for r in subset['relations']],
            'cross_relation_coverage': [{'reference_id': x['id'], 'status': 'pending', 'item_ids': [], 'reason': ''}
                                        for x in subset['cross_section_relations']],
            'prediction_support': [{'item_id': i['item_id'], 'status': 'pending', 'scope_admitted': None,
                                    'reason': ''} for i in items],
            'duplicate_groups': [], 'false_merges': []}, ensure_ascii=False, indent=2), encoding='utf-8')
        prepared += 1
        print('%-4s %-6s %-4s r%s  reviewable items=%d ref_nodes=%d ref_relations=%d ref_cross=%d'
              % (label, job['method'], job['section'], job['repeat'], len(items), len(subset['nodes']),
                 len(subset['relations']), len(subset['cross_section_relations'])))

    (REVIEW / 'mapping_private.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
    policy = json.loads((MERGED / 'review_v3' / 'review_policy.json').read_text(encoding='utf-8'))
    policy['status'] = 'packets_prepared_not_scored'
    policy['scope'] = ('Input-scope round: each packet is judged against its own pre-frozen reference subset, and '
                       'the paired across-scope comparison uses the ch2_s5 subset present in every scope.')
    (REVIEW / 'review_policy.json').write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding='utf-8')
    print('prepared %d packets -> %s' % (prepared, REVIEW))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
