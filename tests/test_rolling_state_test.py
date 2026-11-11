import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_rolling_state_test import check_state, paired_requests


class RollingStateTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [{"id": "c1", "start_line": 1, "end_line": 5}]
        self.graph = {"nodes": {"n1": {}}, "relations": [["n1", "involves", "n1"]]}
        self.note = {"processed_chunk_ids": ["c1"], "reading_progress": [{"source_spans": [{"start_line": 1, "end_line": 5}]}],
                     "scope_conditions": [], "graph_notes": [{"node_ids": ["n1"], "relation_identities": []}],
                     "context_limits": []}

    def test_only_state_differs(self):
        payload = {"graph_state": {"nodes": ["n1"]}}
        pair = paired_requests(payload, self.note)
        self.assertIsNone(pair["no_state"]["rolling_reading_state"])
        self.assertEqual(pair["rolling_state"]["rolling_reading_state"], self.note)
        self.assertEqual(pair["no_state"]["graph_state"], pair["rolling_state"]["graph_state"])
        self.assertNotIn("rolling_reading_state", payload)

    def test_future_citation_rejected(self):
        note = copy.deepcopy(self.note)
        note["reading_progress"][0]["source_spans"][0]["end_line"] = 6
        with self.assertRaises(ValueError):
            check_state(note, self.chunks, self.graph)

    def test_false_completion_rejected(self):
        note = copy.deepcopy(self.note)
        note["processed_chunk_ids"].append("c2")
        with self.assertRaises(ValueError):
            check_state(note, self.chunks, self.graph)

    def test_invented_relation_rejected(self):
        note = copy.deepcopy(self.note)
        note["graph_notes"][0]["relation_identities"] = [["n1", "uses", "n1"]]
        with self.assertRaises(ValueError):
            check_state(note, self.chunks, self.graph)

    def test_valid_state(self):
        self.assertEqual(check_state(self.note, self.chunks, self.graph)["history_order"], "passed")


if __name__ == "__main__":
    unittest.main()
