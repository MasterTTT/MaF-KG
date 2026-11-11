import unittest

from scripts.evaluate_heldout_three_sections import overlaps, validate_alignments


class HeldoutEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.section = {"source": "E:/MaF-KG/data/raw/chapter.md", "start_line": 10, "end_line": 20}

    def test_absolute_source_accepts_windows_path_normalization(self):
        evidence = {"source_path": "e:\\maf-kg\\data\\raw\\chapter.md", "start_line": 15, "end_line": 15}
        self.assertTrue(overlaps(evidence, self.section))

    def test_same_filename_in_another_directory_is_not_same_source(self):
        evidence = {"source_path": "E:/other/chapter.md", "start_line": 15, "end_line": 15}
        self.assertFalse(overlaps(evidence, self.section))
        self.assertFalse(overlaps(evidence, self.section, allow_bare_filename=True))

    def test_bare_ku_evidence_filename_requires_explicit_opt_in(self):
        evidence = {"source_path": "chapter.md", "start_line": 15, "end_line": 15}
        self.assertFalse(overlaps(evidence, self.section))
        self.assertTrue(overlaps(evidence, self.section, allow_bare_filename=True))

    def test_outside_scope_alignment_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_alignments([{"prediction_id": "other", "reference_id": "g1", "accepted": True}], {"p1"}, {"g1"})

    def test_missing_explicit_acceptance_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_alignments([{"prediction_id": "p1", "reference_id": "g1"}], {"p1"}, {"g1"})

    def test_one_prediction_cannot_match_two_reference_nodes(self):
        with self.assertRaises(ValueError):
            validate_alignments([
                {"prediction_id": "p1", "reference_id": "g1", "accepted": True},
                {"prediction_id": "p1", "reference_id": "g2", "accepted": True},
            ], {"p1"}, {"g1", "g2"})


if __name__ == "__main__":
    unittest.main()
