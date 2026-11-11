import copy
import sys
import unittest
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_structural_context_ablation import REMOVED_KEYS, transform
from score_structural_context_ablation import score, forbidden_metadata, validate_request_audit
from run_memory_ablation import digest


class StructuralContextTests(unittest.TestCase):
    def test_plural_metadata_keys_are_detected_recursively(self):
        self.assertTrue(forbidden_metadata([{"nested": {"teaching_block_ids": []}}]))
        self.assertTrue(forbidden_metadata({"outer": [{"teaching_blocks": []}]}))
        self.assertFalse(forbidden_metadata({"text": "discourse_role", "latex": []}))

    def test_request_prompt_and_memory_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            prompt = directory / "snapshot/prompts/extraction_system.txt"
            prompt.parent.mkdir(parents=True)
            prompt.write_text("frozen", encoding="utf-8")
            request = directory / "hidden_structure/requests/c1.json"
            request.parent.mkdir(parents=True)
            payload = {"node_memory": [], "relation_memory": [], "defined_term_hints": [], "source_latex_candidates": []}
            row = {"system_prompt_sha256": digest(prompt), "sent_payload": payload}
            graph = {"diagnostics": {"chunk_summaries": [{"chunk_id": "c1", "memory_node_count": 0, "memory_relation_count": 0}]}}
            request.write_text(json.dumps(row), encoding="utf-8")
            validate_request_audit(directory, "hidden_structure", graph)
            row["system_prompt_sha256"] = "changed"
            request.write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "prompt hash"):
                validate_request_audit(directory, "hidden_structure", graph)
            row["system_prompt_sha256"] = digest(prompt)
            row["sent_payload"]["node_memory"] = [{"id": "unexpected"}]
            request.write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "memory differs"):
                validate_request_audit(directory, "hidden_structure", graph)

    def test_only_metadata_is_removed(self):
        payload = {"chunk": {"heading_path": ["h"], "teaching_block_ids": ["b"],
                   "teaching_blocks": [{"discourse_role": "theorem"}], "text": "1: theorem",
                   "start_line": 1}, "node_memory": [{"id": "n"}], "source_latex_candidates": []}
        original = copy.deepcopy(payload)
        hidden = transform(payload, True)
        self.assertEqual(payload, original)
        self.assertEqual(hidden["chunk"], {"text": "1: theorem", "start_line": 1})
        self.assertEqual(hidden["node_memory"], payload["node_memory"])
        for key in REMOVED_KEYS:
            hidden["chunk"][key] = original["chunk"][key]
        self.assertEqual(hidden, original)

    def test_visible_arm_is_unchanged_copy(self):
        payload = {"chunk": {"text": "1: text"}}
        self.assertEqual(transform(payload, False), payload)
        self.assertIsNot(transform(payload, False), payload)

    def test_missing_frozen_field_fails(self):
        with self.assertRaises(KeyError):
            transform({"chunk": {}}, True)

    def test_incomplete_pair_cannot_be_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            manifest = {"experiment": "explicit_structural_metadata_ablation", "arms": [
                {"name": "visible_structure", "status": "completed"},
                {"name": "hidden_structure", "status": "failed"}]}
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be completed"):
                score(directory, directory / "review.json")

    def test_other_experiment_cannot_be_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "manifest.json").write_text(json.dumps({"experiment": "memory"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Wrong experiment"):
                score(directory, directory / "review.json")


if __name__ == "__main__":
    unittest.main()
