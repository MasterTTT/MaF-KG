import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from merge_multimodel_prediction_audits import merge


ROOT = Path(__file__).resolve().parents[1] / 'evaluation/prospective_2026-10-04'
PAIR = (ROOT / 'blind_prediction_review_ode5', ROOT / 'blinded_ode_prediction_audit_v1/review.json')


def test_existing_audit_is_source_validated_and_missing_not_zero():
    result = merge(ROOT / 'multimodel_run07/manifest.json', [PAIR])
    assert result['reviewed_graphs'] == 5
    assert result['reviewed_nodes'] == 44
    assert result['reviewed_relations'] == 30
    assert result['unreviewed_completed'] == 17
    assert all('audit' not in c for c in result['cells'] if c['status'] == 'failed')


def test_repeated_graph_rejected():
    with pytest.raises(ValueError, match='Repeated graph'):
        merge(ROOT / 'multimodel_run07/manifest.json', [PAIR, PAIR])
