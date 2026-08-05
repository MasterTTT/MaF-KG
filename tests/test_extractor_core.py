from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
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
    def test_create_action_does_not_require_redundant_mention(self) -> None:
        action = NodeAction(
            ref_id="c1",
            action="create",
            type="Concept",
            name="函数",
            description="从数集到数集的映射。",
            evidence=EvidenceRef(start_line=1, end_line=1),
        )
        self.assertEqual(action.mention, "")

    def test_reuse_action_requires_exact_source_mention(self) -> None:
        with self.assertRaises(ValidationError):
            NodeAction(
                ref_id="c1",
                action="reuse",
                existing_id="concept_函数_test",
                evidence=EvidenceRef(start_line=1, end_line=1),
            )

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

    def test_flat_markdown_headings_form_semantic_siblings(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第三节 函数的极限",
            "# 一、函数极限的定义",
            "导入。",
            "# 1. 自变量趋于有限值时函数的极限",
            "有限点定义。",
            "# 2. 自变量趋于无穷大时函数的极限",
            "无穷远定义。",
            "# 二、函数极限的性质",
            "性质正文。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 40,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        spans = state["diagnostics"]["chunk_spans"]
        infinite_span = next(item for item in spans if item["start_line"] == 6)
        property_span = next(item for item in spans if item["start_line"] == 8)
        self.assertEqual(
            infinite_span["heading_path"],
            ["第三节 函数的极限", "一、函数极限的定义", "2. 自变量趋于无穷大时函数的极限"],
        )
        self.assertEqual(
            property_span["heading_path"],
            ["第三节 函数的极限", "二、函数极限的性质"],
        )

    def test_example_conclusion_does_not_swallow_following_exposition(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第三节 函数的极限",
            "例5 证明一个具体极限。",
            "证 先进行估计。",
            "所以",
            "$$",
            "\\lim f(x)=A",
            "$$",
            "上述极限概念中还可以只从一侧趋近。",
            "左极限与右极限统称为单侧极限。",
            "例6 证明另一个具体结论。",
        ]
        extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[2][2], "example")
        self.assertEqual(extractor.source_context_by_line[8][2], "paragraph")
        self.assertEqual(extractor.source_context_by_line[9][2], "paragraph")

    def test_inline_example_conclusion_stops_at_attention_paragraph(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第四节 无穷小与无穷大",
            "例1 因为一个极限等于零，所以它是无穷小。",
            "因为另一个极限等于零，所以它也是无穷小。",
            "注意 零是唯一的常数无穷小。",
            "下面的定理说明无穷小与函数极限的关系。",
            "定理1 函数极限可由无穷小刻画。",
        ]
        extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[2][2], "example")
        self.assertEqual(extractor.source_context_by_line[3][2], "example")
        self.assertEqual(extractor.source_context_by_line[4][2], "paragraph")
        self.assertEqual(extractor.source_context_by_line[5][2], "paragraph")
        self.assertEqual(extractor.source_context_by_line[6][2], "theorem")

    def test_markdown_list_marker_does_not_hide_theorem_boundary(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "推论 一个一般推论。",
            "该推论的补充说明。",
            "* 定理4（函数极限与数列极限的关系）如果函数极限存在，则相应数列极限存在。",
            "证 由函数极限的定义可得。",
        ]
        extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[1][2], "corollary")
        self.assertEqual(extractor.source_context_by_line[2][2], "corollary")
        self.assertEqual(extractor.source_context_by_line[3][2], "theorem")

    def test_chunk_prompt_exposes_teaching_block_roles_without_changing_text(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第四节 无穷小与无穷大",
            "例1 这是一个局部例题。",
            "所以例题结论成立。",
            "注意 下面给出一般性质。",
            "定理1 一个可复用的一般结论。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        chunk = state["chunks"][0]
        prompt_payload = json.loads(
            extractor._chunk_extraction_prompt(chunk, "source.md", [], [])
        )
        self.assertEqual(prompt_payload["chunk"]["text"], chunk.text)
        self.assertEqual(
            [item["discourse_role"] for item in prompt_payload["chunk"]["teaching_blocks"]],
            ["heading", "example", "paragraph", "theorem"],
        )
        self.assertEqual(
            [
                (item["start_line"], item["end_line"])
                for item in prompt_payload["chunk"]["teaching_blocks"]
            ],
            [(1, 1), (2, 3), (4, 4), (5, 5)],
        )

    def test_heading_boundary_keeps_intro_separate_from_first_child_section(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第三节 函数的极限",
            "# 一、函数极限的定义",
            "函数极限有若干变化形式。",
            "# 1. 自变量趋于有限值时函数的极限",
            "这里给出有限点函数极限的正式定义。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(len(state["chunks"]), 2)
        self.assertIn("函数极限有若干变化形式", state["chunks"][0].text)
        self.assertNotIn("正式定义", state["chunks"][0].text)
        self.assertIn("正式定义", state["chunks"][1].text)

    def test_chunk_text_preserves_contiguous_blank_line_numbers(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": ["# 第一节 函数", "定义函数。", "", "函数具有定义域。"],
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(len(state["chunks"]), 1)
        self.assertIn("2: 定义函数。\n3: \n4: 函数具有定义域。", state["chunks"][0].text)

    def test_experimental_spans_replace_budget_grouping_without_splitting_blocks(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第四节 无穷小与无穷大",
            "这里给出无穷小的定义。",
            "",
            "例1 这是一个局部例题。",
            "所以例题结论成立。",
            "",
            "定理1 一个可复用的一般结论。",
            "证 由定义可得。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 1,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "experimental_chunk_spans": ((1, 2), (4, 6), (7, 8)),
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [(chunk.start_line, chunk.end_line) for chunk in state["chunks"]],
            [(1, 2), (4, 6), (7, 8)],
        )
        self.assertEqual(
            [chunk.break_reason for chunk in state["chunks"]],
            ["experimental_manual_span"] * 3,
        )
        self.assertEqual(state["diagnostics"]["chunk_strategy"], "experimental_manual_spans")

    def test_experimental_span_cannot_cut_through_teaching_block(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        with self.assertRaisesRegex(ValueError, "cuts through example teaching block"):
            extractor._chunk_document(
                {
                    "raw_lines": [
                        "# 第一节 示例",
                        "例1 这是一个局部例题。",
                        "所以例题结论成立。",
                    ],
                    "max_chunk_chars": 3200,
                    "chunk_context_before_lines": 0,
                    "chunk_context_after_lines": 0,
                    "experimental_chunk_spans": ((1, 2), (3, 3)),
                    "diagnostics": {},
                }
            )

    def test_experimental_mode_audits_and_skips_pure_example_chunks(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 示例",
                    "定义一个可复用概念。",
                    "",
                    "例1 这是一个局部例题。",
                    "所以例题结论成立。",
                    "",
                    "定理1 一个可复用结论。",
                ],
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "experimental_chunk_spans": ((1, 2), (4, 6), (7, 7)),
                "experimental_skip_pure_example_chunks": True,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [(chunk.start_line, chunk.end_line) for chunk in state["chunks"]],
            [(1, 2), (7, 7)],
        )
        skipped = state["diagnostics"]["experimental_skipped_chunks"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["start_line"], 4)
        self.assertEqual(skipped[0]["end_line"], 6)
        self.assertEqual(skipped[0]["block_types"], ["example"])
        self.assertEqual(len(skipped[0]["teaching_block_ids"]), 1)
        self.assertRegex(skipped[0]["teaching_block_ids"][0], r"^teaching_\d+_4_6_")

    def test_skipping_examples_requires_explicit_chunk_spans(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        with self.assertRaisesRegex(ValueError, "requires experimental_chunk_spans"):
            extractor._chunk_document(
                {
                    "raw_lines": ["# 第一节 示例", "定义一个可复用概念。"],
                    "max_chunk_chars": 3200,
                    "chunk_context_before_lines": 0,
                    "chunk_context_after_lines": 0,
                    "experimental_skip_pure_example_chunks": True,
                    "diagnostics": {},
                }
            )

    def test_teaching_units_skip_consecutive_examples_and_filter_context(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 示例",
                    "先给出一个一般概念。",
                    "",
                    "例1 这是第一个局部例题。",
                    "所以例题一成立。",
                    "",
                    "例2 这是第二个局部例题。",
                    "所以例题二成立。",
                    "",
                    "一般地，下面继续讨论概念本身。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 8,
                "chunk_context_after_lines": 8,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [(chunk.start_line, chunk.end_line) for chunk in state["chunks"]],
            [(1, 2), (10, 10)],
        )
        self.assertNotIn("例1", state["chunks"][0].context_after_text)
        self.assertNotIn("例2", state["chunks"][1].context_before_text)
        skipped = state["diagnostics"]["skipped_teaching_units"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual((skipped[0]["start_line"], skipped[0]["end_line"]), (4, 9))
        self.assertEqual(skipped[0]["unit_kind"], "example_sequence")
        self.assertTrue(state["diagnostics"]["teaching_unit_coverage"]["covered_exactly_once"])

    def test_teaching_units_keep_standalone_claim_separate_from_theorem_leadin(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第四节 无穷小与无穷大",
                    "一般地说，如果函数趋于无穷，那么相应直线是渐近线。",
                    "",
                    "无穷大与无穷小之间有一种简单的关系，即",
                    "",
                    "定理2 无穷大与无穷小互为倒数关系。",
                    "证 由定义可得。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [(chunk.start_line, chunk.end_line) for chunk in state["chunks"]],
            [(1, 2), (4, 7)],
        )
        self.assertEqual(
            [chunk.teaching_unit_kind for chunk in state["chunks"]],
            ["core_statement", "theorem"],
        )
        self.assertEqual(
            state["chunks"][0].break_reason,
            "core_statement_before_theorem",
        )

    def test_teaching_units_group_theorem_variants_and_corollaries(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第二节 性质",
                    "",
                    "下面列出若干一般性质。",
                    "",
                    "定理1 第一个独立结论。",
                    "证 证明一。",
                    "",
                    "",
                    "定理2 第二个独立结论。",
                    "",
                    "定理3 第三个结论。",
                    "",
                    "定理3' 定理3的加强形式。",
                    "",
                    "推论 定理3的直接推论。",
                    "",
                    "定理4 第四个独立结论。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [(chunk.start_line, chunk.end_line) for chunk in state["chunks"]],
            [(1, 10), (11, 16), (17, 17)],
        )
        self.assertEqual(
            [chunk.teaching_unit_kind for chunk in state["chunks"]],
            ["theorem_group", "theorem_family", "theorem"],
        )
        self.assertEqual(len(state["chunks"][1].anchor_block_ids), 3)

    def test_teaching_units_reject_manual_spans(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            extractor._chunk_document(
                {
                    "raw_lines": ["# 第一节", "定义。"],
                    "max_chunk_chars": 3200,
                    "chunk_strategy": "teaching-units",
                    "chunk_context_before_lines": 0,
                    "chunk_context_after_lines": 0,
                    "experimental_chunk_spans": ((1, 2),),
                    "diagnostics": {},
                }
            )


class ApiCompletionTests(unittest.TestCase):
    def test_schema_failure_preserves_payload_and_api_telemetry(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        chunk = DocumentChunk(
            id="chunk_001_1_2",
            start_line=1,
            end_line=2,
            heading_path=["第一节"],
            text="1: 函数\n2: 定义",
        )
        client = SimpleNamespace(
            request_history=[
                {
                    "label": chunk.id,
                    "status": "success",
                    "latency_seconds": 1.25,
                    "usage": {"total_tokens": 100},
                }
            ]
        )
        invalid_payload = {
            "node_actions": [
                {
                    "ref_id": "c1",
                    "action": "reuse",
                    "existing_id": "concept_函数_test",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            "relations": [],
            "expressions": [],
            "notes": [],
        }
        with patch(
            "agents.Extractor._write_invalid_response",
            return_value=Path("schema_debug.json.txt"),
        ) as write_debug:
            with self.assertRaisesRegex(ValueError, "raw JSON and API telemetry were saved"):
                extractor._validate_chunk_response(
                    invalid_payload,
                    chunk,
                    {"input_path": "source.md", "output_path": "result.json"},
                    client,
                    [],
                    [],
                    0,
                    0,
                    0,
                )
        self.assertEqual(client.request_history[0]["status"], "invalid_schema")
        self.assertEqual(client.request_history[0]["debug_path"], "schema_debug.json.txt")
        metadata = write_debug.call_args.args[3]
        self.assertEqual(metadata["api_summary"]["failed_request_count"], 1)
        self.assertEqual(metadata["partial_counts"]["nodes"], 0)

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
