import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
from final_review_check import dist


def test_prediction_support_uses_its_own_status_universe():
    rows = [{"status": x} for x in ["yes", "yes", "partial", "no", "undecided"]]
    assert dist(rows, ("yes", "partial", "no", "undecided")) == {
        "yes": 2, "partial": 1, "no": 1, "undecided": 1}


def test_coverage_status_distribution_is_unchanged():
    assert dist([{"status": "full"}, {"status": "missing"}]) == {"full": 1, "missing": 1}
