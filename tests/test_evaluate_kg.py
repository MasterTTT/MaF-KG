from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from scripts.evaluate_kg import (
    _gold_alignment_lookup,
    canonical_node_name,
    evaluate,
    evaluate_nodes_against_gold,
    evaluate_relation_stability,
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

    def test_chunk_merge_failure_breaks_structural_cleanliness(self) -> None:
        graph = self.base_graph()
        graph["diagnostics"] = {
            "chunk_summaries": [
                {
                    "chunk_id": "chunk_001",
                    "merge_failure": {
                        "chunk_id": "chunk_001",
                        "item_kind": "relation",
                        "item_ref": "c1 -[involves]-> ghost",
                        "reason": "relation endpoint not found in chunk nodes",
                    },
                    "raw_response": {"nodes": []},
                    "created_node_count": 0,
                }
            ]
        }

        report = evaluate(graph, Path("semantic.json"))

        self.assertEqual(report["issues"]["chunk_merge_failure"]["count"], 1)
        self.assertFalse(report["summary"]["is_structurally_clean"])
        self.assertGreater(report["summary"]["severe_issue_count"], 0)

    def test_legacy_skipped_items_and_identity_conflicts_break_cleanliness(self) -> None:
        graph = self.base_graph()
        graph["diagnostics"] = {
            "chunk_summaries": [
                {
                    "chunk_id": "chunk_001",
                    "identity_conflicts": [
                        {
                            "conflict_kind": "model_decision_conflict",
                            "proposed_name": "极限唯一性",
                            "existing_name": "极限的唯一性",
                            "reason": "conflicts with an earlier node",
                        }
                    ],
                    "skipped_items": [{"item_kind": "node_action", "item_ref": "c2"}],
                }
            ]
        }

        report = evaluate(graph, Path("semantic.json"))

        self.assertEqual(report["issues"]["chunk_identity_conflict"]["count"], 1)
        self.assertEqual(report["issues"]["chunk_skipped_item"]["count"], 1)
        self.assertFalse(report["summary"]["is_structurally_clean"])

    def test_pending_unlinked_is_a_warning_not_a_severe_issue(self) -> None:
        graph = self.base_graph()
        graph["diagnostics"] = {
            "chunk_summaries": [
                {"chunk_id": "chunk_001", "pending_node_refs": ["concept_1"]}
            ]
        }

        report = evaluate(graph, Path("semantic.json"))

        self.assertEqual(report["issues"]["chunk_pending_unlinked"]["count"], 1)
        self.assertTrue(report["summary"]["is_structurally_clean"])


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


class GoldAlignmentLookupTests(unittest.TestCase):
    def gold_items(self, *items: dict[str, object]) -> list[dict[str, object]]:
        return list(items)

    def test_m_wins_over_o_on_shared_canonical_key(self) -> None:
        items = self.gold_items(
            {"requirement": "O", "gold_id": "opt_mapping", "type": "Concept", "claim_kind": None, "name": "映射"},
            {"requirement": "M", "gold_id": "mapping", "type": "Concept", "claim_kind": None, "name": "映射"},
        )
        lookup = _gold_alignment_lookup(items)
        key = ("Concept", None, canonical_node_name("映射"))
        self.assertEqual(lookup[key], "mapping")

    def test_m_wins_over_o_via_accepted_names_collision(self) -> None:
        items = self.gold_items(
            {"requirement": "O", "gold_id": "opt_alias", "type": "Concept", "claim_kind": None, "name": "可选概念", "accepted_names": ["映射"]},
            {"requirement": "M", "gold_id": "mapping", "type": "Concept", "claim_kind": None, "name": "映射"},
        )
        lookup = _gold_alignment_lookup(items)
        key = ("Concept", None, canonical_node_name("映射"))
        self.assertEqual(lookup[key], "mapping")

    def test_lookup_is_input_order_independent(self) -> None:
        base = [
            {"requirement": "M", "gold_id": "mapping", "type": "Concept", "claim_kind": None, "name": "映射"},
            {"requirement": "O", "gold_id": "opt_func", "type": "Concept", "claim_kind": None, "name": "函数"},
        ]
        lookup_a = _gold_alignment_lookup(list(base))
        lookup_b = _gold_alignment_lookup(list(reversed(base)))
        self.assertEqual(lookup_a, lookup_b)

    def test_x_items_never_enter_lookup(self) -> None:
        items = self.gold_items(
            {"requirement": "M", "gold_id": "mapping", "type": "Concept", "claim_kind": None, "name": "映射"},
            {"requirement": "X", "gold_id": "forbidden", "type": "Concept", "claim_kind": None, "name": "单调增加"},
        )
        lookup = _gold_alignment_lookup(items)
        key = ("Concept", None, canonical_node_name("单调增加"))
        self.assertNotIn(key, lookup)

    def test_two_m_items_with_same_key_raise(self) -> None:
        items = self.gold_items(
            {"requirement": "M", "gold_id": "mapping_a", "type": "Concept", "claim_kind": None, "name": "映射"},
            {"requirement": "M", "gold_id": "mapping_b", "type": "Concept", "claim_kind": None, "name": "映 射"},
        )
        with self.assertRaises(ValueError) as cm:
            _gold_alignment_lookup(items)
        self.assertIn("mapping_a", str(cm.exception))
        self.assertIn("mapping_b", str(cm.exception))

    def test_two_o_items_with_same_key_raise(self) -> None:
        items = self.gold_items(
            {"requirement": "O", "gold_id": "opt_a", "type": "Concept", "claim_kind": None, "name": "变换"},
            {"requirement": "O", "gold_id": "opt_b", "type": "Concept", "claim_kind": None, "name": "变 换"},
        )
        with self.assertRaises(ValueError) as cm:
            _gold_alignment_lookup(items)
        self.assertIn("opt_a", str(cm.exception))
        self.assertIn("opt_b", str(cm.exception))


class RelationStabilityTests(unittest.TestCase):
    def gold(
        self, groups: list[dict[str, object]], relation_status: str = "pending"
    ) -> dict[str, object]:
        return {
            "schema_version": "maf-kg.semantic-gold.v1",
            "graphs": [
                {
                    "source_file": "section.md",
                    "annotation_status": "reviewed",
                    "relation_annotation_status": relation_status,
                    "node_groups": groups,
                }
            ],
        }

    def graph(
        self, nodes: list[dict[str, object]], relations: list[dict[str, object]]
    ) -> dict[str, object]:
        return {
            "schema_version": "maf-kg.semantic.v1.5.9",
            "source_path": "data/section.md",
            "nodes": nodes,
            "relations": relations,
        }

    def node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        claim_kind: str | None = None,
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "id": node_id,
            "type": node_type,
            "name": name,
            "aliases": [],
        }
        if claim_kind:
            item["claim_kind"] = claim_kind
        return item

    def relation(self, relation_id: str, source_id: str, target_id: str) -> dict[str, object]:
        return {
            "id": relation_id,
            "type": "defined_in_terms_of",
            "source_id": source_id,
            "target_id": target_id,
        }

    def mapping_function_gold(self) -> dict[str, object]:
        return self.gold(
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [
                        {
                            "gold_id": "mapping",
                            "name": "映射",
                            "accepted_names": ["映射函数"],
                        },
                        {"gold_id": "function", "name": "函数"},
                    ],
                }
            ]
        )

    def test_naming_drift_stays_in_raw_but_not_in_gold_aligned_jaccard(self) -> None:
        gold = self.mapping_function_gold()
        run1 = self.graph(
            [self.node("m1", "Concept", "映射"), self.node("f1", "Concept", "函数")],
            [self.relation("r1", "m1", "f1")],
        )
        run2 = self.graph(
            [self.node("m2", "Concept", "映射函数"), self.node("f2", "Concept", "函数")],
            [self.relation("r2", "m2", "f2")],
        )

        report = evaluate_relation_stability([run1, run2], gold, Path("gold.json"))

        pair = report["pairwise"][0]
        self.assertEqual(pair["raw_name_jaccard"], 0.0)
        self.assertEqual(pair["gold_aligned_jaccard"], 1.0)
        self.assertEqual(report["stable_edges"]["raw_name"], [])
        self.assertEqual(
            report["stable_edges"]["gold_aligned"],
            [("mapping", "defined_in_terms_of", "function")],
        )

    def test_relation_with_unmapped_endpoint_is_dropped_from_aligned_set(self) -> None:
        gold = self.mapping_function_gold()
        run = self.graph(
            [
                self.node("m1", "Concept", "映射"),
                self.node("f1", "Concept", "函数"),
                self.node("t1", "Concept", "变换"),
            ],
            [self.relation("r1", "m1", "f1"), self.relation("r2", "t1", "f1")],
        )

        report = evaluate_relation_stability([run], gold, Path("gold.json"))

        run_report = report["runs"][0]
        self.assertEqual(run_report["relation_count"], 2)
        self.assertEqual(run_report["raw_edge_count"], 2)
        self.assertEqual(run_report["aligned_edge_count"], 1)
        self.assertEqual(run_report["dropped_relation_count"], 1)
        self.assertEqual(
            run_report["dropped_relations"][0]["reason"],
            "endpoint without a reviewed gold id",
        )
        self.assertEqual(report["edge_union_counts"]["gold_aligned"], 1)

    def test_stable_edges_are_intersection_across_all_runs(self) -> None:
        gold = self.gold(
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [
                        {"gold_id": "mapping", "name": "映射"},
                        {"gold_id": "function", "name": "函数"},
                        {"gold_id": "set", "name": "集合"},
                    ],
                }
            ]
        )

        def run(i: int) -> dict[str, object]:
            relations = [self.relation(f"e1_{i}", f"m{i}", f"f{i}")]
            if i == 3:
                relations.append(
                    {
                        "id": f"e2_{i}",
                        "type": "special_case_of",
                        "source_id": f"m{i}",
                        "target_id": f"s{i}",
                    }
                )
            return self.graph(
                [
                    self.node(f"m{i}", "Concept", "映射"),
                    self.node(f"f{i}", "Concept", "函数"),
                    self.node(f"s{i}", "Concept", "集合"),
                ],
                relations,
            )

        report = evaluate_relation_stability([run(1), run(2), run(3)], gold, Path("gold.json"))

        self.assertEqual(len(report["pairwise"]), 3)
        self.assertEqual(
            report["stable_edges"]["gold_aligned"],
            [("mapping", "defined_in_terms_of", "function")],
        )
        self.assertEqual(report["edge_union_counts"]["gold_aligned"], 2)
        self.assertEqual(
            {pair["gold_aligned_jaccard"] for pair in report["pairwise"]},
            {1.0, 0.5},
        )

    def test_relation_metrics_stays_null_when_relations_not_annotated(self) -> None:
        gold = self.mapping_function_gold()
        run = self.graph(
            [self.node("m1", "Concept", "映射"), self.node("f1", "Concept", "函数")],
            [self.relation("r1", "m1", "f1")],
        )

        report = evaluate_relation_stability([run], gold, Path("gold.json"))

        self.assertIsNone(report["relation_metrics"])
        self.assertEqual(report["relation_annotation_status"], "pending")

    def test_claim_kind_mismatch_prevents_alignment(self) -> None:
        gold = self.gold(
            [
                {
                    "requirement": "M",
                    "type": "Claim",
                    "claim_kind": "property",
                    "items": [{"gold_id": "uniqueness", "name": "极限唯一性"}],
                },
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [{"gold_id": "limit", "name": "极限"}],
                },
            ]
        )
        run = self.graph(
            [
                self.node("c1", "Claim", "极限唯一性", claim_kind="criterion"),
                self.node("l1", "Concept", "极限"),
            ],
            [
                {
                    "id": "r1",
                    "type": "involves",
                    "source_id": "c1",
                    "target_id": "l1",
                }
            ],
        )

        report = evaluate_relation_stability([run], gold, Path("gold.json"))

        self.assertEqual(report["runs"][0]["aligned_edge_count"], 0)
        self.assertEqual(report["runs"][0]["dropped_relation_count"], 1)

    def test_forbidden_gold_item_does_not_align_endpoint(self) -> None:
        gold = self.gold(
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [{"gold_id": "mapping", "name": "映射"}],
                },
                {
                    "requirement": "X",
                    "type": "Concept",
                    "items": [{"gold_id": "monotone", "name": "单调增加"}],
                },
            ]
        )
        run = self.graph(
            [self.node("m1", "Concept", "映射"), self.node("x1", "Concept", "单调增加")],
            [
                {
                    "id": "r1",
                    "type": "special_case_of",
                    "source_id": "x1",
                    "target_id": "m1",
                }
            ],
        )

        report = evaluate_relation_stability([run], gold, Path("gold.json"))

        self.assertEqual(report["runs"][0]["dropped_relation_count"], 1)
        self.assertEqual(report["runs"][0]["aligned_edge_count"], 0)

    def test_high_jaccard_with_low_alignment_coverage(self) -> None:
        gold = self.gold(
            [
                {
                    "requirement": "M",
                    "type": "Concept",
                    "items": [
                        {"gold_id": "mapping", "name": "映射"},
                        {"gold_id": "function", "name": "函数"},
                    ],
                }
            ]
        )

        def run(label: str) -> dict[str, object]:
            return self.graph(
                [
                    self.node("m1", "Concept", "映射"),
                    self.node("f1", "Concept", "函数"),
                    self.node("t1", "Concept", "变换"),
                ],
                [
                    {
                        "id": "r1",
                        "type": "defined_in_terms_of",
                        "source_id": "m1",
                        "target_id": "f1",
                    },
                    {
                        "id": "r2",
                        "type": "involves",
                        "source_id": "t1",
                        "target_id": "f1",
                    },
                ],
            )

        report = evaluate_relation_stability([run("a"), run("b")], gold, Path("gold.json"))

        self.assertEqual(report["pairwise"][0]["gold_aligned_jaccard"], 1.0)
        self.assertEqual(report["runs"][0]["alignment_coverage"], 0.5)
        self.assertEqual(report["runs"][0]["dropped_relation_count"], 1)
        self.assertEqual(report["runs"][0]["relation_count"], 2)
        self.assertEqual(report["runs"][0]["aligned_edge_count"], 1)


if __name__ == "__main__":
    unittest.main()
