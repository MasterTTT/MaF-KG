import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_chunk_boundary_ablation import character_intervals, make_stream, plan, model_payload
from score_chunk_boundary_ablation import score


class BoundaryTests(unittest.TestCase):
    def test_character_windows_ignore_semantic_boundaries(self):
        lines = ["heading", "definition and condition", "conclusion"]
        stream, positions = make_stream(lines, [1, 2, 3])
        windows = character_intervals(len(stream), 10)
        rows = plan(stream, positions, windows, lines, {1, 2, 3}, 0, 0, "character_boundaries")
        self.assertEqual("".join(row["raw_body"] for row in rows), stream)
        self.assertTrue(any(row["fragments"][0]["source_start"] > 0 for row in rows))
        self.assertTrue(all(len(row["raw_body"]) <= 10 for row in rows))

    def test_excluded_source_is_not_in_core_or_context(self):
        lines = ["A", "excluded example", "B"]
        stream, positions = make_stream(lines, [1, 3])
        rows = plan(stream, positions, [(0, len(stream))], lines, {1, 3}, 6, 4, "test")
        self.assertNotIn("excluded", json.dumps(rows))
        self.assertEqual(stream, "A\nB\n")

    def test_formula_candidate_cannot_restore_cut_suffix(self):
        payload = {"chunk": {"heading_path": [], "teaching_block_ids": [], "teaching_blocks": [], "text": "1: x+y"},
                   "source_latex_candidates": [{"candidate_id": "short", "latex": "x+y"}, {"candidate_id": "long", "latex": "x+y=z"}]}
        filtered = model_payload(payload)
        self.assertEqual([r["candidate_id"] for r in filtered["source_latex_candidates"]], ["short"])
        self.assertNotIn("teaching_blocks", filtered["chunk"])
        self.assertEqual(len(payload["source_latex_candidates"]), 2)

    def test_started_or_failed_pair_cannot_be_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            manifest = {"experiment": "chunk_boundary_ablation", "arms": [
                {"name": "teaching_boundaries", "status": "completed"},
                {"name": "character_boundaries", "status": "running"}]}
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be completed"):
                score(directory, directory / "review.json")


if __name__ == "__main__":
    unittest.main()
