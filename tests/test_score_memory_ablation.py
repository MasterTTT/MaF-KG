import sys
import unittest
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_memory_ablation import action_audit, validate_decisions


class ExplicitReviewTests(unittest.TestCase):
    def test_pre_merge_recreation_is_counted_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "no_visible_memory/runtime/node_cache/semantic_layer_v1_7_0"
            cache.mkdir(parents=True)
            chunks = [{"id": "c1"}, {"id": "c2"}]
            summaries = []
            for chunk in chunks:
                (cache / f"input__{chunk['id']}__hash.json").write_text(
                    json.dumps({"node_actions": [{"ref_id": "local", "action": "create", "name": "Sequence"}]}),
                    encoding="utf-8")
                summaries.append({"decisions": [{"ref_id": "local", "node_id": "canonical"}]})
            audit = action_audit(root, "no_visible_memory", chunks, summaries)
            self.assertEqual(audit["proposed_create"], 2)
            self.assertEqual(audit["proposed_explicit_reuse"], 0)
            self.assertEqual(audit["create_against_prior_canonical_id"], 1)
            (cache / "input__c1__secondhash.json").write_text('{}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one isolated"):
                action_audit(root, "no_visible_memory", chunks, summaries)

    def test_complete_review_is_required(self):
        with self.assertRaisesRegex(ValueError, "cover every"):
            validate_decisions([], [{"id": "a"}], {"valid"})
        with self.assertRaisesRegex(ValueError, "cover every"):
            validate_decisions([{"id": "a", "verdict": "valid", "reason": "reviewed"}] * 2,
                               [{"id": "a"}], {"valid"})

    def test_explicit_verdict_and_reason_are_required(self):
        with self.assertRaisesRegex(ValueError, "explicit verdict"):
            validate_decisions([{"id": "a", "verdict": "valid", "reason": ""}], [{"id": "a"}], {"valid"})
        with self.assertRaisesRegex(ValueError, "explicit verdict"):
            validate_decisions([{"id": "a", "verdict": "auto_accept", "reason": "reviewed"}], [{"id": "a"}], {"valid"})
        validate_decisions([{"id": "a", "verdict": "valid", "reason": "reviewed"}], [{"id": "a"}], {"valid"})


if __name__ == "__main__":
    unittest.main()
