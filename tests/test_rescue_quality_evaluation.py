import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from score_rescue_finite_reference import ALIGNMENTS
from summarize_prospective_quality_reviews import score_graph


class RescueQualityTests(unittest.TestCase):
    def test_exactly_five_missing_cells_selected(self):
        self.assertEqual(len(ALIGNMENTS), 5)
        self.assertEqual(sum(k[0] == 'glm-5.3-flash' for k in ALIGNMENTS), 4)
        self.assertEqual(sum(k[0] == 'mimo-v2.6-flash' for k in ALIGNMENTS), 1)

    def test_formula_content_does_not_override_wrong_type(self):
        ref = {'nodes': [{'id': 'N1', 'section': 's', 'status': 'M', 'type': 'Claim', 'name': 'formula'}],
               'relations_provisional': []}
        graph = {'nodes': [{'id': 'pred', 'type': 'Concept'}], 'relations': []}
        result = score_graph(ref, graph, 's', [
            {'item_id': 'N1', 'identity_id': 'pred', 'identity_status': 'wrong_type', 'content_ids': ['pred']}], [])
        self.assertEqual(result['mandatory_typed_identity_hits'], 0)
        self.assertEqual(result['mandatory_typed_identity_total'], 1)

    def test_missing_total_concept_cannot_be_inferred_from_family_nodes(self):
        ref = {'nodes': [{'id': 'N1', 'section': 's', 'status': 'M', 'type': 'Concept', 'name': 'total'}],
               'relations_provisional': []}
        graph = {'nodes': [{'id': 'family', 'type': 'Concept'}], 'relations': []}
        result = score_graph(ref, graph, 's', [{'item_id': 'N1', 'identity_id': None,
                                               'identity_status': 'missing', 'content_ids': []}], [])
        self.assertEqual(result['mandatory_typed_identity_hits'], 0)

    def test_schema_valid_involves_does_not_count_as_derived_from(self):
        ref = {'nodes': [{'id': i, 'section': 's', 'status': 'M', 'type': 'Claim', 'name': i} for i in ['A','B']],
               'relations_provisional': [{'id': 'R1', 'section': 's', 'start': 'A', 'end': 'B', 'type': 'derived_from'}]}
        graph = {'nodes': [{'id': i, 'type': 'Claim'} for i in ['a','b']],
                 'relations': [{'id': 'r', 'source_id': 'a', 'target_id': 'b', 'type': 'involves'}]}
        rows = [{'item_id': i, 'identity_id': p, 'identity_status': 'matched'} for i,p in [('A','a'),('B','b')]]
        result = score_graph(ref, graph, 's', rows, [{'reference_id':'R1'}])
        self.assertEqual(result['mandatory_typed_identity_hits'], 2)
        self.assertEqual(result['exact_finite_relation_hits'], 0)


if __name__ == '__main__':
    unittest.main()
