from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_cross_topic_memory_ablation import score, validate_alignments, validate_coverage


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class CrossTopicScorerTests(unittest.TestCase):
    def _completed_run(self, root: Path, review_path: Path) -> Path:
        run_dir = root / "differential"
        gold = {
            "topics": [{
                "id": "differential",
                "nodes": [
                    {"gold_id": "g_core", "type": "Concept", "requirement": "M", "tier": "core"},
                    {"gold_id": "g_detail", "type": "Claim", "requirement": "M", "tier": "detail"},
                    {"gold_id": "g_optional", "type": "Concept", "requirement": "O", "tier": "support"},
                ],
                "relations": [
                    {"gold_id": "gr_core", "source_gold_id": "g_core", "target_gold_id": "g_detail",
                     "type": "uses", "requirement": "M", "tier": "core"},
                    {"gold_id": "gr_detail", "source_gold_id": "g_detail", "target_gold_id": "g_core",
                     "type": "derived_from", "requirement": "M", "tier": "detail"},
                    {"gold_id": "gr_optional", "source_gold_id": "g_optional", "target_gold_id": "g_detail",
                     "type": "uses", "requirement": "O", "tier": "support"},
                ],
            }]
        }
        gold_path = run_dir / "snapshot" / "protocol" / "cross_topic_gold_v1.json"
        write_json(gold_path, gold)
        chunks = [{"id": "c1", "start_line": 1, "end_line": 5},
                  {"id": "c2", "start_line": 6, "end_line": 10}]
        chunks_path = run_dir / "chunks.json"
        write_json(chunks_path, chunks)
        common = {"provider": "deepseek", "model": "unit-model"}
        config_template = {
            "provider": "deepseek", "model_requested": "unit-model", "thinking": "enabled",
            "reasoning_effort_sent": "low", "chunk_strategy": "teaching-units",
            "resume_enabled": False,
        }
        graph_hashes = {}
        for name, node_budget, relation_budget in (("visible_memory", 12000, 6000),
                                                    ("no_visible_memory", 2, 2)):
            visible = name == "visible_memory"
            graph = {
                "model": "unit-model",
                "nodes": [
                    {"id": "p_core", "type": "Concept"},
                    {"id": "p_detail", "type": "Claim"},
                    {"id": "p_extra", "type": "Claim"},
                    {"id": "p_optional", "type": "Concept"},
                ],
                "relations": [
                    {"id": "r_core", "source_id": "p_core", "target_id": "p_detail", "type": "uses"},
                    {"id": "r_detail", "source_id": "p_detail", "target_id": "p_core", "type": "derived_from"},
                    {"id": "r_unaligned", "source_id": "p_extra", "target_id": "p_core", "type": "uses"},
                ],
                "diagnostics": {
                    "run_config": {**config_template, "memory_node_bytes": node_budget,
                                   "memory_relation_bytes": relation_budget},
                    "chunk_summaries": [
                        {"chunk_id": "c1", "memory_node_count": 0, "memory_relation_count": 0,
                         "decisions": []},
                        {"chunk_id": "c2", "memory_node_count": int(visible),
                         "memory_relation_count": int(visible), "decisions": []},
                    ],
                    "api_summary": {"calls": 2},
                    "audit": {"merge_count": 0},
                },
            }
            graph_path = run_dir / name / "graph.json"
            write_json(graph_path, graph)
            graph_hashes[name] = digest(graph_path)
            cache = run_dir / name / "runtime" / "node_cache" / "semantic_layer_v1_7_0"
            for chunk in chunks:
                write_json(cache / f"input__{chunk['id']}__test.json", {"node_actions": []})

        arms = [
            {"name": name, "status": "completed", "memory_node_bytes": node_budget,
             "memory_relation_bytes": relation_budget, "graph_sha256": graph_hashes[name]}
            for name, node_budget, relation_budget in (("visible_memory", 12000, 6000),
                                                        ("no_visible_memory", 2, 2))
        ]
        manifest = {
            "experiment": "cross_topic_model_visible_memory", "topic": "differential",
            "common": common, "chunk_count": len(chunks), "arms": arms,
            "gold_sha256": digest(gold_path), "gold_snapshot": str(gold_path),
            "artifacts": [
                {"snapshot": str(gold_path), "sha256": digest(gold_path)},
                {"snapshot": str(chunks_path), "sha256": digest(chunks_path)},
            ],
        }
        write_json(run_dir / "manifest.json", manifest)
        review = {
            "reviewer": "unit-test-reviewer", "gold_sha256": digest(gold_path), "arms": {},
        }
        for name in ("visible_memory", "no_visible_memory"):
            review["arms"][name] = {
                "graph_sha256": graph_hashes[name],
                "nodes": [
                    {"id": "p_core", "verdict": "valid", "gold_id": "g_core", "reason": "identity"},
                    {"id": "p_detail", "verdict": "valid", "gold_id": "g_detail", "reason": "identity"},
                    {"id": "p_extra", "verdict": "uncertain", "reason": "reviewed"},
                    {"id": "p_optional", "verdict": "valid", "gold_id": "g_optional", "reason": "identity"},
                ],
                "relations": [
                    {"id": "r_core", "verdict": "valid", "reason": "exact directed edge"},
                    {"id": "r_detail", "verdict": "valid", "reason": "exact directed edge"},
                    {"id": "r_unaligned", "verdict": "valid", "reason": "reviewed"},
                ],
                "coverage": [
                    {"gold_id": "g_core", "prediction_ids": ["p_core"], "covered": True,
                     "description_complete": True, "reason": "content present"},
                    {"gold_id": "g_detail", "prediction_ids": ["p_detail", "p_extra"], "covered": True,
                     "description_complete": False, "reason": "group represented; condition incomplete"},
                ],
                "duplicate_groups": [["p_detail", "p_extra"]],
                "false_merge_ids": ["p_core"],
                "field_issues": [],
            }
        write_json(review_path, review)
        return run_dir

    def test_missing_required_coverage_is_rejected(self):
        gold = {"g1": {"type": "Concept", "requirement": "M", "tier": "core"}}
        with self.assertRaisesRegex(ValueError, "Missing required Gold coverage"):
            validate_coverage([], gold, {})

    def test_malformed_coverage_decision_is_rejected(self):
        gold = {"g1": {"type": "Concept", "requirement": "M", "tier": "core"}}
        row = {"gold_id": "g1", "prediction_ids": [], "covered": True,
               "description_complete": False, "reason": "reviewed"}
        with self.assertRaisesRegex(ValueError, "at least one prediction"):
            validate_coverage([row], gold, {})

    def test_prediction_decision_requires_verdict_and_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            review_path = Path(folder) / "review.json"
            run_dir = self._completed_run(Path(folder), review_path)
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["arms"]["visible_memory"]["nodes"][0]["reason"] = ""
            write_json(review_path, review)
            with self.assertRaisesRegex(ValueError, "explicit verdict and reason"):
                score(run_dir, review_path)

    def test_one_to_one_alignment_rejects_duplicate_gold_ids(self):
        decisions = [
            {"id": "p1", "gold_id": "g1", "verdict": "valid"},
            {"id": "p2", "gold_id": "g1", "verdict": "valid"},
        ]
        gold = {"g1": {"type": "Concept", "requirement": "M", "tier": "core"}}
        predictions = {"p1": {"type": "Concept"}, "p2": {"type": "Concept"}}
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            validate_alignments(decisions, gold, predictions)

    def test_required_tiers_and_optional_alignments_are_reported_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            review_path = Path(folder) / "review.json"
            run_dir = self._completed_run(Path(folder), review_path)
            result = score(run_dir, review_path)
        arm = result["arms"]["visible_memory"]
        coverage = arm["required_content_coverage"]
        self.assertEqual((coverage["core"]["covered"], coverage["core"]["total"],
                          coverage["core"]["complete_descriptions"]), (1, 1, 1))
        self.assertEqual((coverage["detail"]["covered"], coverage["detail"]["total"],
                          coverage["detail"]["complete_descriptions"]), (1, 1, 0))
        self.assertEqual(arm["exact_entity_hits"]["core"]["hits"], 1)
        self.assertEqual(arm["exact_entity_hits"]["detail"]["hits"], 1)
        self.assertEqual(arm["optional_node_hits"]["hits"], 1)
        self.assertNotIn("total", arm["optional_node_hits"])
        self.assertEqual(coverage["core"]["total"], 1)
        self.assertEqual(coverage["detail"]["total"], 1)
        self.assertEqual(arm["strict_M_relation_hits"]["core"]["hits"], 1)
        self.assertEqual(arm["strict_M_relation_hits"]["detail"]["hits"], 1)
        self.assertEqual(arm["isolated_node_count"], 1)
        self.assertEqual(arm["action_audit"]["proposed_create"], 0)
        self.assertEqual(arm["api_summary"], {"calls": 2})

    def test_incomplete_run_cannot_be_scored(self):
        with tempfile.TemporaryDirectory() as folder:
            review_path = Path(folder) / "review.json"
            run_dir = self._completed_run(Path(folder), review_path)
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["arms"][0]["status"] = "running"
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "Both run arms must be completed"):
                score(run_dir, review_path)


if __name__ == "__main__":
    unittest.main()
