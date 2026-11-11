from __future__ import annotations

import unittest
import os
from pathlib import Path
from unittest.mock import patch

from agents.Extractor import DocumentChunk, Extractor, NodeRecord, ProviderChatClient, _node_id
from scripts.run_memory_ablation import ARMS, COMMON, command, validate_graph


class MemoryAblationTests(unittest.TestCase):
    def test_opencode_session_is_stable_across_chunks(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "unit-test"}):
            client = ProviderChatClient(provider="opencode-go", model="deepseek-v4-flash",
                                        base_url="https://opencode.ai/zen/go/v1", api_key_env=None,
                                        temperature=0.1, timeout=1, max_retries=0, max_tokens=1000)
        response = {"choices": [{"finish_reason": "stop", "message": {"content": '{}'}}]}
        with patch("agents.Extractor._post_httpx", return_value=response) as post:
            client.complete_json("system", "chunk1")
            client.complete_json("system", "chunk2")
        headers = [call.args[3] for call in post.call_args_list]
        self.assertTrue(headers[0]["x-opencode-session"])
        self.assertEqual(headers[0]["x-opencode-session"], headers[1]["x-opencode-session"])
        self.assertIn("MaF-KG-SemanticExtractor", headers[0]["User-Agent"])

    def test_two_byte_budget_really_removes_model_visible_nodes(self):
        extractor = Extractor(save_output=False, verbose=False)
        node = NodeRecord(id=_node_id("Concept", "Sequence"), type="Concept", name="Sequence",
                          aliases=[], description="An ordered sequence.", evidence=[])
        chunk = DocumentChunk(id="c2", start_line=1, end_line=1, heading_path=[], text="1: Sequence")
        store = {node.id: node}
        normal, _, _ = extractor._build_memory_for_chunk(chunk, store, {}, {}, set())
        empty, relations, selection = extractor._build_memory_for_chunk(
            chunk, store, {}, {}, set(), node_char_budget=2, relation_char_budget=2)
        self.assertEqual(len(normal), 1)
        self.assertEqual((empty, relations, selection), ([], [], []))
        self.assertEqual(len(store), 1)

    def test_commands_only_vary_memory_and_output(self):
        manifest = {"input": "input.md", "common": COMMON}
        commands = []
        for name, budgets in ARMS.items():
            arm = {"name": name, "memory_node_bytes": budgets[0], "memory_relation_bytes": budgets[1]}
            args = command(manifest, Path("frozen"), arm)
            self.assertIn("--no-resume", args)
            for disabled in ("--verify", "--semantic-dedupe", "--embedding-memory", "--seed-graph"):
                self.assertNotIn(disabled, args)
            for flag in ("--output", "--memory-node-bytes", "--memory-relation-bytes"):
                args[args.index(flag) + 1] = "VARIABLE"
            args[3] = "SAME_FROZEN_EXTRACTOR_IN_ARM_RUNTIME"
            commands.append(args)
        self.assertEqual(commands[0], commands[1])

    def test_validate_rejects_leaked_memory_and_disabled_thinking(self):
        arm = {"name": "no_visible_memory", "memory_node_bytes": 2, "memory_relation_bytes": 2}
        config = {"provider": "deepseek", "model_requested": "deepseek-v4-flash", "thinking": "enabled",
                  "reasoning_effort_sent": "low", "chunk_strategy": "teaching-units",
                  "memory_node_bytes": 2, "memory_relation_bytes": 2, "resume_enabled": False}
        graph = {"model": "deepseek-v4-flash", "diagnostics": {"run_config": config, "chunk_summaries": [
            {"chunk_id": "c1", "memory_node_count": 0, "memory_relation_count": 0}]}}
        validate_graph(graph, {"common": COMMON}, arm, [{"id": "c1"}])
        graph["diagnostics"]["chunk_summaries"][0]["memory_node_count"] = 1
        with self.assertRaisesRegex(ValueError, "exposed memory"):
            validate_graph(graph, {"common": COMMON}, arm, [{"id": "c1"}])
        config["thinking"] = "disabled"
        with self.assertRaisesRegex(ValueError, "Configuration mismatch"):
            validate_graph(graph, {"common": COMMON}, arm, [{"id": "c1"}])


if __name__ == "__main__":
    unittest.main()
