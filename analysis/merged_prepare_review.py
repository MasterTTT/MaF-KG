# -*- coding: utf-8 -*-
"""Build label-masked review packets for the merged three-section experiment.

Adapted from scripts/prepare_external_baseline_review.py:
  * the frozen merged reference is used (all 42 items, 45 intra relations, 2 cross-section relations);
  * each packet also carries the 15 must_not_merge pairs (for false-merge review) and the
    boundary_groups, and the review template gains a cross_relation_coverage block;
  * an empty native graph (no non-null items) is recorded as a no-output packet, never as
    zero required-content coverage;
  * job-to-label assignment is fixed by a seeded shuffle and never re-mapped.
"""
import argparse, copy, json, random, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
REF = NEW / 'reference' / 'reference_merged_v1.json'
SEED = 20261009


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k not in {'embedding', 'embeddings', 'diagnostics'}}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def section_offsets():
    """merged_line = source_line + offset, per section (used to normalise relation evidence)."""
    sections = json.loads((NEW / 'input' / 'sections.json').read_text(encoding='utf-8'))
    return {s['id']: s['merged_start_line'] - s['start_line'] for s in sections}


def normalise_evidence(reference, offsets):
    """Put every reference item's evidence into merged-document coordinates.

    The frozen reference mixes coordinate systems (verified 2026-10-09 against the merged text):
      * nodes carry chapter-coordinate `evidence` plus a merged-coordinate `merged_evidence`;
      * intra relations carry only chapter-coordinate `evidence`;
      * cross-section relations carry only merged-coordinate `evidence`.
    Reviewers are told to cite merged coordinates, so the packet exposes one unambiguous field and
    keeps the original under `evidence_source_chapter`.
    """
    out = {}
    for group, items in reference.items():
        copied = []
        for item in items:
            item = dict(item)
            raw = item.pop('evidence', None)
            merged = None
            if group == 'nodes':
                merged = item.pop('merged_evidence', None)
                item['evidence_source_chapter'] = raw
            elif group == 'relations':
                section = item.get('section')
                merged = ([line + offsets[section] for line in raw]
                          if raw is not None and section in offsets else None)
                item['evidence_source_chapter'] = raw
            elif group == 'cross_section_relations':
                merged = raw
                item['evidence_source_chapter'] = None
            elif raw is not None:
                item['evidence_source_chapter'] = raw
            if merged is not None or group in {'nodes', 'relations', 'cross_section_relations'}:
                item['evidence_merged'] = merged
                item['evidence_coordinates'] = 'merged document L1-L1094' if merged else 'unresolved'
            copied.append(compact(item))
        out[group] = copied
    return out


def native_items(graph):
    if isinstance(graph, list):
        items, dropped = [], []
        for section, triples in enumerate(graph, 1):
            for index, triple in enumerate(triples, 1):
                if triple is None:
                    dropped.append({'section': section, 'index': index, 'value': None})
                else:
                    if not isinstance(triple, list) or len(triple) != 3:
                        raise ValueError('Invalid native EDC triplet')
                    items.append({'item_id': 'T%03d_%03d' % (section, index), 'kind': 'assertion',
                                  'native': compact(triple)})
        return items, dropped
    if not isinstance(graph, dict):
        raise ValueError('Unknown graph format')
    if 'nodes' in graph:
        node_key, edge_key = 'nodes', 'relations'
    elif 'entities' in graph:
        node_key, edge_key = 'entities', 'relationships'
    else:
        raise ValueError('Unknown graph keys: %s' % list(graph.keys())[:8])
    items = []
    expressions = graph.get('math_expressions') or graph.get('expressions') or []
    by_owner, orphans = {}, []
    for expr in expressions:
        owner = expr.get('owner_id') or expr.get('owner')
        if owner:
            by_owner.setdefault(owner, []).append(compact(expr))
        else:
            orphans.append(compact(expr))
    for i, node in enumerate(graph[node_key], 1):
        native = compact(node)
        owned = by_owner.get(node.get('id'))
        if owned:
            # native formula objects that belong to this node: shown so a statement carried by the
            # formula is not judged absent. Method-agnostic: only applied when the key exists.
            native['math_expressions'] = owned
        items.append({'item_id': 'N%03d' % i, 'kind': 'entity', 'native': native})
    items += [{'item_id': 'R%03d' % i, 'kind': 'assertion', 'native': compact(n)}
              for i, n in enumerate(graph[edge_key], 1)]
    items += [{'item_id': 'X%03d' % i, 'kind': 'expression', 'native': expr}
              for i, expr in enumerate(orphans, 1)]
    return items, []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(NEW / 'review_merged'))
    ap.add_argument('--round', choices=('v2', 'v3', 'v4'), default='v2',
                    help='v2 = first round; v3 = the fixed EDC/iText variants; v4 = the name-preserving iText arm')
    args = ap.parse_args()
    out = Path(args.out)
    ref = json.loads(REF.read_text(encoding='utf-8'))
    offsets = section_offsets()

    jobs = []
    if args.round == 'v4':
        # The name-preserving iText arm: relation names, endpoints AND delivered item names are verbatim,
        # so the pinned lower()/separator normalisation is no longer visible in the graph. Separate round,
        # separate labels, and it replaces the v3 iText arm if it passes the same review.
        for name, arm, method in (('itext_v4', 'itext-symbol-native', 'itext2kg'),):
            manifest = json.loads((NEW / name / 'manifest.json').read_text(encoding='utf-8'))
            for job in manifest['jobs']:
                jobs.append({'method': method, 'arm': arm, 'section': job['section'], 'repeat': job['repeat'],
                             'directory': job['directory'], 'status': job['status'],
                             'graph_sha256': job.get('graph_sha256')})
        random.Random(SEED + 2).shuffle(jobs)
        prefix = 'V'
    elif args.round == 'v3':
        # Fixed variants only: EDC with the Define separator made Unicode-compatible, and iText2KG
        # with the pinned ASCII name sanitiser bypassed. Separate round, separate labels, and never
        # pooled with the v2 arms they replace.
        for name, arm, method in (('itext_v3', 'itext2kg-symbol-compatible', 'itext2kg'),
                                  ('edc_v3', 'edc-parse-compatible', 'edc')):
            manifest = json.loads((NEW / name / 'manifest.json').read_text(encoding='utf-8'))
            for job in manifest['jobs']:
                jobs.append({'method': method, 'arm': arm, 'section': job['section'], 'repeat': job['repeat'],
                             'directory': job['directory'], 'status': job['status'],
                             'graph_sha256': job.get('graph_sha256')})
        random.Random(SEED + 1).shuffle(jobs)
        prefix = 'Q'
    else:
        for method in ('maf', 'direct', 'itext2kg', 'edc'):
            ctl = json.loads((NEW / 'ctl' / method / 'manifest.json').read_text(encoding='utf-8'))
            for job in ctl['jobs']:
                jobs.append({'method': method, 'arm': method, 'section': job['section'], 'repeat': job['repeat'],
                             'directory': job['directory'], 'status': job['status'],
                             'graph_sha256': job.get('graph_sha256')})
        # separately labelled progress-bounded iText variant: never pooled with the unguarded arm
        guarded = json.loads((NEW / 'guarded' / 'manifest.json').read_text(encoding='utf-8'))
        for job in guarded['jobs']:
            jobs.append({'method': 'itext2kg', 'arm': 'itext2kg-progress-bound-3', 'section': job['section'],
                         'repeat': job['repeat'], 'directory': job['directory'], 'status': job['status'],
                         'graph_sha256': job.get('graph_sha256')})
        random.Random(SEED).shuffle(jobs)
        prefix = 'P'

    candidates = []
    for index, job in enumerate(jobs, 1):
        label = '%s%02d' % (prefix, index)
        if job['status'] != 'completed':
            candidates.append({'label': label, **job, 'packet': 'not_completed'})
            continue
        graph_path = Path(job['directory']) / 'graph.json'
        if not graph_path.exists():
            candidates.append({'label': label, **job, 'packet': 'no_graph'})
            continue
        graph = json.loads(graph_path.read_text(encoding='utf-8'))
        items, dropped = native_items(graph)
        if not items:
            candidates.append({'label': label, **job, 'packet': 'empty_output', 'native_nulls': len(dropped)})
            continue
        candidates.append({'label': label, **job, 'packet': 'reviewable', 'items': items, 'dropped': dropped})

    if out.exists():
        raise SystemExit('refusing to overwrite existing packets: %s' % out)
    out.mkdir(parents=True)

    mapping, prepared = [], 0
    for c in candidates:
        label = c['label']
        mapping.append({k: c[k] for k in ('label', 'arm', 'method', 'section', 'repeat', 'directory', 'status')})
        if c['packet'] != 'reviewable':
            (out / (label + '.json')).write_text(json.dumps(
                {'label': label, 'status': c['packet'], 'not_a_zero_score': True,
                 'native_null_slots': c.get('native_nulls')}, ensure_ascii=False, indent=2), encoding='utf-8')
            continue
        items = c['items']
        reference = normalise_evidence({
            'nodes': ref['nodes'],
            'relations': ref['relations'],
            'cross_section_relations': ref['cross_section_relations'],
            'must_not_merge': ref['must_not_merge'],
            'boundary_groups': ref['boundary_groups'],
        }, offsets)
        reference['external_references'] = ref['external_references']
        packet = {
            'label': label, 'section': 'merged', 'items': items, 'native_nulls': c['dropped'],
            'reference': reference,
            'policy': ('Judge only actual native semantic output. Source verifies support but cannot fill absent conditions. '
                       'Cite evidence with `evidence_merged`, which is in merged-document coordinates (L1-L1094; '
                       'ch2_s5 L1-574, ch3_s1 L575-936, ch3_s2 L937-1094). `evidence_source_chapter`, when present, is the '
                       'original chapter-file coordinate and must NOT be cited as a merged line number. '
                       'External references are recorded but never scored.'),
            'blinding_limit': 'Method labels masked; representation may reveal method. Not independent human annotation.',
            'prediction_sha256': c['graph_sha256'],
        }
        (out / (label + '.json')).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / (label + '_review.json')).write_text(json.dumps({
            'label': label, 'reviewer_provenance': 'pending',
            'node_coverage': [{'reference_id': n['id'], 'status': 'pending', 'item_ids': [],
                               'conditions_complete': None, 'reason': ''} for n in ref['nodes']],
            'relation_coverage': [{'reference_id': r['id'], 'status': 'pending', 'item_ids': [], 'reason': ''}
                                  for r in ref['relations']],
            'cross_relation_coverage': [{'reference_id': x['id'], 'status': 'pending', 'item_ids': [], 'reason': ''}
                                        for x in ref['cross_section_relations']],
            'prediction_support': [{'item_id': i['item_id'], 'status': 'pending', 'scope_admitted': None,
                                    'reason': ''} for i in items],
            'duplicate_groups': [], 'false_merges': []}, ensure_ascii=False, indent=2), encoding='utf-8')
        prepared += 1

    (out / 'mapping_private.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'review_policy.json').write_text(json.dumps({
        'coverage_choices': ['full', 'partial', 'name_only', 'missing', 'not_applicable'],
        'support_choices': ['yes', 'partial', 'no', 'undecided'],
        'primary': 'Full required-content recall on the merged document; partial and name-only identification reported separately',
        'conditions': "conditions_complete true requires the output itself to state the material mathematical restrictions",
        'duplicate_groups': ('TIGHTENED (2026-10-09 user review): record only genuine repetition -- the same content '
                             'expressed redundantly in two or more output items, or the same output item repeated. '
                             'A reference item decomposed into output items that carry DIFFERENT parts or conditions '
                             'is a legitimate split, NOT a duplicate: do not record it here, describe it in the '
                             'node_coverage reason instead. Each entry: reference_id, item_ids, reason.'),
        'duplicate_kinds': {'repetition': 'same content expressed twice or more (counted in the metric)',
                            'split': 'one reference item carried by several output items with different content '
                                     '(informational; adjudicated by the main agent, never counted as duplication)'},
        'false_merges': 'a must_not_merge pair collapsed into one output item',
        'cross_relations': 'relations whose two endpoints belong to different source sections/chapters; reported separately from intra relations',
        'no_f1': 'Content recall, relation coverage, duplicate/false-merge counts and cross-section relation coverage are separate universes',
        'native_entities': 'Formal symbols needed to express a general statement are not example-local objects. Do not impose our ontology on foreign graphs.',
        'status': 'packets_prepared_not_scored'}, ensure_ascii=False, indent=2), encoding='utf-8')

    print('jobs:', len(candidates), '| reviewable packets:', prepared)
    for c in candidates:
        print('  %-5s %-27s r%s  %s' % (c['label'], c['arm'], c['repeat'], c['packet']))
    print('wrote ->', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
