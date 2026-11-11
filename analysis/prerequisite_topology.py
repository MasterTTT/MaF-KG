# -*- coding: utf-8 -*-
"""P1-C C0: offline topology diagnostics for the prerequisite relation (no API calls).

Answers three questions that the judgement-based prerequisite review cannot answer by itself:

  1. direction  - do the predicted pairs use `target depends_on prerequisite` in the same orientation as
                  the frozen annotations? Both readings are scored and the better one is reported.
  2. order      - is each relation acyclic (self-loops, cycles)? KST requires a prerequisite partial
                  order, so a cycle is a structural violation, not a matter of opinion.
  3. closure    - for a predicted edge (t, p), are p's own reference prerequisites also covered by t?
                  KST closes knowledge states under prerequisites; an annotation set that is not closed
                  cannot describe a valid knowledge state. The frozen reference is measured with the
                  same probe as a control, because an annotation policy that records only *direct*
                  prerequisites will itself show closure gaps.

Output: evaluation/paper_closeout_2026-10-10/prerequisite_topology/topology.json
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
SRC = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_topology'
sys.stdout.reconfigure(encoding='utf-8')
MAX_EXAMPLES = 12


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def closure(edges):
    """Transitive closure as a dict node -> set of reachable prerequisite nodes."""
    graph = defaultdict(set)
    for source, target in edges:
        graph[source].add(target)
    reach = {}

    def walk(node, seen):
        if node in seen:
            return set()
        seen = seen | {node}
        out = set()
        for nxt in graph.get(node, ()):
            out.add(nxt)
            out |= walk(nxt, seen)
        return out

    for node in set(graph) | {t for _, t in edges}:
        reach[node] = walk(node, set())
    return reach, graph


def cycles(edges):
    """Self-loops and strongly connected components with more than one node."""
    graph = defaultdict(set)
    for source, target in edges:
        graph[source].add(target)
    loops = sorted({s for s, t in edges if s == t})
    index, low, stack, on_stack, result, counter = {}, {}, [], set(), [], [0]
    for start in list(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index[start] = low[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, iterator = work[-1]
            advanced = False
            for nxt in iterator:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    result.append(sorted(component))
    return loops, result


def redundant_edges(edges):
    """Edges already implied by a longer path (removable by transitive reduction)."""
    edge_set = set(edges)
    redundant = []
    for source, target in sorted(edge_set):
        shorter = {(source, target)}
        graph = defaultdict(set)
        for a, b in edge_set - shorter:
            graph[a].add(b)
        seen, stack = set(), [source]
        while stack:
            node = stack.pop()
            for nxt in graph.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if target in seen:
            redundant.append([source, target])
    return redundant


def analyse(name, edges, catalog_ids):
    reach, _ = closure(edges)
    loops, sccs = cycles(edges)
    redundant = redundant_edges(edges)
    unknown = sorted({n for edge in edges for n in edge if n not in catalog_ids})
    return {
        'name': name,
        'edges': len(edges),
        'nodes': len({n for edge in edges for n in edge}),
        'self_loops': loops,
        'cycles': sccs,
        'acyclic': not loops and not sccs,
        'edges_implied_by_longer_path': redundant,
        'edges_implied_by_longer_path_count': len(redundant),
        'endpoints_outside_catalog': unknown[:MAX_EXAMPLES],
        'endpoints_outside_catalog_count': len(unknown),
        'reach': reach,
    }


def closure_violations(name, edges, reference_edges, limit=MAX_EXAMPLES):
    """For each edge (t, p): reference prerequisites of p that t does not reach in `edges`."""
    reach, _ = closure(edges)
    reference_reach, _ = closure(reference_edges)
    direct_reference = defaultdict(set)
    for source, target in reference_edges:
        direct_reference[source].add(target)
    violations, examples = 0, []
    for target, prereq in sorted(set(edges)):
        for indirect in sorted(reference_reach.get(prereq, set())):
            if indirect == target or indirect in reach.get(target, set()):
                continue
            violations += 1
            if len(examples) < limit:
                examples.append({
                    'target': target, 'predicted_prerequisite': prereq,
                    'reference_prerequisite_of_prerequisite': indirect,
                    'reference_marks_it_direct_for_target': indirect in direct_reference.get(target, set()),
                })
    return {'name': name, 'violations': violations, 'examples': examples}


def main() -> int:
    reference = load(SRC / 'frozen_inputs01' / 'reference.json')
    predictions = load(SRC / 'prediction01' / 'semantic_predictions.json')
    catalog = load(SRC / 'reviewed_assembly01' / 'catalog.json')
    catalog_ids = {unit['id'] for unit in catalog['knowledge_units']}

    annotations = reference['annotations']
    reference_edges = [(a['target_ku'], a['prerequisite_ku']) for a in annotations]
    required_reference_edges = [e for e, a in zip(reference_edges, annotations) if a['role'] == 'required']
    verdicts = predictions['verdicts']
    required = [v for v in verdicts if v.get('label') == 'required']
    forced = load(SRC / 'diagnostics01' / 'judge_all_pairs' / 'predictions.json')['verdicts']
    forced_required = [v for v in forced if v.get('label') == 'required']

    # Direction check: both readings of (source_ku, target_ku) against the frozen annotations.
    ref_set = set(reference_edges)
    reading_a = {(v['target_ku'], v['source_ku']) for v in required}   # source is the prerequisite
    reading_b = {(v['source_ku'], v['target_ku']) for v in required}   # target is the prerequisite
    forced_a = {(v['target_ku'], v['source_ku']) for v in forced_required}
    forced_b = {(v['source_ku'], v['target_ku']) for v in forced_required}
    direction = {
        'end_to_end_candidate_pool': {
            'reading_source_ku_is_the_prerequisite': {'pairs': len(reading_a), 'matches_reference': len(reading_a & ref_set)},
            'reading_target_ku_is_the_prerequisite': {'pairs': len(reading_b), 'matches_reference': len(reading_b & ref_set)},
        },
        'forced_all_pairs_review': {
            'reading_source_ku_is_the_prerequisite': {'pairs': len(forced_a), 'matches_reference': len(forced_a & ref_set)},
            'reading_target_ku_is_the_prerequisite': {'pairs': len(forced_b), 'matches_reference': len(forced_b & ref_set)},
        },
    }
    chosen = 'source_is_prerequisite' if len(reading_a & ref_set) >= len(reading_b & ref_set) else 'target_is_prerequisite'
    predicted_edges = sorted(reading_a if chosen == 'source_is_prerequisite' else reading_b)
    forced_edges = sorted(forced_a if chosen == 'source_is_prerequisite' else forced_b)

    report = {
        'purpose': ('Offline partial-order and closure diagnostics for the prerequisite relation, separating '
                    'structural facts (cycles, unknown endpoints, redundancy) from judgement calls.'),
        'sources': {
            'reference': str(SRC / 'frozen_inputs01' / 'reference.json'),
            'predictions': str(SRC / 'prediction01' / 'semantic_predictions.json'),
            'catalog': str(SRC / 'reviewed_assembly01' / 'catalog.json'),
            'note': 'Frozen development material from the prerequisite experiment; nothing re-annotated here.',
        },
        'universe': {
            'reference_annotations': len(annotations),
            'reference_roles': {role: sum(1 for a in annotations if a['role'] == role)
                                for role in sorted({a['role'] for a in annotations})},
            'predicted_verdicts': len(verdicts),
            'predicted_labels': {label: sum(1 for v in verdicts if v.get('label') == label)
                                 for label in sorted({v.get('label') for v in verdicts})},
            'forced_review_verdicts': len(forced),
            'forced_review_labels': {label: sum(1 for v in forced if v.get('label') == label)
                                     for label in sorted({v.get('label') for v in forced})},
            'catalog_units': len(catalog_ids),
            'two_universes_note': ('The end-to-end candidate pool and the forced all-pairs review are separate '
                                   'universes and are never pooled; the forced review covers pairs the candidate '
                                   'generator never proposed.'),
        },
        'direction_check': direction,
        'direction_used': chosen,
        'reference': None,
        'predicted_required_end_to_end': None,
        'predicted_required_forced_pool': None,
        'closure': {},
        'reading_notes': [
            'A cycle or a self-loop is a structural violation of the prerequisite partial order required by KST.',
            'An edge implied by a longer path is not an error; it is the transitive-reduction candidate list.',
            'Closure is probed against the required-only reference order, which is the only acyclic reference subset.',
            'Closure gaps are reported for the predictions and for the frozen reference: if the reference records '
            'only direct prerequisites, its own gaps describe the annotation policy, not a prediction error.',
        ],
    }

    for name, edges in (('reference', reference_edges),
                        ('reference_required_only', required_reference_edges),
                        ('predicted_required_end_to_end', predicted_edges),
                        ('predicted_required_forced_pool', forced_edges)):
        info = analyse(name, edges, catalog_ids)
        info.pop('reach')
        if name.startswith('reference'):
            report[name] = info
        else:
            report[name] = info
        print('%-32s edges=%d nodes=%d acyclic=%s self_loops=%d cycles=%d redundant=%d unknown_endpoints=%d'
              % (name, info['edges'], info['nodes'], info['acyclic'], len(info['self_loops']),
                 len(info['cycles']), info['edges_implied_by_longer_path_count'],
                 info['endpoints_outside_catalog_count']))

    role_subsets = {
        'reference_required_only': required_reference_edges,
        'reference_required_plus_proof_support': [e for e, a in zip(reference_edges, annotations)
                                                  if a['role'] in ('required', 'proof_support')],
        'reference_all_roles': reference_edges,
    }
    report['reference_role_subsets'] = {}
    for name, edges in role_subsets.items():
        info = analyse(name, edges, catalog_ids)
        info.pop('reach')
        info.pop('edges_implied_by_longer_path')
        report['reference_role_subsets'][name] = info
        print('%-38s edges=%d acyclic=%s cycles=%d' % (name, info['edges'], info['acyclic'], len(info['cycles'])))

    report['closure']['predicted_required_end_to_end'] = closure_violations(
        'predicted_required_end_to_end', predicted_edges, required_reference_edges)
    report['closure']['predicted_required_forced_pool'] = closure_violations(
        'predicted_required_forced_pool', forced_edges, required_reference_edges)
    report['closure']['reference_required_only'] = closure_violations(
        'reference_required_only', required_reference_edges, required_reference_edges)
    report['closure']['reference_all_roles'] = closure_violations('reference_all_roles', reference_edges,
                                                                  required_reference_edges)
    for name, info in report['closure'].items():
        print('%-32s closure violations=%d' % (name, info['violations']))

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / 'topology.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
