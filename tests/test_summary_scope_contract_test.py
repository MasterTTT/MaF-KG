import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_reading_summary_test import EXTRACTION_CONTRACT
from run_summary_scope_contract_test import REPLACEMENTS, SOURCE_SCOPE_CONTRACT, transform_prompt


class ScopeContractTests(unittest.TestCase):
    def test_only_explicit_clauses_change(self):
        original = "\n".join(REPLACEMENTS) + "\nUnchanged admission and output rules."
        transformed = transform_prompt(EXTRACTION_CONTRACT + original)
        self.assertTrue(transformed.startswith(SOURCE_SCOPE_CONTRACT))
        self.assertTrue(transformed.endswith("Unchanged admission and output rules."))
        for old, new in REPLACEMENTS.items():
            self.assertNotIn(old, transformed)
            self.assertIn(new, transformed)

    def test_missing_clause_fails_without_fallback(self):
        with self.assertRaises(ValueError):
            transform_prompt(EXTRACTION_CONTRACT + "unknown prompt")


if __name__ == "__main__":
    unittest.main()
