import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from consolidate_multimodel_completion import consolidate, finite


def test_all_run07_successes_have_validated_finite_review():
    result = consolidate(finite.EVALUATION_ROOT / 'multimodel_run07/manifest.json', [])
    assert len(result['rows']) == 36
    assert result['status_counts'] == {'completed': 22, 'failed': 14}
    assert result['unreviewed_completed'] == 0
    assert sum(r['reviewed_graphs'] for r in result['model_summaries']) == 22
    assert sum(r['first_completed'] for r in result['model_summaries']) == 22
    longcat = next(r for r in result['model_summaries'] if r['model'] == 'longcat-2.0')
    assert (longcat['typed_hits'], longcat['typed_total']) == (12, 15)
    assert (longcat['edge_hits'], longcat['edge_total']) == (5, 12)


def test_failed_cells_have_no_quality_zero():
    result = consolidate(finite.EVALUATION_ROOT / 'multimodel_run07/manifest.json', [])
    failures = [r for r in result['rows'] if r['status'] == 'failed']
    assert all('finite_quality' not in r for r in failures)
    assert all(r['finite_review_status'] == 'failed_not_scored' for r in failures)


def test_budget_continuation_preserves_precompletion_outcome(tmp_path, monkeypatch):
    from consolidate_multimodel_completion import failure_kind
    directory = tmp_path / 'latest'
    directory.mkdir()
    (directory / 'stdout.log').write_text('Pre-request token budget gate reached', encoding='utf-8')
    job = dict(model='glm-5.3-flash', section='ch7_s5', name='direct', status='failed',
               directory=str(directory), attempt_number=3,
               previous_failed_attempt=dict(directory='second', attempt_number=2,
                   previous_failed_attempt=dict(directory='precompletion')))
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(dict(jobs=[job], models=['glm-5.3-flash'], sections=[])), encoding='utf-8')
    monkeypatch.setattr('consolidate_multimodel_completion.collect_reviews', lambda extra: {})
    result = consolidate(path, [])
    row = result['rows'][0]
    assert row['first_outcome'] == 'failed'
    assert row['first_attempt_directory'] == 'precompletion'
    assert row['attempt_number'] == 3
    assert row['budget_admin_continuation']
    assert not row['completion_second_attempt']
    assert failure_kind(directory) == 'budget_gate'


def test_schema_failure_is_not_mislabeled_as_transport(tmp_path):
    from consolidate_multimodel_completion import failure_kind
    (tmp_path / 'stdout.log').write_text(
        'ValueError: Chunk response failed schema validation for chunk_004; '
        'relations.0.target_ref Field required', encoding='utf-8')
    assert failure_kind(tmp_path) == 'schema_invalid'
