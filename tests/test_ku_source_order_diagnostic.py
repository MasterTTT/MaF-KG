import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_ku_source_order_diagnostic import paths_for, segments, split_headings


def test_noncontiguous_evidence_is_not_widened():
    assert segments(2, 4, {1: 1, 2: 5, 3: 6, 4: 2}) == [[2, 2], [5, 6]]


def test_every_original_line_preserved():
    result = segments(1, 5, {1: 1, 2: 4, 3: 5, 4: 2, 5: 3})
    assert result == [[1, 5]]


def test_numbered_heading_paths_and_nonnumbered_heading():
    paths = paths_for(['# 第一节 映射与函数', '# 二、函数', '# 2. 性质',
                       '# 定理1', '内容', '# 3. 反函数与复合函数'])
    assert paths[4] == paths[3]
    assert paths[6] == ['第一节 映射与函数', '二、函数', '3. 反函数与复合函数']
    assert split_headings(3, 6, paths) == [[3, 5], [6, 6]]
