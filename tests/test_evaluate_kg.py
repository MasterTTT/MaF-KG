from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from scripts.evaluate_kg import (
    canonical_node_name,
    evaluate,
    evaluate_nodes_against_gold,
    resolve_input,
)


def evidence() -> list[dict[str, object]]:
    return [
        {
            "source_path": "source.md",
            "start_line": 1,
            "end_line": 1,
            "text": "source text",
            "chunk_id": "chunk_001",
            "heading_path": ["Section"],
            "teaching_block_id": "teaching_001",
            "discourse_role": "paragraph",
        }
    ]


class SemanticEvaluatorTests(unittest.TestCase):
    def base_graph(self) -> dict[str, object]:
        return {
            "schema_version": "maf-kg.semantic.v1.4",
            "source_path": "source.md",
            "nodes": [
                {
                    "id": "claim_1",
                    "type": "Claim",
                    "name": "General property",
                    "description": "A reusable property.",
                    "claim_kind": "property",
                    "evidence": evidence(),
                },
                {
                    "id": "concept_1",
                    "type": "Concept",
                    "name": "Concept",
                    "description": "A mathematical concept.",
                    "evidence": evidence(),
                },
            ],
            "relations": [
                {
                    "id": "relation_1",
                    "type": "involves",
                    "source_id": "claim_1",
                    "target_id": "concept_1",
                    "evidence": evidence(),
                }
            ],
            "math_expressions": [
                {
                    "id": "expression_1",
                    "owner_id": "claim_1",
                    "role": "claim_representation",
                    "latex": "x > 0",
                    "evidence": evidence(),
                }
            ],
        }

    def test_semantic_relations_and_expressions_are_counted(self) -> None:
        report = evaluate(self.base_graph(), Path("semantic.json"))

        self.assertEqual(report["counts"]["relationship_count"], 1)
        self.assertEqual(report["counts"]["math_expression_count"], 1)
        self.assertEqual(report["counts"]["connected_component_sizes"], [2])
        self.assertEqual(report["summary"]["severe_issue_count"], 0)

    def test_invalid_signature_and_expression_role_are_reported(self) -> None:
        graph = self.base_graph()
        graph["relations"][0]["source_id"] = "concept_1"
        graph["relations"][0]["target_id"] = "claim_1"
        graph["math_expressions"][0]["role"] = "definition_representation"

        report = evaluate(graph, Path("semantic.json"))

        self.assertEqual(report["issues"]["invalid_relation_signature"]["count"], 1)
        self.assertEqual(report["issues"]["invalid_expression_role"]["count"], 1)
        self.assertFalse(report["summary"]["is_structurally_clean"])

    def test_default_input_discovers_latest_semantic_graph(self) -> None:
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            semantic = directory / "section_语义图_v1_4.json"
            semantic.write_text("{}", encoding="utf-8")

            self.assertEqual(resolve_input(None, str(directory)), semantic)


class GoldNodeEvaluatorTests(unittest.TestCase):
    def evaluate_gold(
        self,
        predicted_nodes: list[dict[str, object]],
        node_groups: list[dict[str, object]],
    ) -> dict[str, object]:
        graph = {"source_path": "source.md", "nodes": predicted_nodes}
        gold = {
            "schema_version": "maf-kg.semantic-gold.v1",
            "graphs": [
                {
                    "source_file": "source.md",
                    "annotation_status": "reviewed",
                    "relation_annotation_status": "pending",
                    "node_groups": node_groups,
                }
            ],
        }
        return evaluate_nodes_against_gold(graph, gold, Path("gold.json"))

    def concept(self, name: str, aliases: list[str] | None = None) -> dict[str, object]:
        return {
            "id": f"concept_{name}",
            "type": "Concept",
            "name": name,
            "aliases": aliases or [],
            "description": "Definition.",
        }

    def test_must_nodes_produce_true_positive_and_false_negative(self) -> None:
        report = self.evaluate_gold(
            [self.concept("映射")],
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [
                        {"gold_id": "mapping", "name": "映射"},
                        {"gold_id": "function", "name": "函数"},
                    ],
                }
            ],
        )

        metrics = report["node_metrics"]
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["false_positive"], 0)
        self.assertEqual(metrics["false_negative"], 1)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(report["true_positive_matches"][0]["gold_id"], "mapping")

    def test_optional_match_is_ignored_by_false_positive_and_false_negative_counts(self) -> None:
        report = self.evaluate_gold(
            [self.concept("映射"), self.concept("变换")],
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [{"gold_id": "mapping", "name": "映射"}],
                },
                {
                    "requirement": "O",
                    "type": "Concept",
                    "items": [{"gold_id": "transformation", "name": "变换"}],
                },
            ],
        )

        metrics = report["node_metrics"]
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["false_positive"], 0)
        self.assertEqual(metrics["false_negative"], 0)
        self.assertEqual(metrics["optional_matched"], 1)

    def test_forbidden_match_is_a_false_positive(self) -> None:
        report = self.evaluate_gold(
            [self.concept("映射"), self.concept("单调增加")],
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [{"gold_id": "mapping", "name": "映射"}],
                },
                {
                    "requirement": "X",
                    "type": "Concept",
                    "items": [{"gold_id": "monotone_increasing", "name": "单调增加"}],
                },
            ],
        )

        self.assertEqual(report["node_metrics"]["false_positive"], 1)
        self.assertEqual(report["false_positives"][0]["reason"], "explicitly forbidden")

    def test_wrong_claim_kind_is_both_false_positive_and_false_negative(self) -> None:
        predicted = {
            "id": "claim_uniqueness",
            "type": "Claim",
            "claim_kind": "criterion",
            "name": "极限唯一性",
            "aliases": [],
            "description": "A limit is unique.",
        }
        report = self.evaluate_gold(
            [predicted],
            [
                {
                    "requirement": "M",
                    "type": "Claim",
                    "claim_kind": "property",
                    "items": [{"gold_id": "limit_uniqueness", "name": "极限唯一性"}],
                }
            ],
        )

        metrics = report["node_metrics"]
        self.assertEqual(metrics["true_positive"], 0)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 1)

    def test_predicted_alias_and_gold_accepted_name_can_match(self) -> None:
        report = self.evaluate_gold(
            [self.concept("bijection", aliases=["双射"])],
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [
                        {
                            "gold_id": "bijection",
                            "name": "一一映射",
                            "accepted_names": ["双射", "一一对应"],
                        }
                    ],
                }
            ],
        )

        metrics = report["node_metrics"]
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["false_positive"], 0)
        self.assertEqual(metrics["false_negative"], 0)

    def test_mathematical_signs_remain_identity_significant(self) -> None:
        self.assertNotEqual(canonical_node_name("x→∞"), canonical_node_name("x→-∞"))
        self.assertNotEqual(canonical_node_name("x→+∞"), canonical_node_name("x→-∞"))


if __name__ == "__main__":
    unittest.main()
