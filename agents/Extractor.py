#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
import requests
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing_extensions import TypedDict

warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph\\..*")
warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects` will change.*")
try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except Exception:
    pass

from langgraph.graph import END, StateGraph

DEFINED_TERM_PATTERNS = [
    re.compile(r"(?:那么|则)?\s*称\s*(.{0,40}?)\s*为\s*(.{2,60}?)(?:[，。,．；;.:：]|$)"),
    re.compile(r"(?:那么|则)?\s*(.{0,80}?)\s*(?:就)?(?:叫做|称为)\s*(.{2,60}?)(?:[，。,．；;.:：]|$)"),
    re.compile(r"记作\s*(\$.+?\$)"),
    re.compile(r"统称为\s*(.{2,60}?)(?:[，。,．；;.:：]|$)"),
]

SEGMENT_META_PATTERN = re.compile(
    r"我们(?:指出|强调)|(?:需要)?注意(?:到)?(?:的是)?|这里(?:我们)?(?:指出|强调)|特别(?:指出|强调)"
)
SEGMENT_RESTATE_PATTERN = re.compile(
    r"可(?:以)?(?:简单地)?表述为|简单地说(?:就是)?|也就是说|等价(?:地|于)|换言之|"
    r"也可以(?:写|记)为|也可(?:写|记)为|记作|记为"
)
SEGMENT_GEOMETRIC_PATTERN = re.compile(
    r"从几何上来说|从图形上看|几何意义|直观(?:上|地)|图形上看|图\s*\d"
)
MATH_LINE_PATTERN = re.compile(r"\$\$|\$")


def classify_definition_segments(
    lines: list[tuple[int, str]],
) -> tuple[tuple[str, int, int], ...]:
    """Label the discourse segments of a definition unit:
    definition -> meta_annotation -> symbolic_restatement -> geometric_interpretation.
    Roles are navigation hints only, derived from template signal words."""
    roles: list[tuple[str, int]] = []
    for line_no, text in lines:
        stripped = text.strip()
        role = "exposition"
        # The definitional statement itself ("定义1 设...") stays exposition even
        # though it contains math; only later restatements are symbolic.
        if stripped.startswith("定义"):
            role = "exposition"
        elif stripped.startswith("![") or SEGMENT_GEOMETRIC_PATTERN.search(stripped):
            role = "geometric_interpretation"
        elif SEGMENT_META_PATTERN.search(stripped):
            role = "meta_annotation"
        elif MATH_LINE_PATTERN.search(stripped) or SEGMENT_RESTATE_PATTERN.search(stripped):
            role = "symbolic_restatement"
        roles.append((role, line_no))
    segments: list[tuple[str, int, int]] = []
    for role, line_no in roles:
        if segments and segments[-1][0] == role and segments[-1][2] == line_no - 1:
            segments[-1] = (role, segments[-1][1], line_no)
        else:
            segments.append((role, line_no, line_no))
    return tuple(segments)

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_INPUT = ROOT / "data" / "raw" / "第一章第一节_映射与函数.md"
DEFAULT_OUTPUT = ROOT / "KG" / "第一章第一节_映射与函数_semantic_v1_7_0.json"
SCHEMA_VERSION = "maf-kg.semantic.v1.7.0"
CHUNK_STRATEGIES = ["budget", "teaching-units"]
RELATION_SIGNATURES: dict[str, tuple[tuple[str, str], ...]] = {
    "involves": (("Claim", "Concept"),),
    "uses": (("Method", "Concept"), ("Method", "Claim"), ("Method", "Method")),
    "establishes": (("Method", "Claim"),),
    "derived_from": (("Claim", "Claim"),),
    "equivalent_to": (("Claim", "Claim"),),
    "special_case_of": (("Concept", "Concept"),),
    "defined_in_terms_of": (("Concept", "Concept"),),
}

NodeType = Literal["Concept", "Claim", "Method"]
ClaimKind = Literal["property", "criterion", "identity", "result"]
LogicalOperator = Literal["implies", "iff"]
ConverseStatus = Literal["proven", "disproved", "unstated"]
RelationType = Literal[
    "involves",
    "uses",
    "establishes",
    "derived_from",
    "equivalent_to",
    "special_case_of",
    "defined_in_terms_of",
]
ExpressionRole = Literal["definition_representation", "claim_representation", "method_template"]

class Evidence(BaseModel):
    source_path: str
    start_line: int
    end_line: int
    text: str
    chunk_id: str
    heading_path: list[str] = Field(default_factory=list)
    teaching_block_id: str
    discourse_role: str

class EvidenceRef(BaseModel):
    start_line: int
    end_line: int

class LogicalForm(BaseModel):
    operator: LogicalOperator
    premises: list[str]
    conclusion: list[str]
    converse_status: ConverseStatus | None = None

    @model_validator(mode="after")
    def validate_logical_form(self) -> "LogicalForm":
        premises = [re.sub(r"\s+", " ", premise).strip() for premise in self.premises]
        if not premises or any(not premise for premise in premises):
            raise ValueError("logical_form premises must contain non-empty source-grounded conditions")
        normalized_premises = _unique_preserve_order(premises)
        conclusions = [re.sub(r"\s+", " ", c).strip() for c in self.conclusion]
        if not conclusions or any(not c for c in conclusions):
            raise ValueError("logical_form conclusion must contain non-empty source-grounded statements")
        normalized_conclusions = _unique_preserve_order(conclusions)
        if self.operator == "iff" and self.converse_status is not None:
            raise ValueError("iff logical_form must omit converse_status")
        self.premises = normalized_premises
        self.conclusion = normalized_conclusions
        return self

class KGNode(BaseModel):
    id: str
    type: NodeType
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    claim_kind: ClaimKind | None = None
    logical_form: LogicalForm | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim_fields(self) -> "KGNode":
        if self.type != "Claim" and self.logical_form is not None:
            raise ValueError("logical_form is valid only for Claim nodes")
        return self

class KGRelation(BaseModel):
    id: str
    type: RelationType
    source_id: str
    target_id: str
    evidence: list[Evidence] = Field(default_factory=list)

class MathExpression(BaseModel):
    id: str
    owner_id: str
    role: ExpressionRole
    latex: str
    evidence: list[Evidence] = Field(default_factory=list)

class ExtractionResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source_path: str
    title: str
    provider: str
    model: str
    nodes: list[KGNode] = Field(default_factory=list)
    relations: list[KGRelation] = Field(default_factory=list)
    math_expressions: list[MathExpression] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

class NodeMemoryItem(BaseModel):
    id: str
    type: NodeType
    name: str
    aliases: list[str] = Field(default_factory=list)
    claim_kind: ClaimKind | None = None
    logical_form: LogicalForm | None = None
    short_description: str = ""

    @model_validator(mode="after")
    def validate_claim_fields(self) -> "NodeMemoryItem":
        if self.type != "Claim" and self.logical_form is not None:
            raise ValueError("logical_form is valid only for Claim nodes")
        return self

class RelationMemoryItem(BaseModel):
    source_id: str
    relation_type: RelationType
    target_id: str

class NodeAction(BaseModel):
    ref_id: str
    action: Literal["reuse", "create"]
    existing_id: str | None = None
    mention: str = ""
    type: NodeType | None = None
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    claim_kind: ClaimKind | None = None
    logical_form: LogicalForm | None = None
    evidence: EvidenceRef
    reason: str = ""

    @model_validator(mode="after")
    def validate_action(self) -> "NodeAction":
        if self.action == "reuse":
            if not self.existing_id:
                raise ValueError("reuse actions require existing_id")
            if not self.mention.strip():
                raise ValueError("reuse actions require the exact current-chunk mention")
        else:
            if self.existing_id is not None:
                raise ValueError("create actions must not include existing_id")
            if self.type is None or not self.name.strip():
                raise ValueError("create actions require type and name")
            if self.type == "Claim" and self.claim_kind is None:
                raise ValueError("create Claim actions require claim_kind")
            if self.type != "Claim" and self.claim_kind is not None:
                raise ValueError("claim_kind is valid only for Claim nodes")
            if self.type != "Claim" and self.logical_form is not None:
                raise ValueError("logical_form is valid only for Claim nodes")
        if not self.ref_id.strip():
            raise ValueError("node action ref_id must not be empty")
        return self

class ProposedRelation(BaseModel):
    source_ref: str
    relation_type: RelationType
    target_ref: str
    evidence: EvidenceRef

class ProposedExpression(BaseModel):
    owner_ref: str
    role: ExpressionRole
    latex_candidate_id: str
    evidence: EvidenceRef

class ChunkExtractionResponse(BaseModel):
    node_actions: list[NodeAction] = Field(default_factory=list)
    relations: list[ProposedRelation] = Field(default_factory=list)
    expressions: list[ProposedExpression] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class LogicalFormReviewItem(BaseModel):
    node_id: str
    logical_form: LogicalForm | None = None

class LogicalFormReviewResponse(BaseModel):
    items: list[LogicalFormReviewItem]

class NodeRecord(BaseModel):
    id: str
    type: NodeType
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    claim_kind: ClaimKind | None = None
    logical_form: LogicalForm | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim_fields(self) -> "NodeRecord":
        if self.type != "Claim" and self.logical_form is not None:
            raise ValueError("logical_form is valid only for Claim nodes")
        return self

class RelationRecord(BaseModel):
    source_id: str
    relation_type: RelationType
    target_id: str
    evidences: list[Evidence] = Field(default_factory=list)

class MathExpressionRecord(BaseModel):
    id: str
    owner_id: str
    role: ExpressionRole
    latex: str
    evidences: list[Evidence] = Field(default_factory=list)

class VerificationVerdict(BaseModel):
    ref_id: str
    kind: Literal["node", "relation"]
    verdict: Literal[0, 1]
    reason: str = ""
    correction: str = ""

class VerifyResponse(BaseModel):
    verdicts: list[VerificationVerdict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

@dataclass
class DocumentBlock:
    start_line: int
    end_line: int
    heading_path: list[str]
    lines: list[tuple[int, str]]
    kind: str
    teaching_block_id: str = ""
    segments: tuple[tuple[str, int, int], ...] = ()
    @property
    def char_count(self) -> int:
        return sum(len(f"{n}: {t}") + 1 for n, t in self.lines)

@dataclass
class DocumentChunk:
    id: str
    start_line: int
    end_line: int
    heading_path: list[str]
    text: str
    context_before_text: str = ""
    context_after_text: str = ""
    block_count: int = 0
    block_types: tuple[str, ...] = ()
    teaching_block_ids: tuple[str, ...] = ()
    teaching_blocks: tuple[dict[str, int | str | list], ...] = ()
    break_reason: str = ""
    teaching_unit_kind: str = ""
    anchor_block_ids: tuple[str, ...] = ()

@dataclass
class TeachingUnitGroup:
    blocks: list[DocumentBlock]
    unit_kind: str
    grouping_reason: str
    anchor_block_ids: tuple[str, ...] = ()
    skipped: bool = False

class ExtractionState(TypedDict, total=False):
    input_path: str
    output_path: str
    provider: str
    model: str
    base_url: str
    api_key_env: str | None
    temperature: float
    max_chunk_chars: int
    chunk_strategy: str
    chunk_context_before_lines: int
    chunk_context_after_lines: int
    experimental_chunk_spans: tuple[tuple[int, int], ...]
    max_retries: int
    request_timeout: float
    max_tokens: int
    memory_node_bytes: int
    memory_relation_bytes: int
    save_output: bool
    use_response_format: bool
    no_proxy: bool
    request_delay: float
    resume: bool
    include_exercises: bool
    verbose: bool
    thinking: str
    reasoning_effort: str
    coverage_review: bool
    logical_form_review: bool
    run_started_at: str
    raw_lines: list[str]
    chunks: list[DocumentChunk]
    result: ExtractionResult
    diagnostics: dict[str, Any]


def _env_first(names: list[str]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None

def _load_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()

def _parse_experimental_chunk_spans(value: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for item in value.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", item)
        if not match:
            raise argparse.ArgumentTypeError(
                "experimental chunk spans must use START-END pairs separated by commas"
            )
        start_line, end_line = (int(part) for part in match.groups())
        if start_line < 1 or end_line < start_line:
            raise argparse.ArgumentTypeError(
                f"invalid experimental chunk span: {start_line}-{end_line}"
            )
        spans.append((start_line, end_line))
    return tuple(spans)

def _stable_hash(*parts: str) -> str:
    return hashlib.sha1("::".join(p.strip() for p in parts).encode("utf-8")).hexdigest()[:14]

def _slug(text: str, limit: int = 48) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text).strip("_")[:limit] or "item"

def _document_block_text(block: DocumentBlock) -> str:
    return " ".join(text.strip() for _, text in block.lines if text.strip())

def _group_char_count(blocks: list[DocumentBlock]) -> int:
    return sum(block.char_count for block in blocks)

def _theorem_label(block: DocumentBlock) -> tuple[str, bool] | None:
    if block.kind != "theorem":
        return None
    text = re.sub(r"^\s*[-+*]\s+", "", _document_block_text(block))
    match = re.match(
        r"^\s*定理\s*([0-9一二三四五六七八九十百]+)?\s*([\u2032'’]?)",
        text,
    )
    if not match:
        return None
    return (match.group(1) or "", bool(match.group(2)))

def _is_theorem_leadin(block: DocumentBlock) -> bool:
    if block.kind not in {"paragraph", "formula_exposition"}:
        return False
    text = _document_block_text(block).strip()
    return bool(
        re.search(r"(下面|以下|下列|下述).{0,24}(定理|结论)", text)
        or re.search(r"有如下.{0,16}(定理|结论)", text)
        or re.search(r"(?:即|如下)\s*[。.]?$", text)
    )

def _is_standalone_core_statement(block: DocumentBlock) -> bool:
    if block.kind not in {"paragraph", "formula_exposition", "property"}:
        return False
    text = _document_block_text(block).strip()
    conditional = bool(
        re.search(r"如果.+(?:那么|则)", text)
        or re.search(r"若.+(?:那么|则)", text)
        or "充分必要条件" in text
    )
    general_marker = bool(re.match(r"^\s*(一般地(?:说)?|由此|根据.+(?:可知|容易证明))", text))
    return conditional and general_marker

def _teaching_unit_kind(blocks: list[DocumentBlock], reason: str) -> str:
    substantive = [block for block in blocks if block.kind != "heading"]
    if not substantive:
        return "heading_only"
    theorem_blocks = [block for block in substantive if block.kind == "theorem"]
    definition_blocks = [block for block in substantive if block.kind == "definition"]
    if definition_blocks:
        if theorem_blocks:
            return "definition_theorem_family"
        if len(definition_blocks) > 1:
            return "definition_group"
        return "definition"
    if theorem_blocks:
        labels = [_theorem_label(block) for block in theorem_blocks]
        root_count = sum(label is None or not label[1] for label in labels)
        has_dependents = any(block.kind == "corollary" for block in substantive) or any(
            label is not None and label[1] for label in labels
        )
        if has_dependents:
            return "theorem_family"
        if root_count > 1:
            return "theorem_group"
        return "theorem"
    if any(block.kind == "corollary" for block in substantive):
        return "corollary"
    if any(block.kind == "property" for block in substantive):
        return "property"
    if reason == "core_statement_before_theorem" or (
        len(substantive) == 1 and _is_standalone_core_statement(substantive[0])
    ):
        return "core_statement"
    return "exposition"

def _teaching_unit_anchor_ids(blocks: list[DocumentBlock], unit_kind: str) -> tuple[str, ...]:
    substantive = [block for block in blocks if block.kind != "heading"]
    if unit_kind.startswith("theorem") or unit_kind == "definition_theorem_family":
        anchors = [
            block
            for block in substantive
            if block.kind in {"theorem", "corollary", "definition"}
        ]
    elif unit_kind in {"property", "corollary", "core_statement", "definition", "definition_group"}:
        anchors = [
            block
            for block in substantive
            if block.kind in {"property", "corollary", "definition"}
            or _is_standalone_core_statement(block)
        ]
    elif unit_kind == "example_sequence":
        anchors = [block for block in substantive if block.kind == "example"]
    else:
        anchors = substantive[:1]
    return tuple(block.teaching_block_id for block in anchors)

def _make_teaching_unit_group(
    blocks: list[DocumentBlock],
    reason: str,
    *,
    skipped: bool = False,
    unit_kind: str | None = None,
) -> TeachingUnitGroup:
    resolved_kind = unit_kind or ("example_sequence" if skipped else _teaching_unit_kind(blocks, reason))
    return TeachingUnitGroup(
        blocks=list(blocks),
        unit_kind=resolved_kind,
        grouping_reason=reason,
        anchor_block_ids=_teaching_unit_anchor_ids(blocks, resolved_kind),
        skipped=skipped,
    )

def _build_teaching_unit_groups(
    blocks: list[DocumentBlock],
    max_chars: int,
) -> list[TeachingUnitGroup]:
    if max_chars < 1:
        raise ValueError("max_chunk_chars must be positive")

    groups: list[TeachingUnitGroup] = []
    current: list[DocumentBlock] = []
    pending_headings: list[DocumentBlock] = []
    theorem_root_count = 0
    theorem_has_dependents = False

    def current_substantive() -> list[DocumentBlock]:
        return [block for block in current if block.kind != "heading"]

    def current_has_theorem() -> bool:
        return any(block.kind == "theorem" for block in current)

    def seed_pending_headings() -> None:
        if pending_headings:
            current.extend(pending_headings)
            pending_headings.clear()

    def reset_theorem_state() -> None:
        nonlocal theorem_root_count, theorem_has_dependents
        theorem_root_count = 0
        theorem_has_dependents = False

    def flush(reason: str) -> None:
        nonlocal current
        if current:
            groups.append(_make_teaching_unit_group(current, reason))
        current = []
        reset_theorem_state()

    def last_root_label() -> str | None:
        for candidate in reversed(current):
            label = _theorem_label(candidate)
            if label is not None and not label[1]:
                return label[0]
        return None

    def theorem_has_following_dependent(index: int, label: tuple[str, bool] | None) -> bool:
        if index + 1 >= len(blocks):
            return False
        following = blocks[index + 1]
        if following.kind == "corollary":
            return True
        following_label = _theorem_label(following)
        return bool(
            label is not None
            and following_label is not None
            and not label[1]
            and following_label[1]
            and label[0] == following_label[0]
        )

    def should_attach_short_preface() -> bool:
        substantive = current_substantive()
        if not substantive:
            return True
        if _is_theorem_leadin(substantive[-1]):
            return True
        preface_limit = min(800, max(120, max_chars // 4))
        return (
            any(block.kind == "heading" for block in current)
            and len(substantive) <= 1
            and _group_char_count(current) <= preface_limit
            and not _is_standalone_core_statement(substantive[-1])
        )

    i = 0
    while i < len(blocks):
        block = blocks[i]

        if block.kind == "heading":
            flush("heading_boundary")
            pending_headings.append(block)
            i += 1
            continue

        if block.kind == "example":
            flush("example_boundary")
            example_blocks = list(pending_headings)
            pending_headings.clear()
            while i < len(blocks) and blocks[i].kind == "example":
                example_blocks.append(blocks[i])
                i += 1
            groups.append(
                _make_teaching_unit_group(
                    example_blocks,
                    "explicit_example_sequence",
                    unit_kind="example_sequence",
                )
            )
            continue

        if block.kind == "theorem":
            label = _theorem_label(block)
            following_dependent = theorem_has_following_dependent(i, label)
            if current_has_theorem():
                variant_of_current = bool(
                    label is not None
                    and label[1]
                    and label[0] == last_root_label()
                )
                if variant_of_current:
                    current.append(block)
                    theorem_has_dependents = True
                    i += 1
                    continue

                combined_chars = _group_char_count(current) + block.char_count
                if (
                    theorem_has_dependents
                    or theorem_root_count >= 2
                    or following_dependent
                    or combined_chars > max_chars
                ):
                    reason = "char_budget" if combined_chars > max_chars else "theorem_cluster_boundary"
                    flush(reason)
                    seed_pending_headings()
                current.append(block)
                theorem_root_count = 1
                theorem_has_dependents = False
                i += 1
                continue

            seed_pending_headings()
            if current_substantive() and not should_attach_short_preface():
                flush("theorem_boundary")
                seed_pending_headings()
            current.append(block)
            theorem_root_count = 1
            theorem_has_dependents = False
            i += 1
            continue

        if block.kind == "corollary":
            seed_pending_headings()
            if current_has_theorem():
                current.append(block)
                theorem_has_dependents = True
            else:
                if current_substantive():
                    flush("corollary_boundary")
                    seed_pending_headings()
                current.append(block)
                theorem_has_dependents = True
            i += 1
            continue

        if block.kind == "property":
            flush("property_boundary")
            seed_pending_headings()
            current.append(block)
            flush("property_unit")
            i += 1
            continue

        if block.kind == "definition":
            seed_pending_headings()
            def_count = 1
            while i + def_count < len(blocks) and blocks[i + def_count].kind == "definition":
                def_count += 1
            family_end = i + def_count
            if family_end < len(blocks) and blocks[family_end].kind == "theorem":
                family_end += 1
                while family_end < len(blocks) and blocks[family_end].kind in {"corollary", "proof"}:
                    family_end += 1
                family = blocks[i:family_end]
                if _group_char_count(current) + sum(b.char_count for b in family) <= max_chars:
                    if current_substantive():
                        flush("definition_family_boundary")
                        seed_pending_headings()
                    current.extend(family)
                    theorem_root_count = 1
                    theorem_has_dependents = any(b.kind == "corollary" for b in family)
                    i = family_end
                    continue
            if current_substantive():
                flush("definition_boundary")
                seed_pending_headings()
            current.extend(blocks[i : i + def_count])
            i += def_count
            continue

        if block.kind == "proof" and current_has_theorem():
            current.append(block)
            i += 1
            continue

        if current_has_theorem():
            flush("post_theorem_boundary")

        seed_pending_headings()
        next_is_theorem = i + 1 < len(blocks) and blocks[i + 1].kind == "theorem"
        if _is_theorem_leadin(block) and next_is_theorem:
            substantive = current_substantive()
            if substantive and (
                _is_standalone_core_statement(substantive[-1])
                or _group_char_count(current) > max(600, max_chars // 3)
            ):
                reason = (
                    "core_statement_before_theorem"
                    if _is_standalone_core_statement(substantive[-1])
                    else "theorem_leadin_boundary"
                )
                flush(reason)
                seed_pending_headings()
            current.append(block)
            i += 1
            continue

        if current_substantive() and _group_char_count(current) + block.char_count > max_chars:
            flush("char_budget")
            seed_pending_headings()
        current.append(block)
        i += 1

    flush("end")
    if pending_headings:
        groups.append(_make_teaching_unit_group(pending_headings, "end_heading"))

    expected_ids = [id(block) for block in blocks]
    assigned_ids = [id(block) for group in groups for block in group.blocks]
    counts: dict[int, int] = {}
    for block_id in assigned_ids:
        counts[block_id] = counts.get(block_id, 0) + 1
    missing = [block for block in blocks if counts.get(id(block), 0) == 0]
    duplicated = [block for block in blocks if counts.get(id(block), 0) > 1]
    unexpected = [block_id for block_id in counts if block_id not in set(expected_ids)]
    if missing or duplicated or unexpected or len(assigned_ids) != len(expected_ids):
        details = {
            "missing": [(block.start_line, block.end_line, block.kind) for block in missing],
            "duplicated": [(block.start_line, block.end_line, block.kind) for block in duplicated],
            "unexpected_count": len(unexpected),
        }
        raise ValueError(f"Teaching-unit coverage validation failed: {details}")
    return groups


_LATEX_SEMANTIC_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\longleftrightarrow", "⇔"),
    (r"\Longleftrightarrow", "⇔"),
    (r"\leftrightarrow", "↔"),
    (r"\Leftrightarrow", "⇔"),
    (r"\longrightarrow", "→"),
    (r"\Longrightarrow", "⇒"),
    (r"\rightarrow", "→"),
    (r"\Rightarrow", "⇒"),
    (r"\longleftarrow", "←"),
    (r"\Longleftarrow", "⇐"),
    (r"\leftarrow", "←"),
    (r"\Leftarrow", "⇐"),
    (r"\geqslant", "≥"),
    (r"\leqslant", "≤"),
    (r"\subseteq", "⊆"),
    (r"\supseteq", "⊇"),
    (r"\implies", "⇒"),
    (r"\infty", "∞"),
    (r"\forall", "∀"),
    (r"\exists", "∃"),
    (r"\notin", "∉"),
    (r"\subset", "⊂"),
    (r"\supset", "⊃"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\partial", "∂"),
    (r"\propto", "∝"),
    (r"\sqrt", "√"),
    (r"\circ", "∘"),
    (r"\sum", "∑"),
    (r"\prod", "∏"),
    (r"\int", "∫"),
    (r"\neq", "≠"),
    (r"\ne", "≠"),
    (r"\geq", "≥"),
    (r"\leq", "≤"),
    (r"\iff", "⇔"),
    (r"\to", "→"),
    (r"\in", "∈"),
    (r"\pm", "±"),
    (r"\mp", "∓"),
)

_SEMANTIC_SYMBOL_TOKENS: dict[str, str] = {
    "→": "<to>",
    "←": "<from>",
    "↔": "<leftrightarrow>",
    "⇒": "<implies>",
    "⇐": "<impliedby>",
    "⇔": "<iff>",
    "+": "<plus>",
    "-": "<minus>",
    "−": "<minus>",
    "±": "<plusminus>",
    "∓": "<minusplus>",
    "∞": "<infinity>",
    ">": "<gt>",
    "<": "<lt>",
    "=": "<eq>",
    "≠": "<neq>",
    "≥": "<ge>",
    "≤": "<le>",
    "∀": "<forall>",
    "∃": "<exists>",
    "∈": "<in>",
    "∉": "<notin>",
    "⊂": "<subset>",
    "⊆": "<subseteq>",
    "⊃": "<supset>",
    "⊇": "<supseteq>",
    "/": "<div>",
    "÷": "<div>",
    "*": "<mul>",
    "×": "<mul>",
    "·": "<mul>",
    "^": "<pow>",
    "|": "<bar>",
    ".": "<dot>",
    ",": "<comma>",
    ":": "<colon>",
    ";": "<semicolon>",
    "'": "<prime>",
    "′": "<prime>",
    "″": "<doubleprime>",
    "!": "<factorial>",
    "%": "<percent>",
    "∘": "<compose>",
    "√": "<sqrt>",
    "∑": "<sum>",
    "∏": "<product>",
    "∫": "<integral>",
    "∂": "<partial>",
    "∝": "<proportional>",
    "¬": "<not>",
    "∧": "<and>",
    "∨": "<or>",
}


def _semantic_compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for latex, symbol in _LATEX_SEMANTIC_REPLACEMENTS:
        normalized = normalized.replace(latex.casefold(), symbol)
    parts: list[str] = []
    for char in normalized:
        if char.isalnum():
            parts.append(char)
            continue
        token = _SEMANTIC_SYMBOL_TOKENS.get(char)
        if token:
            parts.append(token)
    return "".join(parts)


def _normalize_name(text: str) -> str:
    return _semantic_compact(text.strip())

def _normalized_text(text: str) -> str:
    return _semantic_compact(text)

def _char_ngrams(text: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    compact = _normalized_text(text)
    grams: set[str] = set()
    for size in sizes:
        grams.update(compact[index : index + size] for index in range(max(0, len(compact) - size + 1)))
    return grams

def _node_id(node_type: str, name: str) -> str:
    return f"{node_type.lower()}_{_slug(name)}_{_stable_hash(node_type, _normalize_name(name))}"

def _relation_key(source_id: str, relation_type: str, target_id: str) -> tuple[str, str, str]:
    if relation_type == "equivalent_to":
        left, right = sorted([source_id, target_id])
        return left, relation_type, right
    return source_id, relation_type, target_id

def _relation_id(source_id: str, relation_type: str, target_id: str) -> str:
    left, relation, right = _relation_key(source_id, relation_type, target_id)
    return f"relation_{_stable_hash(left, relation, right)}"

def _expression_id(owner_id: str, role: str, latex: str) -> str:
    return f"expression_{_stable_hash(owner_id, role, latex)}"

def _text_snippet(lines: list[str], start_line: int, end_line: int) -> str:
    start = max(1, start_line)
    end = min(len(lines), end_line)
    return "\n".join(lines[start - 1 : end]) if start <= end else ""

def _unique_preserve_order(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _canonical_aliases(name: str, aliases: list[str]) -> list[str]:
    canonical_key = _normalize_name(name)
    seen_keys = {canonical_key}
    canonical_aliases: list[str] = []
    for alias in _unique_preserve_order(aliases):
        alias_key = _normalize_name(alias)
        if not alias_key or alias_key in seen_keys:
            continue
        seen_keys.add(alias_key)
        canonical_aliases.append(alias)
    return canonical_aliases

def _looks_like_latex(text: str) -> bool:
    return bool(text) and not re.search(r"[\u4e00-\u9fff]", text) and any(x in text for x in ["\\", "^", "_", "=", "\\frac", "\\sqrt", "\\lim", "\\in", "\\to"])

def _extract_latex_candidates(text: str) -> list[str]:
    items = []
    items.extend(m.group(1).strip() for m in re.finditer(r"\$\$(.*?)\$\$", text, flags=re.S))
    items.extend(m.group(1).strip() for m in re.finditer(r"\\\[(.*?)\\\]", text, flags=re.S))
    items.extend(m.group(1).strip() for m in re.finditer(r"(?<![\$\\])\$(?!\$)(.*?)(?<![\$\\])\$(?!\$)", text, flags=re.S) if m.group(1).strip())
    if not items and _looks_like_latex(text.strip()):
        items.append(text.strip())
    return [item for item in items if item]

def _load_source_latex_map(lines: list[str]) -> dict[int, list[str]]:
    candidates_by_line: dict[int, list[str]] = {}
    for line_no, line in enumerate(lines, start=1):
        candidates = _extract_latex_candidates(line)
        if candidates:
            candidates_by_line[line_no] = candidates

    opening_markers = {"$$": "$$", r"\[": r"\]", "```math": "```", "```latex": "```"}
    index = 0
    while index < len(lines):
        marker = lines[index].strip().lower()
        closing_marker = opening_markers.get(marker)
        if closing_marker is None:
            index += 1
            continue
        close_index = index + 1
        while close_index < len(lines) and lines[close_index].strip().lower() != closing_marker:
            close_index += 1
        if close_index >= len(lines):
            index += 1
            continue
        block_lines = lines[index + 1 : close_index]
        nonempty_offsets = [offset for offset, text in enumerate(block_lines) if text.strip()]
        if nonempty_offsets:
            first_offset = nonempty_offsets[0]
            last_offset = nonempty_offsets[-1]
            source_line = index + first_offset + 2
            latex = "\n".join(block_lines[first_offset : last_offset + 1]).strip()
            existing = candidates_by_line.setdefault(source_line, [])
            if latex and latex not in existing:
                existing.append(latex)
        index = close_index + 1

    return candidates_by_line

def _latex_candidate_id(line_no: int, candidate_index: int) -> str:
    return f"latex_L{line_no}_{candidate_index + 1}"

def _index_source_latex(source_latex_map: dict[int, list[str]]) -> dict[str, tuple[int, str]]:
    return {
        _latex_candidate_id(line_no, candidate_index): (line_no, latex)
        for line_no, candidates in source_latex_map.items()
        for candidate_index, latex in enumerate(candidates)
    }

def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise

class JsonParseFailure(RuntimeError):
    def __init__(self, message: str, raw_content: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content


class CompletionTruncated(RuntimeError):
    def __init__(self, message: str, metadata: dict[str, Any]) -> None:
        super().__init__(message)
        self.metadata = metadata


class ChunkMergeError(ValueError):
    """A model response failed strict validation for one chunk.

    Raised before any store mutation; the caller keeps the stores unchanged,
    records the failure with chunk_id/item kind/reference/reason, and preserves
    the raw response for reproduction.
    """

    def __init__(
        self,
        chunk_id: str,
        item_kind: str,
        item_ref: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.chunk_id = chunk_id
        self.item_kind = item_kind
        self.item_ref = item_ref
        self.reason = reason
        self.details = details or {}
        super().__init__(
            f"Merge failed for {item_kind} {item_ref!r} in {chunk_id}: {reason}"
        )


def _runtime_dir(name: str) -> Path:
    for candidate in [ROOT / "KG" / name, ROOT / "agents" / name, Path(tempfile.gettempdir()) / "maf_kg" / name]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    raise RuntimeError("Unable to create runtime directory.")

def _write_invalid_response(
    content: str,
    label: str,
    error: Exception,
    metadata: dict[str, Any] | None = None,
) -> Path:
    debug = _runtime_dir("_debug") / f"{int(time.time())}_{_slug(label)}_invalid.json.txt"
    debug.write_text(
        json.dumps(
            {
                "error_type": type(error).__name__,
                "error": str(error),
                "response_metadata": metadata or {},
                "content": content,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return debug


def _parse_response(
    content: str,
    label: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return _extract_json_object(content)
    except json.JSONDecodeError as exc:
        debug = _write_invalid_response(content, label, exc, metadata)
        raise JsonParseFailure(f"JSON parsing failed and the raw response was saved to {debug}: {exc}", content) from exc

def _completion_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"


def _response_json(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Model API returned a non-object JSON response")
    return payload


def _post_httpx(endpoint_url: str, trust_env: bool, timeout: float, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=timeout, http2=False, headers=headers, trust_env=trust_env, limits=httpx.Limits(max_keepalive_connections=0, max_connections=2)) as client:
        response = client.post(endpoint_url, json=payload)
    return _response_json(response)


def _post_requests(endpoint_url: str, trust_env: bool, timeout: float, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = trust_env
    response = session.post(endpoint_url, headers=headers, json=payload, timeout=timeout)
    return _response_json(response)


def _post_powershell(endpoint_url: str, api_key: str, timeout: float, payload: dict[str, Any], label: str) -> dict[str, Any]:
    runtime = _runtime_dir("_tmp")
    stamp = f"{int(time.time())}_{_slug(label)}"
    payload_path = runtime / f"{stamp}_payload.json"
    response_path = runtime / f"{stamp}_response.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    pp = str(payload_path).replace("'", "''")
    rp = str(response_path).replace("'", "''")
    url = endpoint_url.replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Net.Http
$body=[System.IO.File]::ReadAllText('{pp}',[System.Text.Encoding]::UTF8)
$client=[System.Net.Http.HttpClient]::new()
$client.Timeout=[System.TimeSpan]::FromSeconds({int(timeout)})
$request=[System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post,'{url}')
$request.Headers.Authorization=[System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer',$env:MAF_KG_MODEL_API_KEY)
$request.Headers.Accept.ParseAdd('application/json')
$content=[System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($body))
$content.Headers.ContentType=[System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
$request.Content=$content
$response=$client.SendAsync($request).GetAwaiter().GetResult()
$responseBody=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
if(-not $response.IsSuccessStatusCode){{throw "HTTP $([int]$response.StatusCode): $responseBody"}}
[System.IO.File]::WriteAllText('{rp}',$responseBody,[System.Text.Encoding]::UTF8)
"""
    env = os.environ.copy()
    env["MAF_KG_MODEL_API_KEY"] = api_key
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout + 30,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    data = json.loads(response_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError("Model API returned a non-object JSON response")
    return data


class ModelProviderAdapter:
    name = ""
    default_model = ""
    default_base_url = ""
    api_key_envs: tuple[str, ...] = ()
    model_envs: tuple[str, ...] = ()
    base_url_envs: tuple[str, ...] = ()
    supports_response_format = False
    supports_reasoning_effort = False
    omits_temperature_when_thinking = False

    def build_payload(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_response_format: bool,
        thinking: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def extract_content(self, response: dict[str, Any]) -> str:
        code = response.get("code")
        if code not in (None, 0):
            raise RuntimeError(f"{self.name} API error {code}: {response.get('message', 'unknown error')}")
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{self.name} API response does not contain choices[0].message.content") from exc
        if not isinstance(content, str):
            raise RuntimeError(f"{self.name} API returned non-text message content")
        return content

    def extract_metadata(self, response: dict[str, Any]) -> dict[str, Any]:
        try:
            choice = response["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{self.name} API response does not contain a valid first choice") from exc
        reasoning_content = message.get("reasoning_content") if isinstance(message, dict) else None
        usage = response.get("usage")
        return {
            "response_id": response.get("id"),
            "response_model": response.get("model"),
            "created": response.get("created"),
            "system_fingerprint": response.get("system_fingerprint"),
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": usage if isinstance(usage, dict) else {},
            "reasoning_content_chars": len(reasoning_content) if isinstance(reasoning_content, str) else 0,
        }


class DeepSeekProviderAdapter(ModelProviderAdapter):
    name = "deepseek"
    default_model = "deepseek-v4-flash"
    default_base_url = "https://api.deepseek.com"
    api_key_envs = ("DEEPSEEK_API_KEY", "DS_API_KEY", "DEEPSEEK_KEY", "OPENAI_API_KEY")
    model_envs = ("DEEPSEEK_MODEL", "DS_MODEL")
    base_url_envs = ("DEEPSEEK_BASE_URL", "DS_BASE_URL", "OPENAI_BASE_URL")
    supports_response_format = True
    supports_reasoning_effort = True
    omits_temperature_when_thinking = True

    def build_payload(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_response_format: bool,
        thinking: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "max_tokens": max_tokens,
            "thinking": {"type": thinking},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if thinking == "disabled":
            payload["temperature"] = temperature
        elif reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}
        return payload


class OpenCodeGoProviderAdapter(DeepSeekProviderAdapter):
    """DeepSeek models served through the OpenCode Go gateway."""

    name = "opencode-go"
    default_base_url = "https://opencode.ai/zen/go/v1"
    api_key_envs = ("OPENCODE_API_KEY",)
    model_envs = ("OPENCODE_GO_MODEL",)
    base_url_envs = ("OPENCODE_GO_BASE_URL",)


class SparkProviderAdapter(ModelProviderAdapter):
    name = "spark"
    default_model = "spark-x"
    default_base_url = "https://spark-api-open.xf-yun.com/x2"
    api_key_envs = ("SPARK_API_PASSWORD", "XFYUN_API_PASSWORD", "SPARK_API_KEY", "XFYUN_API_KEY")
    model_envs = ("SPARK_MODEL", "XFYUN_MODEL")
    base_url_envs = ("SPARK_BASE_URL", "XFYUN_BASE_URL")

    def build_payload(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_response_format: bool,
        thinking: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        # Spark X2 documents user/assistant/tool roles but not a system role.
        # Keep the same instruction content in a single user message.
        combined_prompt = (
            "SYSTEM INSTRUCTIONS\n"
            "===================\n"
            f"{system_prompt}\n\n"
            "CURRENT EXTRACTION INPUT\n"
            "========================\n"
            f"{user_prompt}"
        )
        return {
            "model": model,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": {"type": thinking},
            "messages": [{"role": "user", "content": combined_prompt}],
        }


PROVIDER_REGISTRY: dict[str, type[ModelProviderAdapter]] = {
    "deepseek": DeepSeekProviderAdapter,
    "opencode-go": OpenCodeGoProviderAdapter,
    "spark": SparkProviderAdapter,
}
PROVIDER_ALIASES = {
    "ds": "deepseek",
    "opencode": "opencode-go",
    "opencode_go": "opencode-go",
    "oc-go": "opencode-go",
    "xfyun": "spark",
    "spark-x": "spark",
}


def _provider_name(value: str | None) -> str:
    name = (value or os.getenv("MODEL_PROVIDER") or "deepseek").strip().lower()
    return PROVIDER_ALIASES.get(name, name)


def _provider_class(name: str) -> type[ModelProviderAdapter]:
    try:
        return PROVIDER_REGISTRY[name]
    except KeyError as exc:
        supported = ", ".join(sorted(PROVIDER_REGISTRY))
        raise ValueError(f"Unsupported model provider '{name}'. Supported providers: {supported}") from exc


class ProviderChatClient:
    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key_env: str | None,
        temperature: float,
        timeout: float,
        max_retries: int,
        max_tokens: int,
        use_response_format: bool = True,
        trust_env: bool = True,
        thinking: Literal["enabled", "disabled"] = "disabled",
        reasoning_effort: str | None = None,
    ) -> None:
        adapter_class = _provider_class(provider)
        self.adapter = adapter_class()
        key_names = (api_key_env,) if api_key_env else adapter_class.api_key_envs
        self.api_key_name = next((name for name in key_names if os.getenv(name)), None)
        self.api_key = os.getenv(self.api_key_name) if self.api_key_name else None
        if not self.api_key:
            expected = api_key_env or " or ".join(adapter_class.api_key_envs)
            raise RuntimeError(f"{provider} API key not found. Set {expected}.")
        self.provider = provider
        self.endpoint_url = _completion_url(base_url)
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.use_response_format = use_response_format and adapter_class.supports_response_format
        self.trust_env = trust_env
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.request_history: list[dict[str, Any]] = []

    def _execute_request(
        self,
        request: Callable[[], dict[str, Any]],
        transport: str,
        attempt: int,
        label: str,
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        metadata: dict[str, Any] = {}
        content = ""
        try:
            response = request()
            content = self.adapter.extract_content(response)
            metadata = self.adapter.extract_metadata(response)
            record = {
                "label": label,
                "transport": transport,
                "attempt": attempt,
                "started_at": started_at,
                "latency_seconds": round(time.perf_counter() - started, 4),
                "content_chars": len(content),
                **metadata,
            }
            if metadata.get("finish_reason") in {"length", "max_tokens"}:
                error = CompletionTruncated(
                    f"{self.provider} response for {label} was truncated with "
                    f"finish_reason={metadata.get('finish_reason')} at max_tokens={self.max_tokens}. "
                    "Increase --max-tokens or lower reasoning effort before retrying.",
                    metadata,
                )
                debug = _write_invalid_response(content, label, error, metadata)
                record.update({"status": "truncated", "debug_path": str(debug)})
                self.request_history.append(record)
                raise error
            try:
                parsed = _parse_response(content, label, metadata)
            except JsonParseFailure as exc:
                record.update({"status": "invalid_json", "error": str(exc)})
                self.request_history.append(record)
                raise
            record["status"] = "success"
            self.request_history.append(record)
            return parsed
        except (CompletionTruncated, JsonParseFailure):
            raise
        except Exception as exc:
            self.request_history.append(
                {
                    "label": label,
                    "transport": transport,
                    "attempt": attempt,
                    "started_at": started_at,
                    "latency_seconds": round(time.perf_counter() - started, 4),
                    "status": "request_error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "content_chars": len(content),
                    **metadata,
                }
            )
            raise

    def complete_json(self, system_prompt: str, user_prompt: str, label: str = "request") -> dict[str, Any]:
        payload = self.adapter.build_payload(
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            use_response_format=self.use_response_format,
            thinking=self.thinking,
            reasoning_effort=self.reasoning_effort,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": f"MaF-KG-SemanticExtractor/0.1 ({self.provider})",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._execute_request(
                    lambda: _post_httpx(self.endpoint_url, self.trust_env, self.timeout, headers, payload),
                    "httpx",
                    attempt + 1,
                    label,
                )
            except CompletionTruncated:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == self.max_retries:
                    fallbacks = [
                        (
                            "requests",
                            lambda: _post_requests(self.endpoint_url, self.trust_env, self.timeout, headers, payload),
                        ),
                        (
                            "powershell",
                            lambda: _post_powershell(
                                self.endpoint_url,
                                str(self.api_key),
                                self.timeout,
                                payload,
                                label,
                            ),
                        ),
                    ]
                    for fallback_index, (transport, fallback) in enumerate(fallbacks, start=1):
                        try:
                            return self._execute_request(
                                fallback,
                                transport,
                                self.max_retries + 1 + fallback_index,
                                label,
                            )
                        except CompletionTruncated:
                            raise
                        except Exception as fallback_exc:
                            last_error = fallback_exc
                else:
                    time.sleep(min(60, 2 ** (attempt + 1)))
        raise RuntimeError(f"{self.provider} API call failed: {last_error}") from last_error

def _make_heartbeat(message: str, verbose: bool, interval: float = 20.0) -> tuple[threading.Event, threading.Thread | None]:
    stop = threading.Event()
    if not verbose:
        return stop, None
    def worker() -> None:
        while not stop.wait(interval):
            print(message, flush=True)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return stop, thread

class Extractor:
    def __init__(
        self,
        input_path: str | Path = DEFAULT_INPUT,
        output_path: str | Path = DEFAULT_OUTPUT,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        temperature: float = 0.1,
        max_chunk_chars: int = 3200,
        chunk_strategy: str = "budget",
        chunk_context_before_lines: int = 6,
        chunk_context_after_lines: int = 4,
        experimental_chunk_spans: tuple[tuple[int, int], ...] = (),
        max_retries: int = 2,
        request_timeout: float = 180,
        max_tokens: int = 12000,
        memory_node_bytes: int = 12000,
        memory_relation_bytes: int = 6000,
        save_output: bool = True,
        use_response_format: bool = True,
        no_proxy: bool = False,
        request_delay: float = 3.0,
        thinking: str = "disabled",
        reasoning_effort: str = "high",
        resume: bool = True,
        include_exercises: bool = False,
        verbose: bool = True,
        verify: bool = False,
        verify_model: str | None = None,
        semantic_dedupe: bool = False,
        embedding_memory: bool = False,
        seed_graph_path: str | Path | None = None,
        coverage_review: bool = False,
        logical_form_review: bool = False,
    ) -> None:
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.provider = _provider_name(provider)
        adapter_class = _provider_class(self.provider)
        self.model = model or _env_first(list(adapter_class.model_envs)) or adapter_class.default_model
        self.base_url = (base_url or _env_first(list(adapter_class.base_url_envs)) or adapter_class.default_base_url).rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.max_chunk_chars = max_chunk_chars
        if chunk_strategy not in CHUNK_STRATEGIES:
            raise ValueError(
                f"Unsupported chunk strategy {chunk_strategy!r}; expected one of {CHUNK_STRATEGIES}"
            )
        self.chunk_strategy = chunk_strategy
        self.chunk_context_before_lines = chunk_context_before_lines
        self.chunk_context_after_lines = chunk_context_after_lines
        self.experimental_chunk_spans = tuple(experimental_chunk_spans)
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_tokens = max_tokens
        self.memory_node_bytes = memory_node_bytes
        self.memory_relation_bytes = memory_relation_bytes
        if self.memory_node_bytes < 2:
            raise ValueError(f"memory_node_bytes must be >= 2, got {self.memory_node_bytes}")
        if self.memory_relation_bytes < 2:
            raise ValueError(f"memory_relation_bytes must be >= 2, got {self.memory_relation_bytes}")
        self.save_output = save_output
        self.use_response_format = use_response_format
        self.no_proxy = no_proxy
        self.request_delay = request_delay
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.resume = resume
        self.include_exercises = include_exercises
        self.verbose = verbose
        self.system_prompt = _load_prompt("extraction_system.txt")
        self.verify = verify
        self.verify_model = verify_model or "deepseek-v4-flash"
        self.verify_prompt = _load_prompt("extraction_verify.txt") if verify else ""
        self.semantic_dedupe = semantic_dedupe
        self.embedding_memory = embedding_memory
        self.seed_graph_path = Path(seed_graph_path) if seed_graph_path else None
        if coverage_review and chunk_strategy != "teaching-units":
            raise ValueError("coverage_review requires chunk_strategy='teaching-units'")
        self.coverage_review = coverage_review
        self.logical_form_review = logical_form_review
        self.logical_form_review_prompt = (
            _load_prompt("logical_form_review.txt") if logical_form_review else ""
        )
        self._sem_embed_cache: dict[str, list[float]] = {}
        self._sem_judge_cache: dict[tuple[str, str], bool] = {}
        self._chunk_embed_cache: dict[str, list[float]] = {}
        self.client: ProviderChatClient | None = None
        self.source_latex_map: dict[int, list[str]] = {}
        self.source_latex_candidates: dict[str, tuple[int, str]] = {}
        self.source_context_by_line: dict[int, tuple[list[str], str, str]] = {}
        self.graph = self._build_graph()

    def run(self) -> ExtractionResult:
        self._log(f"Starting Extractor with provider={self.provider} model={self.model}")
        state: ExtractionState = {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "temperature": self.temperature,
            "max_chunk_chars": self.max_chunk_chars,
            "chunk_strategy": self.chunk_strategy,
            "chunk_context_before_lines": self.chunk_context_before_lines,
            "chunk_context_after_lines": self.chunk_context_after_lines,
            "experimental_chunk_spans": self.experimental_chunk_spans,
            "max_retries": self.max_retries,
            "request_timeout": self.request_timeout,
            "max_tokens": self.max_tokens,
            "memory_node_bytes": self.memory_node_bytes,
            "memory_relation_bytes": self.memory_relation_bytes,
            "save_output": self.save_output,
            "use_response_format": self.use_response_format,
            "no_proxy": self.no_proxy,
            "request_delay": self.request_delay,
            "resume": self.resume,
            "include_exercises": self.include_exercises,
            "verbose": self.verbose,
            "thinking": self.thinking,
            "reasoning_effort": self.reasoning_effort,
            "coverage_review": self.coverage_review,
            "logical_form_review": self.logical_form_review,
            "run_started_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": {},
        }
        return self.graph.invoke(state)["result"]
    def _build_graph(self):
        workflow = StateGraph(ExtractionState)
        workflow.add_node("load_document", self._load_document)
        workflow.add_node("chunk_document", self._chunk_document)
        workflow.add_node("extract_sequentially", self._extract_sequentially)
        workflow.add_node("save_result", self._save_result)
        workflow.set_entry_point("load_document")
        workflow.add_edge("load_document", "chunk_document")
        workflow.add_edge("chunk_document", "extract_sequentially")
        workflow.add_edge("extract_sequentially", "save_result")
        workflow.add_edge("save_result", END)
        return workflow.compile()

    def _client(self, state: ExtractionState) -> ProviderChatClient:
        if self.client is None:
            self.client = ProviderChatClient(
                provider=str(state["provider"]),
                model=str(state["model"]),
                base_url=str(state["base_url"]),
                api_key_env=state.get("api_key_env"),
                temperature=float(state["temperature"]),
                timeout=float(state["request_timeout"]),
                max_retries=int(state["max_retries"]),
                max_tokens=int(state["max_tokens"]),
                use_response_format=bool(state["use_response_format"]),
                trust_env=not bool(state.get("no_proxy", False)),
                thinking=state.get("thinking", "disabled"),
                reasoning_effort=state.get("reasoning_effort"),
            )
        return self.client
    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[Extractor] {message}", flush=True)

    def _load_document(self, state: ExtractionState) -> ExtractionState:
        self._log(f"Loading markdown: {self.input_path}")
        if not self.input_path.exists():
            raise FileNotFoundError(f"Input file not found: {self.input_path}")
        lines = self.input_path.read_text(encoding="utf-8").splitlines()
        if not state.get("include_exercises", False):
            cut = self._find_exercise_start(lines)
            if cut is not None:
                self._log(f"Skipping exercises from line {cut}; pass --include-exercises to extract them")
                lines = lines[: cut - 1]
        self.source_latex_map = _load_source_latex_map(lines)
        self.source_latex_candidates = _index_source_latex(self.source_latex_map)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics.update({"line_count": len(lines), "source_latex_line_count": len(self.source_latex_map), "source_latex_candidate_count": len(self.source_latex_candidates), "input_path": str(self.input_path)})
        return {**state, "raw_lines": lines, "diagnostics": diagnostics}

    def _find_exercise_start(self, lines: list[str]) -> int | None:
        patterns = [
            r"^\s*#*\s*习题",
            r"^\s*#*\s*练习",
            r"^\s*#*\s*本节练习",
        ]
        for idx, line in enumerate(lines, start=1):
            if any(re.match(pattern, line.strip()) for pattern in patterns):
                return idx
        return None

    def _chunk_document(self, state: ExtractionState) -> ExtractionState:
        self._log("Preparing structure-aware line-numbered chunks")
        lines = state["raw_lines"]
        max_chars = int(state["max_chunk_chars"])
        before_lines = int(state["chunk_context_before_lines"])
        after_lines = int(state["chunk_context_after_lines"])

        def semantic_heading_rank(title: str) -> int | None:
            stripped_title = title.strip()
            if re.match(r"^第[一二三四五六七八九十百0-9]+章\b", stripped_title):
                return 1
            if re.match(r"^第[一二三四五六七八九十百0-9]+节\b", stripped_title):
                return 2
            if re.match(r"^[一二三四五六七八九十百]+、", stripped_title):
                return 3
            if re.match(r"^[0-9]+[.、]", stripped_title):
                return 4
            return None

        recognized_heading_ranks = [
            rank
            for line in lines
            if line.startswith("#")
            for rank in [semantic_heading_rank(line.lstrip("#").strip())]
            if rank is not None
        ]
        heading_rank_offset = min(recognized_heading_ranks, default=1) - 1

        def semantic_heading_level(title: str, markdown_level: int) -> int:
            rank = semantic_heading_rank(title)
            return max(1, rank - heading_rank_offset) if rank is not None else markdown_level

        heading_path: list[str] = []
        line_items: list[tuple[int, str, list[str]]] = []
        for line_no, line in enumerate(lines, start=1):
            if line.startswith("#"):
                markdown_level = len(line) - len(line.lstrip("#"))
                title = line[markdown_level:].strip()
                level = semantic_heading_level(title, markdown_level)
                heading_path = heading_path[: level - 1] + [title]
            line_items.append((line_no, line, list(heading_path)))

        def is_heading(line: str) -> bool:
            return line.startswith("#")

        def is_math(line: str) -> bool:
            stripped = line.strip()
            return stripped in {"$$", r"\[", r"\]"} or stripped.startswith("```math") or stripped.startswith("```latex")

        def is_image(line: str) -> bool:
            return bool(re.search(r"!\[.*?\]\((.*?)\)", line) or re.search(r"<img\s", line, flags=re.I))

        def without_markdown_list_marker(text: str) -> str:
            return re.sub(r"^\s*[-+*]\s+", "", text)

        def is_example(text: str) -> bool:
            return bool(re.match(r"^\s*(例\s*\d+|例题\s*\d+)", without_markdown_list_marker(text)))

        def is_theorem_reference(text: str) -> bool:
            normalized = without_markdown_list_marker(text)
            match = re.match(
                r"^\s*定理\s*(?:[0-9一二三四五六七八九十百]+)?",
                normalized,
            )
            if not match:
                return False
            continuation = normalized[match.end() :].lstrip()
            return bool(
                re.match(
                    r"^(?:表示|表明|说明|给出|中的|中所|的(?:结论|条件|证明|推论|内容|结果))",
                    continuation,
                )
            )

        def is_theorem(text: str) -> bool:
            normalized = without_markdown_list_marker(text)
            match = re.match(
                r"^\s*定理\s*(?:[0-9一二三四五六七八九十百]+)?",
                normalized,
            )
            return bool(match) and not is_theorem_reference(normalized)

        def is_property(text: str) -> bool:
            return bool(re.match(r"^\s*性质\s*\d*", without_markdown_list_marker(text)))

        def is_corollary(text: str) -> bool:
            return bool(re.match(r"^\s*推论\s*\d*", without_markdown_list_marker(text)))

        def is_proof(text: str) -> bool:
            return bool(re.match(r"^\s*(证|证明)\s*[:：]?", without_markdown_list_marker(text)))

        def is_definition(text: str) -> bool:
            return bool(re.match(r"^\s*定义\s*\d*\s+设", without_markdown_list_marker(text)))

        def is_fresh_definitional_act(text: str) -> bool:
            """A line opening a new conditional definition outside any example.

            Chinese textbooks define named classes with patterns such as
            "设 f 是从集合 X 到集合 Y 的映射，若 R_f = Y ... 则称 f 为满射"
            without the word 定义. When such a line appears while extending an
            example block, it must terminate the example: example-local content
            is skipped wholesale, so absorbing a general definition here would
            silently drop taught content. The prefix discriminates a fresh
            definitional act from an inline naming gloss of the current
            example object ("这函数称为绝对值函数").
            """
            normalized = without_markdown_list_marker(text).strip()
            if not re.match(r"^(设|若|如果|假设)", normalized):
                return False
            return bool(re.search(r"(则称|那么称|称为|叫做|记为|记作|统称为|定义为)", normalized))

        def is_conclusion_transition(text: str) -> bool:
            return bool(
                re.search(
                    r"(?:^|[，,。；;：:\s])(所以|从而|故|因此|由此可知|这就证明)",
                    text,
                )
            )

        def is_exposition_restart(text: str) -> bool:
            return bool(
                re.match(
                    r"^\s*(注意|上述|下面|以下|现在|一般地|类似地|回到|我们(?:指出|给出|再来|现在))",
                    text,
                )
            )

        def is_special(text: str) -> bool:
            return (
                is_heading(text)
                or is_example(text)
                or is_theorem(text)
                or is_property(text)
                or is_corollary(text)
                or is_proof(text)
                or is_definition(text)
                or is_image(text)
                or is_math(text)
            )

        def block(items: list[tuple[int, str, list[str]]], kind: str) -> DocumentBlock:
            return DocumentBlock(
                start_line=items[0][0],
                end_line=items[-1][0],
                heading_path=items[0][2],
                lines=[(n, t) for n, t, _ in items],
                kind=kind,
            )

        blocks: list[DocumentBlock] = []
        i = 0
        while i < len(line_items):
            _, line, _ = line_items[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue
            if is_heading(line):
                blocks.append(block([line_items[i]], "heading"))
                i += 1
                continue
            if is_math(line):
                start = i
                i += 1
                while i < len(line_items):
                    current = line_items[i][1]
                    i += 1
                    if is_math(current):
                        break
                blocks.append(block(line_items[start:i], "formula_block"))
                continue
            if is_image(line):
                start = i
                i += 1
                while i < len(line_items) and line_items[i][1].strip() and not (is_special(line_items[i][1].strip()) and not is_image(line_items[i][1])):
                    i += 1
                blocks.append(block(line_items[start:i], "figure"))
                continue

            kind_fn = None
            if is_example(stripped):
                kind_fn = "example"
            elif is_theorem(stripped):
                kind_fn = "theorem"
            elif is_property(stripped):
                kind_fn = "property"
            elif is_corollary(stripped):
                kind_fn = "corollary"
            elif is_proof(stripped):
                kind_fn = "proof"
            elif is_definition(stripped):
                kind_fn = "definition"

            if kind_fn:
                start = i
                i += 1
                saw_conclusion = is_conclusion_transition(stripped)
                while i < len(line_items):
                    nxt = line_items[i][1].strip()
                    if nxt and (
                        is_heading(nxt)
                        or is_example(nxt)
                        or is_theorem(nxt)
                        or is_theorem_reference(nxt)
                        or is_corollary(nxt)
                        or is_definition(nxt)
                        or (kind_fn in {"property", "corollary", "proof"} and is_property(nxt))
                    ):
                        break
                    if kind_fn == "example" and is_exposition_restart(nxt):
                        break
                    if kind_fn == "example" and is_fresh_definitional_act(nxt):
                        break
                    if kind_fn == "proof" and saw_conclusion and is_exposition_restart(nxt):
                        break
                    if is_conclusion_transition(nxt):
                        saw_conclusion = True
                    i += 1
                parsed_block = block(line_items[start:i], kind_fn)
                if kind_fn == "definition":
                    parsed_block.segments = classify_definition_segments(parsed_block.lines)
                blocks.append(parsed_block)
                continue

            start = i
            i += 1
            while i < len(line_items):
                nxt = line_items[i][1].strip()
                if not nxt or is_special(nxt):
                    break
                i += 1
            blocks.append(block(line_items[start:i], "paragraph"))
            while i < len(line_items) and not line_items[i][1].strip():
                i += 1

        # Keep an introductory paragraph, its display formula, and the following
        # conclusion together. Splitting this pattern loses definitions such as
        # monotone increase exactly at a character-budget boundary.
        bundled_blocks: list[DocumentBlock] = []
        i = 0
        bundle_char_limit = max(max_chars + 1200, int(max_chars * 1.5))
        while i < len(blocks):
            current_bundle = [blocks[i]]
            if blocks[i].kind == "paragraph":
                j = i
                while (
                    j + 2 < len(blocks)
                    and blocks[j + 1].kind == "formula_block"
                    and blocks[j + 2].kind == "paragraph"
                    and blocks[j + 1].heading_path == blocks[i].heading_path
                    and blocks[j + 2].heading_path == blocks[i].heading_path
                ):
                    candidate = [*current_bundle, blocks[j + 1], blocks[j + 2]]
                    if sum(item.char_count for item in candidate) > bundle_char_limit:
                        break
                    current_bundle = candidate
                    j += 2
                if len(current_bundle) > 1:
                    bundled_blocks.append(
                        DocumentBlock(
                            start_line=current_bundle[0].start_line,
                            end_line=current_bundle[-1].end_line,
                            heading_path=list(current_bundle[0].heading_path),
                            lines=[line for item in current_bundle for line in item.lines],
                            kind="formula_exposition",
                        )
                    )
                    i = j + 1
                    continue
            bundled_blocks.append(blocks[i])
            i += 1
        blocks = bundled_blocks

        self.source_context_by_line = {}
        for block_index, source_block in enumerate(blocks, start=1):
            source_block.teaching_block_id = (
                f"teaching_{block_index:04d}_{source_block.start_line}_{source_block.end_line}_"
                f"{_stable_hash(' / '.join(source_block.heading_path), source_block.kind)[:8]}"
            )
            for line_no, _ in source_block.lines:
                self.source_context_by_line[line_no] = (
                    list(source_block.heading_path),
                    source_block.teaching_block_id,
                    source_block.kind,
                )

        chunk_strategy = str(state.get("chunk_strategy", "budget"))
        if chunk_strategy not in CHUNK_STRATEGIES:
            raise ValueError(
                f"Unsupported chunk strategy {chunk_strategy!r}; expected one of {CHUNK_STRATEGIES}"
            )
        experimental_spans = tuple(state.get("experimental_chunk_spans", ()))
        if experimental_spans and chunk_strategy == "teaching-units":
            raise ValueError(
                "experimental_chunk_spans cannot be combined with chunk_strategy='teaching-units'"
            )
        grouped_units: list[TeachingUnitGroup] = []
        if experimental_spans:
            previous_end = 0
            covered_block_ids: set[int] = set()
            for start_line, end_line in experimental_spans:
                if end_line > len(lines):
                    raise ValueError(
                        f"Experimental chunk span {start_line}-{end_line} exceeds document line count {len(lines)}"
                    )
                if start_line <= previous_end:
                    raise ValueError("Experimental chunk spans must be strictly ordered and non-overlapping")
                overlapping = [
                    blk
                    for blk in blocks
                    if blk.start_line <= end_line and blk.end_line >= start_line
                ]
                crossing = [
                    blk
                    for blk in overlapping
                    if blk.start_line < start_line or blk.end_line > end_line
                ]
                if crossing:
                    blk = crossing[0]
                    raise ValueError(
                        f"Experimental chunk span {start_line}-{end_line} cuts through "
                        f"{blk.kind} teaching block L{blk.start_line}-L{blk.end_line}"
                    )
                if not overlapping:
                    raise ValueError(
                        f"Experimental chunk span {start_line}-{end_line} contains no teaching block"
                    )
                if overlapping[0].start_line != start_line or overlapping[-1].end_line != end_line:
                    raise ValueError(
                        f"Experimental chunk span {start_line}-{end_line} must align with teaching-block boundaries"
                    )
                grouped_units.append(
                    _make_teaching_unit_group(overlapping, "experimental_manual_span")
                )
                covered_block_ids.update(id(blk) for blk in overlapping)
                previous_end = end_line

            uncovered_blocks = [blk for blk in blocks if id(blk) not in covered_block_ids]
            if uncovered_blocks:
                blk = uncovered_blocks[0]
                raise ValueError(
                    f"Experimental chunk spans omit {blk.kind} teaching block "
                    f"L{blk.start_line}-L{blk.end_line}"
                )
            uncovered_text_lines = [
                line_no
                for line_no, text in enumerate(lines, start=1)
                if text.strip()
                and not any(start <= line_no <= end for start, end in experimental_spans)
            ]
            if uncovered_text_lines:
                raise ValueError(
                    f"Experimental chunk spans omit nonblank source line L{uncovered_text_lines[0]}"
                )
        elif chunk_strategy == "teaching-units":
            grouped_units = _build_teaching_unit_groups(blocks, max_chars)
        else:
            groups: list[tuple[list[DocumentBlock], str]] = []
            current: list[DocumentBlock] = []
            current_chars = 0

            def flush(reason: str) -> None:
                nonlocal current, current_chars
                if current:
                    groups.append((current, reason))
                current = []
                current_chars = 0

            for blk in blocks:
                current_has_content = any(x.kind != "heading" for x in current)
                if blk.kind == "heading" and current and current_has_content:
                    flush("heading_boundary")
                elif (
                    current
                    and current_chars + blk.char_count > max_chars
                    and not (
                        blk.kind == "formula_exposition"
                        and current_chars + blk.char_count <= int(max_chars * 1.2)
                    )
                ):
                    flush("char_budget")
                current.append(blk)
                current_chars += blk.char_count
            flush("end")

            min_tail_chars = max(600, max_chars // 5)
            merged_budget_groups: list[tuple[list[DocumentBlock], str]] = []
            for group_blocks, reason in groups:
                group_chars = sum(b.char_count for b in group_blocks)
                if merged_budget_groups:
                    prev_blocks, prev_reason = merged_budget_groups[-1]
                    prev_chars = sum(b.char_count for b in prev_blocks)
                    if group_chars < min_tail_chars and prev_blocks[-1].heading_path == group_blocks[0].heading_path and group_blocks[0].kind != "heading" and prev_chars + group_chars <= int(max_chars * 1.35):
                        merged_budget_groups[-1] = (
                            prev_blocks + group_blocks,
                            f"{prev_reason}+merged_small",
                        )
                        continue
                merged_budget_groups.append((group_blocks, reason))
            grouped_units = [
                _make_teaching_unit_group(group_blocks, reason)
                for group_blocks, reason in merged_budget_groups
            ]

        processed_groups = [group for group in grouped_units if not group.skipped]
        skipped_groups = [group for group in grouped_units if group.skipped]
        skipped_line_numbers = {
            line_no
            for group in skipped_groups
            for line_no in range(group.blocks[0].start_line, group.blocks[-1].end_line + 1)
        }

        chunks: list[DocumentChunk] = []
        for idx, group in enumerate(processed_groups, start=1):
            group_blocks = group.blocks
            start_line = group_blocks[0].start_line
            end_line = group_blocks[-1].end_line
            text = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start_line, end_line + 1))
            before = "\n".join(
                f"{n}: {lines[n - 1]}"
                for n in range(max(1, start_line - before_lines), start_line)
                if 1 <= n <= len(lines) and n not in skipped_line_numbers
            )
            after = "\n".join(
                f"{n}: {lines[n - 1]}"
                for n in range(end_line + 1, min(len(lines), end_line + after_lines) + 1)
                if 1 <= n <= len(lines) and n not in skipped_line_numbers
            )
            chunks.append(
                DocumentChunk(
                    id=f"chunk_{idx:03d}_{start_line}_{end_line}",
                    start_line=start_line,
                    end_line=end_line,
                    heading_path=group_blocks[0].heading_path,
                    text=text,
                    context_before_text=before,
                    context_after_text=after,
                    block_count=len(group_blocks),
                    block_types=tuple(blk.kind for blk in group_blocks),
                    teaching_block_ids=tuple(blk.teaching_block_id for blk in group_blocks),
                    teaching_blocks=tuple(
                        {
                            "teaching_block_id": blk.teaching_block_id,
                            "discourse_role": blk.kind,
                            "start_line": blk.start_line,
                            "end_line": blk.end_line,
                            "segments": [
                                {"role": role, "start_line": s, "end_line": e}
                                for role, s, e in blk.segments
                            ]
                            if blk.segments
                            else [],
                        }
                        for blk in group_blocks
                    ),
                    break_reason=group.grouping_reason,
                    teaching_unit_kind=group.unit_kind,
                    anchor_block_ids=group.anchor_block_ids,
                )
            )

        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["chunk_strategy"] = (
            "experimental_manual_spans" if experimental_spans else chunk_strategy
        )
        diagnostics["experimental_chunk_spans"] = [
            {"start_line": start, "end_line": end}
            for start, end in experimental_spans
        ]
        diagnostics["skipped_teaching_units"] = [
            {
                "start_line": group.blocks[0].start_line,
                "end_line": group.blocks[-1].end_line,
                "unit_kind": group.unit_kind,
                "grouping_reason": group.grouping_reason,
                "block_types": [block.kind for block in group.blocks],
                "teaching_block_ids": [block.teaching_block_id for block in group.blocks],
                "anchor_block_ids": list(group.anchor_block_ids),
            }
            for group in skipped_groups
        ]
        if chunk_strategy == "teaching-units" and not experimental_spans:
            processed_block_count = sum(len(group.blocks) for group in processed_groups)
            skipped_block_count = sum(len(group.blocks) for group in skipped_groups)
            diagnostics["teaching_unit_coverage"] = {
                "source_block_count": len(blocks),
                "processed_block_count": processed_block_count,
                "skipped_block_count": skipped_block_count,
                "covered_exactly_once": processed_block_count + skipped_block_count == len(blocks),
            }
        diagnostics["chunk_count"] = len(chunks)
        diagnostics["chunk_spans"] = [
            {
                "id": c.id,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "block_count": c.block_count,
                "block_types": list(c.block_types),
                "heading_path": c.heading_path,
                "teaching_block_ids": list(c.teaching_block_ids),
                "teaching_blocks": list(c.teaching_blocks),
                "break_reason": c.break_reason,
                "teaching_unit_kind": c.teaching_unit_kind,
                "anchor_block_ids": list(c.anchor_block_ids),
            }
            for c in chunks
        ]
        self._log("Prepared " + str(len(chunks)) + " chunks: " + ", ".join(f"{c.id}[L{c.start_line}-L{c.end_line}]" for c in chunks))
        return {**state, "chunks": chunks, "diagnostics": diagnostics}

    def _load_seed_graph(
        self, path: Path
    ) -> tuple[
        dict[str, NodeRecord],
        dict[tuple[str, str, str], RelationRecord],
        dict[tuple[str, str, str], MathExpressionRecord],
    ]:
        """Load a previously extracted graph JSON as the starting store for incremental update.

        Seed nodes/relations participate in memory retrieval and dedupe like
        chunk-produced ones; relations added by later chunks dedupe by exact
        (source, type, target) key against seed relations.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        node_store: dict[str, NodeRecord] = {}
        relation_store: dict[tuple[str, str, str], RelationRecord] = {}
        expression_store: dict[tuple[str, str, str], MathExpressionRecord] = {}
        for node in data.get("nodes", []):
            node_store[node["id"]] = NodeRecord(
                id=node["id"],
                type=node["type"],
                name=node["name"],
                aliases=list(node.get("aliases", [])),
                description=node.get("description", ""),
                claim_kind=node.get("claim_kind"),
                logical_form=node.get("logical_form"),
                evidence=[Evidence(**entry) for entry in node.get("evidence", [])],
            )
        for rel in data.get("relations", []):
            key = _relation_key(rel["source_id"], rel["type"], rel["target_id"])
            relation_store[key] = RelationRecord(
                source_id=key[0],
                relation_type=key[1],
                target_id=key[2],
                evidences=[Evidence(**entry) for entry in rel.get("evidence", [])],
            )
        for expr in data.get("math_expressions", []):
            key = (expr["owner_id"], expr["role"], expr["latex"])
            expression_store[key] = MathExpressionRecord(
                id=expr["id"],
                owner_id=expr["owner_id"],
                role=expr["role"],
                latex=expr["latex"],
                evidences=[Evidence(**entry) for entry in expr.get("evidence", [])],
            )
        self._log(
            f"Seeded {len(node_store)} nodes / {len(relation_store)} relations / "
            f"{len(expression_store)} expressions from {path}"
        )
        return node_store, relation_store, expression_store

    def _extract_sequentially(self, state: ExtractionState) -> ExtractionState:
        client = self._client(state)
        lines = state["raw_lines"]
        node_store: dict[str, NodeRecord] = {}
        relation_store: dict[tuple[str, str, str], RelationRecord] = {}
        expression_store: dict[tuple[str, str, str], MathExpressionRecord] = {}
        if self.seed_graph_path is not None:
            node_store, relation_store, expression_store = self._load_seed_graph(self.seed_graph_path)
        chunk_summaries: list[dict[str, Any]] = []
        decision_ledger: list[dict[str, Any]] = []
        processed_chunk_ids: list[str] = []
        notes: list[str] = []
        cache_hits = 0
        for idx, chunk in enumerate(state["chunks"], start=1):
            node_memory, relation_memory, memory_selection = self._build_memory_for_chunk(
                chunk,
                node_store,
                relation_store,
                expression_store,
                recent_chunk_ids=set(processed_chunk_ids[-2:]),
                node_char_budget=state.get("memory_node_bytes", 12000),
                relation_char_budget=state.get("memory_relation_bytes", 6000),
            )
            cache_fingerprint = self._cache_fingerprint(chunk, state, node_memory, relation_memory)
            cached = self._read_chunk_cache(chunk.id, cache_fingerprint, state) if state.get("resume", True) else None
            cache_hit = cached is not None
            if cache_hit:
                response = self._validate_chunk_response(
                    cached,
                    chunk,
                    state,
                    client,
                    processed_chunk_ids,
                    chunk_summaries,
                    len(node_store),
                    len(relation_store),
                    len(expression_store),
                )
                cache_hits += 1
                self._log(f"Extractor chunk {idx}/{len(state['chunks'])} resume: {chunk.id} L{chunk.start_line}-L{chunk.end_line}")
            else:
                if idx > 1 and state.get("request_delay", 0) > 0:
                    time.sleep(float(state["request_delay"]))
                self._log(
                    f"Extractor chunk {idx}/{len(state['chunks'])} start: {chunk.id} "
                    f"L{chunk.start_line}-L{chunk.end_line}, memory={len(node_memory)} nodes/{len(relation_memory)} relations"
                )
                stop, thread = _make_heartbeat(
                    f"[Extractor] chunk {idx}/{len(state['chunks'])} waiting on model: {chunk.id}",
                    self.verbose,
                )
                try:
                    payload = client.complete_json(self.system_prompt, self._chunk_extraction_prompt(chunk, state["input_path"], node_memory, relation_memory), label=chunk.id)
                finally:
                    stop.set()
                    if thread is not None:
                        thread.join(timeout=0.1)
                response = self._validate_chunk_response(
                    payload,
                    chunk,
                    state,
                    client,
                    processed_chunk_ids,
                    chunk_summaries,
                    len(node_store),
                    len(relation_store),
                    len(expression_store),
                )
            working_node_store = {node_id: node.model_copy(deep=True) for node_id, node in node_store.items()}
            working_relation_store = {key: relation.model_copy(deep=True) for key, relation in relation_store.items()}
            working_expression_store = {key: expression.model_copy(deep=True) for key, expression in expression_store.items()}
            try:
                summary = self._merge_chunk_response(
                    response,
                    chunk,
                    state["input_path"],
                    lines,
                    working_node_store,
                    working_relation_store,
                    working_expression_store,
                    allowed_existing_ids={item.id for item in node_memory},
                )
            except ChunkMergeError as exc:
                self._log(f"Extractor chunk {idx}/{len(state['chunks'])} merge failed for {chunk.id}: {exc}")
                metadata = {
                    "schema_version": SCHEMA_VERSION,
                    "input_path": state.get("input_path"),
                    "output_path": state.get("output_path"),
                    "chunk_id": chunk.id,
                    "chunk_span": {"start_line": chunk.start_line, "end_line": chunk.end_line},
                    "processed_chunk_ids": list(processed_chunk_ids),
                    "chunk_summaries": list(chunk_summaries),
                    "partial_counts": {
                        "nodes": len(node_store),
                        "relations": len(relation_store),
                        "math_expressions": len(expression_store),
                    },
                    "api_summary": self._api_summary(client.request_history),
                    "api_requests": client.request_history,
                }
                debug_path = _write_invalid_response(
                    json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    f"{chunk.id}_merge",
                    exc,
                    metadata,
                )
                raise ChunkMergeError(
                    exc.chunk_id,
                    exc.item_kind,
                    exc.item_ref,
                    f"{exc.reason} (raw response saved to {debug_path})",
                    exc.details,
                ) from exc
            else:
                node_store = working_node_store
                relation_store = working_relation_store
                expression_store = working_expression_store
                if not cache_hit:
                    self._write_chunk_cache(chunk.id, cache_fingerprint, response.model_dump(mode="json"), state)
                notes.extend(response.notes)
            decision_ledger.extend(summary["decisions"])
            summary["memory_node_count"] = len(node_memory)
            summary["memory_relation_count"] = len(relation_memory)
            summary["node_memory_serialized_bytes"] = self._memory_array_bytes(node_memory)
            summary["relation_memory_serialized_bytes"] = self._memory_array_bytes(relation_memory)
            summary["memory_selection"] = memory_selection
            chunk_summaries.append(summary)
            processed_chunk_ids.append(chunk.id)
            self._log(
                f"Extractor chunk {idx}/{len(state['chunks'])} done: "
                f"create={summary['created_node_count']}, reuse={summary['reused_node_count']}, "
                f"+{summary['new_relation_count']} relations, "
                f"pruned={summary['pruned_relation_count']}, "
                f"+{summary['new_expression_count']} expressions, "
                f"totals={len(node_store)} nodes/{len(relation_store)} relations/{len(expression_store)} expressions"
            )
        coverage_ledger = self._build_coverage_ledger(
            state["chunks"],
            node_store,
            expression_store,
            chunk_strategy=str(state.get("chunk_strategy", "budget")),
        )
        coverage_ledger_before_review = coverage_ledger
        coverage_review_summaries: list[dict[str, Any]] = []
        if state.get("coverage_review", False):
            review_chunks = [
                chunk
                for chunk in state["chunks"]
                if any(
                    item["chunk_id"] == chunk.id and item["review_eligible"]
                    for item in coverage_ledger["uncovered_items"]
                )
            ]
            for review_index, chunk in enumerate(review_chunks, start=1):
                if state.get("request_delay", 0) > 0:
                    time.sleep(float(state["request_delay"]))
                uncovered = [
                    item
                    for item in coverage_ledger["uncovered_items"]
                    if item["chunk_id"] == chunk.id and item["review_eligible"]
                ]
                self._log(
                    f"Coverage review {review_index}/{len(review_chunks)} start: {chunk.id}, "
                    f"uncovered={len(uncovered)}"
                )
                node_memory, relation_memory, _ = self._build_memory_for_chunk(
                    chunk,
                    node_store,
                    relation_store,
                    expression_store,
                    recent_chunk_ids=set(processed_chunk_ids[-2:]),
                    node_char_budget=state.get("memory_node_bytes", 12000),
                    relation_char_budget=state.get("memory_relation_bytes", 6000),
                )
                payload = client.complete_json(
                    self.system_prompt,
                    self._chunk_extraction_prompt(
                        chunk,
                        state["input_path"],
                        node_memory,
                        relation_memory,
                        coverage_guidance=uncovered,
                    ),
                    label=f"{chunk.id}_coverage_review",
                )
                response = self._validate_chunk_response(
                    payload,
                    chunk,
                    state,
                    client,
                    processed_chunk_ids,
                    chunk_summaries,
                    len(node_store),
                    len(relation_store),
                    len(expression_store),
                    request_label=f"{chunk.id}_coverage_review",
                )
                working_node_store = {
                    node_id: node.model_copy(deep=True)
                    for node_id, node in node_store.items()
                }
                working_relation_store = {
                    key: relation.model_copy(deep=True)
                    for key, relation in relation_store.items()
                }
                working_expression_store = {
                    key: expression.model_copy(deep=True)
                    for key, expression in expression_store.items()
                }
                try:
                    summary = self._merge_chunk_response(
                        response,
                        chunk,
                        state["input_path"],
                        lines,
                        working_node_store,
                        working_relation_store,
                        working_expression_store,
                        allowed_existing_ids={item.id for item in node_memory},
                    )
                except ChunkMergeError as exc:
                    metadata = {
                        "schema_version": SCHEMA_VERSION,
                        "input_path": state.get("input_path"),
                        "chunk_id": chunk.id,
                        "review_kind": "coverage",
                        "uncovered_items": uncovered,
                        "processed_chunk_ids": list(processed_chunk_ids),
                        "partial_counts": {
                            "nodes": len(node_store),
                            "relations": len(relation_store),
                            "math_expressions": len(expression_store),
                        },
                        "api_summary": self._api_summary(client.request_history),
                        "api_requests": client.request_history,
                    }
                    debug_path = _write_invalid_response(
                        json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2),
                        f"{chunk.id}_coverage_review_merge",
                        exc,
                        metadata,
                    )
                    raise ChunkMergeError(
                        exc.chunk_id,
                        exc.item_kind,
                        exc.item_ref,
                        f"{exc.reason} (raw response saved to {debug_path})",
                        exc.details,
                    ) from exc
                node_store = working_node_store
                relation_store = working_relation_store
                expression_store = working_expression_store
                summary["chunk_id"] = chunk.id
                summary["reviewed_items"] = uncovered
                coverage_review_summaries.append(summary)
                notes.extend(response.notes)
                decision_ledger.extend(summary["decisions"])
                self._log(
                    f"Coverage review {review_index}/{len(review_chunks)} done: {chunk.id}, "
                    f"create={summary['created_node_count']}, reuse={summary['reused_node_count']}"
                )
            coverage_ledger = self._build_coverage_ledger(
                state["chunks"],
                node_store,
                expression_store,
                chunk_strategy=str(state.get("chunk_strategy", "budget")),
            )
        logical_form_review_diag = self._review_missing_logical_forms(
            node_store,
            state,
        )
        result = self._finalize_result(
            state["input_path"],
            lines,
            node_store,
            relation_store,
            expression_store,
            {
                **dict(state.get("diagnostics", {})),
                "cache_hits": cache_hits,
                "chunk_summaries": chunk_summaries,
                "memory_decisions": decision_ledger,
                "notes": notes,
                "coverage_ledger": coverage_ledger,
                "coverage_review": {
                    "enabled": bool(state.get("coverage_review", False)),
                    "reviewed_chunk_count": len(coverage_review_summaries),
                    "uncovered_before": coverage_ledger_before_review.get(
                        "uncovered_count", 0
                    ),
                    "uncovered_after": coverage_ledger.get("uncovered_count", 0),
                    "chunk_summaries": coverage_review_summaries,
                },
                "logical_form_review": logical_form_review_diag,
                "run_config": self._run_config(state),
                "api_summary": self._api_summary(client.request_history),
                "api_requests": client.request_history,
            },
        )
        if self.verify:
            try:
                result = self._verify_result(result, state)
                verdicts = result.diagnostics.get("verify", {}).get("verdicts", [])
                if any(verdict.get("verdict") == 0 for verdict in verdicts):
                    flagged_chunks = self._collect_flagged_chunks(result, state["chunks"])
                    if flagged_chunks:
                        self._log(
                            f"Verifier flagged {len(flagged_chunks)} chunk(s) for correction: "
                            f"{[chunk.id for chunk in flagged_chunks]}"
                        )
                        result = self._correct_result(result, flagged_chunks, lines, state)
            except Exception as exc:
                self._log(f"Verify pass failed (fail-safe): {exc}")
                result.diagnostics["verify"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        return {**state, "result": result}

    @staticmethod
    def _is_logical_form_review_candidate(node: NodeRecord) -> bool:
        if node.type != "Claim" or node.logical_form is not None:
            return False
        source_text = "\n".join(evidence.text for evidence in node.evidence)
        combined = f"{node.description}\n{source_text}"
        return bool(
            re.search(r"(?:如果|若).{0,240}(?:那么|则)", combined, re.DOTALL)
            or re.search(r"(?:必要|充分|充要|充分必要)条件", combined)
        )

    def _review_missing_logical_forms(
        self,
        node_store: dict[str, NodeRecord],
        state: ExtractionState,
    ) -> dict[str, Any]:
        enabled = bool(state.get("logical_form_review", False))
        candidates = [
            node
            for node in sorted(node_store.values(), key=lambda item: item.id)
            if self._is_logical_form_review_candidate(node)
        ]
        diagnostics: dict[str, Any] = {
            "enabled": enabled,
            "candidate_count": len(candidates),
            "candidate_node_ids": [node.id for node in candidates],
            "updated_count": 0,
            "updated_node_ids": [],
        }
        if not enabled or not candidates:
            diagnostics["status"] = "disabled" if not enabled else "no_candidates"
            return diagnostics

        payload = {
            "candidates": [
                {
                    "node_id": node.id,
                    "name": node.name,
                    "description": node.description,
                    "evidence": [
                        {
                            "start_line": evidence.start_line,
                            "end_line": evidence.end_line,
                            "text": evidence.text,
                        }
                        for evidence in node.evidence
                    ],
                }
                for node in candidates
            ]
        }
        self._log(f"Logical-form review start: {len(candidates)} candidate Claim(s)")
        response = LogicalFormReviewResponse.model_validate(
            self._client(state).complete_json(
                self.logical_form_review_prompt,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                label="logical_form_review",
            )
        )
        expected_ids = [node.id for node in candidates]
        returned_ids = [item.node_id for item in response.items]
        if len(returned_ids) != len(set(returned_ids)):
            raise ValueError("Logical-form review returned duplicate node_id values")
        if set(returned_ids) != set(expected_ids):
            raise ValueError(
                "Logical-form review must return every candidate exactly once; "
                f"expected={sorted(expected_ids)}, returned={sorted(returned_ids)}"
            )

        updated_ids: list[str] = []
        for item in response.items:
            if item.logical_form is None:
                continue
            node_store[item.node_id].logical_form = item.logical_form
            updated_ids.append(item.node_id)
        diagnostics.update(
            {
                "status": "completed",
                "updated_count": len(updated_ids),
                "updated_node_ids": sorted(updated_ids),
                "null_node_ids": sorted(
                    item.node_id for item in response.items if item.logical_form is None
                ),
            }
        )
        self._log(
            f"Logical-form review done: {len(updated_ids)} updated, "
            f"{len(candidates) - len(updated_ids)} unchanged"
        )
        return diagnostics

    def _validate_chunk_response(
        self,
        payload: dict[str, Any],
        chunk: DocumentChunk,
        state: ExtractionState,
        client: ProviderChatClient,
        processed_chunk_ids: list[str],
        chunk_summaries: list[dict[str, Any]],
        node_count: int,
        relation_count: int,
        expression_count: int,
        request_label: str | None = None,
    ) -> ChunkExtractionResponse:
        try:
            return ChunkExtractionResponse.model_validate(payload)
        except ValidationError as exc:
            request_record = next(
                (
                    record
                    for record in reversed(client.request_history)
                    if record.get("label") == (request_label or chunk.id)
                    and record.get("status") == "success"
                ),
                None,
            )
            if request_record is not None:
                request_record.update(
                    {
                        "status": "invalid_schema",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "input_path": state.get("input_path"),
                "output_path": state.get("output_path"),
                "chunk_id": chunk.id,
                "request_label": request_label or chunk.id,
                "chunk_span": {"start_line": chunk.start_line, "end_line": chunk.end_line},
                "processed_chunk_ids": list(processed_chunk_ids),
                "chunk_summaries": list(chunk_summaries),
                "partial_counts": {
                    "nodes": node_count,
                    "relations": relation_count,
                    "math_expressions": expression_count,
                },
                "api_summary": self._api_summary(client.request_history),
                "api_requests": client.request_history,
            }
            debug_path = _write_invalid_response(
                json.dumps(payload, ensure_ascii=False, indent=2),
                f"{chunk.id}_schema",
                exc,
                metadata,
            )
            if request_record is not None:
                request_record["debug_path"] = str(debug_path)
            raise ValueError(
                f"Chunk response failed schema validation for {chunk.id}; "
                f"raw JSON and API telemetry were saved to {debug_path}: {exc}"
            ) from exc

    def _save_result(self, state: ExtractionState) -> ExtractionState:
        if state.get("save_output", True):
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(
                state["result"].model_dump_json(indent=2, ensure_ascii=False, exclude_none=True),
                encoding="utf-8",
            )
            self._log(f"Saved result to {self.output_path}")
        return state
    def _build_memory_for_chunk(
        self,
        chunk: DocumentChunk,
        node_store: dict[str, NodeRecord],
        relation_store: dict[tuple[str, str, str], RelationRecord],
        expression_store: dict[tuple[str, str, str], MathExpressionRecord],
        recent_chunk_ids: set[str],
        node_char_budget: int = 12000,
        relation_char_budget: int = 6000,
    ) -> tuple[list[NodeMemoryItem], list[RelationMemoryItem], list[dict[str, Any]]]:
        if not node_store:
            return [], [], []

        embed_sims: dict[str, float] = {}
        if self.embedding_memory:
            embed_sims = self._chunk_node_similarities(chunk, node_store)

        expressions_by_owner: dict[str, list[MathExpressionRecord]] = {}
        for expression in expression_store.values():
            expressions_by_owner.setdefault(expression.owner_id, []).append(expression)

        ranked: list[tuple[float, list[str], NodeRecord]] = []
        for node in node_store.values():
            score, reasons = self._node_memory_relevance(
                chunk,
                node,
                expressions_by_owner.get(node.id, []),
                recent_chunk_ids,
                embed_sims.get(node.id),
            )
            if score > 0:
                ranked.append((score, reasons, node))
        ranked.sort(key=lambda item: (-item[0], item[2].type, item[2].name, item[2].id))

        selected: dict[str, tuple[NodeRecord, float, list[str]]] = {}
        selection_diag: list[dict[str, Any]] = []
        used_bytes = 2
        for score, reasons, node in ranked:
            item = self._node_memory_item(node)
            size = self._memory_item_bytes(item)
            tentative = used_bytes + (1 if selected else 0) + size
            if tentative > node_char_budget:
                continue
            selected[node.id] = (node, score, reasons)
            selection_diag.append(
                {
                    "id": node.id,
                    "type": node.type,
                    "score": round(score, 4),
                    "reasons": reasons,
                    "source": "ranked",
                }
            )
            used_bytes = tentative

        # Add one-hop neighbors after topical and recent seeds so relations have
        # enough local graph context without sending the entire historical graph.
        seed_ids = set(selected)
        neighbor_candidates: list[tuple[float, str, NodeRecord]] = []
        for rel in relation_store.values():
            if rel.source_id in seed_ids and rel.target_id not in selected and rel.target_id in node_store:
                neighbor_candidates.append((selected[rel.source_id][1] * 0.5, rel.source_id, node_store[rel.target_id]))
            if rel.target_id in seed_ids and rel.source_id not in selected and rel.source_id in node_store:
                neighbor_candidates.append((selected[rel.target_id][1] * 0.5, rel.target_id, node_store[rel.source_id]))
        for neighbor_score, seed_id, node in sorted(neighbor_candidates, key=lambda item: (-item[0], item[2].name, item[2].id)):
            if node.id in selected:
                continue
            item = self._node_memory_item(node)
            size = self._memory_item_bytes(item)
            tentative = used_bytes + (1 if selected else 0) + size
            if tentative > node_char_budget:
                continue
            selected[node.id] = (node, neighbor_score, [f"one_hop:{seed_id}"])
            selection_diag.append(
                {
                    "id": node.id,
                    "type": node.type,
                    "score": round(neighbor_score, 4),
                    "reasons": [f"one_hop:{seed_id}"],
                    "source": "one_hop",
                }
            )
            used_bytes = tentative

        selected_ids = set(selected)
        selected_rels: list[RelationRecord] = []
        used_relation_bytes = 2
        for relation in sorted(
            relation_store.values(),
            key=lambda rel: (
                -(selected[rel.source_id][1] + selected[rel.target_id][1])
                if rel.source_id in selected and rel.target_id in selected
                else 0.0,
                rel.relation_type,
                rel.source_id,
                rel.target_id,
            ),
        ):
            if relation.source_id not in selected_ids or relation.target_id not in selected_ids:
                continue
            item = self._relation_memory_item(relation)
            size = self._memory_item_bytes(item)
            tentative = used_relation_bytes + (1 if selected_rels else 0) + size
            if tentative > relation_char_budget:
                continue
            selected_rels.append(relation)
            used_relation_bytes = tentative
        memory_nodes = [
            self._node_memory_item(node)
            for node, _, _ in sorted(
                selected.values(),
                key=lambda item: (-item[1], item[0].type, item[0].name, item[0].id),
            )
        ]
        return memory_nodes, [self._relation_memory_item(rel) for rel in selected_rels], selection_diag

    @staticmethod
    def _memory_item_bytes(item: BaseModel) -> int:
        return len(
            json.dumps(
                item.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _memory_array_bytes(items: list[BaseModel]) -> int:
        return len(
            json.dumps(
                [item.model_dump(mode="json", exclude_none=True) for item in items],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def _node_memory_relevance(
        self,
        chunk: DocumentChunk,
        node: NodeRecord,
        expressions: list[MathExpressionRecord],
        recent_chunk_ids: set[str],
        embed_sim: float | None = None,
    ) -> tuple[float, list[str]]:
        haystack = "\n".join(
            [chunk.text, chunk.context_before_text, chunk.context_after_text, " ".join(chunk.heading_path)]
        )
        normalized_haystack = _normalized_text(haystack)
        chunk_grams = _char_ngrams(haystack)
        score = 0.0
        reasons: list[str] = []
        for token in [node.name, *node.aliases]:
            token = token.strip()
            normalized_token = _normalized_text(token)
            if len(normalized_token) >= 2 and normalized_token in normalized_haystack:
                score += 8.0 if token == node.name else 6.0
                reasons.append("exact_name" if token == node.name else "exact_alias")
                break

        if embed_sim is not None and embed_sim >= 0.15:
            score += embed_sim * 3.0
            reasons.append(f"embedding:{embed_sim:.2f}")

        node_text = " ".join([node.name, *node.aliases, node.description[:500]])
        node_grams = _char_ngrams(node_text)
        if node_grams and chunk_grams:
            overlap = len(node_grams & chunk_grams) / len(node_grams)
            if overlap >= 0.12:
                score += overlap * 4.0
                reasons.append(f"semantic_ngram:{overlap:.2f}")

        if any(expression.latex and expression.latex in haystack for expression in expressions):
            score += 8.0
            reasons.append("exact_latex")

        if recent_chunk_ids and any(evidence.chunk_id in recent_chunk_ids for evidence in node.evidence):
            score += 2.5
            reasons.append("recent_chunk")
        return score, _unique_preserve_order(reasons)

    def _node_memory_item(self, node: NodeRecord) -> NodeMemoryItem:
        return NodeMemoryItem(
            id=node.id,
            type=node.type,
            name=node.name,
            aliases=node.aliases,
            claim_kind=node.claim_kind,
            logical_form=node.logical_form,
            short_description=self._short_description(node.description),
        )

    @staticmethod
    def _short_description(description: str, limit: int = 100) -> str:
        text = description.strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        cut = text[:limit]
        delimiter_positions = [cut.rfind(delimiter) for delimiter in "。；，！？、.,;"]
        boundary = max(delimiter_positions)
        if boundary >= 1:
            cut = cut[: boundary + 1]
        if cut and 0xD800 <= ord(cut[-1]) <= 0xDBFF:
            cut = cut[:-1]
        return cut

    def _relation_memory_item(self, relation: RelationRecord) -> RelationMemoryItem:
        return RelationMemoryItem(
            source_id=relation.source_id,
            relation_type=relation.relation_type,
            target_id=relation.target_id,
        )

    def _extract_defined_terms(self, chunk_text: str) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for pattern in DEFINED_TERM_PATTERNS:
            for match in pattern.finditer(chunk_text):
                groups = match.groups()
                if not groups:
                    continue
                label = groups[-1].strip().replace("$", "")
                if len(label) < 2 or label in seen:
                    continue
                seen.add(label)
                results.append({"term": label, "context": match.group(0).strip()[:120]})
                if len(results) >= 20:
                    return results
        return results

    def _chunk_extraction_prompt(
        self,
        chunk: DocumentChunk,
        source_path: str,
        node_memory: list[NodeMemoryItem],
        relation_memory: list[RelationMemoryItem],
        verifier_guidance: list[dict[str, Any]] | None = None,
        coverage_guidance: list[dict[str, Any]] | None = None,
    ) -> str:
        evidence_line_numbers = [int(match.group(1)) for line in chunk.text.splitlines() if (match := re.match(r"^([0-9]+):", line))]
        source_latex_candidates = self._source_latex_candidates_for_chunk(chunk)
        payload = {
            "source_path": source_path,
            "chunk": {
                "chunk_id": chunk.id,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "heading_path": chunk.heading_path,
                "teaching_block_ids": list(chunk.teaching_block_ids),
                "teaching_blocks": list(chunk.teaching_blocks),
                "context_before_text": chunk.context_before_text,
                "text": chunk.text,
                "context_after_text": chunk.context_after_text,
                "evidence_line_numbers": evidence_line_numbers,
            },
            "defined_term_hints": self._extract_defined_terms(chunk.text),
            "source_latex_candidates": source_latex_candidates,
            "node_memory": [item.model_dump(mode="json", exclude_none=True) for item in node_memory],
            "relation_memory": [item.model_dump(mode="json", exclude_none=True) for item in relation_memory],
        }
        if verifier_guidance:
            payload["verifier_guidance"] = verifier_guidance
        if coverage_guidance:
            payload["coverage_review"] = {
                "mode": "targeted_recheck",
                "instruction": (
                    "Re-examine the listed source units for eligible reusable mathematical "
                    "semantics omitted by the first pass. Apply the normal ontology and evidence "
                    "rules without relaxing them. Return create/reuse actions only when the source "
                    "really supports an eligible node or expression; an empty response is correct "
                    "when a flagged unit contains no eligible first-layer semantic item."
                ),
                "uncovered_items": coverage_guidance,
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _build_verify_prompt(self, result: ExtractionResult) -> str:
        def evidence_blocks(evidence_list: list[Evidence]) -> list[dict[str, Any]]:
            return [
                {
                    "chunk_id": item.chunk_id,
                    "lines": f"L{item.start_line}-L{item.end_line}",
                    "text": item.text[:500],
                }
                for item in evidence_list
            ]

        node_by_id = {node.id: node for node in result.nodes}
        nodes = [
            {
                "node_id": node.id,
                "type": node.type,
                "name": node.name,
                "description": node.description,
                "claim_kind": node.claim_kind,
                "logical_form": (
                    node.logical_form.model_dump(mode="json") if node.logical_form else None
                ),
                "evidence": evidence_blocks(node.evidence),
            }
            for node in sorted(result.nodes, key=lambda item: (item.type, item.name, item.id))
        ]
        relations = []
        for relation in sorted(result.relations, key=lambda item: (item.type, item.source_id, item.target_id)):
            source = node_by_id.get(relation.source_id)
            target = node_by_id.get(relation.target_id)
            relations.append(
                {
                    "relation_id": relation.id,
                    "type": relation.type,
                    "source_id": relation.source_id,
                    "source_name": source.name if source else "",
                    "source_type": source.type if source else "",
                    "target_id": relation.target_id,
                    "target_name": target.name if target else "",
                    "target_type": target.type if target else "",
                    "evidence": evidence_blocks(relation.evidence),
                }
            )
        expressions = []
        for expression in sorted(result.math_expressions, key=lambda item: (item.role, item.owner_id, item.id)):
            owner = node_by_id.get(expression.owner_id)
            expressions.append(
                {
                    "expression_id": expression.id,
                    "role": expression.role,
                    "latex": expression.latex,
                    "owner_name": owner.name if owner else "",
                    "owner_type": owner.type if owner else "",
                    "evidence": evidence_blocks(expression.evidence),
                }
            )
        payload = {
            "source_path": result.source_path,
            "node_count": len(nodes),
            "relation_count": len(relations),
            "expression_count": len(expressions),
            "nodes": nodes,
            "relations": relations,
            "expressions": expressions,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _verify_client(self, state: ExtractionState) -> ProviderChatClient:
        return ProviderChatClient(
            provider=str(state["provider"]),
            model=self.verify_model,
            base_url=str(state["base_url"]),
            api_key_env=state.get("api_key_env"),
            temperature=0.1,
            timeout=float(state["request_timeout"]),
            max_retries=int(state["max_retries"]),
            max_tokens=4000,
            use_response_format=False,
            trust_env=not bool(state.get("no_proxy", False)),
            thinking="disabled",
        )

    def _verify_result(self, result: ExtractionResult, state: ExtractionState) -> ExtractionResult:
        started = time.perf_counter()
        verify_diag: dict[str, Any] = {
            "status": "running",
            "model": self.verify_model,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        result.diagnostics["verify"] = verify_diag
        try:
            verify_client = self._verify_client(state)
            payload = verify_client.complete_json(
                self.verify_prompt,
                self._build_verify_prompt(result),
                label="verify",
            )
            parsed = VerifyResponse.model_validate(payload)
            verdicts = [verdict.model_dump(mode="json") for verdict in parsed.verdicts]
            verify_diag.update(
                {
                    "status": "completed",
                    "total_nodes_checked": len(result.nodes),
                    "total_relations_checked": len(result.relations),
                    "flagged_node_count": sum(
                        1 for verdict in verdicts if verdict["kind"] == "node" and verdict["verdict"] == 0
                    ),
                    "flagged_relation_count": sum(
                        1
                        for verdict in verdicts
                        if verdict["kind"] == "relation" and verdict["verdict"] == 0
                    ),
                    "verdicts": verdicts,
                    "notes": parsed.notes,
                    "latency_seconds": round(time.perf_counter() - started, 4),
                    "api_summary": self._api_summary(verify_client.request_history),
                    "api_requests": verify_client.request_history,
                }
            )
            self._log(
                f"Verifier completed: {verify_diag['flagged_node_count']} nodes, "
                f"{verify_diag['flagged_relation_count']} relations flagged"
            )
        except Exception as exc:
            verify_diag.update(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_seconds": round(time.perf_counter() - started, 4),
                }
            )
            self._log(f"Verify pass failed (fail-safe): {exc}")
        return result

    def _collect_flagged_chunks(
        self,
        result: ExtractionResult,
        chunks: list[DocumentChunk],
    ) -> list[DocumentChunk]:
        verdicts = result.diagnostics.get("verify", {}).get("verdicts", [])
        flagged_ids = {verdict["ref_id"] for verdict in verdicts if verdict.get("verdict") == 0}
        node_by_id = {node.id: node for node in result.nodes}
        relation_by_id = {relation.id: relation for relation in result.relations}
        chunk_ids: set[str] = set()
        for ref_id in flagged_ids:
            item = node_by_id.get(ref_id) or relation_by_id.get(ref_id)
            if item is None:
                continue
            chunk_ids.update(evidence.chunk_id for evidence in item.evidence)
        return [chunk for chunk in chunks if chunk.id in chunk_ids]

    def _verifier_guidance_by_chunk(
        self,
        result: ExtractionResult,
        chunks: list[DocumentChunk],
    ) -> dict[str, list[dict[str, Any]]]:
        verdicts = result.diagnostics.get("verify", {}).get("verdicts", [])
        node_by_id = {node.id: node for node in result.nodes}
        relation_by_id = {relation.id: relation for relation in result.relations}
        guidance: dict[str, list[dict[str, Any]]] = {chunk.id: [] for chunk in chunks}
        for verdict in verdicts:
            if verdict.get("verdict") != 0:
                continue
            item = node_by_id.get(verdict["ref_id"]) or relation_by_id.get(verdict["ref_id"])
            if item is None:
                continue
            entry = {
                "ref_id": verdict["ref_id"],
                "kind": verdict.get("kind"),
                "name": item.name if hasattr(item, "name") else "",
                "reason": verdict.get("reason", ""),
                "correction": verdict.get("correction", ""),
            }
            for evidence in item.evidence:
                if evidence.chunk_id in guidance:
                    guidance[evidence.chunk_id].append(entry)
        return {chunk_id: items for chunk_id, items in guidance.items() if items}

    def _correct_result(
        self,
        result: ExtractionResult,
        flagged_chunks: list[DocumentChunk],
        lines: list[str],
        state: ExtractionState,
    ) -> ExtractionResult:
        client = self._client(state)
        node_store = {
            node.id: NodeRecord(
                id=node.id,
                type=node.type,
                name=node.name,
                aliases=list(node.aliases),
                description=node.description,
                claim_kind=node.claim_kind,
                logical_form=node.logical_form,
                evidence=list(node.evidence),
            )
            for node in result.nodes
        }
        relation_store = {
            (relation.source_id, relation.type, relation.target_id): RelationRecord(
                source_id=relation.source_id,
                relation_type=relation.type,
                target_id=relation.target_id,
                evidences=list(relation.evidence),
            )
            for relation in result.relations
        }
        expression_store = {
            (expression.owner_id, expression.role, expression.id): MathExpressionRecord(
                id=expression.id,
                owner_id=expression.owner_id,
                role=expression.role,
                latex=expression.latex,
                evidences=list(expression.evidence),
            )
            for expression in result.math_expressions
        }
        guidance = self._verifier_guidance_by_chunk(result, flagged_chunks)
        corrected_chunk_ids: list[str] = []
        correction_summaries: list[dict[str, Any]] = []
        for idx, chunk in enumerate(flagged_chunks, start=1):
            node_memory, relation_memory, _ = self._build_memory_for_chunk(
                chunk,
                node_store,
                relation_store,
                expression_store,
                recent_chunk_ids=set(),
                node_char_budget=state.get("memory_node_bytes", 12000),
                relation_char_budget=state.get("memory_relation_bytes", 6000),
            )
            working_node_store = {node_id: node.model_copy(deep=True) for node_id, node in node_store.items()}
            working_relation_store = {key: relation.model_copy(deep=True) for key, relation in relation_store.items()}
            working_expression_store = {key: expression.model_copy(deep=True) for key, expression in expression_store.items()}
            self._log(
                f"Verifier correcting chunk {idx}/{len(flagged_chunks)}: {chunk.id} "
                f"L{chunk.start_line}-L{chunk.end_line}"
            )
            try:
                payload = client.complete_json(
                    self.system_prompt,
                    self._chunk_extraction_prompt(
                        chunk,
                        state["input_path"],
                        node_memory,
                        relation_memory,
                        verifier_guidance=guidance.get(chunk.id),
                    ),
                    label=f"{chunk.id}_correct",
                )
                response = self._validate_chunk_response(
                    payload,
                    chunk,
                    state,
                    client,
                    corrected_chunk_ids,
                    correction_summaries,
                    len(node_store),
                    len(relation_store),
                    len(expression_store),
                    request_label=f"{chunk.id}_correct",
                )
                summary = self._merge_chunk_response(
                    response,
                    chunk,
                    state["input_path"],
                    lines,
                    working_node_store,
                    working_relation_store,
                    working_expression_store,
                    allowed_existing_ids={item.id for item in node_memory},
                )
            except ChunkMergeError:
                raise
            node_store = working_node_store
            relation_store = working_relation_store
            expression_store = working_expression_store
            corrected_chunk_ids.append(chunk.id)
            correction_summaries.append(summary)
        return self._finalize_result(
            state["input_path"],
            lines,
            node_store,
            relation_store,
            expression_store,
            {
                **dict(result.diagnostics),
                "verify_corrections": {
                    "corrected_chunk_ids": corrected_chunk_ids,
                    "guidance_count": sum(len(items) for items in guidance.values()),
                    "chunk_summaries": correction_summaries,
                },
            },
        )

    def _source_latex_candidates_for_chunk(self, chunk: DocumentChunk) -> list[dict[str, Any]]:
        visible_lines = {
            int(match.group(1))
            for line in chunk.text.splitlines()
            if (match := re.match(r"^([0-9]+):", line))
        }
        return [
            {"candidate_id": candidate_id, "line": line_no, "latex": latex}
            for candidate_id, (line_no, latex) in self.source_latex_candidates.items()
            if line_no in visible_lines
        ]

    def _build_coverage_ledger(
        self,
        chunks: list[DocumentChunk],
        node_store: dict[str, NodeRecord],
        expression_store: dict[tuple[str, str, str], MathExpressionRecord],
        *,
        chunk_strategy: str,
    ) -> dict[str, Any]:
        if chunk_strategy != "teaching-units":
            return {
                "status": "not_applicable",
                "reason": "semantic coverage requires chunk_strategy='teaching-units'",
                "unit_count": 0,
                "check_count": 0,
                "uncovered_count": 0,
                "review_eligible_count": 0,
                "uncovered_items": [],
                "units": [],
            }

        nodes = list(node_store.values())
        expressions = list(expression_store.values())
        units: list[dict[str, Any]] = []
        uncovered_items: list[dict[str, Any]] = []
        reviewable_kinds = {
            "definition",
            "definition_group",
            "definition_theorem_family",
            "theorem",
            "theorem_group",
            "theorem_family",
            "corollary",
            "property",
            "core_statement",
        }

        for chunk in chunks:
            checks: list[dict[str, Any]] = []
            for block in chunk.teaching_blocks:
                block_id = str(block.get("teaching_block_id", ""))
                role = str(block.get("discourse_role", ""))
                start_line = int(block.get("start_line", chunk.start_line))
                end_line = int(block.get("end_line", chunk.end_line))
                node_ids = sorted(
                    {
                        node.id
                        for node in nodes
                        for evidence in node.evidence
                        if evidence.teaching_block_id == block_id
                    }
                )
                concept_ids = sorted(
                    {
                        node.id
                        for node in nodes
                        if node.type == "Concept"
                        for evidence in node.evidence
                        if evidence.teaching_block_id == block_id
                    }
                )
                claim_ids = sorted(
                    {
                        node.id
                        for node in nodes
                        if node.type == "Claim"
                        for evidence in node.evidence
                        if evidence.teaching_block_id == block_id
                    }
                )
                expression_ids = sorted(
                    {
                        expression.id
                        for expression in expressions
                        for evidence in expression.evidences
                        if evidence.teaching_block_id == block_id
                    }
                )

                expected_semantics: list[str] = []
                if role == "definition":
                    expected_semantics.append("Concept")
                elif role in {"theorem", "corollary", "property"}:
                    expected_semantics.append("Claim")
                elif (
                    role in {"paragraph", "formula_exposition"}
                    and chunk.teaching_unit_kind == "core_statement"
                    and block_id in set(chunk.anchor_block_ids)
                ):
                    expected_semantics.append("Node")

                latex_candidate_ids = self._source_latex_candidates_in_span(
                    start_line, end_line
                )
                candidate_semantics = (
                    ["MathExpression"]
                    if role in {"formula_block", "formula_exposition"}
                    and latex_candidate_ids
                    else []
                )
                candidate_missing = [
                    semantic
                    for semantic in candidate_semantics
                    if semantic == "MathExpression" and not expression_ids
                ]

                covered = True
                missing: list[str] = []
                for expected in expected_semantics:
                    present = {
                        "Concept": bool(concept_ids),
                        "Claim": bool(claim_ids),
                        "Node": bool(node_ids),
                        "MathExpression": bool(expression_ids),
                    }[expected]
                    if not present:
                        covered = False
                        missing.append(expected)
                if not expected_semantics:
                    covered = bool(node_ids or expression_ids)

                check = {
                    "chunk_id": chunk.id,
                    "teaching_unit_kind": chunk.teaching_unit_kind,
                    "teaching_block_id": block_id,
                    "discourse_role": role,
                    "start_line": start_line,
                    "end_line": end_line,
                    "expected_semantics": expected_semantics,
                    "candidate_semantics": candidate_semantics,
                    "latex_candidate_ids": latex_candidate_ids,
                    "covered": covered,
                    "missing_semantics": missing,
                    "candidate_missing_semantics": candidate_missing,
                    "node_ids": node_ids,
                    "expression_ids": expression_ids,
                    "review_eligible": bool(missing)
                    and chunk.teaching_unit_kind in reviewable_kinds,
                }
                checks.append(check)
                if missing:
                    uncovered_items.append(check)

            units.append(
                {
                    "chunk_id": chunk.id,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "teaching_unit_kind": chunk.teaching_unit_kind,
                    "anchor_block_ids": list(chunk.anchor_block_ids),
                    "checks": checks,
                    "required_check_count": sum(
                        bool(check["expected_semantics"]) for check in checks
                    ),
                    "uncovered_check_count": sum(
                        bool(check["missing_semantics"]) for check in checks
                    ),
                }
            )

        required_checks = [
            check
            for unit in units
            for check in unit["checks"]
            if check["expected_semantics"]
        ]
        candidate_checks = [
            check
            for unit in units
            for check in unit["checks"]
            if check["candidate_semantics"]
        ]
        return {
            "status": "completed",
            "unit_count": len(units),
            "check_count": len(required_checks),
            "covered_count": sum(check["covered"] for check in required_checks),
            "uncovered_count": len(uncovered_items),
            "review_eligible_count": sum(
                item["review_eligible"] for item in uncovered_items
            ),
            "formula_candidate_check_count": len(candidate_checks),
            "formula_candidate_without_expression_count": sum(
                bool(check["candidate_missing_semantics"])
                for check in candidate_checks
            ),
            "uncovered_items": uncovered_items,
            "units": units,
        }

    def _source_latex_candidates_in_span(
        self, start_line: int, end_line: int
    ) -> list[str]:
        return [
            candidate_id
            for candidate_id, (line_no, _) in self.source_latex_candidates.items()
            if start_line <= line_no <= end_line
        ]

    def _judge_same_concept(self, name_a: str, desc_a: str, name_b: str, desc_b: str) -> bool:
        import os
        import urllib.error
        import urllib.request

        sys_prompt = (
            "你是数学知识图谱的命名判定器。给定两个数学概念名及其上下文描述，判断它们"
            "是否指代同一个数学对象。同一对象的不同写法（同义改写、措辞顺序不同、含 "
            "f(x) 与否、“x→+∞”与“x趋于正无穷”等写法差异）应判定为同一；名称相近但指代"
            "不同对象（如左极限与右极限、概念与其性质、“自变量趋于无穷大”这一趋近方式"
            "与“自变量趋于无穷大时的函数极限”这一极限概念）应判定为不同。"
            '只输出 JSON：{"same": true/false, "reason": "一句话理由"}'
        )
        user_prompt = (
            f'概念A: "{name_a}"；描述：{desc_a}\n'
            f'概念B: "{name_b}"；描述：{desc_b}\n'
            "判断二者是否同一对象。"
        )
        payload = {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        endpoints = (
            ("https://opencode.ai/zen/go/v1/chat/completions", "OPENCODE_API_KEY"),
            ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
        )
        errors: list[str] = []
        for url, key_env in endpoints:
            api_key = os.getenv(key_env)
            if not api_key:
                errors.append(f"{key_env} not set")
                continue
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    response = json.loads(resp.read())
                if response.get("code") not in (None, 0):
                    errors.append(f"{url} API error {response.get('code')}: {response.get('message')}")
                    continue
                content = response["choices"][0]["message"]["content"]
                try:
                    return bool(json.loads(content).get("same", False))
                except json.JSONDecodeError:
                    return False
            except urllib.error.URLError as exc:
                errors.append(f"{url} unreachable: {exc}")
                continue
        raise RuntimeError("semantic dedupe judge failed on all endpoints: " + "; ".join(errors))

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embed texts via local ollama qwen3-embedding. Raises on failure;
        callers decide how to degrade."""
        import urllib.request

        req = urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=json.dumps({"model": "qwen3-embedding:0.6b", "input": texts}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())["embeddings"]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b)) / (
            (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        )

    def _semantic_duplicate_id(
        self,
        name: str,
        description: str,
        node_store: dict[str, NodeRecord],
    ) -> str | None:
        """Return the id of an existing Concept that the proposed name denotes,
        per embedding retrieval + flash judge, else None. Any network failure
        falls back to None so the dedupe machinery never breaks extraction."""
        candidates = [record for record in node_store.values() if record.type == "Concept"]
        if not candidates or not name.strip():
            return None
        try:
            unembedded = [cand.id for cand in candidates if cand.id not in self._sem_embed_cache]
            if unembedded:
                embs = self._embed_texts([node_store[cid].name for cid in unembedded])
                for cid, emb in zip(unembedded, embs):
                    self._sem_embed_cache[cid] = emb
            (query_emb,) = self._embed_texts([name])
            best = max(
                candidates, key=lambda cand: self._cosine(query_emb, self._sem_embed_cache[cand.id])
            )
            if self._cosine(query_emb, self._sem_embed_cache[best.id]) < 0.80:
                return None
            pair = (name, best.name)
            if pair not in self._sem_judge_cache:
                self._sem_judge_cache[pair] = self._judge_same_concept(
                    name, description, best.name, best.description
                )
            return best.id if self._sem_judge_cache[pair] else None
        except Exception:
            return None

    def _chunk_node_similarities(
        self, chunk: DocumentChunk, node_store: dict[str, NodeRecord]
    ) -> dict[str, float]:
        """Batch-embed the chunk haystack and every node text; return node.id ->
        cosine similarity. Empty dict on failure (caller falls back to rules)."""
        haystack = "\n".join(
            [chunk.text, chunk.context_before_text, chunk.context_after_text, " ".join(chunk.heading_path)]
        )
        try:
            if chunk.id not in self._chunk_embed_cache:
                (query_emb,) = self._embed_texts([haystack])
                self._chunk_embed_cache[chunk.id] = query_emb
            query_emb = self._chunk_embed_cache[chunk.id]
            unembedded = [node for node in node_store.values() if node.id not in self._sem_embed_cache]
            if unembedded:
                texts = [
                    " ".join([node.name, *node.aliases, node.description[:100]])
                    for node in unembedded
                ]
                embs = self._embed_texts(texts)
                for node, emb in zip(unembedded, embs):
                    self._sem_embed_cache[node.id] = emb
            return {
                node.id: self._cosine(query_emb, self._sem_embed_cache[node.id])
                for node in node_store.values()
            }
        except Exception:
            return {}

    def _merge_chunk_response(
        self,
        response: ChunkExtractionResponse,
        chunk: DocumentChunk,
        source_path: str,
        raw_lines: list[str],
        node_store: dict[str, NodeRecord],
        relation_store: dict[tuple[str, str, str], RelationRecord],
        expression_store: dict[tuple[str, str, str], MathExpressionRecord],
        allowed_existing_ids: set[str],
    ) -> dict[str, Any]:
        """Strict transactional merge of one chunk response.

        Phase 1 validates the entire response against the current stores
        without mutating anything. Any illegal node action, illegal reuse,
        identity conflict, illegal relation endpoint or signature, illegal
        evidence, or illegal expression raises ChunkMergeError with the
        chunk_id, item kind, reference, and reason. Phase 2 applies the fully
        validated plan, so a failure can never leave partial modifications in
        the stores.
        """
        planned_nodes: list[dict[str, Any]] = []
        ref_to_final: dict[str, str] = {}
        conflict_notes: list[dict[str, Any]] = []

        def fail(kind: str, ref: str, reason: str, details: dict[str, Any] | None = None) -> None:
            raise ChunkMergeError(chunk.id, kind, ref, reason, details)

        def resolve_node_reference(reference: str) -> str | None:
            local_id = ref_to_final.get(reference)
            if local_id is not None:
                return local_id
            if reference in allowed_existing_ids and reference in node_store:
                return reference
            return None

        def node_type_of(node_id: str) -> str:
            if node_id in node_store:
                return node_store[node_id].type
            return planned_node_type[node_id]

        planned_node_type: dict[str, str] = {}
        planned_node_name: dict[str, str] = {}

        for action in response.node_actions:
            ref_id = action.ref_id.strip()
            if not ref_id:
                fail("node_action", action.ref_id, "empty ref_id")
            if ref_id in ref_to_final:
                fail("node_action", ref_id, "duplicate ref_id")
            try:
                evidence = self._sanitize_evidence(action.evidence, chunk, source_path, raw_lines)
            except ValueError as exc:
                fail("node_action", ref_id, str(exc))
            if action.action == "reuse":
                existing_id = str(action.existing_id)
                if existing_id not in allowed_existing_ids:
                    fail("node_action", ref_id, f"reuse target was not supplied in node_memory: {existing_id}")
                if existing_id not in node_store:
                    fail("node_action", ref_id, f"reuse target does not exist in canonical memory: {existing_id}")
                existing = node_store[existing_id]
                if action.type is not None and action.type != existing.type:
                    fail(
                        "node_action",
                        ref_id,
                        f"reuse type mismatch: {action.type} != canonical {existing.type} for {existing_id}",
                    )
                if action.claim_kind is not None:
                    if existing.type != "Claim":
                        fail("node_action", ref_id, f"claim_kind supplied for non-Claim reuse: {existing.id}")
                    if existing.claim_kind is not None and action.claim_kind != existing.claim_kind:
                        # A reuse's claim_kind is a coarse re-tag the model attached on top of
                        # an explicit reuse intent; the canonical first-created tag wins. Keep
                        # the existing tag and record the mismatch instead of aborting the run.
                        conflict_notes.append(
                            {
                                "conflict_kind": "reuse_claim_kind_mismatch",
                                "action": "keep_existing",
                                "ref_id": ref_id,
                                "node_id": existing_id,
                                "name": existing.name,
                                "existing_claim_kind": existing.claim_kind,
                                "proposed_claim_kind": action.claim_kind,
                                "start_line": evidence.start_line,
                                "end_line": evidence.end_line,
                            }
                        )
                        self._log(
                            f"[Extractor] reuse_claim_kind_mismatch {ref_id} for {existing_id}: "
                            f"keep canonical {existing.claim_kind} (model suggested {action.claim_kind})"
                        )
                if action.logical_form is not None and existing.type != "Claim":
                    fail("node_action", ref_id, f"logical_form supplied for non-Claim reuse: {existing.id}")
                planned_nodes.append(
                    {"action": action, "kind": "reuse", "existing_id": existing_id, "evidence": evidence}
                )
                ref_to_final[ref_id] = existing_id
            else:
                try:
                    sanitized = self._sanitize_create_action(action, evidence)
                except ValueError as exc:
                    fail("node_action", ref_id, str(exc))
                node_type = sanitized.type
                if node_type is None:
                    fail("node_action", ref_id, "create action missing node type")
                final_id = _node_id(node_type, sanitized.name)
                if final_id in node_store:
                    conflict_kind = (
                        "model_decision_conflict"
                        if final_id in allowed_existing_ids
                        else "memory_retrieval_miss"
                    )
                    if conflict_kind == "model_decision_conflict" and not self.semantic_dedupe:
                        # The model re-created a node it was explicitly shown; exact-name
                        # identity is deterministic and safe to collapse, so auto-reuse it
                        # and record the conflict in the audit instead of aborting the whole
                        # chapter run (formula/table re-listings trigger this benignly).
                        conflict_notes.append(
                            {
                                "conflict_kind": conflict_kind,
                                "action": "auto_reused",
                                "ref_id": ref_id,
                                "node_id": final_id,
                                "type": node_type,
                                "name": sanitized.name,
                                "existing_type": node_store[final_id].type,
                                "proposed_description": sanitized.description,
                                "existing_description": node_store[final_id].description,
                                "start_line": evidence.start_line,
                                "end_line": evidence.end_line,
                            }
                        )
                        self._log(
                            f"[Extractor] conflict_auto_reuse {ref_id}: {sanitized.name} "
                            f"-> existing {final_id} in {chunk.id}"
                        )
                    planned_nodes.append(
                        {
                            "action": action,
                            "kind": "reuse",
                            "existing_id": final_id,
                            "evidence": evidence,
                            "extra_aliases": [sanitized.name],
                            "semantic_merged": True,
                        }
                    )
                    ref_to_final[ref_id] = final_id
                    continue
                if final_id in planned_node_type:
                    # Two create actions in this same chunk collapse to the same canonical
                    # name (e.g. re-listed integral-table rows). Auto-reuse the later one
                    # onto the earlier planned node and record it.
                    conflict_notes.append(
                        {
                            "conflict_kind": "model_decision_conflict",
                            "action": "auto_reused_within_chunk",
                            "ref_id": ref_id,
                            "node_id": final_id,
                            "type": node_type,
                            "name": sanitized.name,
                            "existing_type": planned_node_type[final_id],
                            "existing_name": planned_node_name[final_id],
                            "start_line": evidence.start_line,
                            "end_line": evidence.end_line,
                        }
                    )
                    self._log(
                        f"[Extractor] conflict_auto_reuse_within_chunk {ref_id}: "
                        f"{sanitized.name} -> {final_id} in {chunk.id}"
                    )
                    planned_nodes.append(
                        {
                            "action": action,
                            "kind": "reuse",
                            "existing_id": final_id,
                            "evidence": evidence,
                            "extra_aliases": [sanitized.name],
                            "semantic_merged": True,
                        }
                    )
                    ref_to_final[ref_id] = final_id
                    continue
                if self.semantic_dedupe and node_type == "Concept":
                    duplicate_id = self._semantic_duplicate_id(
                        sanitized.name, sanitized.description, node_store
                    )
                    if duplicate_id is not None:
                        planned_nodes.append(
                            {
                                "action": action,
                                "kind": "reuse",
                                "existing_id": duplicate_id,
                                "evidence": evidence,
                                "extra_aliases": [sanitized.name],
                                "semantic_merged": True,
                            }
                        )
                        ref_to_final[ref_id] = duplicate_id
                        continue
                planned_node_type[final_id] = node_type
                planned_node_name[final_id] = sanitized.name
                planned_nodes.append(
                    {
                        "action": action,
                        "kind": "create",
                        "sanitized": sanitized,
                        "final_id": final_id,
                        "evidence": evidence,
                    }
                )
                ref_to_final[ref_id] = final_id

        planned_relations: list[dict[str, Any]] = []
        for relation in response.relations:
            source_ref = relation.source_ref.strip()
            target_ref = relation.target_ref.strip()
            relation_ref = f"{source_ref} -[{relation.relation_type}]-> {target_ref}"
            source_id = resolve_node_reference(source_ref)
            target_id = resolve_node_reference(target_ref)
            if source_id is None or target_id is None:
                fail(
                    "relation",
                    relation_ref,
                    "endpoint is neither a valid local ref_id nor a supplied node_memory id",
                )
            if source_id == target_id:
                fail("relation", relation_ref, "self relation")
            if not self._relation_signature_valid(
                relation.relation_type, node_type_of(source_id), node_type_of(target_id)
            ):
                fail(
                    "relation",
                    relation_ref,
                    f"invalid type signature: {node_type_of(source_id)} -> {node_type_of(target_id)}",
                )
            try:
                evidence = self._sanitize_evidence(relation.evidence, chunk, source_path, raw_lines)
            except ValueError as exc:
                fail("relation", relation_ref, str(exc))
            planned_relations.append(
                {
                    "source_ref": source_ref,
                    "target_ref": target_ref,
                    "source_id": source_id,
                    "relation_type": relation.relation_type,
                    "target_id": target_id,
                    "evidence": evidence,
                }
            )

        expected_role_by_type: dict[str, str] = {
            "Concept": "definition_representation",
            "Claim": "claim_representation",
            "Method": "method_template",
        }
        planned_expressions: list[dict[str, Any]] = []
        for expression in response.expressions:
            owner_ref = expression.owner_ref.strip()
            expression_ref = f"{owner_ref}:{expression.role}:{expression.latex_candidate_id}"
            owner_id = resolve_node_reference(owner_ref)
            if owner_id is None:
                fail(
                    "expression",
                    expression_ref,
                    "owner_ref is neither a valid local ref_id nor a supplied node_memory id",
                )
            owner_type = node_type_of(owner_id)
            if expression.role != expected_role_by_type[owner_type]:
                fail("expression", expression_ref, f"invalid expression role {expression.role} for {owner_type}")
            try:
                evidence = self._sanitize_evidence(expression.evidence, chunk, source_path, raw_lines)
                candidate_id = expression.latex_candidate_id.strip()
                candidate = self.source_latex_candidates.get(candidate_id)
                if candidate is None:
                    raise ValueError(f"unknown latex_candidate_id: {candidate_id}")
                line_no, latex = candidate
                if line_no < evidence.start_line or line_no > evidence.end_line:
                    raise ValueError(
                        f"LaTeX candidate {candidate_id} at L{line_no} is outside evidence "
                        f"L{evidence.start_line}-L{evidence.end_line}"
                    )
            except ValueError as exc:
                fail("expression", expression_ref, str(exc))
            planned_expressions.append(
                {
                    "owner_ref": owner_ref,
                    "owner_id": owner_id,
                    "role": expression.role,
                    "latex": latex,
                    "evidence": evidence,
                }
            )

        created_node_count = 0
        reused_node_count = 0
        created_refs: set[str] = set()
        relation_refs: set[str] = set()
        expression_owner_refs: set[str] = set()
        decisions: list[dict[str, Any]] = []
        for plan in planned_nodes:
            action = plan["action"]
            ref_id = action.ref_id.strip()
            evidence = plan["evidence"]
            if plan["kind"] == "reuse":
                existing = node_store[plan["existing_id"]]
                merge_outcome = self._merge_reuse_action(existing, action, evidence)
                extra_aliases = plan.get("extra_aliases") or []
                if extra_aliases:
                    previous_aliases = list(existing.aliases)
                    existing.aliases = _canonical_aliases(
                        existing.name, [*existing.aliases, *extra_aliases]
                    )
                    merge_outcome["aliases_added"] = [
                        alias for alias in existing.aliases if alias not in previous_aliases
                    ]
                final_id = existing.id
                reused_node_count += 1
            else:
                sanitized = plan["sanitized"]
                final_id = plan["final_id"]
                node_store[final_id] = NodeRecord(
                    id=final_id,
                    type=sanitized.type,
                    name=sanitized.name,
                    aliases=_canonical_aliases(sanitized.name, sanitized.aliases),
                    description=sanitized.description,
                    claim_kind=sanitized.claim_kind,
                    logical_form=sanitized.logical_form,
                    evidence=[evidence],
                )
                created_refs.add(ref_id)
                created_node_count += 1
                merge_outcome: dict[str, Any] = {
                    "mention_matches_known_surface": None,
                    "aliases_added": [],
                    "description_action": "created",
                }
            decision = {
                "chunk_id": chunk.id,
                "ref_id": ref_id,
                "mention": action.mention.strip(),
                "action": action.action,
                "node_id": final_id,
                "start_line": evidence.start_line,
                "end_line": evidence.end_line,
                "reason": action.reason.strip(),
                "status": "accepted",
                **merge_outcome,
            }
            if plan.get("semantic_merged"):
                decision["action"] = "semantic_reuse"
                decision["semantic_merged_to"] = final_id
            decisions.append(decision)

        new_relation_count = 0
        for plan in planned_relations:
            relation_refs.update(
                reference
                for reference in (plan["source_ref"], plan["target_ref"])
                if reference in ref_to_final
            )
            key = _relation_key(plan["source_id"], plan["relation_type"], plan["target_id"])
            if key in relation_store:
                self._append_relation_evidence(relation_store[key], plan["evidence"])
                continue
            left_source, rel_type, left_target = key
            relation_store[key] = RelationRecord(
                source_id=left_source,
                relation_type=rel_type,
                target_id=left_target,
                evidences=[plan["evidence"]],
            )
            new_relation_count += 1

        pruned_relations = self._prune_dominated_involves(relation_store)

        new_expression_count = 0
        for plan in planned_expressions:
            key = (plan["owner_id"], plan["role"], plan["latex"])
            if key in expression_store:
                self._append_expression_evidence(expression_store[key], plan["evidence"])
                continue
            if plan["owner_ref"] in ref_to_final:
                expression_owner_refs.add(plan["owner_ref"])
            expression_store[key] = MathExpressionRecord(
                id=_expression_id(plan["owner_id"], plan["role"], plan["latex"]),
                owner_id=plan["owner_id"],
                role=plan["role"],
                latex=plan["latex"],
                evidences=[plan["evidence"]],
            )
            new_expression_count += 1

        pending_refs = sorted(created_refs - relation_refs - expression_owner_refs)
        for ref_id in pending_refs:
            for decision in decisions:
                if decision["ref_id"] == ref_id:
                    decision["status"] = "pending_unlinked"
                    break
        return {
            "chunk_id": chunk.id,
            "created_node_count": created_node_count,
            "reused_node_count": reused_node_count,
            "new_relation_count": new_relation_count,
            "pruned_relation_count": len(pruned_relations),
            "pruned_relations": pruned_relations,
            "new_expression_count": new_expression_count,
            "total_nodes": len(node_store),
            "total_relations": len(relation_store),
            "total_expressions": len(expression_store),
            "skipped_item_count": 0,
            "skipped_items": [],
            "identity_conflict_count": len(conflict_notes),
            "identity_conflicts": conflict_notes,
            "pending_node_count": len(pending_refs),
            "pending_node_refs": pending_refs,
            "decisions": decisions,
        }

    def _sanitize_create_action(self, action: NodeAction, evidence: Evidence) -> NodeAction:
        node_type = action.type
        name = action.name.strip()
        description = re.sub(r"\s+", " ", action.description).strip()
        if node_type is None or not name:
            raise ValueError(f"Create action {action.ref_id} requires type and name")
        if not description:
            raise ValueError(f"Create action {action.ref_id} requires a concrete description")
        return NodeAction(
            ref_id=action.ref_id.strip(),
            action="create",
            mention=action.mention.strip(),
            type=node_type,
            name=name,
            aliases=_canonical_aliases(name, action.aliases),
            description=description,
            claim_kind=action.claim_kind,
            logical_form=action.logical_form,
            evidence=EvidenceRef(start_line=evidence.start_line, end_line=evidence.end_line),
            reason=action.reason.strip(),
        )

    def _sanitize_evidence(self, evidence: EvidenceRef, chunk: DocumentChunk, source_path: str, raw_lines: list[str]) -> Evidence:
        start = evidence.start_line
        end = evidence.end_line
        # The model only ever sees the chunk span PLUS its context window
        # (both rendered with line-number prefixes), so an evidence endpoint in
        # the context window is a legitimate grounding on text the model saw,
        # not a hallucination. Accept endpoints from chunk.text and
        # context_before/after; log a warning when they fall outside the span.
        visible_line_numbers = {
            int(match.group(1))
            for text in (chunk.text, chunk.context_before_text, chunk.context_after_text)
            for line in text.splitlines()
            if (match := re.match(r"^([0-9]+):", line))
        }
        if start not in visible_line_numbers or end not in visible_line_numbers or end < start:
            raise ValueError(
                f"Evidence endpoints are not visible source lines in {chunk.id}: L{start}-L{end}"
            )
        if start < chunk.start_line or end > chunk.end_line:
            self._log(
                f"[Extractor] evidence L{start}-L{end} in {chunk.id} falls in the context "
                f"window (outside span L{chunk.start_line}-L{chunk.end_line}); accepted"
            )
        source_excerpt = _text_snippet(raw_lines, start, end)
        if not source_excerpt.strip():
            raise ValueError(f"Evidence lines contain no source text in {chunk.id}: L{start}-L{end}")
        source_context = self.source_context_by_line.get(start)
        if source_context is None:
            raise ValueError(f"Evidence start line has no source structure metadata in {chunk.id}: L{start}")
        heading_path, teaching_block_id, discourse_role = source_context
        return Evidence(
            source_path=source_path,
            start_line=start,
            end_line=end,
            text=source_excerpt,
            chunk_id=chunk.id,
            heading_path=heading_path,
            teaching_block_id=teaching_block_id,
            discourse_role=discourse_role,
        )
    def _merge_reuse_action(
        self,
        existing: NodeRecord,
        action: NodeAction,
        evidence: Evidence,
    ) -> dict[str, Any]:
        mention = action.mention.strip()
        known_surface_keys = {
            _normalize_name(surface)
            for surface in [existing.name, *existing.aliases]
            if _normalize_name(surface)
        }
        mention_key = _normalize_name(mention)
        mention_matches_known_surface = bool(mention_key and mention_key in known_surface_keys)

        if action.claim_kind is not None:
            if existing.type != "Claim":
                raise ValueError(f"claim_kind supplied for non-Claim reuse: {existing.id}")
            if existing.claim_kind is not None and action.claim_kind != existing.claim_kind:
                raise ValueError(
                    f"claim_kind mismatch for {existing.id}: {action.claim_kind} != {existing.claim_kind}"
                )
            existing.claim_kind = action.claim_kind

        logical_form_action = "kept"
        logical_form_revision: dict[str, Any] | None = None
        if action.logical_form is not None:
            if existing.type != "Claim":
                raise ValueError(f"logical_form supplied for non-Claim reuse: {existing.id}")
            previous_logical_form = existing.logical_form
            if previous_logical_form is None:
                logical_form_action = "initialized"
                existing.logical_form = action.logical_form
            elif previous_logical_form != action.logical_form:
                logical_form_action = "replaced"
                existing.logical_form = action.logical_form
            else:
                logical_form_action = "unchanged"
            if logical_form_action in {"initialized", "replaced"}:
                logical_form_revision = {
                    "action": logical_form_action,
                    "previous_logical_form": (
                        previous_logical_form.model_dump(mode="json")
                        if previous_logical_form is not None
                        else None
                    ),
                    "new_logical_form": action.logical_form.model_dump(mode="json"),
                    "start_line": evidence.start_line,
                    "end_line": evidence.end_line,
                    "reason": action.reason.strip(),
                }

        previous_aliases = list(existing.aliases)
        existing.aliases = _canonical_aliases(existing.name, [*existing.aliases, *action.aliases])
        aliases_added = [alias for alias in existing.aliases if alias not in previous_aliases]

        description = re.sub(r"\s+", " ", action.description).strip()
        current_description = re.sub(r"\s+", " ", existing.description).strip()
        description_action = "kept"
        description_revision: dict[str, Any] | None = None
        if description and description != current_description:
            description_action = "initialized" if not current_description else "replaced"
            description_revision = {
                "action": description_action,
                "previous_description": current_description,
                "new_description": description,
                "start_line": evidence.start_line,
                "end_line": evidence.end_line,
                "reason": action.reason.strip(),
            }
            existing.description = description
        elif description:
            description_action = "unchanged"
        self._append_node_evidence(existing, evidence)
        return {
            "mention_matches_known_surface": mention_matches_known_surface,
            "aliases_added": aliases_added,
            "description_action": description_action,
            "description_revision": description_revision,
            "logical_form_action": logical_form_action,
            "logical_form_revision": logical_form_revision,
        }

    def _append_node_evidence(self, node: NodeRecord, evidence: Evidence) -> None:
        signature = (evidence.source_path, evidence.start_line, evidence.end_line, evidence.text.strip())
        existing = {
            (item.source_path, item.start_line, item.end_line, item.text.strip())
            for item in node.evidence
        }
        if signature not in existing:
            node.evidence.append(evidence)

    def _relation_signature_valid(self, relation_type: str, source_type: str, target_type: str) -> bool:
        return (source_type, target_type) in RELATION_SIGNATURES.get(relation_type, ())

    @staticmethod
    def _prune_dominated_involves(
        relation_store: dict[tuple[str, str, str], RelationRecord],
    ) -> list[dict[str, Any]]:
        """Remove only concept anchors already supplied by a direct basis Claim."""
        derived_bases: dict[str, set[str]] = {}
        concept_anchors: dict[str, set[str]] = {}
        for relation in relation_store.values():
            if relation.relation_type == "derived_from":
                derived_bases.setdefault(relation.source_id, set()).add(relation.target_id)
            elif relation.relation_type == "involves":
                concept_anchors.setdefault(relation.source_id, set()).add(relation.target_id)

        pruned: list[dict[str, Any]] = []
        for derived_id, basis_ids in sorted(derived_bases.items()):
            inherited_anchors: dict[str, list[str]] = {}
            for basis_id in sorted(basis_ids):
                for concept_id in sorted(concept_anchors.get(basis_id, set())):
                    inherited_anchors.setdefault(concept_id, []).append(basis_id)
            duplicate_concepts = concept_anchors.get(derived_id, set()) & inherited_anchors.keys()
            # Never strip a derived Claim to zero anchors: retain one principal
            # involves so the Claim stays retrievable without walking derived_from.
            if duplicate_concepts and duplicate_concepts == concept_anchors.get(derived_id, set()):
                duplicate_concepts = set(sorted(duplicate_concepts)[1:])
            for concept_id in sorted(duplicate_concepts):
                key = _relation_key(derived_id, "involves", concept_id)
                if key not in relation_store:
                    continue
                del relation_store[key]
                pruned.append(
                    {
                        "source_id": derived_id,
                        "relation_type": "involves",
                        "target_id": concept_id,
                        "inherited_through": inherited_anchors[concept_id],
                        "rule": "direct_derived_from_anchor_dominance",
                    }
                )
        return pruned

    def _append_relation_evidence(self, relation: RelationRecord, evidence: Evidence) -> None:
        sig = (evidence.start_line, evidence.end_line, evidence.text.strip())
        existing = {(e.start_line, e.end_line, e.text.strip()) for e in relation.evidences}
        if sig not in existing:
            relation.evidences.append(evidence)

    def _append_expression_evidence(self, expression: MathExpressionRecord, evidence: Evidence) -> None:
        sig = (evidence.start_line, evidence.end_line, evidence.text.strip())
        existing = {(e.start_line, e.end_line, e.text.strip()) for e in expression.evidences}
        if sig not in existing:
            expression.evidences.append(evidence)

    def _run_config(self, state: ExtractionState) -> dict[str, Any]:
        adapter_class = _provider_class(str(state.get("provider")))
        thinking_enabled = state.get("thinking") == "enabled"
        reasoning_effort_sent = (
            state.get("reasoning_effort")
            if adapter_class.supports_reasoning_effort and thinking_enabled
            else None
        )
        temperature_sent = not (
            adapter_class.omits_temperature_when_thinking and thinking_enabled
        )
        return {
            "run_started_at": state.get("run_started_at"),
            "run_finished_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
            "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "system_prompt_sha256": hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest(),
            "provider": state.get("provider"),
            "model_requested": state.get("model"),
            "base_url": state.get("base_url"),
            "thinking": state.get("thinking"),
            "reasoning_effort_requested": state.get("reasoning_effort"),
            "reasoning_effort_sent": reasoning_effort_sent,
            "temperature_requested": state.get("temperature"),
            "temperature_sent": state.get("temperature") if temperature_sent else None,
            "max_tokens": state.get("max_tokens"),
            "max_chunk_chars": state.get("max_chunk_chars"),
            "chunk_strategy": state.get("chunk_strategy"),
            "chunk_context_before_lines": state.get("chunk_context_before_lines"),
            "chunk_context_after_lines": state.get("chunk_context_after_lines"),
            "experimental_chunk_spans": [
                {"start_line": start, "end_line": end}
                for start, end in state.get("experimental_chunk_spans", ())
            ],
            "max_retries": state.get("max_retries"),
            "request_timeout": state.get("request_timeout"),
            "request_delay": state.get("request_delay"),
            "memory_node_bytes": state.get("memory_node_bytes"),
            "memory_relation_bytes": state.get("memory_relation_bytes"),
            "response_format_enabled": state.get("use_response_format"),
            "resume_enabled": state.get("resume"),
            "include_exercises": state.get("include_exercises"),
            "coverage_review_enabled": state.get("coverage_review", False),
            "logical_form_review_enabled": state.get("logical_form_review", False),
        }

    def _api_summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        usage_keys = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        )
        usage_totals = {key: 0 for key in usage_keys}
        reasoning_tokens = 0
        for record in records:
            usage = record.get("usage")
            if not isinstance(usage, dict):
                continue
            for key in usage_keys:
                value = usage.get(key)
                if isinstance(value, int):
                    usage_totals[key] += value
            completion_details = usage.get("completion_tokens_details")
            if isinstance(completion_details, dict):
                value = completion_details.get("reasoning_tokens")
                if isinstance(value, int):
                    reasoning_tokens += value
        return {
            "request_count": len(records),
            "successful_request_count": sum(record.get("status") == "success" for record in records),
            "failed_request_count": sum(record.get("status") != "success" for record in records),
            "total_latency_seconds": round(
                sum(float(record.get("latency_seconds", 0.0)) for record in records),
                4,
            ),
            "usage_totals": {**usage_totals, "reasoning_tokens": reasoning_tokens},
            "response_models": sorted(
                {
                    str(record["response_model"])
                    for record in records
                    if record.get("response_model")
                }
            ),
            "system_fingerprints": sorted(
                {
                    str(record["system_fingerprint"])
                    for record in records
                    if record.get("system_fingerprint")
                }
            ),
        }

    def _finalize_result(
        self,
        source_path: str,
        lines: list[str],
        node_store: dict[str, NodeRecord],
        relation_store: dict[tuple[str, str, str], RelationRecord],
        expression_store: dict[tuple[str, str, str], MathExpressionRecord],
        diagnostics: dict[str, Any],
    ) -> ExtractionResult:
        nodes = [
            KGNode(
                id=node.id,
                type=node.type,
                name=node.name,
                aliases=_canonical_aliases(node.name, node.aliases),
                description=node.description,
                claim_kind=node.claim_kind,
                logical_form=node.logical_form,
                evidence=sorted(node.evidence, key=lambda item: (item.start_line, item.end_line)),
            )
            for node in sorted(node_store.values(), key=lambda item: (item.type, item.name, item.id))
        ]
        relations = [
            KGRelation(
                id=_relation_id(relation.source_id, relation.relation_type, relation.target_id),
                type=relation.relation_type,
                source_id=relation.source_id,
                target_id=relation.target_id,
                evidence=sorted(relation.evidences, key=lambda item: (item.start_line, item.end_line)),
            )
            for relation in sorted(
                relation_store.values(),
                key=lambda item: (item.relation_type, item.source_id, item.target_id),
            )
        ]
        expressions = [
            MathExpression(
                id=expression.id,
                owner_id=expression.owner_id,
                role=expression.role,
                latex=expression.latex,
                evidence=sorted(expression.evidences, key=lambda item: (item.start_line, item.end_line)),
            )
            for expression in sorted(
                expression_store.values(),
                key=lambda item: (item.role, item.owner_id, item.id),
            )
        ]
        diagnostics = dict(diagnostics)
        diagnostics["node_count"] = len(nodes)
        diagnostics["relation_count"] = len(relations)
        diagnostics["math_expression_count"] = len(expressions)
        diagnostics["node_type_counts"] = self._count_by_type(nodes)
        diagnostics["claim_kind_counts"] = self._count_by_claim_kind(nodes)
        diagnostics["relation_type_counts"] = self._count_by_relation_type(relations)
        diagnostics["math_expression_role_counts"] = self._count_by_expression_role(expressions)
        incident_node_ids = {
            endpoint_id
            for relation in relations
            for endpoint_id in (relation.source_id, relation.target_id)
        }
        isolated_nodes = [node.id for node in nodes if node.id not in incident_node_ids]
        wide_evidence_nodes = [
            {
                "node_id": node.id,
                "max_span_lines": max(
                    evidence.end_line - evidence.start_line + 1
                    for evidence in node.evidence
                ),
            }
            for node in nodes
            if node.evidence
            and max(
                evidence.end_line - evidence.start_line + 1
                for evidence in node.evidence
            ) > 20
        ]
        chunk_summaries = diagnostics.get("chunk_summaries", [])
        identity_conflicts = [
            conflict
            for summary in chunk_summaries
            for conflict in summary.get("identity_conflicts", [])
        ]
        memory_decisions = diagnostics.get("memory_decisions", [])
        reuse_surface_reviews = [
            decision
            for decision in memory_decisions
            if decision.get("action") == "reuse"
            and decision.get("mention_matches_known_surface") is False
        ]
        description_revisions = [
            decision
            for decision in memory_decisions
            if decision.get("description_revision")
        ]
        pruned_relations = [
            relation
            for summary in chunk_summaries
            for relation in summary.get("pruned_relations", [])
        ]
        diagnostics["audit"] = {
            "isolated_node_count": len(isolated_nodes),
            "isolated_node_ids": isolated_nodes,
            "wide_evidence_node_count": len(wide_evidence_nodes),
            "wide_evidence_nodes": wide_evidence_nodes,
            "identity_conflict_count": len(identity_conflicts),
            "memory_retrieval_miss_count": sum(
                conflict.get("conflict_kind") == "memory_retrieval_miss"
                for conflict in identity_conflicts
            ),
            "model_decision_conflict_count": sum(
                conflict.get("conflict_kind") == "model_decision_conflict"
                for conflict in identity_conflicts
            ),
            "reuse_surface_review_count": len(reuse_surface_reviews),
            "description_revision_count": len(description_revisions),
            "pruned_relation_count": len(pruned_relations),
            "pruned_relations": pruned_relations,
        }
        diagnostics["title"] = self._title_from_lines(lines)
        return ExtractionResult(
            source_path=source_path,
            title=self._title_from_lines(lines),
            provider=self.provider,
            model=self.model,
            nodes=nodes,
            relations=relations,
            math_expressions=expressions,
            diagnostics=diagnostics,
        )

    def _count_by_type(self, nodes: list[KGNode]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in nodes:
            counts[node.type] = counts.get(node.type, 0) + 1
        return dict(sorted(counts.items()))

    def _count_by_relation_type(self, relations: list[KGRelation]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for relation in relations:
            counts[relation.type] = counts.get(relation.type, 0) + 1
        return dict(sorted(counts.items()))

    def _count_by_claim_kind(self, nodes: list[KGNode]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in nodes:
            if node.type == "Claim" and node.claim_kind is not None:
                counts[node.claim_kind] = counts.get(node.claim_kind, 0) + 1
        return dict(sorted(counts.items()))

    def _count_by_expression_role(self, expressions: list[MathExpression]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for expression in expressions:
            counts[expression.role] = counts.get(expression.role, 0) + 1
        return dict(sorted(counts.items()))

    def _title_from_lines(self, lines: list[str]) -> str:
        for line in lines:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                if title:
                    return title
        return self.input_path.stem

    def _cache_dir(self) -> Path:
        path = ROOT / "node_cache" / "semantic_layer_v1_7_0"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _cache_fingerprint(
        self,
        chunk: DocumentChunk,
        state: ExtractionState,
        node_memory: list[NodeMemoryItem],
        relation_memory: list[RelationMemoryItem],
    ) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "chunk_strategy": state.get("chunk_strategy", "budget"),
            "system_prompt_sha256": hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest(),
            "chunk": {
                "id": chunk.id,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "heading_path": chunk.heading_path,
                "teaching_block_ids": list(chunk.teaching_block_ids),
                "teaching_blocks": list(chunk.teaching_blocks),
                "context_before_text": chunk.context_before_text,
                "text": chunk.text,
                "context_after_text": chunk.context_after_text,
            },
            "node_memory": [item.model_dump(mode="json", exclude_none=True) for item in node_memory],
            "relation_memory": [item.model_dump(mode="json", exclude_none=True) for item in relation_memory],
            "source_latex_candidates": self._source_latex_candidates_for_chunk(chunk),
            "provider": state["provider"],
            "model": state["model"],
            "base_url": state["base_url"],
            "temperature": state["temperature"],
            "thinking": state.get("thinking"),
            "reasoning_effort": state.get("reasoning_effort"),
            "max_tokens": state["max_tokens"],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    def _cache_path(self, chunk_id: str, fingerprint: str, state: ExtractionState) -> Path:
        filename = (
            f"{_slug(Path(state['input_path']).stem, 60)}__"
            f"{_slug(str(state['provider']), 20)}__{_slug(str(state['model']), 40)}__{chunk_id}__{fingerprint}.json"
        )
        return self._cache_dir() / filename

    def _read_chunk_cache(self, chunk_id: str, fingerprint: str, state: ExtractionState) -> dict[str, Any] | None:
        path = self._cache_path(chunk_id, fingerprint, state)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_chunk_cache(self, chunk_id: str, fingerprint: str, payload: dict[str, Any], state: ExtractionState) -> None:
        self._cache_path(chunk_id, fingerprint, state).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

def check_config(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    thinking: str = "disabled",
    reasoning_effort: str = "high",
) -> int:
    provider_name = _provider_name(provider)
    adapter_class = _provider_class(provider_name)
    resolved_model = model or _env_first(list(adapter_class.model_envs)) or adapter_class.default_model
    resolved_base_url = (base_url or _env_first(list(adapter_class.base_url_envs)) or adapter_class.default_base_url).rstrip("/")
    key_names = (api_key_env,) if api_key_env else adapter_class.api_key_envs
    key_name = next((name for name in key_names if os.getenv(name)), None)
    print(
        json.dumps(
            {
                "provider": provider_name,
                "api_key_env": key_name or api_key_env,
                "api_key_found": bool(key_name),
                "base_url": resolved_base_url,
                "endpoint_url": _completion_url(resolved_base_url),
                "model": resolved_model,
                "thinking": thinking,
                "reasoning_effort_requested": reasoning_effort,
                "reasoning_effort_sent": (
                    reasoning_effort
                    if adapter_class.supports_reasoning_effort and thinking == "enabled"
                    else None
                ),
                "response_format_supported": adapter_class.supports_response_format,
                "chunk_strategies": CHUNK_STRATEGIES,
                "chunk_context_before_lines": 6,
                "chunk_context_after_lines": 4,
                "schema_version": SCHEMA_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if key_name else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sequential first-layer mathematical semantic extractor using LangGraph.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Markdown source path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON output path.")
    parser.add_argument(
        "--provider",
        default=None,
        choices=sorted(set(PROVIDER_REGISTRY) | set(PROVIDER_ALIASES)),
        help="Model provider. Defaults to MODEL_PROVIDER or deepseek.",
    )
    parser.add_argument("--model", default=None, help="Provider model name. Uses the provider-specific environment variable or default.")
    parser.add_argument("--base-url", default=None, help="Provider base URL or full /chat/completions endpoint.")
    parser.add_argument("--api-key-env", default=None, help="Name of an environment variable containing the provider API key.")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-chunk-chars", type=int, default=3200)
    parser.add_argument(
        "--chunk-strategy",
        default="budget",
        choices=CHUNK_STRATEGIES,
        help=(
            "Chunk construction strategy. 'teaching-units' skips explicit example sequences "
            "and groups headings, theorem lead-ins, theorem variants, and corollaries."
        ),
    )
    parser.add_argument("--chunk-context-before-lines", type=int, default=6)
    parser.add_argument("--chunk-context-after-lines", type=int, default=4)
    parser.add_argument(
        "--experimental-chunk-spans",
        type=_parse_experimental_chunk_spans,
        default=(),
        help=(
            "Strict experiment-only START-END spans separated by commas. "
            "Every nonblank source line and teaching block must be covered exactly once."
        ),
    )
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument(
        "--memory-node-bytes",
        type=int,
        default=12000,
        help="Serialized byte budget for nodes in Top-K memory (default ~40 nodes).",
    )
    parser.add_argument(
        "--memory-relation-bytes",
        type=int,
        default=6000,
        help="Serialized byte budget for relations in Top-K memory (default ~60 relations).",
    )
    parser.add_argument("--request-delay", type=float, default=3.0, help="Seconds to sleep between API calls.")
    parser.add_argument("--thinking", default="disabled", choices=["enabled", "disabled"], help="Provider thinking mode when supported.")
    parser.add_argument(
        "--reasoning-effort",
        default="high",
        choices=["low", "medium", "high", "max"],
        help="Requested reasoning effort. DeepSeek currently maps medium to high.",
    )
    parser.add_argument("--no-save", action="store_true", help="Run the graph but do not write output file.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore cached chunk outputs.")
    parser.add_argument("--include-exercises", action="store_true", help="Also extract exercise content.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logs.")
    parser.add_argument("--no-response-format", action="store_true", help="Disable JSON response_format for providers that support it.")
    parser.add_argument("--no-proxy", action="store_true", help="Ignore HTTP(S)_PROXY environment variables for model API calls.")
    parser.add_argument("--verify", action="store_true", help="Run a post-extraction verifier pass over the result graph.")
    parser.add_argument("--verify-model", default=None, help="Model used by the verifier (default: deepseek-v4-flash).")
    parser.add_argument(
        "--semantic-dedupe",
        action="store_true",
        help="Merge creates that denote an already-known Concept (embedding retrieval + flash judge).",
    )
    parser.add_argument(
        "--embedding-memory",
        action="store_true",
        help="Add an embedding similarity component to node_memory selection (qwen3-embedding via ollama).",
    )
    parser.add_argument(
        "--seed-graph",
        default=None,
        help="Path to a previously extracted KG JSON to seed the node/relation store "
        "(incremental update). Seed nodes participate in memory and dedupe; relations "
        "dedupe by exact (source, type, target) key against seed relations.",
    )
    parser.add_argument(
        "--coverage-review",
        action="store_true",
        help=(
            "After the first pass, re-run the same Extractor only on high-confidence "
            "uncovered definition/theorem/property/corollary teaching units. Requires "
            "--chunk-strategy teaching-units. The coverage ledger is always diagnostic "
            "and never creates facts by rule."
        ),
    )
    parser.add_argument(
        "--logical-form-review",
        action="store_true",
        help=(
            "Review only existing Claim nodes that lack logical_form but whose evidence "
            "contains an explicit implication or condition signal."
        ),
    )
    parser.add_argument("--check-config", action="store_true", help="Check the selected provider configuration without calling the model.")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args()
    if args.check_config:
        raise SystemExit(
            check_config(
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
                thinking=args.thinking,
                reasoning_effort=args.reasoning_effort,
            )
        )
    extractor = Extractor(
        input_path=args.input,
        output_path=args.output,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        max_chunk_chars=args.max_chunk_chars,
        chunk_strategy=args.chunk_strategy,
        chunk_context_before_lines=args.chunk_context_before_lines,
        chunk_context_after_lines=args.chunk_context_after_lines,
        experimental_chunk_spans=args.experimental_chunk_spans,
        max_retries=args.max_retries,
        request_timeout=args.request_timeout,
        max_tokens=args.max_tokens,
        memory_node_bytes=args.memory_node_bytes,
        memory_relation_bytes=args.memory_relation_bytes,
        save_output=not args.no_save,
        use_response_format=not args.no_response_format,
        no_proxy=args.no_proxy,
        request_delay=args.request_delay,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        resume=not args.no_resume,
        include_exercises=args.include_exercises,
        verbose=not args.quiet,
        verify=args.verify,
        verify_model=args.verify_model,
        semantic_dedupe=args.semantic_dedupe,
        embedding_memory=args.embedding_memory,
        seed_graph_path=args.seed_graph,
        coverage_review=args.coverage_review,
        logical_form_review=args.logical_form_review,
    )
    try:
        result = extractor.run()
    except (RuntimeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(f"Extractor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result.diagnostics, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
