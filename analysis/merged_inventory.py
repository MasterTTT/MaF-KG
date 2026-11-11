# -*- coding: utf-8 -*-
"""Machine-generated output inventory for the merged three-section experiment.

Collects, per arm and repeat: status, native node/relation counts, null slots, chat-call count,
finish reasons, token/cost ledger entries. Nothing is transcribed by hand into the results
document; that document cites this file.

Output: evaluation/external_baselines_merged_2026-10-09/inventory.json
"""
import json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return None


def native_counts(graph):
    if isinstance(graph, list):
        nulls = sum(1 for section in graph for triple in section if triple is None)
        items = sum(1 for section in graph for triple in section if triple is not None)
        return {'format': 'edc_triplet_list', 'nodes': None, 'relations': None,
                'non_null_triplets': items, 'native_null_slots': nulls}
    if not isinstance(graph, dict):
        return {'format': 'unknown'}
    if 'nodes' in graph:
        node_key, edge_key = 'nodes', 'relations'
    elif 'entities' in graph:
        node_key, edge_key = 'entities', 'relationships'
    else:
        return {'format': 'unknown', 'keys': list(graph.keys())[:8]}
    nodes, edges = graph[node_key], graph[edge_key]
    node_keys, edge_keys = set(), set()
    for node in nodes:
        node_keys |= set(node.keys())
    edge_names = {}
    for edge in edges:
        edge_keys |= set(edge.keys())
        label = edge.get('type') or edge.get('name')
        label = '<empty>' if label in (None, '') else str(label)
        edge_names[label] = edge_names.get(label, 0) + 1
    out = {'format': 'node_edge', 'node_key': node_key, 'nodes': len(nodes),
           'relations': len(edges),
           'node_keys': sorted(node_keys), 'relation_keys': sorted(edge_keys),
           'relation_type_distribution': dict(sorted(edge_names.items(), key=lambda kv: -kv[1])[:12]),
           'relations_without_named_type': sum(v for k, v in edge_names.items() if k in ('<empty>', '___'))}
    for extra in ('math_expressions', 'expressions'):
        if extra in graph:
            out['math_expressions'] = len(graph[extra])
    return out


def job_row(arm, method, directory, status, repeat):
    directory = Path(directory)
    completion = read(directory / 'completion.json')
    graph = read(directory / 'graph.json')
    row = {'arm': arm, 'method': method, 'repeat': repeat, 'directory': str(directory),
           'job_status': status, 'graph_present': graph is not None, 'completion': completion}
    if graph is not None:
        row.update(native_counts(graph))
    return row


def main() -> int:
    arms = []
    for method in ('maf', 'direct', 'itext2kg', 'edc'):
        ctl = read(NEW / 'ctl' / method / 'manifest.json')
        for job in ctl['jobs']:
            arms.append(job_row(method, method, job['directory'], job['status'], job['repeat']))
    guarded = read(NEW / 'guarded' / 'manifest.json')
    for job in guarded['jobs']:
        arms.append(job_row('itext2kg-progress-bound-3', 'itext2kg', job['directory'], job['status'], job['repeat']))
    # fixed variants required by the 2026-10-09 review
    for variant, arm, method in (('edc_v3', 'edc-parse-compatible', 'edc'),
                                 ('itext_v3', 'itext2kg-symbol-compatible', 'itext2kg')):
        manifest = read(NEW / variant / 'manifest.json')
        if manifest is None:
            continue
        for job in manifest['jobs']:
            arms.append(job_row(arm, method, job['directory'], job['status'], job['repeat']))

    ledgers = {}
    for path in sorted((NEW / 'budgets').glob('*.json')):
        ledger = read(path)
        if ledger is None or 'requests' not in ledger:
            continue  # skips derived files such as ledger_split.json
        ledgers[path.stem] = {'usd_limit': ledger.get('usd_limit'), 'token_limit': ledger.get('token_limit'),
                              'accounting_usd': ledger.get('accounting_usd'),
                              'accounting_tokens': ledger.get('accounting_tokens'),
                              'requests': len(ledger.get('requests', [])),
                              'limit_raise': ledger.get('limit_raise')}
    payload = {
        'arms': arms,
        'ledgers': ledgers,
        'ledger_total_usd': round(sum(v['accounting_usd'] or 0 for v in ledgers.values()), 6),
        'ledger_total_tokens': sum(v['accounting_tokens'] or 0 for v in ledgers.values()),
        'ledger_note': ('iText2KG ledger is cumulative across its unguarded and progress-bounded runs and '
                        'includes the sunk spend of the aborted v1 attempt; consumption is never zeroed.'),
        'reference_digest': str(NEW / 'reference' / 'reference_digest.json'),
    }
    (NEW / 'inventory.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in arms:
        print('%-27s r%s %-10s %s' % (row['arm'], row['repeat'], row['job_status'],
                                      {k: v for k, v in row.items()
                                       if k in ('nodes', 'relations', 'non_null_triplets',
                                                'native_null_slots', 'math_expressions')}))
    print('ledgers:', {k: (v['accounting_tokens'], None if v['accounting_usd'] is None else round(v['accounting_usd'], 4))
                       for k, v in ledgers.items()})
    print('total tokens %d | total usd %.4f' % (payload['ledger_total_tokens'], payload['ledger_total_usd']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
