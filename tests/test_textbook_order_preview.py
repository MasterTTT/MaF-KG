import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preview_textbook_order_fix import reorder


def test_reorder_preserves_bytes_and_inverse():
    original = [b"A\r\n", b"B\r\n", b"C\r\n", b"D"]
    result, order = reorder(original, [(1, 1), (3, 3), (2, 2), (4, 4)])
    assert order == [1, 3, 2, 4]
    assert b"".join(result) == b"A\r\nC\r\nB\r\nD"
    inverse = {old: new for new, old in enumerate(order, 1)}
    assert [result[inverse[i]-1] for i in range(1, 5)] == original


@pytest.mark.parametrize("spans", [[(1, 2), (2, 4)], [(1, 3)], [(0, 4)], [(1, 5)]])
def test_invalid_permutations_rejected(spans):
    with pytest.raises(ValueError):
        reorder([b"a", b"b", b"c", b"d"], spans)
