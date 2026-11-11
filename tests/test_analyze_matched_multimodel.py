import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_matched_multimodel import analyze, revalidate, summarize_cells, AXES, read

ROOT = Path(__file__).resolve().parents[1] / 'evaluation/prospective_2026-10-04/multimodel_completion_2026-10-05'


def inputs():
    return read(ROOT / 'final_review_matrix.json'), read(ROOT / 'final_prediction_quality.json')


def test_locked_inputs_revalidate_and_common_pool_is_exact():
    matrix, quality = revalidate(ROOT / 'final_review_matrix.json', ROOT / 'final_prediction_quality.json')
    result = analyze(matrix, quality)
    common = result['groups'][0]
    assert common['coordinates'] == [['ch4_s3', 'sequential'], ['ch7_s5', 'direct']]
    assert len(common['summaries']) == 5
    assert common['supplement']['model'] == 'longcat-2.0'
    assert all(r['typed']['total'] == 9 and r['edge']['total'] == 7 for r in common['summaries'])
    assert result['groups'][1]['models'] == ['deepseek-v4-flash', 'gpt-6-luna', 'kimi-k2.6']
    assert len(result['pairs']) == 13
    ds = next(r for r in result['paired_models'] if r['model'] == 'deepseek-v4-flash')
    assert ds['edge_macro_delta'] == pytest.approx(0.3)
    assert ds['edge_pooled'] == {'direct_hits': 9, 'sequential_hits': 9, 'total': 12, 'ratio_delta': 0}


def test_macro_is_not_pooled_and_zero_axis_is_not_perfect():
    cells = []
    for total in (2, 8):
        cells.append({'finite_quality': {'mandatory_typed_identity_hits': 1,
                      'mandatory_typed_identity_total': total, 'exact_finite_relation_hits': 0,
                      'exact_finite_relation_total': 0},
                      'audit': {a: {'counts': {}, 'applicable': 0, 'uncertain': 0} for a in AXES}})
    result = summarize_cells(cells)
    assert result['typed']['macro_ratio'] == (1 / 2 + 1 / 8) / 2
    assert result['typed']['hits'] / result['typed']['total'] == 0.2
    assert result['edge']['macro_ratio'] is None
    assert result['node_admission']['macro_lower'] is None
    assert result['node_admission']['applicable_cells'] == 0


def test_uncertain_is_not_silently_excluded():
    cell = {'finite_quality': {'mandatory_typed_identity_hits': 1, 'mandatory_typed_identity_total': 1,
                             'exact_finite_relation_hits': 1, 'exact_finite_relation_total': 1},
            'audit': {a: {'counts': {}, 'applicable': 0, 'uncertain': 0} for a in AXES}}
    cell['audit']['node_admission'] = {'counts': {'eligible': 1, 'uncertain': 1},
                                       'applicable': 2, 'uncertain': 1}
    result = summarize_cells([cell])['node_admission']
    assert result['macro_lower'] == 0.5
    assert result['macro_upper'] == 1


def test_pair_reference_denominator_mismatch_rejected():
    matrix, quality = inputs()
    matrix = copy.deepcopy(matrix)
    row = next(r for r in matrix['rows'] if r['model'] == 'deepseek-v4-flash'
               and r['section'] == 'ch3_s3' and r['name'] == 'direct')
    row['finite_quality']['mandatory_typed_identity_total'] += 1
    with pytest.raises(ValueError, match='Pair reference denominators'):
        analyze(matrix, quality)


def test_graph_binding_mismatch_rejected():
    matrix, quality = inputs()
    quality = copy.deepcopy(quality)
    cell = next(c for c in quality['cells'] if 'audit' in c)
    cell['audit']['graph_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Different graph'):
        analyze(matrix, quality)


def test_changed_summary_rejected(tmp_path):
    matrix, _ = inputs()
    matrix['rows'][0]['finite_quality']['mandatory_typed_identity_hits'] = -1
    path = tmp_path / 'matrix.json'
    import json
    path.write_text(json.dumps(matrix), encoding='utf-8')
    with pytest.raises(ValueError, match='Finite summary differs'):
        revalidate(path, ROOT / 'final_prediction_quality.json')


def test_duplicate_finite_coordinate_rejected():
    matrix, quality = inputs()
    matrix['rows'].append(copy.deepcopy(matrix['rows'][0]))
    with pytest.raises(ValueError, match='Duplicate finite coordinates'):
        analyze(matrix, quality)
