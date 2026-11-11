"""Bind only newly rescued graphs to the existing locked review machinery."""
import argparse
from pathlib import Path
import run_prospective_multimodel as runner
from run_memory_ablation import digest, write_json
from prepare_blind_prediction_review import prepare


def build(rescue, output):
    state = runner.read(rescue / 'manifest.json')
    if state.get('status') != 'completed':
        raise ValueError('Rescue must be settled before evaluation')
    old = runner.read(Path(state['previous_manifest']))
    if digest(Path(state['previous_manifest'])) != state['previous_manifest_sha256']:
        raise ValueError('Original manifest changed')
    successes = state.get('prior_successes', []) + state['jobs']
    jobs = []
    for job in successes:
        last = job['attempts'][-1]
        if last['status'] != 'completed':
            raise ValueError('Cannot evaluate incomplete rescue')
        graph_path = Path(last['directory']) / 'graph.json'
        if digest(graph_path) != last['graph_sha256']:
            raise ValueError('Rescue graph changed')
        jobs.append({k: job[k] for k in ('model', 'section', 'name')})
        jobs[-1].update(directory=last['directory'], graph_sha256=last['graph_sha256'],
                        nodes=last['nodes'], relations=last['relations'], status='completed',
                        exit_code=0, attempt_number=last['number'], rescue=True)
    if len(jobs) != 5 or len({(j['model'], j['section'], j['name']) for j in jobs}) != 5:
        raise ValueError('Expected five unique rescued cells')
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {str(Path(a['path']).resolve()): a for a in state['artifacts'] + state['frozen_source_artifacts']}
    manifest = {'experiment': 'rescue-five-graph-quality-evaluation',
                'snapshot': str(rescue / 'snapshot'), 'sections': state['sections'],
                'reference': old['reference'], 'jobs': jobs, 'artifacts': list(artifacts.values()),
                'rescue_manifest': str(rescue / 'manifest.json'),
                'rescue_manifest_sha256': digest(rescue / 'manifest.json'),
                'limits': 'Adaptive rescue settings, not the fixed-configuration benchmark.'}
    write_json(output / 'manifest.json', manifest)
    protocol = runner.ROOT / 'evaluation/prospective_2026-10-04/prediction_validity_protocol_v1.md'
    return prepare(output / 'manifest.json', output / 'packets', protocol=protocol)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rescue', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.rescue.resolve(), args.output.resolve()))
