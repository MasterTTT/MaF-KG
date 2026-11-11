import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_multimodel_quality_cases import export, render


ROOT = Path(__file__).resolve().parents[1] / 'evaluation/prospective_2026-10-04'


def test_all_flagged_rows_include_actual_prediction_and_source():
    data = export(ROOT / 'multimodel_completion_2026-10-05/before_wave_prediction_quality.json')
    assert len(data['cases']) == 61
    assert len({(c['graph_sha256'], c['review_id']) for c in data['cases']}) == 61
    assert all(c['source_excerpts'] and c['prediction'] and c['reason'] for c in data['cases'])
    assert '模型预测' in render(data)
    assert all(any(v in ('unsupported', 'uncertain', 'ineligible', 'incomplete', 'incorrect', 'invalid')
                   for v in c['labels'].values()) or c['duplicate_group'] for c in data['cases'])
