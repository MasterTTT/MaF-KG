from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError

from agents.Extractor import (
    ChunkExtractionResponse,
    ChunkMergeError,
    CompletionTruncated,
    DocumentChunk,
    Evidence,
    EvidenceRef,
    ExtractionState,
    ExtractionResult,
    Extractor,
    KGNode,
    KGRelation,
    LogicalForm,
    NodeAction,
    NodeMemoryItem,
    NodeRecord,
    ProviderChatClient,
    RelationMemoryItem,
    RelationRecord,
    VerifyResponse,
    _provider_class,
    _provider_name,
    _node_id,
    _normalize_name,
    classify_definition_segments,
)


class ProviderAdapterTests(unittest.TestCase):
    def test_opencode_alias_resolves_to_go_provider(self) -> None:
        self.assertEqual(_provider_name("opencode"), "opencode-go")
        adapter = _provider_class("opencode-go")
        self.assertEqual(adapter.default_model, "deepseek-v4-flash")
        self.assertEqual(adapter.default_base_url, "https://opencode.ai/zen/go/v1")
        self.assertEqual(adapter.api_key_envs, ("OPENCODE_API_KEY",))

    def test_opencode_go_uses_openai_compatible_deepseek_payload(self) -> None:
        response = {
            "id": "response_1",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok": true}'},
                }
            ],
            "usage": {},
        }
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "unit-test"}, clear=False):
            client = ProviderChatClient(
                provider="opencode-go",
                model="deepseek-v4-flash",
                base_url="https://opencode.ai/zen/go/v1",
                api_key_env=None,
                temperature=0.1,
                timeout=1,
                max_retries=0,
                max_tokens=4096,
                thinking="enabled",
                reasoning_effort="low",
            )
        with patch("agents.Extractor._post_httpx", return_value=response) as post:
            self.assertEqual(client.complete_json("system", "user"), {"ok": True})
        endpoint, _, _, headers, payload = post.call_args.args
        self.assertEqual(
            endpoint,
            "https://opencode.ai/zen/go/v1/chat/completions",
        )
        self.assertEqual(headers["Authorization"], "Bearer unit-test")
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("temperature", payload)


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

    def test_exact_duplicate_reuses_canonical_identity(self) -> None:
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
                store = {existing.id: existing.model_copy(deep=True)}
                result = self.extractor._merge_chunk_response(
                    response,
                    chunk,
                    "source.md",
                    ["复合函数"],
                    store,
                    {},
                    {},
                    allowed_existing_ids=allowed,
                )
                self.assertEqual(result["created_node_count"], 0)
                self.assertEqual(result["reused_node_count"], 1)
                self.assertEqual(len(store), 1)
                if allowed:
                    self.assertEqual(result["identity_conflicts"][0]["conflict_kind"], expected)
                    self.assertEqual(result["identity_conflicts"][0]["action"], "auto_reused")
                self.assertIn(existing.id, store)
                self.assertEqual(store[existing.id].description, "重复定义")
                self.assertEqual(len(store[existing.id].evidence), 1)


class SemanticDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False, semantic_dedupe=True)

    def existing(self, name: str, aliases: list[str] | None = None) -> NodeRecord:
        return NodeRecord(
            id=_node_id("Concept", name),
            type="Concept",
            name=name,
            aliases=aliases or [],
            description=f"{name}的已有定义",
            evidence=[],
        )

    def merge(self, store: dict[str, NodeRecord], name: str, mention: str = "") -> dict:
        chunk = DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=["第一节"],
            text="1: " + name,
        )
        self.extractor.source_context_by_line = {1: (["第一节"], "teaching_1", "paragraph")}
        response = ChunkExtractionResponse(
            node_actions=[
                NodeAction(
                    ref_id="c1",
                    action="create",
                    mention=mention or name,
                    type="Concept",
                    name=name,
                    description=f"{name}的新定义",
                    evidence=EvidenceRef(start_line=1, end_line=1),
                )
            ]
        )
        return self.extractor._merge_chunk_response(
            response, chunk, "source.md", [name], store, {}, {}, allowed_existing_ids=set()
        )

    def test_identity_collision_is_rescued_as_reuse_under_semantic_dedupe(self) -> None:
        existing = self.existing("复合函数")
        store = {existing.id: existing}
        summary = self.merge(store, "复合函数")
        self.assertEqual(summary["reused_node_count"], 1)
        self.assertEqual(summary["created_node_count"], 0)
        self.assertEqual(len(store), 1)
        self.assertEqual(store[existing.id].description, "复合函数的新定义")
        decision = summary["decisions"][0]
        self.assertEqual(decision["action"], "semantic_reuse")
        self.assertEqual(decision["semantic_merged_to"], existing.id)

    def test_drift_variant_merges_to_known_concept_with_alias(self) -> None:
        existing = self.existing("自变量趋于正无穷时的函数极限")
        store = {existing.id: existing}
        drift_name = "函数 f(x) 当 x → +∞ 时的极限"
        self.extractor._semantic_duplicate_id = lambda name, desc, node_store: existing.id
        summary = self.merge(store, drift_name)
        self.assertEqual(summary["reused_node_count"], 1)
        self.assertEqual(summary["created_node_count"], 0)
        self.assertEqual(len(store), 1)
        self.assertIn(drift_name, store[existing.id].aliases)
        self.assertEqual(summary["decisions"][0]["action"], "semantic_reuse")
        self.assertEqual(summary["decisions"][0]["semantic_merged_to"], existing.id)

    def test_judge_different_keeps_create(self) -> None:
        existing = self.existing("左极限")
        store = {existing.id: existing}
        self.extractor._semantic_duplicate_id = lambda name, desc, node_store: None
        summary = self.merge(store, "右极限")
        self.assertEqual(summary["created_node_count"], 1)
        self.assertEqual(summary["reused_node_count"], 0)
        self.assertEqual(len(store), 2)
        self.assertIn(_node_id("Concept", "右极限"), store)

    def test_flag_off_keeps_plain_create_even_if_duplicate_found(self) -> None:
        plain = Extractor(save_output=False, verbose=False, semantic_dedupe=False)
        existing = self.existing("自变量趋于正无穷时的函数极限")
        store = {existing.id: existing}
        plain._semantic_duplicate_id = lambda name, desc, node_store: existing.id
        chunk = DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=["第一节"],
            text="1: 函数当 x → +∞ 时的极限",
        )
        plain.source_context_by_line = {1: (["第一节"], "teaching_1", "paragraph")}
        response = ChunkExtractionResponse(
            node_actions=[
                NodeAction(
                    ref_id="c1",
                    action="create",
                    mention="函数当 x → +∞ 时的极限",
                    type="Concept",
                    name="函数当 x → +∞ 时的极限",
                    description="定义",
                    evidence=EvidenceRef(start_line=1, end_line=1),
                )
            ]
        )
        summary = plain._merge_chunk_response(
            response,
            chunk,
            "source.md",
            ["函数当 x → +∞ 时的极限"],
            store,
            {},
            {},
            allowed_existing_ids=set(),
        )
        self.assertEqual(summary["created_node_count"], 1)
        self.assertEqual(len(store), 2)


class EmbeddingMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False, embedding_memory=True)

    def test_embedding_component_raises_rule_score_when_similar(self) -> None:
        chunk = DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=["第三节"],
            text="1: 函数当 x → +∞ 时的极限",
        )
        node = NodeRecord(
            id=_node_id("Concept", "自变量趋于正无穷时的函数极限"),
            type="Concept",
            name="自变量趋于正无穷时的函数极限",
            aliases=[],
            description="当 x 趋于正无穷时函数 f(x) 的极限",
            evidence=[],
        )
        self.extractor._embed_texts = lambda texts: [
            [1.0, 0.0, 0.0] if "函数当" in texts[0] else [0.9, 0.1, 0.0] for text in texts
        ]
        score, reasons = self.extractor._node_memory_relevance(
            chunk, node, [], set(), embed_sim=0.9
        )
        self.assertGreater(score, 2.0)
        self.assertTrue(any(reason.startswith("embedding:") for reason in reasons))

    def test_embedding_below_floor_is_ignored(self) -> None:
        chunk = DocumentChunk(id="c", start_line=1, end_line=1, heading_path=[], text="1: x")
        node = NodeRecord(
            id=_node_id("Concept", "极限"), type="Concept", name="极限", aliases=[], evidence=[]
        )
        score, reasons = self.extractor._node_memory_relevance(chunk, node, [], set(), embed_sim=0.10)
        self.assertFalse(any(reason.startswith("embedding:") for reason in reasons))

    def test_flag_off_makes_no_embedding_calls(self) -> None:
        plain = Extractor(save_output=False, verbose=False)
        chunk = DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=["第三节"],
            text="1: 函数极限",
        )
        node = NodeRecord(
            id=_node_id("Concept", "函数极限"),
            type="Concept",
            name="函数极限",
            aliases=[],
            description="定义",
            evidence=[],
        )
        plain._embed_texts = lambda texts: (_ for _ in ()).throw(AssertionError("no embed calls"))
        plain._chunk_node_similarities = (
            lambda chunk, store: (_ for _ in ()).throw(AssertionError("no similarity calls"))
        )
        store = {node.id: node}
        memory, _, _ = plain._build_memory_for_chunk(
            chunk, store, {}, {}, set(), node_char_budget=12000, relation_char_budget=6000
        )
        self.assertEqual([item.id for item in memory], [node.id])

    def test_embedding_failure_falls_back_to_rules(self) -> None:
        chunk = DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=["第三节"],
            text="1: 函数极限",
        )
        node = NodeRecord(
            id=_node_id("Concept", "函数极限"),
            type="Concept",
            name="函数极限",
            aliases=[],
            description="定义",
            evidence=[],
        )
        self.extractor._chunk_node_similarities = lambda chunk, store: {}
        store = {node.id: node}
        memory, _, _ = self.extractor._build_memory_for_chunk(
            chunk, store, {}, {}, set(), node_char_budget=12000, relation_char_budget=6000
        )
        self.assertEqual([item.id for item in memory], [node.id])


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

    def test_claim_logical_form_preserves_joint_premises(self) -> None:
        action = NodeAction(
            ref_id="q1",
            action="create",
            type="Claim",
            name="夹逼准则",
            description="两个边界数列收敛到同一极限且夹住中间数列时，中间数列收敛到该极限。",
            claim_kind="criterion",
            logical_form={
                "operator": "implies",
                "premises": [
                    "y_n <= x_n <= z_n",
                    "lim y_n = a",
                    "lim z_n = a",
                ],
                "conclusion": ["lim x_n = a"],
                "converse_status": "unstated",
            },
            evidence=EvidenceRef(start_line=1, end_line=1),
        )
        self.assertEqual(len(action.logical_form.premises), 3)
        self.assertEqual(action.logical_form.conclusion, ["lim x_n = a"])

    def test_iff_logical_form_rejects_converse_status(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalForm(
                operator="iff",
                premises=["左极限和右极限都存在且相等"],
                conclusion=["函数极限存在"],
                converse_status="proven",
            )

    def test_non_claim_rejects_logical_form(self) -> None:
        with self.assertRaises(ValidationError):
            NodeAction(
                ref_id="c1",
                action="create",
                type="Concept",
                name="数列",
                description="按正整数下标排列的实数序列。",
                logical_form={
                    "operator": "implies",
                    "premises": ["n 为正整数"],
                    "conclusion": ["x_n 为数列的一项"],
                },
                evidence=EvidenceRef(start_line=1, end_line=1),
            )

    def test_reuse_initializes_logical_form_with_audit_record(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        node = NodeRecord(
            id=_node_id("Claim", "收敛数列必有界"),
            type="Claim",
            name="收敛数列必有界",
            description="如果数列收敛，那么该数列有界。",
            claim_kind="property",
        )
        action = NodeAction(
            ref_id="q1",
            action="reuse",
            existing_id=node.id,
            mention="收敛数列的有界性",
            logical_form={
                "operator": "implies",
                "premises": ["数列收敛"],
                "conclusion": ["数列有界"],
                "converse_status": "disproved",
            },
            evidence=EvidenceRef(start_line=1, end_line=1),
        )
        evidence = Evidence(
            source_path="source.md",
            start_line=1,
            end_line=1,
            text="如果数列收敛，那么数列一定有界，但有界数列未必收敛。",
            chunk_id="chunk_1",
            heading_path=["数列的极限"],
            teaching_block_id="theorem_1",
            discourse_role="theorem",
        )
        outcome = extractor._merge_reuse_action(node, action, evidence)
        self.assertEqual(outcome["logical_form_action"], "initialized")
        self.assertEqual(
            outcome["logical_form_revision"]["new_logical_form"]["converse_status"],
            "disproved",
        )
        self.assertEqual(node.logical_form.conclusion, ["数列有界"])


class LogicalFormReviewTests(unittest.TestCase):
    @staticmethod
    def claim(name: str, text: str) -> NodeRecord:
        return NodeRecord(
            id=_node_id("Claim", name),
            type="Claim",
            name=name,
            description=text,
            claim_kind="property",
            evidence=[
                Evidence(
                    source_path="source.md",
                    start_line=10,
                    end_line=12,
                    text=text,
                    chunk_id="chunk_1",
                    heading_path=["数列的极限"],
                    teaching_block_id="theorem_1",
                    discourse_role="theorem",
                )
            ],
        )

    def test_candidate_filter_requires_explicit_condition_signal(self) -> None:
        conditional = self.claim("收敛数列必有界", "若数列收敛，则该数列有界。")
        plain_property = self.claim("极限唯一性", "收敛数列的极限是唯一的。")
        self.assertTrue(Extractor._is_logical_form_review_candidate(conditional))
        self.assertFalse(Extractor._is_logical_form_review_candidate(plain_property))

    def test_review_updates_only_returned_logical_form(self) -> None:
        extractor = Extractor(
            save_output=False,
            verbose=False,
            logical_form_review=True,
        )
        node = self.claim(
            "收敛数列必有界",
            "若数列收敛，则该数列有界；反之不一定成立。",
        )
        extractor.client = SimpleNamespace(
            complete_json=lambda *_args, **_kwargs: {
                "items": [
                    {
                        "node_id": node.id,
                        "logical_form": {
                            "operator": "implies",
                            "premises": ["数列收敛"],
                            "conclusion": ["数列有界"],
                            "converse_status": "disproved",
                        },
                    }
                ]
            }
        )
        store = {node.id: node}
        diagnostics = extractor._review_missing_logical_forms(
            store,
            ExtractionState(logical_form_review=True),
        )
        self.assertEqual(diagnostics["updated_node_ids"], [node.id])
        self.assertEqual(store[node.id].logical_form.conclusion, ["数列有界"])

    def test_review_requires_exact_candidate_coverage(self) -> None:
        extractor = Extractor(
            save_output=False,
            verbose=False,
            logical_form_review=True,
        )
        node = self.claim("收敛数列必有界", "若数列收敛，则该数列有界。")
        extractor.client = SimpleNamespace(
            complete_json=lambda *_args, **_kwargs: {"items": []}
        )
        with self.assertRaisesRegex(ValueError, "return every candidate exactly once"):
            extractor._review_missing_logical_forms(
                {node.id: node},
                ExtractionState(logical_form_review=True),
            )


class CoverageLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False)

    def chunk(
        self,
        *,
        chunk_id: str,
        unit_kind: str,
        role: str,
        block_id: str,
    ) -> DocumentChunk:
        return DocumentChunk(
            id=chunk_id,
            start_line=1,
            end_line=2,
            heading_path=["第一节"],
            text="1: 教材正文。\n2: 教材正文。",
            teaching_unit_kind=unit_kind,
            teaching_block_ids=(block_id,),
            teaching_blocks=(
                {
                    "teaching_block_id": block_id,
                    "discourse_role": role,
                    "start_line": 1,
                    "end_line": 2,
                    "segments": [],
                },
            ),
            anchor_block_ids=(block_id,),
        )

    def node(self, node_type: str, block_id: str) -> NodeRecord:
        return NodeRecord(
            id=f"{node_type.lower()}_covered",
            type=node_type,
            name="已覆盖语义",
            description="教材明确陈述的可复用数学语义。",
            claim_kind="property" if node_type == "Claim" else None,
            evidence=[
                Evidence(
                    source_path="source.md",
                    start_line=1,
                    end_line=2,
                    text="教材正文。",
                    chunk_id="chunk_1",
                    heading_path=["第一节"],
                    teaching_block_id=block_id,
                    discourse_role="definition" if node_type == "Concept" else "theorem",
                )
            ],
        )

    def test_definition_requires_concept_evidence(self) -> None:
        chunk = self.chunk(
            chunk_id="chunk_1",
            unit_kind="definition",
            role="definition",
            block_id="definition_1",
        )
        ledger = self.extractor._build_coverage_ledger(
            [chunk], {}, {}, chunk_strategy="teaching-units"
        )
        self.assertEqual(ledger["uncovered_count"], 1)
        self.assertEqual(
            ledger["uncovered_items"][0]["missing_semantics"], ["Concept"]
        )
        self.assertTrue(ledger["uncovered_items"][0]["review_eligible"])

        concept = self.node("Concept", "definition_1")
        covered = self.extractor._build_coverage_ledger(
            [chunk], {concept.id: concept}, {}, chunk_strategy="teaching-units"
        )
        self.assertEqual(covered["uncovered_count"], 0)
        self.assertEqual(covered["covered_count"], 1)

    def test_theorem_requires_claim_not_merely_related_concept(self) -> None:
        chunk = self.chunk(
            chunk_id="chunk_1",
            unit_kind="theorem",
            role="theorem",
            block_id="theorem_1",
        )
        concept = self.node("Concept", "theorem_1")
        ledger = self.extractor._build_coverage_ledger(
            [chunk], {concept.id: concept}, {}, chunk_strategy="teaching-units"
        )
        self.assertEqual(
            ledger["uncovered_items"][0]["missing_semantics"], ["Claim"]
        )

        claim = self.node("Claim", "theorem_1")
        covered = self.extractor._build_coverage_ledger(
            [chunk],
            {concept.id: concept, claim.id: claim},
            {},
            chunk_strategy="teaching-units",
        )
        self.assertEqual(covered["uncovered_count"], 0)

    def test_exposition_without_output_is_not_reported_as_required_gap(self) -> None:
        chunk = self.chunk(
            chunk_id="chunk_1",
            unit_kind="exposition",
            role="paragraph",
            block_id="paragraph_1",
        )
        ledger = self.extractor._build_coverage_ledger(
            [chunk], {}, {}, chunk_strategy="teaching-units"
        )
        self.assertEqual(ledger["check_count"], 0)
        self.assertEqual(ledger["uncovered_count"], 0)

    def test_core_statement_accepts_any_node_evidence(self) -> None:
        chunk = self.chunk(
            chunk_id="chunk_1",
            unit_kind="core_statement",
            role="paragraph",
            block_id="statement_1",
        )
        ledger = self.extractor._build_coverage_ledger(
            [chunk], {}, {}, chunk_strategy="teaching-units"
        )
        self.assertEqual(
            ledger["uncovered_items"][0]["missing_semantics"], ["Node"]
        )
        self.assertTrue(ledger["uncovered_items"][0]["review_eligible"])

        concept = self.node("Concept", "statement_1")
        covered = self.extractor._build_coverage_ledger(
            [chunk], {concept.id: concept}, {}, chunk_strategy="teaching-units"
        )
        self.assertEqual(covered["uncovered_count"], 0)

    def test_budget_strategy_marks_coverage_not_applicable(self) -> None:
        ledger = self.extractor._build_coverage_ledger(
            [], {}, {}, chunk_strategy="budget"
        )
        self.assertEqual(ledger["status"], "not_applicable")

    def test_coverage_review_requires_teaching_units(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires chunk_strategy"):
            Extractor(coverage_review=True, save_output=False, verbose=False)


class RunConfigTests(unittest.TestCase):
    def test_memory_budgets_are_recorded(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state: ExtractionState = {
            "provider": "deepseek",
            "thinking": "disabled",
            "memory_node_bytes": 14000,
            "memory_relation_bytes": 7000,
        }

        config = extractor._run_config(state)

        self.assertEqual(config["memory_node_bytes"], 14000)
        self.assertEqual(config["memory_relation_bytes"], 7000)


class CoveragePipelineTests(unittest.TestCase):
    def chunk(self, *, role: str = "theorem", unit_kind: str = "theorem") -> DocumentChunk:
        return DocumentChunk(
            id="chunk_1",
            start_line=1,
            end_line=1,
            heading_path=["第一节"],
            text="1: 定理1 极限具有唯一性。",
            block_count=1,
            block_types=(role,),
            teaching_block_ids=("teaching_1",),
            teaching_blocks=(
                {
                    "teaching_block_id": "teaching_1",
                    "discourse_role": role,
                    "start_line": 1,
                    "end_line": 1,
                    "segments": [],
                },
            ),
            teaching_unit_kind=unit_kind,
            anchor_block_ids=("teaching_1",),
        )

    def state(self, chunk: DocumentChunk, *, coverage_review: bool) -> dict[str, Any]:
        return {
            "input_path": "source.md",
            "output_path": "result.json",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "temperature": 0.1,
            "thinking": "enabled",
            "reasoning_effort": "low",
            "max_tokens": 4096,
            "raw_lines": ["定理1 极限具有唯一性。"],
            "chunks": [chunk],
            "diagnostics": {},
            "resume": False,
            "request_delay": 0,
            "save_output": False,
            "memory_node_bytes": 12000,
            "memory_relation_bytes": 6000,
            "use_response_format": True,
            "no_proxy": True,
            "max_chunk_chars": 3200,
            "chunk_strategy": "teaching-units",
            "chunk_context_before_lines": 6,
            "chunk_context_after_lines": 4,
            "experimental_chunk_spans": (),
            "max_retries": 0,
            "request_timeout": 1,
            "include_exercises": False,
            "coverage_review": coverage_review,
            "verbose": False,
            "run_started_at": "2026-08-12T00:00:00+00:00",
        }

    def run_pipeline(
        self,
        *,
        coverage_review: bool,
        responses: dict[str, dict[str, Any]],
        chunk: DocumentChunk | None = None,
        latex_candidates: dict[str, tuple[int, str]] | None = None,
    ) -> tuple[ExtractionResult, list[str]]:
        extractor = Extractor(
            chunk_strategy="teaching-units",
            coverage_review=coverage_review,
            save_output=False,
            verbose=False,
        )
        source_chunk = chunk or self.chunk()
        role = str(source_chunk.teaching_blocks[0]["discourse_role"])
        extractor.source_context_by_line = {1: (["第一节"], "teaching_1", role)}
        extractor.source_latex_candidates = latex_candidates or {}
        calls: list[str] = []
        client = SimpleNamespace(
            complete_json=lambda system, user, label: calls.append(label)
            or responses[label],
            request_history=[],
        )
        with (
            patch.object(extractor, "_client", return_value=client),
            patch.object(extractor, "_write_chunk_cache"),
        ):
            result = extractor._extract_sequentially(
                self.state(source_chunk, coverage_review=coverage_review)
            )["result"]
        return result, calls

    def test_first_pass_ledger_adds_no_model_call(self) -> None:
        empty = {"node_actions": [], "relations": [], "expressions": [], "notes": []}
        result, calls = self.run_pipeline(
            coverage_review=False,
            responses={"chunk_1": empty},
        )

        self.assertEqual(calls, ["chunk_1"])
        self.assertEqual(result.diagnostics["coverage_ledger"]["uncovered_count"], 1)
        self.assertEqual(result.diagnostics["coverage_review"]["reviewed_chunk_count"], 0)

    def test_empty_coverage_review_is_recorded_and_gap_remains(self) -> None:
        empty = {"node_actions": [], "relations": [], "expressions": [], "notes": []}
        result, calls = self.run_pipeline(
            coverage_review=True,
            responses={
                "chunk_1": empty,
                "chunk_1_coverage_review": empty,
            },
        )

        self.assertEqual(calls, ["chunk_1", "chunk_1_coverage_review"])
        review = result.diagnostics["coverage_review"]
        self.assertEqual(review["reviewed_chunk_count"], 1)
        self.assertEqual((review["uncovered_before"], review["uncovered_after"]), (1, 1))

    def test_review_can_create_claim_and_close_gap(self) -> None:
        empty = {"node_actions": [], "relations": [], "expressions": [], "notes": []}
        review_payload = {
            "node_actions": [
                {
                    "ref_id": "claim_1",
                    "action": "create",
                    "type": "Claim",
                    "name": "极限唯一性",
                    "description": "收敛对象的极限是唯一的。",
                    "claim_kind": "property",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            "relations": [],
            "expressions": [],
            "notes": [],
        }
        result, calls = self.run_pipeline(
            coverage_review=True,
            responses={
                "chunk_1": empty,
                "chunk_1_coverage_review": review_payload,
            },
        )

        self.assertEqual(calls, ["chunk_1", "chunk_1_coverage_review"])
        self.assertEqual([node.name for node in result.nodes], ["极限唯一性"])
        review = result.diagnostics["coverage_review"]
        self.assertEqual((review["uncovered_before"], review["uncovered_after"]), (1, 0))

    def test_formula_candidates_are_diagnostic_not_required(self) -> None:
        chunk = self.chunk(role="formula_block", unit_kind="exposition")
        empty = {"node_actions": [], "relations": [], "expressions": [], "notes": []}
        result, calls = self.run_pipeline(
            coverage_review=False,
            responses={"chunk_1": empty},
            chunk=chunk,
            latex_candidates={"latex_1": (1, r"f(x)=x^2")},
        )

        ledger = result.diagnostics["coverage_ledger"]
        self.assertEqual(calls, ["chunk_1"])
        self.assertEqual(ledger["check_count"], 0)
        self.assertEqual(ledger["uncovered_count"], 0)
        self.assertEqual(ledger["formula_candidate_check_count"], 1)
        self.assertEqual(ledger["formula_candidate_without_expression_count"], 1)


class StructureAwareChunkTests(unittest.TestCase):
    def test_extraction_state_preserves_memory_budgets(self) -> None:
        self.assertIn("memory_node_bytes", ExtractionState.__annotations__)
        self.assertIn("memory_relation_bytes", ExtractionState.__annotations__)

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

    def test_fresh_definitional_act_breaks_out_of_skipped_example_region(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第一节 映射与函数",
            "# 一、映射",
            "定义 设 X、Y 是两个非空集合，如果存在一个法则 f，则称 f 为映射。",
            "例1 设 f: R → R, f(x) = x^2。",
            "例2 设 g: R → R, g(x) = sin x。",
            "设 f 是从集合 X 到集合 Y 的映射，若 R_f = Y，则称 f 为满射；若不同元素的像不同，则称 f 为单射。",
            "上面例 1 中的映射既非单射又非满射。",
            "# 2. 逆映射",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        skipped = state["diagnostics"]["skipped_teaching_units"]
        self.assertEqual(skipped, [])
        self.assertEqual(extractor.source_context_by_line[6][2], "paragraph")
        self.assertTrue(any("满射" in chunk.text for chunk in state["chunks"]))

    def test_example_inline_naming_gloss_does_not_break_example_block(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第一节 映射与函数",
            "例7 函数 y = sgn x 的定义如下。",
            "称为符号函数，它的定义域 D=(-∞,+∞)。",
            "例8 不超过 x 的最大整数称为 x 的整数部分。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[2][2], "example")
        self.assertEqual(extractor.source_context_by_line[3][2], "example")
        example_chunk = state["chunks"][0]
        self.assertEqual(example_chunk.teaching_unit_kind, "example_sequence")

    def test_teaching_units_keep_method_summary_after_example_without_transition_word(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第二节 数列的极限",
            "例2 证明一个具体数列的极限。",
            "证 从定义出发进行估计。",
            "取 N 大于一个确定的数，结论成立。",
            "注意 在利用数列极限定义论证时，重要的是指出正整数 N 确实存在。",
            "例3 证明另一个具体极限。",
            "证 直接计算。",
            "定理1 收敛数列的极限唯一。",
        ]
        state = extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[2][2], "example")
        self.assertEqual(extractor.source_context_by_line[5][2], "paragraph")
        self.assertTrue(any("重要的是指出正整数 N" in chunk.text for chunk in state["chunks"]))
        skipped = state["diagnostics"]["skipped_teaching_units"]
        self.assertFalse(any(item["start_line"] <= 5 <= item["end_line"] for item in skipped))

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

    def test_teaching_units_keep_example_sequence_as_a_chunk(self) -> None:
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
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        spans = [(chunk.start_line, chunk.end_line) for chunk in state["chunks"]]
        self.assertEqual(spans, [(1, 2), (4, 6), (7, 7)])
        example_chunk = state["chunks"][1]
        self.assertEqual(example_chunk.teaching_unit_kind, "example_sequence")
        self.assertEqual(example_chunk.block_types, ("example",))
        self.assertEqual(state["diagnostics"]["skipped_teaching_units"], [])
        self.assertTrue(state["diagnostics"]["teaching_unit_coverage"]["covered_exactly_once"])

    def test_teaching_units_group_consecutive_examples_into_one_chunk(self) -> None:
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
            [(1, 2), (4, 9), (10, 10)],
        )
        example_chunk = state["chunks"][1]
        self.assertEqual(example_chunk.teaching_unit_kind, "example_sequence")
        self.assertEqual(
            [block["discourse_role"] for block in example_chunk.teaching_blocks],
            ["example", "example"],
        )
        self.assertEqual(state["diagnostics"]["skipped_teaching_units"], [])

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

    def test_definition_block_kind_detected(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第一节 函数",
            "定义1 设函数 $f(x)$ 在点 $x_0$ 的某一去心邻域内有定义。",
            "如果存在常数 $A$，那么常数 $A$ 就叫做函数 $f(x)$ 当 $x \\to x_0$ 时的极限。",
            "定义 设数集 $D$ 为非空集合。",
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
        self.assertEqual(extractor.source_context_by_line[2][2], "definition")
        self.assertEqual(extractor.source_context_by_line[3][2], "definition")
        self.assertEqual(extractor.source_context_by_line[4][2], "definition")

    def test_definition_mention_without_设_is_not_a_block(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第一节 函数",
            "定义函数。",
            "根据定义可得该结论。",
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
        self.assertEqual(extractor.source_context_by_line[2][2], "paragraph")
        self.assertEqual(extractor.source_context_by_line[3][2], "paragraph")

    def test_theorem_references_are_exposition_not_new_theorems(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        lines = [
            "# 第五节 极限运算法则",
            "定理6表示，如果满足该定理的条件，那么可作变量代换。",
            "定理 2 表明, 求两个无穷小之比时可作等价替换。",
            "定理3说明该结论还可推广。",
            "定理3中的(1)、(2)可推广到有限个函数。",
            "定理4的结论也适用于另一种情形。",
        ]
        extractor._chunk_document(
            {
                "raw_lines": lines,
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "experimental_chunk_spans": (),
                "diagnostics": {},
            }
        )
        self.assertTrue(
            all(
                extractor.source_context_by_line[line_no][2] == "paragraph"
                for line_no in range(2, 7)
            )
        )

    def test_actual_named_theorem_remains_theorem(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第五节 极限运算法则",
                    "定理6（复合函数的极限运算法则）设函数满足下列条件。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "experimental_chunk_spans": (),
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[2][2], "theorem")

    def test_theorem_reference_ends_preceding_theorem_block(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第五节 极限运算法则",
                    "定理6（复合函数的极限运算法则）设函数满足下列条件。",
                    "则复合函数的极限存在。",
                    "定理6表示，如果满足该定理的条件，那么可作变量代换。",
                    "这里给出应用说明。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "experimental_chunk_spans": (),
                "diagnostics": {},
            }
        )
        self.assertEqual(extractor.source_context_by_line[2][2], "theorem")
        self.assertEqual(extractor.source_context_by_line[3][2], "theorem")
        self.assertEqual(extractor.source_context_by_line[4][2], "paragraph")
        self.assertEqual(extractor.source_context_by_line[5][2], "paragraph")
        self.assertNotEqual(
            extractor.source_context_by_line[2][1],
            extractor.source_context_by_line[4][1],
        )

    def test_teaching_unit_kind_definition_group(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 函数",
                    "定义1 设函数 $f(x)$ 在点 $x_0$ 的去心邻域内有定义。",
                    "那么常数 $A$ 就叫做函数 $f(x)$ 当 $x \\to x_0$ 时的极限。",
                    "定义2 设函数 $f(x)$ 当 $|x|$ 大于某一正数时有定义。",
                    "那么常数 $A$ 叫做函数 $f(x)$ 当 $x \\to \\infty$ 时的极限。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [chunk.teaching_unit_kind for chunk in state["chunks"]],
            ["definition_group"],
        )
        self.assertEqual(len(state["chunks"][0].anchor_block_ids), 2)

    def test_definition_theorem_family_bundled_into_one_chunk(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 函数",
                    "定义1 设函数 $f(x)$ 在点 $x_0$ 的去心邻域内有定义。",
                    "那么常数 $A$ 就叫做函数 $f(x)$ 当 $x \\to x_0$ 时的极限。",
                    "定理1 函数 $f(x)$ 当 $x \\to x_0$ 时极限唯一。",
                    "证 由定义直接可得。",
                    "性质1 极限与去心邻域无关。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [chunk.teaching_unit_kind for chunk in state["chunks"]],
            ["definition_theorem_family"],
        )
        family = state["chunks"][0]
        self.assertIn("定理1", family.text)
        self.assertIn("证", family.text)
        self.assertIn("定义1", family.text)

    def test_definition_theorem_family_splits_when_definition_followed_by_heading(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 函数",
                    "定义1 设函数 $f(x)$ 在点 $x_0$ 的去心邻域内有定义。",
                    "那么常数 $A$ 就叫做函数 $f(x)$ 当 $x \\to x_0$ 时的极限。",
                    "# 二、函数极限的性质",
                    "性质1 极限与去心邻域无关。",
                ],
                "max_chunk_chars": 3200,
                "chunk_strategy": "teaching-units",
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        self.assertEqual(
            [chunk.teaching_unit_kind for chunk in state["chunks"]],
            ["definition", "property"],
        )

    def test_defined_term_hints_extract_template_definitions(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        hints = extractor._extract_defined_terms(
            "那么常数 $A$ 就叫做函数 $f(x)$ 当 $x \\to x_0$ 时的极限，记作\n"
            "左极限与右极限统称为单侧极限。\n"
            "那么称函数 $f(x)$ 为当 $x \\to x_0$ 时的无穷小。"
        )
        terms = [hint["term"] for hint in hints]
        self.assertIn("函数 f(x) 当 x \\to x_0 时的极限", terms)
        self.assertIn("单侧极限", terms)
        self.assertIn("当 x \\to x_0 时的无穷小", terms)
        for hint in hints:
            self.assertNotIn("$", hint["term"])
        for hint in hints:
            self.assertIn("term", hint)
            self.assertIn("context", hint)

    def test_chunk_prompt_includes_defined_term_hints(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 函数",
                    "定义1 设函数 $f(x)$ 在点 $x_0$ 的去心邻域内有定义。",
                    "那么常数 $A$ 就叫做函数 $f(x)$ 当 $x \\to x_0$ 时的极限，记作",
                    "$$",
                    "\\lim_{x\\to x_0}f(x)=A",
                    "$$",
                ],
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        prompt = extractor._chunk_extraction_prompt(
            state["chunks"][0],
            "test.md",
            [],
            [],
        )
        payload = json.loads(prompt)
        self.assertTrue(payload["defined_term_hints"])
        self.assertIn(
            "函数 f(x) 当 x \\to x_0 时的极限",
            [hint["term"] for hint in payload["defined_term_hints"]],
        )

    def test_definition_segment_roles_labeled_from_signal_words(self) -> None:
        segments = classify_definition_segments(
            [
                (10, "定义1 设函数 $f(x)$ 在点 $x_0$ 的去心邻域内有定义。"),
                (11, "这里我们指出，极限值与函数在该点的值无关。"),
                (12, "也就是说，$\lim_{x\\to x_0}f(x)=A$。"),
                (13, "从几何上来说，当 $x$ 趋于 $x_0$ 时曲线无限接近直线 $y=A$。"),
                (14, "我们需要注意，上述结论只在邻域内成立。"),
            ]
        )
        roles = [role for role, _, _ in segments]
        self.assertEqual(roles[0], "exposition")
        self.assertEqual(roles[1], "meta_annotation")
        self.assertEqual(roles[2], "symbolic_restatement")
        self.assertEqual(roles[3], "geometric_interpretation")
        self.assertEqual(roles[4], "meta_annotation")

    def test_definition_segments_merge_contiguous_same_role_lines(self) -> None:
        segments = classify_definition_segments(
            [
                (10, "定义 设数集 $D$ 为非空集合。"),
                (11, "从几何上来说，这是实数轴上的一个区间。"),
                (12, "从图形上看，数集对应数轴上的点集。"),
                (13, "也就是说是数轴上的一段。"),
            ]
        )
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0], ("exposition", 10, 10))
        self.assertEqual(segments[1], ("geometric_interpretation", 11, 12))
        self.assertEqual(segments[2], ("symbolic_restatement", 13, 13))

    def test_definition_segments_serialized_in_chunk_payload(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        state = extractor._chunk_document(
            {
                "raw_lines": [
                    "# 第一节 函数",
                    "定义1 设函数 $f(x)$ 在点 $x_0$ 的去心邻域内有定义。",
                    "这里我们指出，极限值与函数在该点的值无关。",
                    "从几何上来说，曲线无限接近直线 $y=A$。",
                ],
                "max_chunk_chars": 3200,
                "chunk_context_before_lines": 0,
                "chunk_context_after_lines": 0,
                "diagnostics": {},
            }
        )
        prompt = extractor._chunk_extraction_prompt(
            state["chunks"][0],
            "test.md",
            [],
            [],
        )
        payload = json.loads(prompt)
        definition_block = next(
            blk
            for blk in payload["chunk"]["teaching_blocks"]
            if blk["discourse_role"] == "definition"
        )
        roles = [seg["role"] for seg in definition_block["segments"]]
        self.assertEqual(roles, ["exposition", "meta_annotation", "geometric_interpretation"])
        self.assertEqual(
            definition_block["segments"][0],
            {"role": "exposition", "start_line": 2, "end_line": 2},
        )
        self.assertIn("start_line", definition_block["segments"][1])
        self.assertIn("end_line", definition_block["segments"][1])


class StrictMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False)

    def chunk(self, chunk_id: str = "chunk_1", lines: list[str] | None = None) -> DocumentChunk:
        lines = lines or ["函数"]
        text = "\n".join(f"{i}: {line}" for i, line in enumerate(lines, start=1))
        chunk = DocumentChunk(
            id=chunk_id,
            start_line=1,
            end_line=len(lines),
            heading_path=["第一节"],
            text=text,
        )
        self.extractor.source_context_by_line = {
            line: (["第一节"], "teaching_1", "paragraph")
            for line in range(1, len(lines) + 1)
        }
        return chunk

    def create_action(self, ref_id: str = "c1", name: str = "函数", start: int = 1, end: int = 1) -> dict[str, object]:
        return {
            "ref_id": ref_id,
            "action": "create",
            "type": "Concept",
            "name": name,
            "description": f"{name}的定义",
            "evidence": {"start_line": start, "end_line": end},
        }

    def test_relation_reduction_prunes_only_inherited_concept_anchor(self) -> None:
        store = {
            ("claim_derived", "derived_from", "claim_basis"): RelationRecord(
                source_id="claim_derived",
                relation_type="derived_from",
                target_id="claim_basis",
            ),
            ("claim_basis", "involves", "concept_shared"): RelationRecord(
                source_id="claim_basis",
                relation_type="involves",
                target_id="concept_shared",
            ),
            ("claim_derived", "involves", "concept_shared"): RelationRecord(
                source_id="claim_derived",
                relation_type="involves",
                target_id="concept_shared",
            ),
            ("claim_derived", "involves", "concept_new"): RelationRecord(
                source_id="claim_derived",
                relation_type="involves",
                target_id="concept_new",
            ),
        }
        pruned = self.extractor._prune_dominated_involves(store)
        self.assertNotIn(("claim_derived", "involves", "concept_shared"), store)
        self.assertIn(("claim_derived", "involves", "concept_new"), store)
        self.assertIn(("claim_basis", "involves", "concept_shared"), store)
        self.assertEqual(pruned[0]["inherited_through"], ["claim_basis"])

    def test_relation_reduction_requires_explicit_derived_from(self) -> None:
        store = {
            ("claim_basis", "involves", "concept_shared"): RelationRecord(
                source_id="claim_basis",
                relation_type="involves",
                target_id="concept_shared",
            ),
            ("claim_other", "involves", "concept_shared"): RelationRecord(
                source_id="claim_other",
                relation_type="involves",
                target_id="concept_shared",
            ),
        }
        self.assertEqual(self.extractor._prune_dominated_involves(store), [])
        self.assertEqual(len(store), 2)

    def test_valid_response_commits_nodes_relations_and_expressions_atomically(self) -> None:
        chunk = self.chunk(lines=["函数", "极限"])
        self.extractor.source_latex_candidates = {"latex_L1_1": (1, r"\lim")}
        response = ChunkExtractionResponse(
            node_actions=[
                self.create_action("c1", "函数"),
                self.create_action("c2", "极限", start=2, end=2),
            ],
            relations=[
                {
                    "source_ref": "c1",
                    "relation_type": "defined_in_terms_of",
                    "target_ref": "c2",
                    "evidence": {"start_line": 1, "end_line": 2},
                }
            ],
            expressions=[
                {
                    "owner_ref": "c2",
                    "role": "definition_representation",
                    "latex_candidate_id": "latex_L1_1",
                    "evidence": {"start_line": 1, "end_line": 2},
                }
            ],
            notes=[],
        )
        node_store: dict[str, NodeRecord] = {}
        summary = self.extractor._merge_chunk_response(
            response,
            chunk,
            "source.md",
            ["函数", "极限"],
            node_store,
            {},
            {},
            allowed_existing_ids=set(),
        )
        self.assertEqual(summary["created_node_count"], 2)
        self.assertEqual(summary["new_relation_count"], 1)
        self.assertEqual(summary["new_expression_count"], 1)
        self.assertEqual(summary["skipped_item_count"], 0)
        self.assertEqual(summary["identity_conflict_count"], 0)
        self.assertEqual(summary["pending_node_count"], 0)
        self.assertEqual(len(node_store), 2)

    def test_invalid_relation_endpoint_leaves_no_partial_modification(self) -> None:
        chunk = self.chunk()
        response = ChunkExtractionResponse(
            node_actions=[self.create_action()],
            relations=[
                {
                    "source_ref": "c1",
                    "relation_type": "involves",
                    "target_ref": "ghost",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            expressions=[],
            notes=[],
        )
        node_store: dict[str, NodeRecord] = {}
        with self.assertRaises(ChunkMergeError) as cm:
            self.extractor._merge_chunk_response(
                response, chunk, "source.md", ["函数"], node_store, {}, {}, set()
            )
        self.assertEqual(cm.exception.item_kind, "relation")
        self.assertIn("ghost", cm.exception.item_ref)
        self.assertIn("endpoint is neither", cm.exception.reason)
        self.assertEqual(node_store, {})

    def test_invalid_relation_signature_and_self_loop_fail_chunk(self) -> None:
        chunk = self.chunk()
        self_loop = ChunkExtractionResponse(
            node_actions=[self.create_action()],
            relations=[
                {
                    "source_ref": "c1",
                    "relation_type": "involves",
                    "target_ref": "c1",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            expressions=[],
            notes=[],
        )
        with self.assertRaisesRegex(ChunkMergeError, "self relation"):
            self.extractor._merge_chunk_response(
                self_loop, chunk, "source.md", ["函数"], {}, {}, {}, set()
            )

        wrong_types = ChunkExtractionResponse(
            node_actions=[self.create_action("c1", "函数"), self.create_action("c2", "极限")],
            relations=[
                {
                    "source_ref": "c1",
                    "relation_type": "involves",
                    "target_ref": "c2",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            expressions=[],
            notes=[],
        )
        with self.assertRaises(ChunkMergeError) as cm:
            self.extractor._merge_chunk_response(
                wrong_types, chunk, "source.md", ["函数", "极限"], {}, {}, {}, set()
            )
        self.assertEqual(cm.exception.item_kind, "relation")
        self.assertIn("invalid type signature", cm.exception.reason)

    def test_expression_role_mismatch_and_unknown_latex_fail_chunk(self) -> None:
        chunk = self.chunk()
        self.extractor.source_latex_candidates = {}
        role_mismatch = ChunkExtractionResponse(
            node_actions=[self.create_action()],
            relations=[],
            expressions=[
                {
                    "owner_ref": "c1",
                    "role": "claim_representation",
                    "latex_candidate_id": "latex_L1_1",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            notes=[],
        )
        with self.assertRaises(ChunkMergeError) as cm:
            self.extractor._merge_chunk_response(
                role_mismatch, chunk, "source.md", ["函数"], {}, {}, {}, set()
            )
        self.assertEqual(cm.exception.item_kind, "expression")
        self.assertIn("invalid expression role", cm.exception.reason)

        unknown_candidate = ChunkExtractionResponse(
            node_actions=[self.create_action()],
            relations=[],
            expressions=[
                {
                    "owner_ref": "c1",
                    "role": "definition_representation",
                    "latex_candidate_id": "latex_ghost",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            notes=[],
        )
        with self.assertRaises(ChunkMergeError) as cm:
            self.extractor._merge_chunk_response(
                unknown_candidate, chunk, "source.md", ["函数"], {}, {}, {}, set()
            )
        self.assertIn("unknown latex_candidate_id", cm.exception.reason)

    def test_invalid_evidence_fails_chunk(self) -> None:
        chunk = self.chunk()
        response = ChunkExtractionResponse(
            node_actions=[self.create_action(start=1, end=99)],
            relations=[],
            expressions=[],
            notes=[],
        )
        with self.assertRaises(ChunkMergeError) as cm:
            self.extractor._merge_chunk_response(
                response, chunk, "source.md", ["函数"], {}, {}, {}, set()
            )
        self.assertEqual(cm.exception.item_kind, "node_action")
        self.assertIn("Evidence", cm.exception.reason)

    def test_duplicate_ref_id_fails_chunk(self) -> None:
        chunk = self.chunk()
        response = ChunkExtractionResponse(
            node_actions=[self.create_action("c1", "函数"), self.create_action("c1", "极限")],
            relations=[],
            expressions=[],
            notes=[],
        )
        with self.assertRaisesRegex(ChunkMergeError, "duplicate ref_id"):
            self.extractor._merge_chunk_response(
                response, chunk, "source.md", ["函数", "极限"], {}, {}, {}, set()
            )

    def test_duplicate_canonical_node_in_chunk_is_reused_and_audited(self) -> None:
        chunk = self.chunk()
        response = ChunkExtractionResponse(
            node_actions=[
                self.create_action("c1", "函数"),
                self.create_action("c2", "函数"),
            ],
            relations=[],
            expressions=[],
            notes=[],
        )
        node_store: dict[str, NodeRecord] = {}
        result = self.extractor._merge_chunk_response(
            response, chunk, "source.md", ["函数"], node_store, {}, {}, set()
        )
        self.assertEqual(len(node_store), 1)
        self.assertEqual(result["created_node_count"], 1)
        self.assertEqual(result["reused_node_count"], 1)
        self.assertEqual(result["identity_conflicts"][0]["action"], "auto_reused_within_chunk")
        self.assertEqual(result["identity_conflicts"][0]["ref_id"], "c2")

    def test_reuse_of_unknown_memory_id_fails_chunk(self) -> None:
        chunk = self.chunk()
        response = ChunkExtractionResponse(
            node_actions=[
                {
                    "ref_id": "c1",
                    "action": "reuse",
                    "existing_id": "concept_未知",
                    "mention": "函数",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            relations=[],
            expressions=[],
            notes=[],
        )
        with self.assertRaises(ChunkMergeError) as cm:
            self.extractor._merge_chunk_response(
                response, chunk, "source.md", ["函数"], {}, {}, {}, set()
            )
        self.assertEqual(cm.exception.item_kind, "node_action")
        self.assertIn("was not supplied in node_memory", cm.exception.reason)

    def test_reuse_claim_kind_mismatch_preserves_canonical_kind(self) -> None:
        chunk = self.chunk()
        existing = NodeRecord(
            id=_node_id("Claim", "极限唯一性"),
            type="Claim",
            name="极限唯一性",
            aliases=[],
            description="已有断言",
            claim_kind="property",
            evidence=[],
        )
        response = ChunkExtractionResponse(
            node_actions=[
                {
                    "ref_id": "c1",
                    "action": "reuse",
                    "existing_id": existing.id,
                    "mention": "极限唯一性",
                    "type": "Claim",
                    "claim_kind": "criterion",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            relations=[],
            expressions=[],
            notes=[],
        )
        store = {existing.id: existing.model_copy(deep=True)}
        result = self.extractor._merge_chunk_response(
            response, chunk, "source.md", ["函数"], store, {}, {}, {existing.id}
        )
        self.assertEqual(result["reused_node_count"], 1)
        self.assertEqual(result["identity_conflicts"][0]["conflict_kind"], "reuse_claim_kind_mismatch")
        self.assertEqual(result["identity_conflicts"][0]["action"], "keep_existing")
        self.assertEqual(store[existing.id].claim_kind, "property")

    def test_pending_unlinked_is_recorded_for_valid_response(self) -> None:
        chunk = self.chunk()
        response = ChunkExtractionResponse(
            node_actions=[self.create_action()],
            relations=[],
            expressions=[],
            notes=[],
        )
        node_store: dict[str, NodeRecord] = {}
        summary = self.extractor._merge_chunk_response(
            response, chunk, "source.md", ["函数"], node_store, {}, {}, set()
        )
        self.assertEqual(summary["pending_node_count"], 1)
        self.assertEqual(summary["pending_node_refs"], ["c1"])
        self.assertEqual(summary["decisions"][0]["status"], "pending_unlinked")

    def test_pipeline_merge_failure_terminates_task(self) -> None:
        extractor = Extractor(save_output=False, verbose=False)
        chunk1 = DocumentChunk(
            id="chunk_1", start_line=1, end_line=1, heading_path=[], text="1: 函数"
        )
        chunk2 = DocumentChunk(
            id="chunk_2", start_line=2, end_line=2, heading_path=[], text="2: 极限"
        )
        extractor.source_context_by_line = {
            1: (["第一节"], "t1", "paragraph"),
            2: (["第一节"], "t2", "paragraph"),
        }
        extractor.source_latex_candidates = {}
        invalid_payload = {
            "node_actions": [
                {
                    "ref_id": "c1",
                    "action": "create",
                    "type": "Concept",
                    "name": "函数",
                    "description": "定义",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            "relations": [
                {
                    "source_ref": "c1",
                    "relation_type": "involves",
                    "target_ref": "ghost",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            "expressions": [],
            "notes": [],
        }
        state = {
            "input_path": "source.md",
            "output_path": "result.json",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "temperature": 0.1,
            "thinking": "enabled",
            "reasoning_effort": "medium",
            "max_tokens": 4096,
            "raw_lines": ["函数", "极限"],
            "chunks": [chunk1, chunk2],
            "diagnostics": {},
            "resume": False,
            "request_delay": 0,
            "save_output": False,
        }
        calls: list[str] = []
        client = SimpleNamespace(
            complete_json=lambda system, user, label: calls.append(label) or (
                invalid_payload if label == "chunk_1" else {"node_actions": [], "relations": [], "expressions": [], "notes": []}
            ),
            request_history=[],
        )
        captured: dict[str, Any] = {}

        def fake_finalize(
            input_path: str,
            lines: list[str],
            node_store: dict[str, NodeRecord],
            relation_store: dict,
            expression_store: dict,
            diagnostics: dict[str, Any],
        ) -> None:
            del input_path, lines, relation_store, expression_store
            captured["diagnostics"] = diagnostics

        with (
            patch.object(extractor, "_client", return_value=client),
            patch.object(extractor, "_write_chunk_cache") as write_cache,
            patch.object(extractor, "_finalize_result", side_effect=fake_finalize),
            patch.object(extractor, "_save_result"),
            patch("agents.Extractor._write_invalid_response", return_value=Path("debug.json.txt")),
        ):
            with self.assertRaises(ChunkMergeError) as cm:
                extractor._extract_sequentially(state)

        exc = cm.exception
        self.assertEqual(exc.chunk_id, "chunk_1")
        self.assertEqual(exc.item_kind, "relation")
        self.assertIn("ghost", exc.item_ref)
        self.assertIn("raw response saved to", exc.reason)
        self.assertIn("debug.json.txt", exc.reason)
        # Only chunk_1 was called; chunk_2 was never reached
        self.assertEqual(calls, ["chunk_1"])
        # finalize and save were never called after failure
        self.assertDictEqual(captured, {})
        # No cache written for the failed chunk
        write_cache.assert_not_called()



class MemoryDtoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False)

    def evidence(self, line: int = 1) -> Evidence:
        return Evidence(
            source_path="data/source.md",
            start_line=line,
            end_line=line,
            text="函数在点 x0 的邻域内有定义。",
            chunk_id="chunk_1",
            heading_path=["第一节"],
            teaching_block_id="teaching_1",
            discourse_role="paragraph",
        )

    def node(self, node_id: str, name: str) -> NodeRecord:
        return NodeRecord(
            id=node_id,
            type="Concept",
            name=name,
            aliases=[f"{name}的别名"],
            description="这是一个概念的定义，包含完整语义。" * 6,
            claim_kind=None,
            evidence=[self.evidence()],
        )

    def build(
        self, node_count: int, relation_count: int
    ) -> tuple[dict[str, NodeRecord], dict[tuple[str, str, str], RelationRecord], dict]:
        nodes = {
            f"concept_c{i:03d}": self.node(f"concept_c{i:03d}", f"概念{i}")
            for i in range(node_count)
        }
        relations: dict[tuple[str, str, str], RelationRecord] = {}
        ids = list(nodes)
        for i in range(min(relation_count, node_count - 1)):
            relations[(ids[i], "defined_in_terms_of", ids[i + 1])] = RelationRecord(
                source_id=ids[i],
                relation_type="defined_in_terms_of",
                target_id=ids[i + 1],
                evidences=[self.evidence()],
            )
        return nodes, relations, {}

    def chunk(self, node_count: int) -> DocumentChunk:
        text = "1: " + " ".join(f"概念{i}" for i in range(node_count))
        return DocumentChunk(id="chunk_1", start_line=1, end_line=1, heading_path=["第一节"], text=text)

    @staticmethod
    def serialized_bytes(items: list[object]) -> int:
        dumped = [item.model_dump(mode="json", exclude_none=True) for item in items]
        return len(json.dumps(dumped, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def test_memory_dto_serializes_only_whitelist_fields(self) -> None:
        nodes, relations, expressions = self.build(5, 4)
        node_memory, relation_memory, _ = self.extractor._build_memory_for_chunk(
            self.chunk(5),
            nodes,
            relations,
            expressions,
            set(),
            node_char_budget=50000,
            relation_char_budget=10000,
        )

        self.assertGreater(len(node_memory), 0)
        for item in node_memory:
            self.assertEqual(
                set(item.model_dump(mode="json", exclude_none=True)),
                {"id", "type", "name", "aliases", "short_description"},
            )
        for item in relation_memory:
            self.assertEqual(
                set(item.model_dump(mode="json")),
                {"source_id", "relation_type", "target_id"},
            )

    def test_memory_respects_node_char_budget(self) -> None:
        nodes, relations, expressions = self.build(60, 0)
        small_memory, _, _ = self.extractor._build_memory_for_chunk(
            self.chunk(60), nodes, relations, expressions, set(), node_char_budget=1500
        )
        large_memory, _, _ = self.extractor._build_memory_for_chunk(
            self.chunk(60), nodes, relations, expressions, set(), node_char_budget=12000
        )

        self.assertLessEqual(self.serialized_bytes(small_memory), 1500)
        self.assertLessEqual(self.serialized_bytes(large_memory), 12000)
        self.assertGreater(len(large_memory), len(small_memory))

    def test_memory_respects_relation_char_budget(self) -> None:
        nodes, relations, expressions = self.build(60, 55)
        _, small_relations, _ = self.extractor._build_memory_for_chunk(
            self.chunk(60),
            nodes,
            relations,
            expressions,
            set(),
            node_char_budget=20000,
            relation_char_budget=800,
        )
        _, large_relations, _ = self.extractor._build_memory_for_chunk(
            self.chunk(60),
            nodes,
            relations,
            expressions,
            set(),
            node_char_budget=20000,
            relation_char_budget=6000,
        )

        self.assertLessEqual(self.serialized_bytes(small_relations), 800)
        self.assertLessEqual(self.serialized_bytes(large_relations), 6000)
        self.assertGreater(len(large_relations), len(small_relations))

    def test_relation_endpoints_are_closed_in_node_memory(self) -> None:
        nodes, relations, expressions = self.build(10, 9)
        node_memory, relation_memory, _ = self.extractor._build_memory_for_chunk(
            self.chunk(10),
            nodes,
            relations,
            expressions,
            set(),
            node_char_budget=1200,
            relation_char_budget=6000,
        )

        node_ids = {item.id for item in node_memory}
        self.assertGreater(len(relation_memory), 0)
        for relation in relation_memory:
            self.assertIn(relation.source_id, node_ids)
            self.assertIn(relation.target_id, node_ids)

    def test_memory_selection_is_deterministic(self) -> None:
        nodes, relations, expressions = self.build(20, 15)
        first = self.extractor._build_memory_for_chunk(self.chunk(20), nodes, relations, expressions, set())
        second = self.extractor._build_memory_for_chunk(self.chunk(20), nodes, relations, expressions, set())

        self.assertEqual(
            [item.model_dump(mode="json") for item in first[0]],
            [item.model_dump(mode="json") for item in second[0]],
        )
        self.assertEqual(
            [item.model_dump(mode="json") for item in first[1]],
            [item.model_dump(mode="json") for item in second[1]],
        )
        self.assertEqual(first[2], second[2])

    def test_selection_diag_records_scores_and_reasons(self) -> None:
        nodes, relations, expressions = self.build(3, 0)
        _, _, selection = self.extractor._build_memory_for_chunk(self.chunk(3), nodes, relations, expressions, set())

        self.assertEqual([entry["id"] for entry in selection], [f"concept_c{i:03d}" for i in range(3)])
        self.assertTrue(all(entry["score"] > 0 for entry in selection))
        self.assertTrue(all("reasons" in entry and "source" in entry for entry in selection))

    def test_short_description_truncates_at_sentence_boundary(self) -> None:
        with_boundary = "这是第一句完整语义，包含逗号。" + "补充内容" * 30
        short = self.extractor._short_description(with_boundary)
        self.assertLessEqual(len(short), 100)
        self.assertEqual(short, "这是第一句完整语义，包含逗号。")

        no_boundary = "无标点连续内容" * 20
        self.assertEqual(len(self.extractor._short_description(no_boundary)), 100)

        plain = "短描述"
        self.assertEqual(self.extractor._short_description(plain), "短描述")
        self.assertEqual(self.extractor._short_description("   "), "")

    def test_array_bytes_exact_budget_includes_item(self) -> None:
        nodes, relations, expressions = self.build(3, 0)
        item0 = self.extractor._node_memory_item(nodes["concept_c000"])
        item1 = self.extractor._node_memory_item(nodes["concept_c001"])
        both_bytes = self.extractor._memory_array_bytes([item0, item1])
        memory_at_budget, _, _ = self.extractor._build_memory_for_chunk(
            self.chunk(3), nodes, relations, expressions, set(),
            node_char_budget=both_bytes, relation_char_budget=0,
        )
        self.assertEqual(len(memory_at_budget), 2)
        memory_one_less, _, _ = self.extractor._build_memory_for_chunk(
            self.chunk(3), nodes, relations, expressions, set(),
            node_char_budget=both_bytes - 1, relation_char_budget=0,
        )
        self.assertEqual(len(memory_one_less), 1)

    def test_compact_prompt_serialization_contains_arrays(self) -> None:
        nodes, relations, expressions = self.build(5, 4)
        node_memory, relation_memory, _ = self.extractor._build_memory_for_chunk(
            self.chunk(5), nodes, relations, expressions, set(),
            node_char_budget=5000, relation_char_budget=3000,
        )
        prompt = self.extractor._chunk_extraction_prompt(
            self.chunk(1), "source.md", node_memory, relation_memory,
        )
        self.assertLessEqual(self.extractor._memory_array_bytes(node_memory), 5000)
        self.assertLessEqual(self.extractor._memory_array_bytes(relation_memory), 3000)
        node_json = json.dumps(
            [item.model_dump(mode="json", exclude_none=True) for item in node_memory],
            ensure_ascii=False, separators=(",", ":"),
        )
        rel_json = json.dumps(
            [item.model_dump(mode="json", exclude_none=True) for item in relation_memory],
            ensure_ascii=False, separators=(",", ":"),
        )
        self.assertIn('"node_memory":' + node_json, prompt)
        self.assertIn('"relation_memory":' + rel_json, prompt)

    def test_relation_excluded_when_budget_exceeded_despite_closed_endpoints(self) -> None:
        nodes, relations, expressions = self.build(10, 9)
        first_rel = self.extractor._relation_memory_item(
            relations[("concept_c000", "defined_in_terms_of", "concept_c001")]
        )
        first_rel_bytes = self.extractor._memory_array_bytes([first_rel])
        node_memory, relation_memory, _ = self.extractor._build_memory_for_chunk(
            self.chunk(10), nodes, relations, expressions, set(),
            node_char_budget=20000, relation_char_budget=first_rel_bytes - 1,
        )
        node_ids = {item.id for item in node_memory}
        self.assertIn("concept_c000", node_ids)
        self.assertIn("concept_c001", node_ids)
        self.assertEqual(len(relation_memory), 0)

    def test_memory_budget_counts_multibyte_utf8_bytes(self) -> None:
        nodes: dict[str, NodeRecord] = {}
        for i in range(3):
            name = f"概念{i}"
            nodes[f"node_{i}"] = NodeRecord(
                id=f"node_{i}",
                type="Concept",
                name=name,
                aliases=[],
                description="",
                claim_kind=None,
                evidence=[],
                short_description="",
                source_chunks=[],
                heading_paths=[],
                chunk_heading_paths=[],
                section_labels=[],
                teaching_blocks=[],
            )
        item = self.extractor._node_memory_item(nodes["node_0"])
        item_bytes = self.extractor._memory_item_bytes(item)
        expected = len(
            json.dumps(
                item.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        self.assertEqual(item_bytes, expected)
        arr_bytes = self.extractor._memory_array_bytes([self.extractor._node_memory_item(nodes[f"node_{i}"]) for i in range(2)])
        node_memory, _, _ = self.extractor._build_memory_for_chunk(
            self.chunk(3), nodes, {}, {}, set(),
            node_char_budget=arr_bytes, relation_char_budget=0,
        )
        self.assertEqual(len(node_memory), 2)


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


class VerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor(save_output=False, verbose=False, verify=True)
        self.evidence1 = Evidence(
            source_path="source.md",
            start_line=1,
            end_line=1,
            text="函数定义",
            chunk_id="chunk_1",
            teaching_block_id="t1",
            discourse_role="paragraph",
        )
        self.evidence2 = Evidence(
            source_path="source.md",
            start_line=2,
            end_line=2,
            text="极限定义",
            chunk_id="chunk_2",
            teaching_block_id="t2",
            discourse_role="paragraph",
        )
        self.node1 = KGNode(
            id="concept_函数_test1",
            type="Concept",
            name="函数",
            aliases=[],
            description="映射",
            claim_kind=None,
            evidence=[self.evidence1],
        )
        self.node2 = KGNode(
            id="concept_极限_test2",
            type="Concept",
            name="极限",
            aliases=[],
            description="趋近值",
            claim_kind=None,
            evidence=[self.evidence2],
        )
        self.result = ExtractionResult(
            schema_version="maf-kg.semantic.v1.5.9",
            source_path="source.md",
            title="第一章",
            provider="deepseek",
            model="deepseek-v4-pro",
            nodes=[self.node1, self.node2],
            relations=[
                KGRelation(
                    id="relation_x",
                    type="defined_in_terms_of",
                    source_id=self.node1.id,
                    target_id=self.node2.id,
                    evidence=[self.evidence2],
                )
            ],
            math_expressions=[],
            diagnostics={},
        )
        self.chunk1 = DocumentChunk(
            id="chunk_1", start_line=1, end_line=1, heading_path=[], text="1: 函数"
        )
        self.chunk2 = DocumentChunk(
            id="chunk_2", start_line=2, end_line=2, heading_path=[], text="2: 极限"
        )
        self.extractor.source_context_by_line = {
            1: (["第一节"], "t1", "paragraph"),
            2: (["第一节"], "t2", "paragraph"),
        }
        self.extractor.source_latex_candidates = {}

    def test_verify_prompt_contains_all_nodes_and_evidence(self) -> None:
        prompt = self.extractor._build_verify_prompt(self.result)
        self.assertIn("concept_函数_test1", prompt)
        self.assertIn("concept_极限_test2", prompt)
        self.assertIn("函数定义", prompt)
        self.assertIn("defined_in_terms_of", prompt)
        parsed = json.loads(prompt)
        self.assertEqual(parsed["node_count"], 2)
        self.assertEqual(parsed["relation_count"], 1)
        self.assertEqual(parsed["relations"][0]["source_name"], "函数")
        self.assertEqual(parsed["relations"][0]["target_name"], "极限")

    def test_verify_prompt_evidence_truncation(self) -> None:
        long_text = "定义" * 400
        node = KGNode(
            id="concept_长定义_test",
            type="Concept",
            name="长定义",
            aliases=[],
            description="",
            claim_kind=None,
            evidence=[
                Evidence(
                    source_path="source.md",
                    start_line=1,
                    end_line=1,
                    text=long_text,
                    chunk_id="chunk_1",
                    teaching_block_id="t1",
                    discourse_role="paragraph",
                )
            ],
        )
        result = self.result.model_copy(deep=True)
        result.nodes = [node]
        prompt = self.extractor._build_verify_prompt(result)
        parsed = json.loads(prompt)
        self.assertLessEqual(len(parsed["nodes"][0]["evidence"][0]["text"]), 500)

    def test_verify_response_parses_valid_verdicts(self) -> None:
        payload = {
            "verdicts": [
                {
                    "ref_id": "n1",
                    "kind": "node",
                    "verdict": 0,
                    "reason": "unsupported",
                    "correction": "fix it",
                },
                {"ref_id": "r1", "kind": "relation", "verdict": 1},
            ],
            "notes": ["pattern observed"],
        }
        parsed = VerifyResponse.model_validate(payload)
        self.assertEqual(len(parsed.verdicts), 2)
        self.assertEqual(parsed.verdicts[0].verdict, 0)
        self.assertEqual(parsed.verdicts[0].correction, "fix it")
        self.assertEqual(parsed.verdicts[1].verdict, 1)
        self.assertEqual(parsed.notes, ["pattern observed"])

    def test_verify_result_preserves_original_on_api_failure(self) -> None:
        fake_client = SimpleNamespace(
            complete_json=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("api down")
            ),
            request_history=[],
        )
        state = {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "request_timeout": 1,
            "max_retries": 0,
            "no_proxy": True,
        }
        with patch.object(self.extractor, "_verify_client", return_value=fake_client):
            out = self.extractor._verify_result(self.result, state)
        self.assertIs(out, self.result)
        self.assertEqual(out.diagnostics["verify"]["status"], "failed")
        self.assertIn("RuntimeError", out.diagnostics["verify"]["error"])
        self.assertEqual(len(out.nodes), 2)
        self.assertEqual(len(out.relations), 1)

    def test_verify_result_preserves_original_on_parse_failure(self) -> None:
        fake_client = SimpleNamespace(
            complete_json=lambda *args, **kwargs: {
                "verdicts": [
                    {"ref_id": "n1", "kind": "node", "verdict": 5},
                ]
            },
            request_history=[],
        )
        state = {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "request_timeout": 1,
            "max_retries": 0,
            "no_proxy": True,
        }
        with patch.object(self.extractor, "_verify_client", return_value=fake_client):
            out = self.extractor._verify_result(self.result, state)
        self.assertIs(out, self.result)
        self.assertEqual(out.diagnostics["verify"]["status"], "failed")
        self.assertEqual(len(out.nodes), 2)

    def test_verify_result_records_verdicts_on_success(self) -> None:
        verdict_payload = {
            "verdicts": [
                {"ref_id": self.node1.id, "kind": "node", "verdict": 1},
                {"ref_id": "relation_x", "kind": "relation", "verdict": 0, "reason": "bad"},
            ],
            "notes": [],
        }
        fake_client = SimpleNamespace(
            complete_json=lambda *args, **kwargs: verdict_payload,
            request_history=[],
        )
        state = {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "request_timeout": 1,
            "max_retries": 0,
            "no_proxy": True,
        }
        with patch.object(self.extractor, "_verify_client", return_value=fake_client):
            out = self.extractor._verify_result(self.result, state)
        self.assertEqual(out.diagnostics["verify"]["status"], "completed")
        self.assertEqual(out.diagnostics["verify"]["flagged_node_count"], 0)
        self.assertEqual(out.diagnostics["verify"]["flagged_relation_count"], 1)

    def test_collect_flagged_chunks_identifies_correct_chunks(self) -> None:
        self.result.diagnostics["verify"] = {
            "verdicts": [
                {"ref_id": self.node1.id, "kind": "node", "verdict": 0},
                {"ref_id": "relation_x", "kind": "relation", "verdict": 1},
            ]
        }
        flagged = self.extractor._collect_flagged_chunks(
            self.result, [self.chunk1, self.chunk2]
        )
        self.assertEqual([chunk.id for chunk in flagged], ["chunk_1"])

    def test_correction_applies_only_to_flagged_chunks(self) -> None:
        self.result.diagnostics["verify"] = {
            "verdicts": [
                {
                    "ref_id": self.node1.id,
                    "kind": "node",
                    "verdict": 0,
                    "reason": "evidence does not define 函数",
                    "correction": "re-extract",
                }
            ]
        }
        calls: list[str] = []
        client = SimpleNamespace(
            complete_json=lambda system, user, label: calls.append(label) or {
                "node_actions": [],
                "relations": [],
                "expressions": [],
                "notes": [],
            },
            request_history=[],
        )
        state = {
            "input_path": "source.md",
            "output_path": "result.json",
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "base_url": "https://api.deepseek.com",
            "temperature": 0.1,
            "thinking": "enabled",
            "reasoning_effort": "medium",
            "max_tokens": 4096,
            "request_timeout": 1,
            "max_retries": 0,
            "no_proxy": True,
            "resume": False,
            "request_delay": 0,
            "memory_node_bytes": 12000,
            "memory_relation_bytes": 6000,
            "diagnostics": {},
        }
        with (
            patch.object(self.extractor, "_client", return_value=client),
            patch.object(self.extractor, "_write_chunk_cache"),
        ):
            out = self.extractor._correct_result(
                self.result, [self.chunk1], ["函数", "极限"], state
            )
        self.assertEqual(calls, ["chunk_1_correct"])
        self.assertEqual(
            out.diagnostics["verify_corrections"]["corrected_chunk_ids"], ["chunk_1"]
        )
        self.assertEqual(out.diagnostics["verify_corrections"]["guidance_count"], 1)

    def test_verify_disabled_preserves_original_flow(self) -> None:
        extractor = Extractor(save_output=False, verbose=False, verify=False)
        self.assertEqual(extractor.verify_prompt, "")
        extractor.source_context_by_line = {
            1: (["第一节"], "t1", "paragraph"),
        }
        extractor.source_latex_candidates = {}
        payload = {
            "node_actions": [
                {
                    "ref_id": "c1",
                    "action": "create",
                    "type": "Concept",
                    "name": "函数",
                    "description": "定义",
                    "evidence": {"start_line": 1, "end_line": 1},
                }
            ],
            "relations": [],
            "expressions": [],
            "notes": [],
        }
        chunk = DocumentChunk(
            id="chunk_1", start_line=1, end_line=1, heading_path=[], text="1: 函数"
        )
        state = {
            "input_path": "source.md",
            "output_path": "result.json",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "temperature": 0.1,
            "thinking": "enabled",
            "reasoning_effort": "medium",
            "max_tokens": 4096,
            "raw_lines": ["函数"],
            "chunks": [chunk],
            "diagnostics": {},
            "resume": False,
            "request_delay": 0,
            "save_output": False,
            "memory_node_bytes": 12000,
            "memory_relation_bytes": 6000,
            "use_response_format": True,
            "no_proxy": True,
            "max_chunk_chars": 3200,
            "chunk_strategy": "budget",
            "chunk_context_before_lines": 6,
            "chunk_context_after_lines": 4,
            "experimental_chunk_spans": (),
            "max_retries": 0,
            "request_timeout": 1,
            "include_exercises": False,
            "verbose": False,
            "run_started_at": "2026-08-07T00:00:00+00:00",
        }
        calls: list[str] = []
        client = SimpleNamespace(
            complete_json=lambda system, user, label: calls.append(label) or payload,
            request_history=[],
        )
        with (
            patch.object(extractor, "_client", return_value=client),
            patch.object(extractor, "_write_chunk_cache"),
        ):
            out_state = extractor._extract_sequentially(state)
        self.assertEqual(calls, ["chunk_1"])
        self.assertNotIn("verify", out_state["result"].diagnostics)


if __name__ == "__main__":
    unittest.main()
