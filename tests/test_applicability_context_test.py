import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_applicability_context_test import cited_excerpt, paired_payloads, response_audit
from audit_applicability_context_test import audit_response


class ApplicabilityContextTests(unittest.TestCase):
    def test_only_supplement_changes(self):
        original = {"graph_state": {"nodes": [{"id": "n1"}]}, "current_evidence": {"text": "source"}}
        pair = paired_payloads(original, [{"text": "context"}])
        self.assertNotIn("source_applicability_context", original)
        pair["control"].pop("source_applicability_context")
        pair["source_context"].pop("source_applicability_context")
        self.assertEqual(pair["control"], pair["source_context"])

    def test_exact_source_and_range(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.md"
            source.write_text("first\nsecond\nthird\n", encoding="utf-8")
            row = cited_excerpt(source, 2, 3)
            self.assertEqual(row["text"], "L2: second\nL3: third")
            with self.assertRaises(ValueError):
                cited_excerpt(source, 0, 1)

    def test_invalid_reuse_is_visible(self):
        audit = response_audit({"node_actions": [{"action": "reuse", "existing_id": "outside"}]},
                               {"graph_state": {"nodes": [{"id": "inside"}]}})
        self.assertEqual(len(audit["problems"]), 1)

    def test_reference_audit_checks_real_output_fields(self):
        payload = {"graph_state": {"nodes": [{"id": "inside"}]},
                   "current_evidence": {"evidence_line_numbers": [1, 2],
                                        "source_latex_candidates": [{"candidate_id": "latex1"}]}}
        response = {"node_actions": [{"ref_id": "r1", "action": "reuse",
                    "existing_id": "inside", "evidence": {"start_line": 1, "end_line": 1}}],
                    "relations": [], "expressions": [{"owner_ref": "r1",
                    "latex_candidate_id": "latex1", "evidence": {"start_line": 2, "end_line": 2}}]}
        self.assertEqual(audit_response(response, payload), [])
        response["node_actions"][0]["existing_id"] = "outside"
        response["expressions"][0]["evidence"]["end_line"] = 3
        self.assertEqual(set(audit_response(response, payload)),
                         {"reuse_outside_memory", "evidence_outside_current_region"})


if __name__ == "__main__":
    unittest.main()
