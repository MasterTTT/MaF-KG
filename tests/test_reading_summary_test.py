import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_reading_summary_test import build_arms, check_summary


class ReadingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"current_evidence": {"start_line": 10, "end_line": 12},
                        "graph_state": {"nodes": [{"id": "n1"}]},
                        "source_applicability_context": [{"start_line": 1, "end_line": 2}]}
        self.note = {"current_focus": {"text": "focus", "source_spans": [{"start_line": 10, "end_line": 12}]},
                     "relevant_source_context": [{"text": "context", "source_spans": [{"start_line": 1, "end_line": 2}]}],
                     "graph_context": {"visible_identity_ids": ["n1"]}, "uncertainties": []}

    def test_summary_is_only_primary_pair_change(self):
        arms = build_arms(self.payload, self.note)
        self.assertEqual(arms["source_context"]["graph_state"], arms["source_and_summary"]["graph_state"])
        self.assertEqual(arms["source_context"]["source_applicability_context"],
                         arms["source_and_summary"]["source_applicability_context"])
        self.assertIsNone(arms["source_context"]["reading_state_summary"])
        self.assertEqual(arms["source_and_summary"]["reading_state_summary"], self.note)
        self.assertNotIn("reading_state_summary", self.payload)

    def test_citation_gap_rejected(self):
        note = copy.deepcopy(self.note)
        note["current_focus"]["source_spans"] = [{"start_line": 2, "end_line": 10}]
        with self.assertRaises(ValueError):
            check_summary(note, self.payload)

    def test_unknown_identity_rejected(self):
        note = copy.deepcopy(self.note)
        note["graph_context"]["visible_identity_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            check_summary(note, self.payload)

    def test_valid_summary(self):
        self.assertEqual(check_summary(self.note, self.payload)["citation_range_check"], "passed")

    def test_supplied_neighbor_lines_are_valid_context(self):
        payload = copy.deepcopy(self.payload)
        payload["reading_context"] = {"context_before_text": "8: neighbor\n9: context", "context_after_text": ""}
        note = copy.deepcopy(self.note)
        note["relevant_source_context"][0]["source_spans"] = [{"start_line": 8, "end_line": 9}]
        self.assertEqual(check_summary(note, payload)["citation_range_check"], "passed")


if __name__ == "__main__":
    unittest.main()
