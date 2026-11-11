import copy
import json
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extraction_context_state import (ContextState, INPUT_FIELDS, UPDATE_BYTES, compact_bytes,
                                      data_hash, graph_snapshot, structured_system_prompt)
from run_context_state_experiment import install_hooks


def chunk(number):
    return {"id": f"c{number}", "start_line": number, "end_line": number,
            "heading_path": ["Topic"], "text": f"{number}: source statement",
            "teaching_block_ids": [f"b{number}"],
            "teaching_blocks": [{"teaching_block_id": f"b{number}", "discourse_role": "theorem",
                                 "start_line": number, "end_line": number}],
            "context_before_text": "0: navigation only", "context_after_text": "4: preview only"}


def payload(number, nodes=None, relations=None):
    current = chunk(number)
    current["chunk_id"] = current.pop("id")
    current["evidence_line_numbers"] = [number]
    return {"source_path": "source.md", "chunk": current,
            "node_memory": nodes or [], "relation_memory": relations or [],
            "defined_term_hints": [], "source_latex_candidates": [{"latex": "f'(x)", "line": number}]}


def node(description="Continuous f on the same interval; F'=f."):
    return {"id": "n1", "type": "Claim", "name": "Result", "aliases": [],
            "description": description, "claim_kind": "theorem", "logical_form": None,
            "evidence_sha256": data_hash([])}


def memory(row):
    result = {key: value for key, value in row.items() if key != "evidence_sha256"}
    result["short_description"] = result.pop("description")
    return result


class ContextStateTests(unittest.TestCase):
    def test_evidence_is_separate_and_static_content_is_unchanged(self):
        state = ContextState([chunk(1), chunk(2)])
        legacy = payload(1)
        before = copy.deepcopy(legacy)
        result = state.render(legacy)
        self.assertEqual(result["current_evidence"]["text"], legacy["chunk"]["text"])
        self.assertEqual(result["current_evidence"]["source_latex_candidates"], legacy["source_latex_candidates"])
        self.assertNotIn("context_before_text", result["current_evidence"])
        self.assertEqual(result["reading_context"]["context_before_text"], "0: navigation only")
        self.assertEqual(result["graph_state"]["completed_chunk_count"], 0)
        self.assertEqual(legacy, before)
        self.assertNotIn("reading_note", result["reading_context"])

    def test_failed_or_out_of_order_work_is_not_completed(self):
        state = ContextState([chunk(1), chunk(2)])
        with self.assertRaisesRegex(ValueError, "successful graph commits"):
            state.render(payload(2))
        with self.assertRaisesRegex(ValueError, "out of frozen chunk order"):
            state.commit("c2", state.graph, state.graph)
        self.assertEqual(state.completed, [])

    def test_preview_does_not_fabricate_processed_history(self):
        state = ContextState([chunk(1), chunk(2)])
        result = state.render(payload(2), preview=True)
        self.assertEqual(result["reading_context"]["chunk_index"], 2)
        self.assertEqual(result["graph_state"]["completed_chunk_count"], 0)

    def test_full_description_is_preserved_and_cropped_input_rejected(self):
        state = ContextState([chunk(1), chunk(2)])
        row = node("x" * 150 + "; denominator is nonzero, same interval and continuity required.")
        state.commit("c1", state.graph, {"nodes": {"n1": row}, "relations": []})
        full = state.render(payload(2, [memory(row)]))
        self.assertEqual(full["graph_state"]["nodes"][0]["description"], row["description"])
        cropped = memory(row)
        cropped["short_description"] = cropped["short_description"][:100]
        with self.assertRaisesRegex(ValueError, "cropped or stale"):
            state.render(payload(2, [cropped]))

    def test_updates_are_graph_diffs_not_model_reasons(self):
        state = ContextState([chunk(1), chunk(2), chunk(3)])
        first = {"nodes": {"n1": node()}, "relations": []}
        state.commit("c1", state.graph, first)
        second = copy.deepcopy(first)
        second["nodes"]["n1"]["description"] += " Endpoint derivatives are one-sided."
        second["nodes"]["n1"]["evidence_sha256"] = "new-source-evidence"
        committed = state.commit("c2", first, second)
        self.assertEqual([e["kind"] for e in committed["events"]],
                         ["node_semantics_updated", "node_evidence_updated"])
        result = state.render(payload(3, [memory(second["nodes"]["n1"])]))
        kinds = [e["kind"] for e in result["graph_state"]["recent_updates"]]
        self.assertEqual(kinds, ["node_created", "node_semantics_updated", "node_evidence_updated"])
        self.assertTrue(result["graph_state"]["processed_does_not_mean_complete_extraction"])

    def test_relation_endpoint_must_be_visible_and_committed(self):
        state = ContextState([chunk(1), chunk(2)])
        second_node = node()
        second_node["id"] = "n2"
        after = {"nodes": {"n1": node(), "n2": second_node}, "relations": [["n1", "derived_from", "n2"]]}
        state.commit("c1", state.graph, after)
        relation = {"source_id": "n1", "relation_type": "derived_from", "target_id": "n2"}
        with self.assertRaisesRegex(ValueError, "invisible endpoint"):
            state.render(payload(2, [memory(after["nodes"]["n1"])], [relation]))
        result = state.render(payload(2, [memory(n) for n in after["nodes"].values()], [relation]))
        self.assertEqual(result["graph_state"]["relations"], [relation])

    def test_unselected_nodes_do_not_leak_through_update_history(self):
        state = ContextState([chunk(1), chunk(2)])
        state.commit("c1", state.graph, {"nodes": {"n1": node()}, "relations": []})
        result = state.render(payload(2))
        self.assertEqual(result["graph_state"]["recent_updates"], [])
        self.assertNotIn("n1", json.dumps(result))
        self.assertEqual(result["graph_state"]["scope"], "partial_relevant_view")

    def test_update_budget_drops_whole_events_with_explicit_omission_count(self):
        state = ContextState([chunk(1), chunk(2)])
        nodes = {}
        for i in range(150):
            row = node()
            row["id"] = f"n{i}"
            nodes[row["id"]] = row
        state.commit("c1", state.graph, {"nodes": nodes, "relations": []})
        result = state.render(payload(2, [memory(n) for n in nodes.values()]))["graph_state"]
        self.assertLessEqual(compact_bytes(result["recent_updates"]), UPDATE_BYTES)
        self.assertGreater(result["recent_updates_scope"]["omitted_relevant_events"], 0)
        self.assertEqual(len(result["recent_updates"]) + result["recent_updates_scope"]["omitted_relevant_events"], 150)

    def test_stale_graph_and_future_identity_fail(self):
        state = ContextState([chunk(1)])
        with self.assertRaisesRegex(ValueError, "committed state"):
            state.observe({"nodes": {"n1": node()}, "relations": []})
        with self.assertRaisesRegex(ValueError, "not yet committed"):
            state.render(payload(1, [memory(node())]))

    def test_frozen_source_tampering_fails(self):
        state = ContextState([chunk(1)])
        legacy = payload(1)
        legacy["chunk"]["text"] += " invented condition"
        with self.assertRaisesRegex(ValueError, "Frozen source context changed"):
            state.render(legacy)

    def test_system_contract_changes_field_paths_not_ontology_rules(self):
        original = "Use chunk.text; node_memory; relation_memory. Claim -> Concept. Exclude proof-local nodes."
        result = structured_system_prompt(original)
        self.assertIn("Use current_evidence.text; graph_state.nodes; graph_state.relations.", result)
        self.assertIn("Claim -> Concept. Exclude proof-local nodes.", result)
        for key, value in INPUT_FIELDS.items():
            self.assertIn(value, structured_system_prompt(key))

    def test_qualified_legacy_paths_are_replaced_without_double_prefix(self):
        result = structured_system_prompt("Use chunk.evidence_line_numbers and chunk.defined_term_hints.")
        self.assertIn("Use current_evidence.evidence_line_numbers and current_evidence.defined_term_hints.", result)
        self.assertNotIn("chunk.current_evidence", result)
        self.assertNotIn("chunk.evidence_line_numbers", result)


@dataclass
class FakeChunk:
    id: str
    start_line: int
    end_line: int
    heading_path: list
    text: str
    teaching_block_ids: list
    teaching_blocks: list
    context_before_text: str
    context_after_text: str


class FakeRecord:
    def __init__(self, row):
        self.row = copy.deepcopy(row)
        self.description = row.get("description", "")

    def model_dump(self, **kwargs):
        return copy.deepcopy(self.row)

    def model_copy(self, update):
        return FakeRecord(self.row | update)


class HookTests(unittest.TestCase):
    def module(self):
        class Extractor:
            system_prompt = "Use node_memory and chunk.text."

            def _chunk_document(self, state):
                return {"chunks": [FakeChunk(**chunk(1)), FakeChunk(**chunk(2))], "diagnostics": {}}

            def _node_memory_item(self, node):
                item = {key: value for key, value in node.row.items() if key not in ("description", "evidence")}
                item["short_description"] = node.description[:100]
                return FakeRecord(item)

            def _build_memory_for_chunk(self, current, nodes, relations, *args, **kwargs):
                return [self._node_memory_item(n) for n in nodes.values()], [], []

            def _chunk_extraction_prompt(self, current, path, nodes, relations):
                return json.dumps(payload(int(current.id[1:]), [n.model_dump() for n in nodes]))

            def _merge_chunk_response(self, response, current, path, lines, nodes, relations, expressions, allowed):
                if response == "fail":
                    raise ValueError("model response failed merge")
                row = node("x" * 150 + "; important nonzero condition")
                row.pop("evidence_sha256")
                row["evidence"] = []
                nodes["n1"] = FakeRecord(row)
                return {"chunk_id": current.id}

            def _cache_fingerprint(self, *args):
                return "old-fingerprint"

        return SimpleNamespace(Extractor=Extractor)

    def test_hooks_keep_flat_memory_short_and_structured_memory_complete(self):
        for arm in ("flat_context", "structured_state"):
            module = self.module()
            requests, commits = [], []
            install_hooks(module, [chunk(1), chunk(2)], arm, requests.append, commits.append)
            extractor = module.Extractor()
            state = extractor._chunk_document({})
            c1, c2 = state["chunks"]
            nodes, relations = {}, {}
            with self.assertRaisesRegex(ValueError, "failed merge"):
                extractor._merge_chunk_response("fail", c1, "source.md", [], nodes, relations, {}, set())
            self.assertEqual(commits, [])
            extractor._merge_chunk_response("ok", c1, "source.md", [], nodes, relations, {}, set())
            selected, selected_relations, _ = extractor._build_memory_for_chunk(c2, nodes, relations)
            extractor._chunk_extraction_prompt(c2, "source.md", selected, selected_relations)
            self.assertEqual(requests[0]["committed_chunk_ids"], ["c1"])
            sent = requests[0]["sent_payload"]
            if arm == "structured_state":
                self.assertIn("important nonzero condition", sent["graph_state"]["nodes"][0]["description"])
                self.assertEqual(sent["graph_state"]["recent_updates"][0]["kind"], "node_created")
            else:
                self.assertEqual(len(sent["node_memory"][0]["short_description"]), 100)

    def test_cache_fingerprint_includes_new_context_and_system_prompt(self):
        module = self.module()
        install_hooks(module, [chunk(1), chunk(2)], "structured_state", lambda row: None, lambda row: None)
        extractor = module.Extractor()
        current = extractor._chunk_document({})["chunks"][0]
        state = {"input_path": "source.md"}
        original = extractor._cache_fingerprint(current, state, [], [])
        self.assertEqual(len(original), 20)
        extractor.system_prompt += " explicit contract"
        changed = extractor._cache_fingerprint(current, state, [], [])
        self.assertNotEqual(original, changed)

    def test_full_memory_size_is_used_before_budget_selection(self):
        module = self.module()
        install_hooks(module, [chunk(1), chunk(2)], "structured_state", lambda row: None, lambda row: None)
        extractor = module.Extractor()
        row = node("x" * 250 + "; nonzero denominator")
        row.pop("evidence_sha256")
        selected = extractor._node_memory_item(FakeRecord(row)).model_dump()
        self.assertGreater(compact_bytes(selected), 250)
        self.assertEqual(selected["short_description"], row["description"])


if __name__ == "__main__":
    unittest.main()
