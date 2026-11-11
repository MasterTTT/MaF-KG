import copy
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_multimodel_completion_wave as completion_wave

retry_keys = completion_wave.retry_keys


EXPECTED_KEYS = [
    ('deepseek-v4-flash', 'ch4_s3', 'sequential'),
    ('deepseek-v4-flash', 'ch4_s3', 'direct'),
    ('deepseek-v4-flash', 'ch7_s5', 'sequential'),
    ('gpt-6-luna', 'ch4_s3', 'direct'),
    ('gpt-6-luna', 'ch7_s5', 'sequential'),
    ('longcat-2.0', 'ch4_s3', 'sequential'),
    ('longcat-2.0', 'ch7_s5', 'direct'),
    ('kimi-k2.6', 'ch7_s5', 'sequential'),
    ('glm-5.3-flash', 'ch4_s3', 'sequential'),
]


def _job(key, status):
    model, section, name = key
    job = {
        'model': model,
        'section': section,
        'name': name,
        'repeat': 1,
        'memory_node_bytes': 12000,
        'memory_relation_bytes': 6000,
        'directory': f'old/{model}/{section}/{name}',
        'status': status,
        'first_pass_marker': f'{model}:{section}:{name}:{status}',
    }
    if status == 'failed':
        job['error'] = f'original failure: {model}/{section}/{name}'
    return job


def test_missing_cells_only_and_glm_is_bounded():
    jobs = [dict(model=m, section=s, name=a, status=status)
            for m, s, a, status in [
                ('deepseek-v4-flash', 'ch4_s3', 'sequential', 'failed'),
                ('gpt-6-luna', 'ch4_s3', 'direct', 'completed'),
                ('glm-5.3-flash', 'ch4_s3', 'sequential', 'failed'),
                ('glm-5.3-flash', 'ch4_s3', 'direct', 'failed'),
                ('glm-5.3-flash', 'ch7_s5', 'sequential', 'failed'),
                ('longcat-2.0', 'ch7_s5', 'direct', 'failed')]]
    assert retry_keys({'jobs': jobs}) == [
        ('deepseek-v4-flash', 'ch4_s3', 'sequential'),
        ('glm-5.3-flash', 'ch4_s3', 'sequential'),
        ('longcat-2.0', 'ch7_s5', 'direct')]


def test_completed_jobs_not_selected():
    assert retry_keys({'jobs': [dict(model='kimi-k2.6', section='ch3_s3',
                                   name='sequential', status='completed')]}) == []


def test_third_attempt_rejected_and_explicit_remaining_allowed():
    from run_multimodel_completion_wave import explicit_keys
    old = {'jobs': [
        dict(model='glm-5.3-flash', section='ch3_s3', name='direct', status='failed'),
        dict(model='longcat-2.0', section='ch3_s3', name='sequential', status='failed', attempt_number=2)]}
    decision = {'schema_version': 'multimodel-completion-selection.v1',
                'selected_keys': [['glm-5.3-flash', 'ch3_s3', 'direct']]}
    assert explicit_keys(old, decision) == [('glm-5.3-flash', 'ch3_s3', 'direct')]
    assert retry_keys(old) == []
    decision['selected_keys'] = [['longcat-2.0', 'ch3_s3', 'sequential']]
    import pytest
    with pytest.raises(ValueError, match='not already second-attempted'):
        explicit_keys(old, decision)


def test_prepare_selects_nine_keys_preserves_old_outcomes_and_budgets(
        tmp_path, monkeypatch):
    glm_failures = [
        ('glm-5.3-flash', 'ch4_s3', 'sequential'),
        ('glm-5.3-flash', 'ch4_s3', 'direct'),
        ('glm-5.3-flash', 'ch7_s5', 'sequential'),
        ('glm-5.3-flash', 'ch7_s5', 'direct'),
        ('glm-5.3-flash', 'ch3_s3', 'sequential'),
        ('glm-5.3-flash', 'ch3_s3', 'direct'),
    ]
    completed_key = ('deepseek-v4-flash', 'ch3_s3', 'direct')
    old_manifest = {
        'jobs': ([_job(key, 'failed') for key in EXPECTED_KEYS]
                 + [_job(key, 'failed') for key in glm_failures[1:]]
                 + [_job(completed_key, 'completed')]),
        'artifacts': [],
        'per_model_budget_usd': 0.50,
        'per_model_token_limit': 1_000_000,
        'model_budget_overrides': {'kimi-k2.6': 1.50},
        'budget_decision': 'inherited-decision.json',
    }
    original_snapshot = copy.deepcopy(old_manifest)
    previous = tmp_path / 'previous'
    output = tmp_path / 'completion'
    writes = {}
    digests = []
    binds = []
    audits = []
    prepare_calls = []

    def fake_read(path):
        assert Path(path) == previous / 'manifest.json'
        return copy.deepcopy(old_manifest)

    def fake_prepare(out, prev):
        prepare_calls.append((out, prev))
        return copy.deepcopy(old_manifest)

    def fake_write_json(path, value):
        writes[Path(path)] = copy.deepcopy(value)

    def fake_digest(path):
        digests.append(Path(path))
        return 'previous-manifest-digest'

    def fake_bind(path):
        binds.append(Path(path))
        return {'path': str(path), 'sha256': 'decision-digest'}

    def fake_audit(manifest):
        audits.append(copy.deepcopy(manifest))

    monkeypatch.setattr(completion_wave.runner, 'read', fake_read)
    monkeypatch.setattr(completion_wave.runner, 'prepare', fake_prepare)
    monkeypatch.setattr(completion_wave, 'write_json', fake_write_json)
    monkeypatch.setattr(completion_wave, 'digest', fake_digest)
    monkeypatch.setattr(completion_wave.runner.baseline, 'bind', fake_bind)
    monkeypatch.setattr(completion_wave.runner.baseline, 'audit_prepared', fake_audit)

    prepared = completion_wave.prepare(output, previous)

    assert prepare_calls == [(output, previous)]
    decision = prepared['completion_wave']
    assert decision['selected_keys'] == [list(key) for key in EXPECTED_KEYS]
    assert len(decision['selected_keys']) == 9
    assert decision['budgets'] == 'Inherited cumulative USD/token limits; no increase'
    assert decision['previous_manifest_sha256'] == 'previous-manifest-digest'
    assert digests == [previous / 'manifest.json']
    assert binds == [output / 'completion_decision.json']
    assert prepared['artifacts'] == [
        {'path': str(output / 'completion_decision.json'), 'sha256': 'decision-digest'}]

    old_by_key = {(j['model'], j['section'], j['name']): j
                  for j in old_manifest['jobs']}
    prepared_by_key = {(j['model'], j['section'], j['name']): j
                       for j in prepared['jobs']}
    for key in EXPECTED_KEYS:
        job = prepared_by_key[key]
        assert job['status'] == 'pending'
        assert job['attempt_number'] == 2
        assert job['previous_failed_attempt'] == old_by_key[key]
        assert job['retry_reason'] == 'Explicit user-authorized bounded completion wave'

    for key in glm_failures[1:]:
        assert prepared_by_key[key] == old_by_key[key]
    assert prepared_by_key[completed_key] == old_by_key[completed_key]
    for field in ('per_model_budget_usd', 'per_model_token_limit',
                  'model_budget_overrides', 'budget_decision'):
        assert prepared[field] == original_snapshot[field]
    assert old_manifest == original_snapshot
    assert prepared['status'] == 'prepared_completion_wave'
    assert set(writes) == {output / 'completion_decision.json', output / 'manifest.json'}
    assert writes[output / 'manifest.json'] == prepared
    assert len(audits) == 1
    assert audits[0] == prepared


def test_execute_advances_only_pending_selected_cell_once(tmp_path, monkeypatch, capsys):
    pending_key = ('deepseek-v4-flash', 'ch4_s3', 'sequential')
    completed_key = ('gpt-6-luna', 'ch4_s3', 'direct')
    failed_key = ('glm-5.3-flash', 'ch7_s5', 'direct')
    state = {'manifest': {
        'completion_wave': {'selected_keys': [list(k) for k in
                                               (pending_key, completed_key, failed_key)]},
        'jobs': [_job(pending_key, 'pending'), _job(completed_key, 'completed'),
                 _job(failed_key, 'failed')],
    }}
    output = tmp_path / 'completion'
    execute_calls = []

    def fake_read(path):
        assert Path(path) == output / 'manifest.json'
        return copy.deepcopy(state['manifest'])

    def fake_execute(out, max_jobs, only_models, skip_recorded_failures=False):
        execute_calls.append((out, max_jobs, only_models, skip_recorded_failures))
        assert out == output
        assert max_jobs == 1
        assert skip_recorded_failures is True
        matching = [j for j in state['manifest']['jobs']
                    if j['model'] in only_models and j['status'] == 'pending']
        assert len(matching) == 1
        matching[0]['status'] = 'completed'

    monkeypatch.setattr(completion_wave.runner, 'read', fake_read)
    monkeypatch.setattr(completion_wave.runner, 'execute', fake_execute)

    completion_wave.execute(output)

    assert execute_calls == [(output, 1, [pending_key[0]], True)]
    final_by_key = {(j['model'], j['section'], j['name']): j
                    for j in state['manifest']['jobs']}
    assert final_by_key[pending_key]['status'] == 'completed'
    assert final_by_key[completed_key]['status'] == 'completed'
    assert final_by_key[failed_key]['status'] == 'failed'
    assert all(j['status'] not in ('pending', 'running')
               for j in state['manifest']['jobs'])
    assert json.loads(capsys.readouterr().out) == {'completed': 2, 'failed': 1}


def test_execute_raises_if_a_pending_cell_remains(tmp_path, monkeypatch):
    pending_key = ('deepseek-v4-flash', 'ch4_s3', 'sequential')
    state = {'manifest': {
        'completion_wave': {'selected_keys': [list(pending_key)]},
        'jobs': [_job(pending_key, 'pending')],
    }}
    output = tmp_path / 'completion'
    execute_calls = []

    def fake_read(path):
        return copy.deepcopy(state['manifest'])

    def fake_execute(*args, **kwargs):
        execute_calls.append((args, kwargs))

    monkeypatch.setattr(completion_wave.runner, 'read', fake_read)
    monkeypatch.setattr(completion_wave.runner, 'execute', fake_execute)

    try:
        completion_wave.execute(output)
    except ValueError as exc:
        assert str(exc) == 'Completion wave left unsettled jobs'
    else:
        raise AssertionError('execute should reject a manifest that remains pending')

    assert len(execute_calls) == 1


def _token_gate_approval():
    return {
        'schema_version': 'glm-token-gate-continuation.v1',
        'decision_source': 'human-user-in-chat',
        'approved_at': '2026-10-05',
        'previous_run': 'multimodel_run09',
        'model': 'glm-5.3-flash',
        'old_token_limit': 1_000_000,
        'new_token_limit': 1_500_000,
        'old_usd_limit': 0.50,
        'new_usd_limit': 0.50,
        'attempt_number': 3,
        'scope': 'budget-only',
        'request_generation_changed': False,
        'selected_keys': [
            ['glm-5.3-flash', 'ch7_s5', 'sequential'],
            ['glm-5.3-flash', 'ch7_s5', 'direct'],
        ],
    }


def test_token_gate_continuation_is_narrow_and_preserves_history_and_spend(
        tmp_path, monkeypatch):
    approved = [
        ('glm-5.3-flash', 'ch7_s5', 'sequential'),
        ('glm-5.3-flash', 'ch7_s5', 'direct'),
    ]
    truncated = [
        ('gpt-6-luna', 'ch7_s5', 'sequential'),
        ('kimi-k2.6', 'ch7_s5', 'direct'),
    ]
    previous = tmp_path / 'multimodel_run09'
    previous.mkdir()
    jobs = []
    for key in approved:
        job = _job(key, 'failed')
        job.update(attempt_number=2, directory=str(previous / key[0] / key[1] / key[2]),
                   previous_failed_attempt={'attempt_number': 1, 'status': 'failed',
                                            'history_marker': 'first attempt'})
        log = Path(job['directory']) / 'stdout.log'
        log.parent.mkdir(parents=True)
        log.write_text('Traceback (most recent call last):\n'
                       'ValueError: Pre-request token budget gate reached\n', encoding='utf-8')
        jobs.append(job)
    for key in truncated:
        job = _job(key, 'failed')
        job.update(attempt_number=2, directory=str(previous / key[0] / key[1] / key[2]))
        log = Path(job['directory']) / 'stdout.log'
        log.parent.mkdir(parents=True)
        log.write_text('CompletionTruncated: completion ended early\n', encoding='utf-8')
        jobs.append(job)
    jobs.append(_job(('deepseek-v4-flash', 'ch4_s3', 'direct'), 'completed'))
    old_manifest = {'jobs': jobs, 'status': 'stopped_on_failure'}
    (previous / 'manifest.json').write_text(json.dumps(old_manifest), encoding='utf-8')
    previous_budgets = previous / 'budgets'
    previous_budgets.mkdir()
    original_requests = [{'label': 'settled-1', 'status': 'reported_usage'},
                         {'label': 'settled-2', 'status': 'unknown_usage_keep_full_reservation'}]
    ledger = {'model': 'glm-5.3-flash', 'usd_limit': 0.50, 'token_limit': 1_000_000,
              'accounting_usd': 0.2666535, 'accounting_tokens': 934_946,
              'requests': original_requests}
    (previous_budgets / 'glm-5.3-flash.json').write_text(json.dumps(ledger), encoding='utf-8')
    approval_path = tmp_path / 'glm-token-gate-approval.json'
    approval_path.write_text(json.dumps(_token_gate_approval()), encoding='utf-8')
    output = tmp_path / 'multimodel_run10'
    audits = []

    def fake_prepare(out, prev):
        out.mkdir()
        (out / 'snapshot/scripts').mkdir(parents=True)
        (out / 'budgets').mkdir()
        shutil.copy2(prev / 'budgets/glm-5.3-flash.json', out / 'budgets/glm-5.3-flash.json')
        return {
            'jobs': copy.deepcopy(old_manifest['jobs']), 'status': 'prepared_remaining',
            'snapshot': str(out / 'snapshot'), 'artifacts': [],
            'per_model_budget_usd': 0.50, 'per_model_token_limit': 1_000_000,
            'model_budget_overrides': {'kimi-k2.6': 1.50}, 'budget_decision': 'inherited-kimi-decision',
        }

    monkeypatch.setattr(completion_wave.runner, 'prepare', fake_prepare)
    monkeypatch.setattr(completion_wave.runner.baseline, 'audit_prepared',
                        lambda manifest: audits.append(copy.deepcopy(manifest)))

    prepared = completion_wave.prepare(output, previous, token_gate_approval=approval_path)

    prepared_jobs = {(j['model'], j['section'], j['name']): j for j in prepared['jobs']}
    old_jobs = {(j['model'], j['section'], j['name']): j for j in old_manifest['jobs']}
    for key in approved:
        row = prepared_jobs[key]
        assert row['status'] == 'pending'
        assert row['attempt_number'] == 3
        assert row['previous_failed_attempt'] == old_jobs[key]
        assert row['previous_failed_attempt']['previous_failed_attempt']['history_marker'] == 'first attempt'
    for key in truncated:
        assert prepared_jobs[key] == old_jobs[key]

    upgraded = json.loads((output / 'budgets/glm-5.3-flash.json').read_text(encoding='utf-8'))
    assert upgraded['usd_limit'] == ledger['usd_limit']
    assert upgraded['token_limit'] == 1_500_000
    assert upgraded['accounting_usd'] == ledger['accounting_usd']
    assert upgraded['accounting_tokens'] == ledger['accounting_tokens']
    assert upgraded['requests'] == original_requests
    assert prepared['per_model_budget_usd'] == 0.50
    assert prepared['model_budget_overrides'] == {'kimi-k2.6': 1.50}
    assert prepared['model_token_overrides'] == {'glm-5.3-flash': 1_500_000}
    assert prepared['completion_wave']['selected_keys'] == [list(k) for k in approved]
    assert prepared['status'] == 'prepared_glm_token_gate_continuation'
    assert len(audits) == 1


def test_token_gate_approval_rejects_truncation_or_widened_selection(tmp_path):
    import pytest
    previous = tmp_path / 'multimodel_run09'
    key = ('glm-5.3-flash', 'ch7_s5', 'sequential')
    job = _job(key, 'failed')
    job.update(attempt_number=2, directory=str(tmp_path / 'failed'))
    Path(job['directory']).mkdir()
    (Path(job['directory']) / 'stdout.log').write_text(
        'CompletionTruncated: genuine truncation\n', encoding='utf-8')
    manifest = {'jobs': [job]}
    approval = _token_gate_approval()
    with pytest.raises(ValueError, match='token-gate failure'):
        completion_wave.validate_token_gate_approval(previous, approval, manifest)

    approval['selected_keys'].append(['gpt-6-luna', 'ch7_s5', 'sequential'])
    with pytest.raises(ValueError, match='Unrecognized GLM token-gate approval'):
        completion_wave.validate_token_gate_approval(previous, approval, manifest)


def test_real_traceback_token_gate_signature_is_recognized(tmp_path):
    job = _job(('glm-5.3-flash', 'ch7_s5', 'sequential'), 'failed')
    job['directory'] = str(tmp_path)
    (tmp_path / 'stdout.log').write_text(
        'Traceback (most recent call last):\n'
        '  File "prospective_native_client.py", line 80, in reserve\n'
        '    raise ValueError("Pre-request token budget gate reached")\n'
        'ValueError: Pre-request token budget gate reached\n'
        'During task with name extract_sequentially\n',
        encoding='utf-8')

    assert completion_wave._is_exact_token_gate_failure(job)


def test_frozen_worker_passes_manifest_token_override(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import run_prospective_multimodel as runner

    output = tmp_path
    directory = output / 'glm-5.3-flash/ch7_s5/direct'
    directory.mkdir(parents=True)
    manifest = {
        'session': 'frozen-session',
        'sections': [{'id': 'ch7_s5', 'input': 'frozen-input.md'}],
        'jobs': [{'model': 'glm-5.3-flash', 'section': 'ch7_s5', 'name': 'direct',
                  'directory': str(directory)}],
        'per_model_budget_usd': 0.50,
        'per_model_token_limit': 1_000_000,
        'model_token_overrides': {'glm-5.3-flash': 1_500_000},
        'reasoning_configuration': {'glm-5.3-flash': 'native gateway default'},
    }
    (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    captured = {}

    class Extractor:
        def __init__(self, **config):
            self.output_path = Path(config['output_path'])

        def run(self):
            self.output_path.write_text(json.dumps({'nodes': [], 'relations': [],
                                                    'diagnostics': {}}), encoding='utf-8')

    monkeypatch.setattr(runner, 'setup_baseline', lambda: None)
    monkeypatch.setattr(runner.baseline, 'audit_prepared', lambda manifest: None)
    monkeypatch.setattr(runner, 'load_module', lambda path: SimpleNamespace(Extractor=Extractor))
    monkeypatch.setattr(runner, 'install', lambda *args, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(runner.baseline, 'install_hooks', lambda *args: None)

    runner.worker(output, 0)

    assert captured == {'usd_limit': 0.50, 'token_limit': 1_500_000}


def test_execute_configures_baseline_before_preflight_audit(tmp_path, monkeypatch, capsys):
    from run_multimodel_completion_wave import TOKEN_GATE_KEYS, TOKEN_GATE_MODEL, TOKEN_GATE_NEW_LIMIT

    approval_path = tmp_path / 'glm-token-gate-approval.json'
    approval = _token_gate_approval()
    manifest = {
        'completion_wave': {
            'continuation_type': 'glm-token-budget-gate',
            'selected_keys': [list(key) for key in TOKEN_GATE_KEYS],
            'approval_decision': str(approval_path),
            'approval_decision_sha256': 'approval-hash',
        },
        'model_token_overrides': {TOKEN_GATE_MODEL: TOKEN_GATE_NEW_LIMIT},
        'per_model_budget_usd': 0.50,
        'jobs': [dict(model=key[0], section=key[1], name=key[2], attempt_number=3,
                      status='completed') for key in TOKEN_GATE_KEYS],
    }
    events = []
    configured = {'value': False}

    def setup_baseline():
        configured['value'] = True
        events.append('setup')

    def fake_read(path):
        if Path(path) == approval_path:
            return approval
        return copy.deepcopy(manifest)

    def audit_prepared(value):
        assert configured['value']
        events.append('audit')

    monkeypatch.setattr(completion_wave.runner, 'setup_baseline', setup_baseline)
    monkeypatch.setattr(completion_wave.runner, 'read', fake_read)
    monkeypatch.setattr(completion_wave, 'digest', lambda path: 'approval-hash')
    monkeypatch.setattr(completion_wave.runner.baseline, 'audit_prepared', audit_prepared)

    completion_wave.execute(tmp_path / 'run10')

    assert events == ['setup', 'audit']
    assert json.loads(capsys.readouterr().out) == {'completed': 2, 'failed': 0}
