import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_context_state_replay import (
    AuditError,
    _select_unique_cache,
    audit_arm,
    compare_semantics,
)


class ContextStateReplayTests(unittest.TestCase):
    def test_pending_or_running_arm_without_graph_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for status in ("pending", "running"):
                with self.subTest(status=status):
                    pair_dir = Path(tmp) / status
                    arm_dir = pair_dir / "flat_context"
                    arm_dir.mkdir(parents=True)
                    (pair_dir / "manifest.json").write_text(
                        '{"experiment":"structured_extraction_context_state",'
                        f'"arms":[{{"name":"flat_context","status":"{status}"}}]}}',
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(AuditError, "graph.json is required"):
                        audit_arm(arm_dir)

    def test_failed_arm_with_graph_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            pair_dir = Path(tmp) / "topic"
            arm_dir = pair_dir / "flat_context"
            arm_dir.mkdir(parents=True)
            (arm_dir / "graph.json").write_text("{}", encoding="utf-8")
            manifest = {
                "experiment": "structured_extraction_context_state",
                "arms": [
                    {"name": "flat_context", "status": "failed", "exit_code": 0,
                     "error": "Request prompt or committed history mismatch"},
                    {"name": "structured_state", "status": "pending"},
                ],
            }
            (pair_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(AuditError, "only completed arms"):
                audit_arm(arm_dir)

    def test_missing_cache_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(AuditError, "No cached response"):
                _select_unique_cache(Path(tmp), "input__provider__model__chunk_001__")

    def test_multiple_cache_responses_for_one_chunk_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            prefix = "input__provider__model__chunk_001__"
            (cache_dir / f"{prefix}{'a' * 20}.json").write_text("{}", encoding="utf-8")
            (cache_dir / f"{prefix}{'b' * 20}.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(AuditError, "found 2"):
                _select_unique_cache(cache_dir, prefix)

    def test_evidence_change_is_reported_even_when_identity_is_unchanged(self):
        graph = {
            "nodes": [
                {
                    "id": "Concept:test",
                    "type": "Concept",
                    "name": "test",
                    "aliases": [],
                    "description": "a definition",
                    "evidence": [
                        {
                            "source_path": "frozen.md",
                            "start_line": 1,
                            "end_line": 1,
                            "text": "definition",
                            "chunk_id": "chunk_001",
                            "heading_path": ["Section"],
                            "teaching_block_id": "block_1",
                            "discourse_role": "definition",
                        }
                    ],
                }
            ],
            "relations": [],
            "math_expressions": [],
        }
        changed = copy.deepcopy(graph)
        changed["nodes"][0]["evidence"][0]["text"] = "changed evidence"
        result = compare_semantics(graph, changed)
        self.assertFalse(result["node_evidence_equal"])
        self.assertFalse(result["nodes_equal"])
        self.assertFalse(result["all_semantics_and_evidence_equal"])


if __name__ == "__main__":
    unittest.main()
