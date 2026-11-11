"""Explicit bounded second attempts; preserve frozen inputs and all first outcomes."""
import argparse
import copy
import json
import re
import shutil
from pathlib import Path

import run_prospective_multimodel as runner
from run_memory_ablation import digest, write_json

TOKEN_GATE_SCHEMA = 'glm-token-gate-continuation.v1'
TOKEN_GATE_MODEL = 'glm-5.3-flash'
TOKEN_GATE_KEYS = [
    (TOKEN_GATE_MODEL, 'ch7_s5', 'sequential'),
    (TOKEN_GATE_MODEL, 'ch7_s5', 'direct'),
]
TOKEN_GATE_OLD_LIMIT = 1_000_000
TOKEN_GATE_NEW_LIMIT = 1_500_000
TOKEN_GATE_USD_LIMIT = 0.50


def retry_keys(manifest):
    keys = []
    for job in manifest['jobs']:
        if job['status'] != 'failed' or job.get('attempt_number', 1) >= 2:
            continue
        # GLM gets only the shortest sequential viability test, not six more
        # long default-reasoning calls after six recorded failures.
        if job['model'] == 'glm-5.3-flash' and (job['section'], job['name']) != ('ch4_s3', 'sequential'):
            continue
        keys.append((job['model'], job['section'], job['name']))
    return keys


def explicit_keys(old, decision):
    if decision.get('schema_version') != 'multimodel-completion-selection.v1':
        raise ValueError('Unrecognized completion selection')
    keys = [tuple(k) for k in decision['selected_keys']]
    jobs = {(j['model'], j['section'], j['name']): j for j in old['jobs']}
    if len(set(keys)) != len(keys) or any(len(k) != 3 for k in keys):
        raise ValueError('Invalid or duplicate completion key')
    if any(k not in jobs or jobs[k]['status'] != 'failed'
           or jobs[k].get('attempt_number', 1) >= 2 for k in keys):
        raise ValueError('Selection must contain only failures not already second-attempted')
    return keys


def validate_token_gate_approval(previous, decision, previous_manifest=None):
    expected = {
        'schema_version': TOKEN_GATE_SCHEMA,
        'decision_source': 'human-user-in-chat',
        'previous_run': 'multimodel_run09',
        'model': TOKEN_GATE_MODEL,
        'old_token_limit': TOKEN_GATE_OLD_LIMIT,
        'new_token_limit': TOKEN_GATE_NEW_LIMIT,
        'old_usd_limit': TOKEN_GATE_USD_LIMIT,
        'new_usd_limit': TOKEN_GATE_USD_LIMIT,
        'attempt_number': 3,
        'scope': 'budget-only',
        'request_generation_changed': False,
        'selected_keys': [list(key) for key in TOKEN_GATE_KEYS],
    }
    if any(decision.get(key) != value for key, value in expected.items()):
        raise ValueError('Unrecognized GLM token-gate approval')
    if previous.name != decision['previous_run']:
        raise ValueError('Token-gate approval is bound to a different previous run')
    if previous_manifest is not None:
        old_jobs = {(j['model'], j['section'], j['name']): j for j in previous_manifest['jobs']}
        for key in TOKEN_GATE_KEYS:
            job = old_jobs.get(key)
            if (job is None or job['status'] != 'failed' or job.get('attempt_number') != 2
                    or not _is_exact_token_gate_failure(job)):
                raise ValueError(f'Approved job is not an attempt-2 token-gate failure: {key}')
    return list(TOKEN_GATE_KEYS)


def _is_exact_token_gate_failure(job):
    log_path = Path(job.get('directory', '')) / 'stdout.log'
    if not log_path.is_file():
        return False
    log = log_path.read_text(encoding='utf-8')
    exception_lines = re.findall(
        r'(?m)^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Truncated): .+$', log)
    return exception_lines == ['ValueError: Pre-request token budget gate reached']


def prepare(output, previous, selection_decision=None, token_gate_approval=None):
    if selection_decision is not None and token_gate_approval is not None:
        raise ValueError('Use either a completion selection or a token-gate approval, not both')
    old = runner.read(previous / 'manifest.json')
    selection = runner.read(selection_decision) if selection_decision else None
    approval = runner.read(token_gate_approval) if token_gate_approval else None
    if approval:
        selected = validate_token_gate_approval(previous, approval, old)
    else:
        selected = explicit_keys(old, selection) if selection else retry_keys(old)
    if not selected or any(j['status'] in ('pending', 'running') for j in old['jobs']):
        raise ValueError('Previous experiment must be settled and have failed jobs')
    for path in (previous / 'budgets').glob('*.json'):
        if any(r['status'] == 'reserved' for r in runner.read(path)['requests']):
            raise ValueError('Unsettled budget reservation')
    if approval:
        old_glm_budget = previous / 'budgets' / f'{TOKEN_GATE_MODEL}.json'
        if not old_glm_budget.is_file():
            raise ValueError('Previous GLM budget ledger is missing')
        ledger = runner.read(old_glm_budget)
        if (ledger.get('usd_limit') != TOKEN_GATE_USD_LIMIT
                or ledger.get('token_limit') != TOKEN_GATE_OLD_LIMIT
                or ledger.get('accounting_usd', TOKEN_GATE_USD_LIMIT + 1) > TOKEN_GATE_USD_LIMIT
                or ledger.get('accounting_tokens', TOKEN_GATE_OLD_LIMIT + 1) > TOKEN_GATE_OLD_LIMIT):
            raise ValueError('Previous GLM budget does not match the approved settled limits')
    manifest = runner.prepare(output, previous)
    decision = {
        'authorization': 'Human requested prioritizing a complete multi-model comparison on 2026-10-05',
        'previous_manifest': str(previous / 'manifest.json'),
        'previous_manifest_sha256': digest(previous / 'manifest.json'),
        'policy': 'One fresh full-job second attempt for each selected failure; no within-job retry, response repair or changed generation setting. First-pass reliability remains a separate endpoint.',
        'selected_keys': [list(k) for k in selected],
        'budgets': 'Inherited cumulative USD/token limits; no increase',
        'glm_policy': 'Only shortest sequential section viability test; other five failures retained without more spend',
        'selection_basis': 'All non-GLM missing cells, not quality scores',
    }
    if selection_decision:
        target = output / 'snapshot' / 'completion_selection.json'
        shutil.copy2(selection_decision, target)
        executor = output / 'snapshot/scripts/run_multimodel_completion_wave.py'
        shutil.copy2(Path(__file__), executor)
        manifest['artifacts'] += [runner.baseline.bind(target), runner.baseline.bind(executor)]
        decision.update(selection_basis=selection['selection_basis'], glm_policy=selection['glm_policy'],
                        selection_decision=str(target), selection_decision_sha256=digest(target),
                        executor_sha256=digest(executor))
    if approval:
        target = output / 'snapshot/evaluation/prospective_2026-10-04/glm_token_gate_approval_v1.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(token_gate_approval, target)
        executor = output / 'snapshot/scripts/run_multimodel_completion_wave.py'
        executor.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__), executor)
        manifest['artifacts'] += [runner.baseline.bind(target), runner.baseline.bind(executor)]
        decision.update(
            continuation_type='glm-token-budget-gate',
            selected_keys=[list(k) for k in selected],
            policy='One fresh full-job attempt 3 for only the two approved GLM token-gate failures; no within-job retry, response repair, or generation change. Attempts 1 and 2 remain in history.',
            budgets='Cumulative USD limit unchanged at $0.50; GLM token limit increased from 1,000,000 to 1,500,000',
            glm_policy='Only ch7_s5 sequential/direct attempt-2 failures caused by the exact pre-request token budget gate are eligible; all other failures are retained.',
            approval_decision=str(target), approval_decision_sha256=digest(target),
            executor_sha256=digest(executor), attempt_number=3)
        manifest['model_token_overrides'] = {
            **manifest.get('model_token_overrides', {}), TOKEN_GATE_MODEL: TOKEN_GATE_NEW_LIMIT}
        manifest['token_budget_decision'] = str(target)
        ledger_path = output / 'budgets' / f'{TOKEN_GATE_MODEL}.json'
        ledger = runner.read(ledger_path)
        if (ledger.get('usd_limit') != TOKEN_GATE_USD_LIMIT
                or ledger.get('token_limit') != TOKEN_GATE_OLD_LIMIT
                or any(r['status'] == 'reserved' for r in ledger['requests'])):
            raise ValueError('Copied GLM budget ledger is not settled at the approved limits')
        ledger['token_limit'] = TOKEN_GATE_NEW_LIMIT
        write_json(ledger_path, ledger)
    decision_path = output / 'completion_decision.json'
    write_json(decision_path, decision)
    manifest['artifacts'].append(runner.baseline.bind(decision_path))
    manifest['completion_wave'] = decision
    for job in manifest['jobs']:
        key = (job['model'], job['section'], job['name'])
        if key not in selected:
            continue
        prior = copy.deepcopy(job)
        if approval and (prior['status'] != 'failed' or prior.get('attempt_number') != 2):
            raise ValueError(f'Prepared continuation did not preserve the approved failed attempt: {key}')
        directory = output / job['model'] / job['section'] / job['name']
        job.clear()
        job.update(model=key[0], section=key[1], name=key[2], repeat=1,
                   attempt_number=3 if approval else 2,
                   memory_node_bytes=12000, memory_relation_bytes=6000,
                   directory=str(directory), status='pending', previous_failed_attempt=prior,
                   retry_reason=('User-approved token-budget-only continuation; attempt-2 failure retained'
                                 if approval else 'Explicit user-authorized bounded completion wave'))
    manifest['status'] = ('prepared_glm_token_gate_continuation' if approval
                          else 'prepared_completion_wave')
    write_json(output / 'manifest.json', manifest)
    runner.baseline.audit_prepared(manifest)
    return manifest


def execute(output):
    runner.setup_baseline()
    manifest = runner.read(output / 'manifest.json')
    if 'completion_wave' not in manifest:
        raise ValueError('No explicit completion decision')
    completion = manifest['completion_wave']
    selected = {tuple(k) for k in completion['selected_keys']}
    if completion.get('continuation_type') == 'glm-token-budget-gate':
        expected = set(TOKEN_GATE_KEYS)
        if (selected != expected
                or manifest.get('model_token_overrides', {}).get(TOKEN_GATE_MODEL) != TOKEN_GATE_NEW_LIMIT
                or manifest.get('per_model_budget_usd') != TOKEN_GATE_USD_LIMIT
                or any((j['model'], j['section'], j['name']) in expected
                       and j.get('attempt_number') != 3 for j in manifest['jobs'])):
            raise ValueError('Invalid GLM token-gate continuation manifest')
        approval_path = Path(completion['approval_decision'])
        if digest(approval_path) != completion['approval_decision_sha256']:
            raise ValueError('Frozen GLM token-gate approval changed')
        approval = runner.read(approval_path)
        validate_token_gate_approval(Path(approval['previous_run']), approval)
        runner.baseline.audit_prepared(manifest)
    for path in (output / 'budgets').glob('*.json'):
        if any(r['status'] == 'reserved' for r in runner.read(path)['requests']):
            raise ValueError('Unsettled budget reservation')
    # The frozen executor stops after any failure. Each invocation below starts
    # only the next pending cell; it never retries an already failed cell.
    for job in manifest['jobs']:
        key = (job['model'], job['section'], job['name'])
        current = runner.read(output / 'manifest.json')
        row = next(j for j in current['jobs'] if (j['model'], j['section'], j['name']) == key)
        if key in selected and row['status'] == 'pending':
            runner.execute(output, 1, [job['model']], skip_recorded_failures=True)
    final = runner.read(output / 'manifest.json')
    pending = [j for j in final['jobs'] if j['status'] in ('pending', 'running')]
    if pending:
        raise ValueError('Completion wave left unsettled jobs')
    print(json.dumps({'completed': sum(j['status'] == 'completed' for j in final['jobs']),
                      'failed': sum(j['status'] == 'failed' for j in final['jobs'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--selection-decision', type=Path, help='Frozen explicit remaining-cell selection')
    parser.add_argument('--token-gate-approval', type=Path,
                        help='Explicit approval for the two GLM attempt-3 token-gate continuations')
    args = parser.parse_args()
    if args.prepare:
        if args.previous is None:
            parser.error('--prepare requires --previous')
        prepare(args.output.resolve(), args.previous.resolve(),
                args.selection_decision.resolve() if args.selection_decision else None,
                args.token_gate_approval.resolve() if args.token_gate_approval else None)
    if args.execute:
        execute(args.output.resolve())
