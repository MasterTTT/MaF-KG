from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_knowledge_units.py"
SPEC = importlib.util.spec_from_file_location("build_knowledge_units", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PlannerCardTests(unittest.TestCase):
    def test_heading_groups_attach_intro_and_keep_nested_headings(self) -> None:
        cards = [
            {"card_id": "c1", "heading_path": ["section"]},
            {"card_id": "c2", "heading_path": ["section", "first"]},
            {"card_id": "c3", "heading_path": ["section", "first", "nested"]},
            {"card_id": "c4", "heading_path": ["section", "second"]},
            {"card_id": "c5", "heading_path": ["section"]},
        ]
        goals = MODULE.heading_objectives("section", cards)
        self.assertEqual([g["card_ids"] for g in goals], [["c1", "c2", "c3"], ["c4", "c5"]])

    def test_no_subheading_is_one_unit(self) -> None:
        cards = [{"card_id": "c1", "heading_path": ["section"]},
                 {"card_id": "c2", "heading_path": ["section"]}]
        self.assertEqual(MODULE.heading_objectives("section", cards)[0]["card_ids"], ["c1", "c2"])

    def test_empty_cards_fail_instead_of_inventing_a_goal(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.heading_objectives("section", [])

    def test_main_uses_heading_boundary_without_planner_or_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            section = "第一节 测试"
            source.write_text(f"# {section}\n# 一、主题\n定义\n", encoding="utf-8")
            graph = root / "graph.json"
            graph.write_text(json.dumps({
                "nodes": [{"id": "n1", "type": "Concept", "name": "主题",
                           "description": "定义", "evidence": [{"heading_path": ["第一章", section]}]}],
                "relations": [], "diagnostics": {"input_path": str(source)},
            }, ensure_ascii=False), encoding="utf-8")
            output = root / "units.json"
            cards = [{"card_id": "c1", "heading_path": [section, "一、主题"], "node_ids": ["n1"]}]
            result = ([{"goal_id": "g1", "name": "主题", "description": "学习主题",
                        "centers_on": ["n1"], "contains": []}], {"n1": ["g1"]}, [], {},
                      {"total_tokens": 10}, 1)
            args = [str(SCRIPT), str(graph), "--out", str(output), "--src-files", str(source)]
            with patch.object(MODULE.sys, "argv", args), \
                    patch.object(MODULE, "build_planner_cards", return_value=cards), \
                    patch.object(MODULE, "plan_objectives", side_effect=AssertionError("Planner called")), \
                    patch.object(MODULE, "audit_boundaries", side_effect=AssertionError("Audit called")), \
                    patch.object(MODULE, "assign_members", return_value=result) as assign:
                self.assertEqual(MODULE.main(), 0)
            assign.assert_called_once()
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["run"]["boundary_strategy"], "textbook-subheading")
            self.assertEqual(data["run"]["request_count"], 1)
            self.assertEqual(data["run"]["usage_totals"]["total_tokens"], 10)
            self.assertNotIn("planner_prompt", data["run"])
            self.assertNotIn("boundary_audits", data)
            self.assertEqual(len(data["knowledge_units"]), 1)

    def test_cards_include_structured_chunks_without_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text("# section\nCore statement.\nSupporting example.\n", encoding="utf-8")
            outline = {
                "section": {
                    "file": source.name,
                    "path": str(source),
                    "start": 1,
                    "end": 3,
                    "subs": [],
                }
            }
            nodes = [{
                "id": "n1",
                "type": "Concept",
                "name": "Core",
                "description": "",
                "evidence": [{
                    "source_path": str(source),
                    "start_line": 2,
                    "end_line": 2,
                    "chunk_id": "chunk_1",
                }],
            }]
            structure_data = {"diagnostics": {
                "input_path": str(source),
                "chunk_spans": [
                    {
                        "id": "chunk_1",
                        "start_line": 1,
                        "end_line": 2,
                        "heading_path": ["section"],
                        "block_types": ["heading", "paragraph"],
                        "teaching_unit_kind": "exposition",
                    },
                    {
                        "id": "chunk_2",
                        "start_line": 3,
                        "end_line": 3,
                        "heading_path": ["section"],
                        "block_types": ["example"],
                        "teaching_unit_kind": "example",
                    },
                ],
            }}

            cards = MODULE.build_planner_cards(
                "section", nodes, outline, {source.name: structure_data}
            )

        self.assertEqual([card["card_id"] for card in cards], ["c1", "c2"])
        self.assertEqual(cards[0]["node_ids"], ["n1"])
        self.assertEqual(cards[1]["node_ids"], [])
        self.assertIn("Supporting example", cards[1]["anchor_text"])

    def test_objective_plan_requires_exact_card_partition(self) -> None:
        cards = [{"card_id": "c1"}, {"card_id": "c2"}]
        objective = {
            "goal_id": "g1",
            "core_question": "What is the goal?",
            "objective": "Objective",
            "boundary_reason": "Reason",
            "card_ids": ["c1", "c2"],
        }
        self.assertEqual(
            MODULE._require_objective_plan({"objectives": [objective]}, "section", cards),
            [objective],
        )

        invalid = {**objective, "card_ids": ["c1"]}
        with self.assertRaisesRegex(ValueError, "missing"):
            MODULE._require_objective_plan({"objectives": [invalid]}, "section", cards)

    def test_boundary_audit_repartitions_only_target_cards(self) -> None:
        cards = [{"card_id": "c1"}, {"card_id": "c2"}, {"card_id": "c3"}]
        candidates = [
            {"goal_id": "g1", "core_question": "Q1?", "objective": "O1",
             "boundary_reason": "R1", "card_ids": ["c1", "c2"]},
            {"goal_id": "g2", "core_question": "Q2?", "objective": "O2",
             "boundary_reason": "R2", "card_ids": ["c3"]},
        ]
        response = {"operations": [
            {
                "action": "split",
                "targets": ["g1"],
                "objectives": [
                    {"core_question": "Q1a?", "objective": "O1a",
                     "boundary_reason": "R1a", "card_ids": ["c1"]},
                    {"core_question": "Q1b?", "objective": "O1b",
                     "boundary_reason": "R1b", "card_ids": ["c2"]},
                ],
                "reason": "Two complete targets",
            },
            {
                "action": "keep",
                "targets": ["g2"],
                "objectives": [
                    {"core_question": "Q2?", "objective": "O2",
                     "boundary_reason": "R2", "card_ids": ["c3"]},
                ],
                "reason": "Already coherent",
            },
        ]}

        objectives, operations = MODULE._require_boundary_audit(
            response, "section", cards, candidates
        )

        self.assertEqual([item["goal_id"] for item in objectives], ["g1", "g2", "g3"])
        self.assertEqual(len(operations), 2)

        response["operations"][0]["objectives"][1]["card_ids"] = ["c3"]
        with self.assertRaisesRegex(ValueError, "only repartition"):
            MODULE._require_boundary_audit(response, "section", cards, candidates)

    def test_assignment_rejects_more_than_three_centers(self) -> None:
        nodes = [
            {"id": f"node_{index}", "type": "Concept", "name": f"Node {index}", "description": ""}
            for index in range(1, 5)
        ]
        cards = [{"card_id": "c1", "heading_path": [], "discourse_roles": [],
                  "node_ids": [node["id"] for node in nodes]}]
        objectives = [{"goal_id": "g1", "core_question": "Question?", "objective": "Objective",
                       "boundary_reason": "Reason", "card_ids": ["c1"]}]
        response = {
            "units": [{
                "goal_id": "g1",
                "name": "Goal",
                "description": "Goal description",
                "centers_on": ["n1", "n2", "n3", "n4"],
            }]
        }

        with patch.object(MODULE, "_llm_json", return_value=(response, {})):
            with self.assertRaisesRegex(ValueError, "at most 3"):
                MODULE.assign_members(
                    "section", nodes, [], cards, objectives,
                    "router prompt", "finalizer prompt", "model"
                )

    def test_candidate_routing_preassigns_single_candidate_nodes(self) -> None:
        nodes = [
            {"id": "n1", "type": "Concept", "name": "One", "description": ""},
            {"id": "n2", "type": "Concept", "name": "Two", "description": ""},
        ]
        cards = [
            {"card_id": "c1", "node_ids": ["n1", "n2"]},
            {"card_id": "c2", "node_ids": ["n2"]},
        ]
        objectives = [
            {"goal_id": "g1", "core_question": "Q1?", "objective": "O1",
             "boundary_reason": "R1", "card_ids": ["c1"]},
            {"goal_id": "g2", "core_question": "Q2?", "objective": "O2",
             "boundary_reason": "R2", "card_ids": ["c2"]},
        ]

        candidates = MODULE._node_candidate_goals("section", nodes, cards, objectives)

        self.assertEqual(candidates, {"n1": ["g1"], "n2": ["g1", "g2"]})

        response = {"assignments": [
            {"node_alias": "n2", "goal_id": "g2", "reason": "Primary definition"}
        ]}
        with patch.object(MODULE, "_llm_json", return_value=(response, {})) as call:
            owner, records, _, requests = MODULE.route_members(
                "section", nodes, [], cards, objectives, candidates, "prompt", "model"
            )

        self.assertEqual(owner, {"n1": "g1", "n2": "g2"})
        self.assertEqual(len(records), 1)
        self.assertEqual(requests, 1)
        call.assert_called_once()

    def test_checkpoint_requires_exact_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "run.checkpoint.json"
            completed = {"section": {"units": []}}
            MODULE._write_checkpoint(checkpoint, "fingerprint-a", completed)

            self.assertEqual(
                MODULE._load_checkpoint(checkpoint, "fingerprint-a"),
                completed,
            )
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                MODULE._load_checkpoint(checkpoint, "fingerprint-b")


if __name__ == "__main__":
    unittest.main()
