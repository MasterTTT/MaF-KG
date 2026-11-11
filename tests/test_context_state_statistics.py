import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_memory_ablation import digest
from summarize_context_state_experiment import summarize


class ContextStatisticsTests(unittest.TestCase):
    def test_incomplete_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "manifest.json").write_text(json.dumps({"arms": [{"status": "running"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "All arms must complete"):
                summarize(directory)

    def test_statistics_count_existing_source_edges_without_quality_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            target = directory / "structured_state"
            (target / "requests").mkdir(parents=True)
            (target / "commits").mkdir()
            graph = {"nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
                     "relations": [{"source_id": "n1", "target_id": "n2"}],
                     "diagnostics": {"api_summary": {}}}
            (target / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
            arm = {"name": "structured_state", "status": "completed", "graph_sha256": digest(target / "graph.json")}
            (directory / "manifest.json").write_text(json.dumps({"topic": "test", "arms": [arm]}), encoding="utf-8")
            (directory / "chunks.json").write_text(json.dumps([{"id": "c1"}]), encoding="utf-8")
            event = {"kind": "relation_added", "source_id": "n1", "target_id": "n2"}
            request = {"legacy_payload": {"node_memory": [{"id": "n1", "short_description": "x" * 200}], "relation_memory": []},
                       "sent_payload": {"graph_state": {"recent_updates": [event]}}}
            commit = {"before": {"nodes": {"n1": {}}}, "events": [event]}
            (target / "requests/c1.json").write_text(json.dumps(request), encoding="utf-8")
            (target / "commits/c1.json").write_text(json.dumps(commit), encoding="utf-8")
            with patch("summarize_context_state_experiment.validate_run"):
                result = summarize(directory)
            self.assertEqual(result["status"], "descriptive_not_semantic_quality")
            measured = result["arms"]["structured_state"]
            self.assertEqual(measured["added_edges_from_preexisting_sources"], 1)
            self.assertEqual(measured["isolated_node_ids"], ["n3"])
            self.assertEqual(measured["chunks"][0]["descriptions_over_100_characters"], 1)
            self.assertEqual(measured["displayed_event_exposures"], {"relation_added": 1})


if __name__ == "__main__":
    unittest.main()
