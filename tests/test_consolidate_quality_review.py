import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("consolidation", ROOT / "scripts/consolidate_quality_review.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConsolidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = MODULE.consolidate(
            ROOT / "evaluation/human_quality_review_2026-10-04/run01",
            ROOT / "evaluation/math_quality_2026-10-04/run02_adjudicated/adjudicated_metrics.json")

    def test_all_predictions_have_one_decision(self):
        rows = [e for p in self.result["packets"] for e in p["entries"]]
        self.assertEqual(len(rows), 199)
        self.assertEqual(len({r["review_id"] for r in rows}), 199)
        self.assertEqual(sum(self.result["counts"]["node"].values()), 93)
        self.assertEqual(sum(self.result["counts"]["relation"].values()), 106)

    def test_chat_approval_does_not_spread(self):
        rows = [e for p in self.result["packets"] for e in p["entries"]
                if e["provenance"] == "explicit current chat acceptance"]
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(r["review_id"].startswith("P01-") for r in rows))

    def test_pending_metadata_and_admission_are_distinct(self):
        rows = {e["review_id"]: e for p in self.result["packets"] for e in p["entries"]}
        for code in self.result["pending_ids"]:
            self.assertEqual(rows[code]["verdict"], "body_valid_metadata_scope_pending")
        self.assertEqual(rows["P02-N009"]["verdict"], "admission_issue")
        self.assertEqual(rows["P02-N010"]["verdict"], "admission_issue")

    def test_scope_approval_changes_only_three_items(self):
        packet_dir = ROOT / "evaluation/human_quality_review_2026-10-04/run01"
        final = MODULE.consolidate(
            packet_dir,
            ROOT / "evaluation/math_quality_2026-10-04/run02_adjudicated/adjudicated_metrics.json",
            packet_dir / "metadata_scope_decision_v1.json")
        before = {e["review_id"]: e for p in self.result["packets"] for e in p["entries"]}
        after = {e["review_id"]: e for p in final["packets"] for e in p["entries"]}
        self.assertEqual({code for code in before if before[code] != after[code]},
                         set(self.result["pending_ids"]))
        self.assertEqual(final["pending_ids"], [])
        self.assertEqual(final["counts"]["node"]["valid"], 84)
        self.assertEqual(final["counts"]["relation"]["valid"], 98)


if __name__ == "__main__":
    unittest.main()
