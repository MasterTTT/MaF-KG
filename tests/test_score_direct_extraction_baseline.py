import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_direct_extraction_baseline import score_review


class ExplicitScoringTests(unittest.TestCase):
    def setUp(self):
        self.graph = {"nodes": [{"id": "p", "type": "Concept"}], "relations": []}
        self.reference = {"nodes": [{"id": "g", "section": "s", "type": "Concept", "status": "M", "tier": "core"}],
                          "relations_provisional": []}
        self.review = {"gold_reviews": [{"gold_id": "g", "identity_id": "p", "content_ids": ["p"],
                                        "description_complete": True, "conditions_complete": True, "reason": "Source-supported definition."}],
                       "node_reviews": [{"id": "p", "verdict": "valid", "reason": "Explicit definition."}],
                       "relation_reviews": [], "duplicate_groups": [], "false_merges": []}

    def test_explicit_alignment(self):
        result = score_review(self.graph, self.reference, "s", self.review)
        self.assertEqual(result["counts"]["identity_hits"], 1)

    def test_missing_review_rejected(self):
        self.review["node_reviews"] = []
        with self.assertRaises(ValueError):
            score_review(self.graph, self.reference, "s", self.review)

    def test_wrong_type_rejected(self):
        self.graph["nodes"][0]["type"] = "Claim"
        with self.assertRaises(ValueError):
            score_review(self.graph, self.reference, "s", self.review)

    def test_content_can_exist_without_independent_identity(self):
        self.review["gold_reviews"][0]["identity_id"] = None
        self.graph["nodes"][0]["type"] = "Method"
        result = score_review(self.graph, self.reference, "s", self.review)
        self.assertEqual(result["counts"]["identity_hits"], 0)
        self.assertEqual(result["counts"]["content_hits"], 1)

    def test_no_implicit_boolean_verdict(self):
        self.review["gold_reviews"][0]["description_complete"] = 1
        with self.assertRaises(ValueError):
            score_review(self.graph, self.reference, "s", self.review)


if __name__ == "__main__":
    unittest.main()
