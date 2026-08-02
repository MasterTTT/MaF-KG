from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from agents.Extractor import (
    ChunkExtractionResponse,
    CompletionTruncated,
    DocumentChunk,
    Evidence,
    EvidenceRef,
    Extractor,
    NodeAction,
    NodeRecord,
    ProviderChatClient,
    _node_id,
    _normalize_name,
)


class SemanticIdentityTests(unittest.TestCase):
    def test_mathematical_operators_remain_identity_significant(self) -> None:
        distinct_pairs = [
            ("x → +∞ 时函数的极限", "x → -∞ 时函数的极限"),
            ("f(x) > 0", "f(x) < 0"),
            ("A = 0", "A ≠ 0"),
            ("x → x₀⁺", "x → x₀⁻"),
            ("f(x)", "f'(x)"),
            ("1.2", "12"),
        ]
        for left, right in distinct_pairs:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(_normalize_name(left), _normalize_name(right))
                self.assertNotEqual(_node_id("Concept", left), _node_id("Concept", right))

    def test_latex_and_unicode_semantic_symbols_share_retrieval_form(self) -> None:
        self.assertEqual(_normalize_name(r"x \to +\infty"), _normalize_name("x → +∞"))
        self.assertEqual(_normalize_name(r"\exists N"), _normalize_name("∃N"))


class MemoryMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False)

    def test_symbol_alias_is_retrieved_but_distinct_concept_is_not_exact(self) -> None:
        quantifier = NodeRecord(
            id=_node_id("Concept", "存在量词"),
            type="Concept",
            name="存在量词",
            aliases=["∃"],
            description="表示存在。",
            evidence=[],
        )
        symbol_chunk = DocumentChunk(
            id="chunk_symbol",
            start_line=1,
            end_line=1,
            heading_path=[],
            text=r"1: \exists N",
        )
        _, reasons = self.extractor._node_memory_relevance(symbol_chunk, quantifier, [], set())
        self.assertIn("exact_alias", reasons)

        epsilon_neighborhood = NodeRecord(
            id=_node_id("Concept", "ε邻域"),
            type="Concept",
            name="ε邻域",
            aliases=[],
            description="以点为中心的开区间。",
            evidence=[],
        )
        punctured_chunk = DocumentChunk(
            id="chunk_punctured",
            start_line=1,
            end_line=1,
            heading_path=[],
            text="1: 去心邻域",
        )
        _, reasons = self.extractor._node_memory_relevance(
            punctured_chunk,
            epsilon_neighborhood,
            [],
            set(),
        )
        self.assertNotIn("exact_name", reasons)

    def test_reuse_mention_is_not_promoted_to_alias_and_revision_is_audited(self) -> None:
        node = NodeRecord(
            id=_node_id("Concept", "取整函数"),
            type="Concept",
            name="取整函数",
            aliases=["高斯函数"],
            description="旧描述",
            evidence=[],
        )
        action = NodeAction(
            ref_id="c1",
            action="reuse",
            existing_id=node.id,
            mention="取N = [1/√ε]",
            type="Concept",
            aliases=["整数部分函数"],
            description="新描述",
            evidence=EvidenceRef(start_line=1, end_line=1),
            reason="authoritative definition",
        )
        outcome = self.extractor._merge_reuse_action(
            node,
            action,
            Evidence(
                source_path="source.md",
                start_line=1,
                end_line=1,
                text="source",
                chunk_id="chunk_1",
                heading_path=["第一节"],
                teaching_block_id="teaching_1",
                discourse_role="paragraph",
            ),
        )
        self.assertNotIn("取N = [1/√ε]", node.aliases)
        self.assertIn("整数部分函数", node.aliases)
        self.assertEqual(outcome["description_action"], "replaced")
        self.assertEqual(outcome["description_revision"]["previous_description"], "旧描述")

    def test_identity_conflict_distinguishes_model_error_from_retrieval_miss(self) -> None:
        existing = NodeRecord(
            id=_node_id("Concept", "复合函数"),
            type="Concept",
            name="复合函数",
            aliases=[],
            description="已有定义",
            evidence=[],
        )
        response = ChunkExtractionResponse(
            node_actions=[
                NodeAction(
                    ref_id="c1",
                    action="create",
                    mention="复合函数",
                    type="Concept",
                    name="复合函数",
                    description="重复定义",
                    evidence=EvidenceRef(start_line=1, end_line=1),
                )
            ]
        )
        chunk = DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=[],
            text="1: 复合函数",
        )
        self.extractor.source_context_by_line = {
            1: (["第一节"], "teaching_1", "paragraph")
        }
        for allowed, expected in [({existing.id}, "model_decision_conflict"), (set(), "memory_retrieval_miss")]:
            with self.subTest(expected=expected):
                summary = self.extractor._merge_chunk_response(
                    response,
                    chunk,
                    "source.md",
                    ["复合函数"],
                    {existing.id: existing.model_copy(deep=True)},
                    {},
                    {},
                    allowed_existing_ids=allowed,
                )
                self.assertEqual(summary["identity_conflict_count"], 1)
                self.assertEqual(summary["identity_conflicts"][0]["conflict_kind"], expected)


class ClaimKindTests(unittest.TestCase):
    def test_create_claim_requires_claim_kind(self) -> None:
        with self.assertRaises(ValidationError):
            NodeAction(
                ref_id="q1",
                action="create",
                mention="函数相等准则",
                type="Claim",
                name="函数相等准则",
                description="两个函数相等当且仅当定义域和对应法则相同。",
                evidence=EvidenceRef(start_line=1, end_line=1),
            )

    def test_non_claim_rejects_claim_kind(self) -> None:
        with self.assertRaises(ValidationError):
            NodeAction(
                ref_id="c1",
                action="create",
                mention="函数",
                type="Concept",
                name="函数",
                description="从数集到数集的映射。",
                claim_kind="property",
                evidence=EvidenceRef(start_line=1, end_line=1),
            )


class StructureAwareChunkTests(unittest.TestCase):
    def test_formula_exposition_is_not_split_at_formula_boundary(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第一节 函数",
            "设函数在区间上满足以下条件，则称为单调增加：",
            "$$",
            "f(x_1)<f(x_2)",
            "$$",
            "那么称函数在该区间上单调增加；如果满足",
            "$$",
            "f(x_1)>f(x_2)",
            "$$",
            "那么称函数在该区间上单调减少。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 80,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        content_chunks = [chunk for chunk in state["chunks"] if "单调增加" in chunk.text]
        self.assertEqual(len(content_chunks), 1)
        self.assertIn("单调减少", content_chunks[0].text)
        self.assertIn("formula_exposition", content_chunks[0].block_types)
        self.assertTrue(content_chunks[0].teaching_block_ids)


class ApiCompletionTests(unittest.TestCase):
    def test_length_finish_reason_is_non_retryable_and_audited(self) -> None:
        response = {
            "id": "response_1",
            "model": "deepseek-v4-flash",
            "system_fingerprint": "fp_test",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "reasoning"},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 12000,
                "total_tokens": 12100,
                "completion_tokens_details": {"reasoning_tokens": 12000},
            },
        }
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test"}, clear=False):
            client = ProviderChatClient(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_key_env="DEEPSEEK_API_KEY",
                temperature=0.1,
                timeout=1,
                max_retries=4,
                max_tokens=12000,
                thinking="enabled",
                reasoning_effort="max",
            )
        with (
            patch("agents.Extractor._post_httpx", return_value=response) as post,
            patch("agents.Extractor._write_invalid_response", return_value=Path("debug.json.txt")),
        ):
            with self.assertRaises(CompletionTruncated):
                client.complete_json("system", "user", label="chunk_1")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(client.request_history), 1)
        self.assertEqual(client.request_history[0]["status"], "truncated")
        self.assertEqual(
            client.request_history[0]["usage"]["completion_tokens_details"]["reasoning_tokens"],
            12000,
        )


if __name__ == "__main__":
    unittest.main()
