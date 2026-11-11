"""Separate, bounded rescue experiment; never overwrite registered results."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import traceback
import uuid
from pathlib import Path

import prospective_native_client as native
import run_direct_extraction_baseline as baseline
import run_prospective_multimodel as runner
from run_memory_ablation import digest, load_module, write_json

MODELS = ('glm-5.3-flash', 'mimo-v2.6-flash')
ORIGINAL_PAYLOAD = native.payload
JSON_MODE = False


def rescue_payload(model, system, user, limit):
    route, body = ORIGINAL_PAYLOAD(model, system, user, limit)
    if model not in MODELS:
        raise ValueError('Rescue is limited to GLM and MiMo')
    if model == 'glm-5.3-flash':
        body['reasoning_effort'] = 'none'
        if JSON_MODE:
            body['response_format'] = {'type': 'json_object'}
    else:
        body['thinking'] = {'type': 'disabled'}
    return route, body


def credential():
    if not os.environ.get('OPENCODE_API_KEY'):
        import subprocess
        value = subprocess.check_output([
            'powershell', '-NoProfile', '-Command',
            "[Environment]::GetEnvironmentVariable('OPENCODE_API_KEY','User')"
        ], text=True).strip()
        if value:
            os.environ['OPENCODE_API_KEY'] = value
    if not os.environ.get('OPENCODE_API_KEY'):
        raise RuntimeError('OPENCODE_API_KEY missing')


def prepare(previous, output, prior_rescue=None):
    old = runner.read(previous / 'manifest.json')
    runner.setup_baseline()
    baseline.audit_prepared(old)
    selected = [j for j in old['jobs'] if j['model'] in MODELS and j['status'] == 'failed']
    if len(selected) != 5:
        raise ValueError('Expected the five registered missing cells')
    prior = runner.read(prior_rescue / 'manifest.json') if prior_rescue else None
    prior_successes = []
    if prior:
        check(prior)
        prior_successes = prior.get('prior_successes', []) + [
            j for j in prior['jobs'] if j['status'] == 'completed']
        for row in prior_successes:
            if row['status'] == 'completed':
                success = row['attempts'][-1]
                if digest(Path(success['directory']) / 'graph.json') != success['graph_sha256']:
                    raise ValueError('Prior rescue graph changed')
        completed = {(j['model'], j['section'], j['name']) for j in prior_successes}
        selected = [j for j in selected if (j['model'], j['section'], j['name']) not in completed]
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / 'snapshot'
    shutil.copytree(previous / 'snapshot' / 'agents', snapshot / 'agents')
    shutil.copytree(previous / 'snapshot' / 'prompts', snapshot / 'prompts')
    script = snapshot / 'run_multimodel_rescue.py'
    shutil.copy2(__file__, script)
    manifest = {
        'experiment': 'adaptive-success-rescue-not-fixed-configuration-comparison',
        'previous_manifest': str(previous / 'manifest.json'),
        'previous_manifest_sha256': digest(previous / 'manifest.json'),
        'session': str(uuid.uuid4()), 'sections': old['sections'],
        'configuration': {'glm_reasoning_effort': 'none', 'glm_json_mode': JSON_MODE,
                          'mimo_thinking': 'disabled', 'output_limits': [24000, 48000],
                          'request_timeout': 600, 'schema_regenerations_per_request': 1,
                          'additional_usd_limit_per_model': 1.0,
                          'additional_token_limit_per_model': 2000000,
                          'max_full_attempts_per_missing_cell': 2},
        'scope': 'Only failed cells; retain all attempts. Adaptive settings are not original benchmark scores.',
        'authorization': 'Human requested any reasonable measures to maximize success on 2026-10-05.',
        'jobs': [{'model': j['model'], 'section': j['section'], 'name': j['name'],
                  'original_failure': copy.deepcopy(j), 'attempts': [], 'status': 'pending'}
                 for j in selected],
        'artifacts': [baseline.bind(p) for p in snapshot.rglob('*') if p.is_file()],
        'frozen_source_artifacts': old['artifacts'],
    }
    if prior:
        manifest['previous_rescue'] = baseline.bind(prior_rescue / 'manifest.json')
        manifest['prior_successes'] = prior_successes
        for path in (prior_rescue / 'budgets').glob('*.json'):
            target = output / 'budgets' / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    write_json(output / 'manifest.json', manifest)
    return manifest


def check(manifest):
    for artifact in manifest['artifacts'] + manifest['frozen_source_artifacts']:
        if digest(Path(artifact['path'])) != artifact['sha256']:
            raise ValueError('Frozen input changed: ' + artifact['path'])


def run_attempt(output, manifest, job, attempt, limit):
    directory = output / job['model'] / job['section'] / job['name'] / f'attempt{attempt}'
    directory.mkdir(parents=True, exist_ok=False)
    for name in ('requests', 'responses', 'raw'):
        (directory / name).mkdir()
    for name in ('agents', 'prompts'):
        shutil.copytree(output / 'snapshot' / name, directory / 'rt' / name)
    module = load_module(directory / 'rt/agents/Extractor.py')
    native.payload = rescue_payload
    native.install(module, job['model'], directory,
                   output / 'budgets' / f"{job['model']}.json", manifest['session'],
                   usd_limit=1.0, token_limit=2000000)
    native_complete = module.ProviderChatClient.complete_json

    def validated(self, system, user, label='request'):
        result = None
        try:
            result = native_complete(self, system, user, label)
            module.ChunkExtractionResponse.model_validate(result)
            return result
        except (module.ValidationError, module.JsonParseFailure) as error:
            write_json(directory / 'schema_failures' / f'{label}.json',
                       {'response': result, 'error_type': type(error).__name__, 'error': str(error)})
            instruction = (
                '\nYour previous response failed the required JSON schema. Generate the entire '
                'extraction again from the original source. Do not invent endpoints or facts. '
                'Every relation needs source_ref and target_ref using node ref_id or supplied '
                'memory identifiers as required by the original schema. Return valid JSON only; '
                'escape every LaTeX backslash as a JSON double backslash. Validation errors:\n' + str(error)
            )
            regenerated = native_complete(self, system + instruction, user, label + '_schema_regeneration')
            module.ChunkExtractionResponse.model_validate(regenerated)
            return regenerated

    module.ProviderChatClient.complete_json = validated
    section = next(s for s in manifest['sections'] if s['id'] == job['section'])
    baseline.install_hooks(module, section, job, directory)
    config = {**runner.CONFIG, 'model': job['model'], 'input_path': section['input'],
              'output_path': directory / 'graph.json', 'resume': False, 'verbose': True,
              'thinking': 'disabled', 'reasoning_effort': '', 'max_tokens': limit,
              'memory_node_bytes': 12000, 'memory_relation_bytes': 6000}
    module.Extractor(**config).run()
    graph = runner.read(directory / 'graph.json')
    # A successful run must have processed every frozen chunk, not just saved a partial graph.
    templates = runner.read(Path(section['plan_dir']) / 'templates.json')[job['name']]
    observed = graph['diagnostics']['chunk_summaries']
    if [t['chunk']['chunk_id'] for t in templates] != [c['chunk_id'] for c in observed]:
        raise ValueError('Incomplete chunk coverage')
    for node in graph['nodes']:
        module.KGNode.model_validate(node)
    for relation in graph['relations']:
        module.KGRelation.model_validate(relation)
    node_ids = {n['id'] for n in graph['nodes']}
    if len(node_ids) != len(graph['nodes']) or any(
            r['source_id'] not in node_ids or r['target_id'] not in node_ids for r in graph['relations']):
        raise ValueError('Duplicate node identifiers or dangling relation endpoints')
    return {'directory': str(directory), 'graph_sha256': digest(directory / 'graph.json'),
            'nodes': len(graph['nodes']), 'relations': len(graph['relations']),
            'chunks': len(observed)}


def execute(output, manifest):
    credential()
    check(manifest)
    for job in manifest['jobs']:
        if job['status'] == 'completed':
            continue
        if job['attempts']:
            raise ValueError('Interrupted rescue requires inspection before continuation')
        for attempt, limit in enumerate((24000, 48000), 1):
            row = {'number': attempt, 'max_tokens': limit, 'status': 'running'}
            job['attempts'].append(row)
            job['status'] = 'running'
            write_json(output / 'manifest.json', manifest)
            print(f"Starting {job['model']} {job['section']} {job['name']} rescue {attempt}", flush=True)
            try:
                row.update(run_attempt(output, manifest, job, attempt, limit), status='completed')
                job['status'] = 'completed'
            except Exception as error:
                row.update(status='failed', error_type=type(error).__name__, error=str(error))
                job['status'] = 'failed'
                path = output / job['model'] / job['section'] / job['name'] / f'attempt{attempt}'
                path.mkdir(parents=True, exist_ok=True)
                (path / 'failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
            write_json(output / 'manifest.json', manifest)
            print(f"Finished: {job['status']}", flush=True)
            if job['status'] == 'completed':
                break
            if 'budget' in row.get('error', '').lower():
                break
    manifest['status'] = 'completed' if all(j['status'] == 'completed' for j in manifest['jobs']) else 'partial'
    write_json(output / 'manifest.json', manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prior-rescue', type=Path)
    parser.add_argument('--json-mode', action='store_true')
    args = parser.parse_args()
    JSON_MODE = args.json_mode
    result = prepare(args.previous.resolve(), args.output.resolve(),
                     args.prior_rescue.resolve() if args.prior_rescue else None)
    execute(args.output.resolve(), result)
