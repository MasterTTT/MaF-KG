import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction_context_state import LayoutContextState, layout_system_prompt
from test_extraction_context_state import chunk, memory, node, payload


class LayoutContextTests(unittest.TestCase):
    def test_original_memory_is_preserved_without_history(self):
        state = LayoutContextState([chunk(1), chunk(2)])
        row = node("a" * 150)
        state.commit("c1", state.graph, {"nodes": {"n1": row}, "relations": []})
        item = memory(row)
        item["short_description"] = "a" * 100
        legacy = payload(2, [item])
        original = copy.deepcopy(legacy)
        rendered = state.render(legacy)
        self.assertEqual(rendered["graph_state"]["nodes"], original["node_memory"])
        self.assertEqual(rendered["graph_state"]["relations"], original["relation_memory"])
        self.assertNotIn("recent_updates", rendered["graph_state"])
        self.assertNotIn("completed_chunk_count", rendered["graph_state"])
        self.assertEqual(rendered["current_evidence"]["text"], original["chunk"]["text"])
        self.assertEqual(legacy, original)

    def test_unknown_or_modified_memory_rejected(self):
        state = LayoutContextState([chunk(1), chunk(2)])
        with self.assertRaisesRegex(ValueError, "not yet committed"):
            state.render(payload(1, [memory(node())]))
        row = node()
        state.commit("c1", state.graph, {"nodes": {"n1": row}, "relations": []})
        item = memory(row)
        item["short_description"] = "invented condition"
        with self.assertRaisesRegex(ValueError, "summary differs"):
            state.render(payload(2, [item]))

    def test_prompt_has_no_full_description_or_update_promise(self):
        transformed = layout_system_prompt("chunk.text node_memory relation_memory")
        self.assertIn("current_evidence.text graph_state.nodes graph_state.relations", transformed)
        self.assertIn("original selected node summaries", transformed)
        self.assertNotIn("descriptions are not cropped", transformed)
        self.assertNotIn("Recent updates describe", transformed)


if __name__ == "__main__":
    unittest.main()
