"""Lock explicit reviewer revisions and disclose coordinator consistency changes."""
import argparse
import copy
from pathlib import Path
from run_memory_ablation import digest, write_json
from run_prospective_multimodel import read


def lock(directory):
    target = directory / 'review.json'
    if target.exists():
        raise FileExistsError('Do not overwrite a locked review')
    selections = ['audit_parts_ode_revision.json', 'audit_taylor_a_revision.json', 'audit_taylor_b.json']
    graphs, bindings = [], []
    for name in selections:
        path = directory / name
        review = read(path)
        graphs.extend(copy.deepcopy(review['graphs']))
        bindings.append({'path': str(path), 'sha256': digest(path), 'prior_exposure': review['prior_exposure']})
    by_pid = {g['packet_id']: g for g in graphs}
    if len(by_pid) != 5 or len(graphs) != 5:
        raise ValueError('Duplicate or missing packets')
    change_path = directory / 'coordinator_adjudications.json'
    decisions = read(change_path)
    logs = []
    for item in decisions['changes']:
        row_key = 'node_reviews' if item['kind'] == 'node' else 'relation_reviews'
        rows = [r for r in by_pid[item['packet_id']][row_key] if r['review_id'] == item['review_id']]
        if len(rows) != 1:
            raise ValueError('Ambiguous coordinator review ID')
        row = rows[0]
        logs.append({'decision': item, 'old_value': row[item['field']], 'old_reason': row['reason'],
                     'old_source_lines': row['source_lines']})
        row[item['field']] = item['value']
        row['reason'] += ' 统筹一致性复核：' + item['reason']
        row['source_lines'] = sorted(set(row['source_lines'] + item['source_lines']))
    bindings.append({'path': str(change_path), 'sha256': digest(change_path),
                     'prior_exposure': decisions['prior_exposure']})
    write_json(target, {'reviewer': 'gpt-6-luna/max masked assistants, source correction and exposed Codex coordinator consistency review',
                       'prior_exposure': 'Initial masked audits retained. Source/ontology feedback disclosed in revisions; coordinator saw model identities, reference and prior audits. Not independent expert Gold or strict blind review.',
                       'review_sources': bindings, 'graphs': sorted(graphs, key=lambda g:g['packet_id'])})
    write_json(directory / 'review_change_log.json', {'review_sha256': digest(target),
               'selections': bindings, 'coordinator_changes': logs,
               'initial_audits': [{'path': str(directory / name), 'sha256': digest(directory / name)}
                                  for name in ('audit_parts_ode.json', 'audit_taylor_a.json', 'audit_taylor_b.json')]})
    print('Locked five packets with', len(logs), 'disclosed consistency decisions')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', type=Path, required=True)
    lock(parser.parse_args().evaluation.resolve())
