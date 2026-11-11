import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_seeded_memory_experiment import audit_stage


class IncrementalAuditTests(unittest.TestCase):
    def fixture(self, raw_action, merged_action, exposed):
        node = {"id": "old", "name": "原函数", "description": "定义", "evidence": []}
        seed = {"nodes": [node], "relations": []}
        graph = {"nodes": [node], "relations": [], "diagnostics": {"api_summary": {}, "memory_decisions": [
            {"chunk_id": "c1", "ref_id": "r1", "node_id": "old", "action": merged_action,
             "description_action": "kept", "mention": "原函数", "reason": "重现"}]}}
        action = {"action": raw_action, "ref_id": "r1", "existing_id": "old",
                  "mention": "原函数", "reason": "重现", "evidence": {}}
        return seed, graph, [{"id": "c1"}], {"c1": {"node_actions": [action]}}, {
            "c1": {"node_memory": [{"id": "old"}] if exposed else []}}, {"old"}

    def test_hidden_create_is_not_explicit_reuse(self):
        result = audit_stage(*self.fixture("create", "semantic_reuse", False))
        self.assertEqual(result["counts"]["raw_create"], 1)
        self.assertEqual(result["counts"]["create_collapse_initial_seed"], 1)
        self.assertNotIn("raw_reuse", result["counts"])

    def test_explicit_reuse_needs_exposure(self):
        result = audit_stage(*self.fixture("reuse", "reuse", True))
        self.assertEqual(result["counts"]["explicit_reuse_initial_seed"], 1)
        with self.assertRaisesRegex(ValueError, "provided memory"):
            audit_stage(*self.fixture("reuse", "reuse", False))

    def test_field_loss_is_reported(self):
        args = list(self.fixture("reuse", "reuse", True))
        args[0]["nodes"] = [{"id": "old", "name": "原函数", "description": "完整定义", "evidence": [
            {"source_path": "prior.md", "start_line": 1, "end_line": 2, "text": "旧证据"}]}]
        result = audit_stage(*args)
        self.assertEqual(result["prior_node_field_changes"][0]["changed_fields"], ["description"])
        self.assertEqual(result["lost_prior_node_evidence"][0]["count"], 1)

    def test_previous_section_identity_is_separate_from_initial_seed(self):
        args = list(self.fixture("reuse", "reuse", True))
        args[-1] = set()
        result = audit_stage(*args)
        self.assertEqual(result["counts"]["explicit_reuse_previous_section"], 1)

    def test_new_identity_is_not_a_collapsed_create(self):
        args = list(self.fixture("create", "create", False))
        args[0] = {"nodes": [], "relations": []}
        args[-1] = set()
        result = audit_stage(*args)
        self.assertEqual(result["new_node_count"], 1)
        self.assertNotIn("deterministic_create_collapse", result["counts"])

    def test_direct_endpoint_use_is_separate_from_node_reuse(self):
        args = list(self.fixture("reuse", "reuse", True))
        args[3]["c1"]["relations"] = [{"source_ref": "r1", "target_ref": "old",
                                         "relation_type": "involves", "evidence": {}}]
        result = audit_stage(*args)
        self.assertEqual(result["counts"]["raw_reuse"], 1)
        self.assertEqual(result["counts"]["direct_memory_endpoint_occurrences"], 1)


if __name__ == "__main__":
    unittest.main()
