"""Post-verification semantic linker for MaF-KG v5 artifacts.

ARCHIVED (已归档): this module targets the retired ``maf-kg.agent.v5`` entity-graph
schema and imports ``agents.ConceptFormulaExtractor``, which no longer exists in this
repository. It is kept for historical reference only and cannot be imported as-is.
The active schema is ``maf-kg.semantic.v1.6.0`` (Concept / Claim / Method), produced by
``agents/Extractor.py``.

Original description follows:

This module adds a conservative second layer after ConceptFormulaExtractor:
deterministic candidate generation, one LLM linker, deterministic validation,
and an optional patch merge. It intentionally does not create new nodes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

try:
    from agents.ConceptFormulaExtractor import DeepSeekChatClient
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from agents.ConceptFormulaExtractor import DeepSeekChatClient


ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_KG_DIR = ROOT / "KG"

RELATION_SIGNATURES: dict[str, tuple[set[str], set[str]]] = {
    "has_formula": ({"Concept", "Property", "Theorem"}, {"Formula"}),
    "has_property": ({"Concept", "Theorem"}, {"Property"}),
    "example_of": ({"Example"}, {"Concept", "Property", "Theorem"}),
    "illustrated_by": ({"Concept", "Example", "Property", "Theorem"}, {"Figure"}),
    "prerequisite_of": ({"Concept", "Property", "Theorem"}, {"Concept", "Property", "Theorem"}),
    "similar_to": ({"Concept", "Property", "Formula", "Theorem"}, {"Concept", "Property", "Formula", "Theorem"}),
    "proved_by": ({"Theorem"}, {"Proof"}),
}

TASK_RELATION = {
    "formula_linking": "has_formula",
    "property_linking": "has_property",
    "example_linking": "example_of",
    "figure_linking": "illustrated_by",
    "prerequisite_linking": "prerequisite_of",
    "similarity_linking": "similar_to",
    "proof_linking": "proved_by",
}

DIRECT_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.72


class SupportItem(BaseModel):
    source_anchor: dict[str, Any] = Field(default_factory=dict)
    target_anchor: dict[str, Any] = Field(default_factory=dict)
    bridge_span: dict[str, Any] | None = None


class EdgeProposal(BaseModel):
    source_id: str
    relation: Literal["has_formula", "has_property", "example_of", "illustrated_by", "prerequisite_of", "similar_to", "proved_by"]
    target_id: str
    confidence: float = Field(ge=0, le=1)
    evidence_mode: Literal["direct", "contextual", "derived"] = "direct"
    strength: Literal["strong", "weak"] = "strong"
    reason: str
    support: list[SupportItem] = Field(default_factory=list)
    rule_hits: list[str] = Field(default_factory=list)
    validator_flags: list[str] = Field(default_factory=list)
    write_decision: Literal["accept", "review"] = "review"
    candidate_id: str | None = None


class RejectedPair(BaseModel):
    candidate_id: str | None = None
    source_id: str
    target_id: str
    relation: str
    reason: str


class LinkingResponse(BaseModel):
    accepted_edges: list[EdgeProposal] = Field(default_factory=list)
    review_edges: list[EdgeProposal] = Field(default_factory=list)
    rejected_pairs: list[RejectedPair] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_model_drift(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if isinstance(data.get("notes"), str):
            data["notes"] = [data["notes"]]

        rejected_pairs = list(data.get("rejected_pairs") or [])
        for bucket_name in ["accepted_edges", "review_edges"]:
            cleaned = []
            for item in data.get(bucket_name) or []:
                if not isinstance(item, dict):
                    cleaned.append(item)
                    continue
                decision = str(item.get("write_decision", "")).strip().lower()
                if decision == "reject":
                    rejected_pairs.append(
                        {
                            "candidate_id": item.get("candidate_id"),
                            "source_id": item.get("source_id"),
                            "target_id": item.get("target_id"),
                            "relation": item.get("relation", ""),
                            "reason": item.get("reason") or "model_returned_reject_decision",
                        }
                    )
                    continue
                if item.get("confidence") is None:
                    item = dict(item)
                    item["confidence"] = DIRECT_THRESHOLD if bucket_name == "accepted_edges" else REVIEW_THRESHOLD
                cleaned.append(item)
            data[bucket_name] = cleaned
        data["rejected_pairs"] = rejected_pairs
        return data


class PatchFile(BaseModel):
    schema_version: str = "maf-kg.semantic-linking-patch.v1"
    source_kg_path: str
    model: str
    accepted_edges: list[dict[str, Any]]
    review_edges: list[dict[str, Any]]
    rejected_pairs: list[dict[str, Any]]
    coverage_report: dict[str, Any]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


@dataclass
class CandidatePair:
    id: str
    task_type: str
    relation: str
    source_id: str
    target_id: str
    score: float
    rule_hits: list[str]
    source_node: dict[str, Any]
    target_node: dict[str, Any]

    def compact(self) -> dict[str, Any]:
        return {
            "candidate_id": self.id,
            "task_type": self.task_type,
            "relation": self.relation,
            "source": compact_node(self.source_node),
            "target": compact_node(self.target_node),
            "candidate_score": round(self.score, 4),
            "rule_hits": self.rule_hits,
        }


class SemanticLinkingAgent:
    def __init__(
        self,
        kg_path: str | Path | None = None,
        patch_path: str | Path | None = None,
        enhanced_path: str | Path | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_retries: int = 3,
        request_timeout: float = 180,
        max_tokens: int = 12000,
        request_delay: float = 3.0,
        no_proxy: bool = False,
        batch_size: int = 20,
        top_k: int = 6,
        line_window: int = 80,
        dry_run: bool = False,
        merge: bool = False,
        save_patch: bool = True,
        resume: bool = True,
        verbose: bool = True,
    ) -> None:
        self.kg_path = resolve_kg_path(kg_path)
        self.patch_path = Path(patch_path) if patch_path else default_patch_path(self.kg_path)
        self.enhanced_path = Path(enhanced_path) if enhanced_path else default_enhanced_path(self.kg_path)
        self.model = model or os.getenv("DEEPSEEK_LINKING_MODEL") or os.getenv("DS_LINKING_MODEL") or "deepseek-v4-flash"
        self.temperature = temperature
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_tokens = max_tokens
        self.request_delay = request_delay
        self.no_proxy = no_proxy
        self.batch_size = max(1, batch_size)
        self.top_k = max(1, top_k)
        self.line_window = max(1, line_window)
        self.dry_run = dry_run
        self.merge = merge
        self.save_patch = save_patch
        self.resume = resume
        self.verbose = verbose
        self.client: DeepSeekChatClient | None = None

    def run(self) -> PatchFile:
        self._log(f"Loading KG: {self.kg_path}")
        kg = json.loads(self.kg_path.read_text(encoding="utf-8"))
        validate_kg(kg)
        candidates = CandidateBuilder(kg, top_k=self.top_k, line_window=self.line_window).build()
        self._log(f"CandidateBuilder generated {len(candidates)} candidate pairs")

        if self.dry_run:
            patch = self._build_patch(kg, [], [], [], candidates, {"dry_run": True})
            if self.save_patch:
                self._write_patch(patch)
            return patch

        accepted: list[dict[str, Any]] = []
        review: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        grouped: dict[str, list[CandidatePair]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.task_type, []).append(candidate)

        existing_edge_keys = existing_edges(kg)
        candidate_by_id = {candidate.id: candidate for candidate in candidates}
        seen_patch_keys: set[tuple[str, str, str]] = set()

        for task_type, task_candidates in grouped.items():
            for index, batch in enumerate(batch_items(task_candidates, self.batch_size), start=1):
                if accepted or review or rejected:
                    time.sleep(self.request_delay)
                self._log(f"SemanticLinkingAgent {task_type} batch {index} start: {len(batch)} candidates")
                cache_label = self._batch_cache_label(task_type, index, batch)
                cached = self._read_cache(cache_label) if self.resume else None
                if cached:
                    response = LinkingResponse.model_validate(cached)
                    self._log(f"SemanticLinkingAgent {task_type} batch {index} resume")
                else:
                    response = self._call_linker(task_type, batch, kg)
                    self._write_cache(cache_label, response.model_dump(mode="json"))
                batch_accepted, batch_review, batch_rejected = validate_linking_response(
                    response,
                    kg,
                    candidate_by_id,
                    existing_edge_keys,
                    seen_patch_keys,
                )
                accepted.extend(batch_accepted)
                review.extend(batch_review)
                rejected.extend(batch_rejected)
                self._log(
                    f"SemanticLinkingAgent {task_type} batch {index} done: "
                    f"{len(batch_accepted)} accepted, {len(batch_review)} review, {len(batch_rejected)} rejected"
                )

        patch = self._build_patch(
            kg,
            accepted,
            review,
            dedupe_rejected_pairs(rejected),
            candidates,
            {"dry_run": False},
        )
        if self.save_patch:
            self._write_patch(patch)
        if self.merge and self.save_patch:
            self._write_enhanced_kg(kg, patch)
        return patch

    def _client(self) -> DeepSeekChatClient:
        if self.client is None:
            self.client = DeepSeekChatClient(
                model=self.model,
                temperature=self.temperature,
                timeout=self.request_timeout,
                max_retries=self.max_retries,
                max_tokens=self.max_tokens,
                use_response_format=True,
                trust_env=not self.no_proxy,
                thinking="disabled",
            )
        return self.client

    def _call_linker(self, task_type: str, candidates: list[CandidatePair], kg: dict[str, Any]) -> LinkingResponse:
        prompt = {
            "task": "Judge whether each candidate pair supports the requested semantic relationship.",
            "task_type": task_type,
            "allowed_relation": TASK_RELATION[task_type],
            "source_kg": {
                "schema_version": kg.get("schema_version"),
                "source_path": kg.get("source_path"),
                "title": kg.get("title"),
            },
            "decision_contract": {
                "accept": "Use only for direct, high-confidence evidence.",
                "review": "Use for plausible but incomplete evidence.",
                "reject": "Use when evidence is insufficient, ambiguous, or type semantics do not match.",
                "no_new_nodes": True,
            },
            "candidate_pairs": [candidate.compact() for candidate in candidates],
            "output_schema": {
                "accepted_edges": [
                    {
                        "candidate_id": "candidate id",
                        "source_id": "existing source node id",
                        "relation": TASK_RELATION[task_type],
                        "target_id": "existing target node id",
                        "confidence": "0..1",
                        "evidence_mode": "direct|contextual",
                        "strength": "strong|weak",
                        "reason": "short evidence-grounded reason",
                        "support": [
                            {
                                "source_anchor": {"start_line": 1, "end_line": 1, "text": "..."},
                                "target_anchor": {"start_line": 1, "end_line": 1, "text": "..."},
                                "bridge_span": {"start_line": 1, "end_line": 1, "text": "..."},
                            }
                        ],
                        "rule_hits": ["rule names from candidate if applicable"],
                        "write_decision": "accept",
                    }
                ],
                "review_edges": [],
                "rejected_pairs": [
                    {
                        "candidate_id": "candidate id",
                        "source_id": "source id",
                        "target_id": "target id",
                        "relation": TASK_RELATION[task_type],
                        "reason": "why rejected",
                    }
                ],
                "notes": [],
            },
        }
        raw = self._client().complete_json(
            load_prompt("semantic_linking_system.txt"),
            json.dumps(prompt, ensure_ascii=False),
            label=f"semantic_{task_type}",
        )
        return LinkingResponse.model_validate(raw)

    def _build_patch(
        self,
        kg: dict[str, Any],
        accepted: list[dict[str, Any]],
        review: list[dict[str, Any]],
        rejected: list[dict[str, Any]],
        candidates: list[CandidatePair],
        extra_diag: dict[str, Any],
    ) -> PatchFile:
        diagnostics = {
            "candidate_count": len(candidates),
            "candidate_count_by_task": count_by(candidates, lambda item: item.task_type),
            "accepted_count": len(accepted),
            "review_count": len(review),
            "rejected_count": len(rejected),
            "model": self.model,
            "batch_size": self.batch_size,
            "top_k": self.top_k,
            "line_window": self.line_window,
            **extra_diag,
        }
        return PatchFile(
            source_kg_path=str(self.kg_path),
            model=self.model,
            accepted_edges=accepted,
            review_edges=review,
            rejected_pairs=rejected,
            coverage_report=coverage_report(kg, accepted),
            diagnostics=diagnostics,
        )

    def _write_patch(self, patch: PatchFile) -> None:
        self.patch_path.parent.mkdir(parents=True, exist_ok=True)
        self.patch_path.write_text(patch.model_dump_json(indent=2), encoding="utf-8")
        self._log(f"Wrote semantic linking patch: {self.patch_path}")

    def _write_enhanced_kg(self, kg: dict[str, Any], patch: PatchFile) -> None:
        enhanced = merge_patch(kg, patch.accepted_edges)
        self.enhanced_path.parent.mkdir(parents=True, exist_ok=True)
        self.enhanced_path.write_text(json.dumps(enhanced, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"Wrote enhanced KG: {self.enhanced_path}")

    def _cache_dir(self) -> Path:
        cache_dir = ROOT / "KG" / "_cache" / "semantic_linking"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir
        except OSError:
            cache_dir = Path(tempfile.gettempdir()) / "maf_kg" / "semantic_linking_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir

    def _batch_cache_label(self, task_type: str, index: int, batch: list[CandidatePair]) -> str:
        candidate_ids = ",".join(candidate.id for candidate in batch)
        raw = (
            f"{self.kg_path.resolve()}|{self.model}|{task_type}|{index}|"
            f"{self.batch_size}|{self.top_k}|{self.line_window}|semantic_linking_v1|{candidate_ids}"
        )
        return f"{task_type}_{index:03d}_{stable_hash(raw)}.json"

    def _read_cache(self, label: str) -> dict[str, Any] | None:
        path = self._cache_dir() / label
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, label: str, payload: dict[str, Any]) -> None:
        try:
            path = self._cache_dir() / label
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[SemanticLinkingAgent] {message}", flush=True)


class CandidateBuilder:
    def __init__(self, kg: dict[str, Any], top_k: int = 6, line_window: int = 80) -> None:
        self.kg = kg
        self.nodes = kg.get("nodes", [])
        self.node_by_id = {node["id"]: node for node in self.nodes}
        self.relationships = kg.get("relationships", [])
        self.existing = existing_edges(kg)
        self.top_k = top_k
        self.line_window = line_window

    def build(self) -> list[CandidatePair]:
        candidates: list[CandidatePair] = []
        candidates.extend(self._formula_candidates())
        candidates.extend(self._property_candidates())
        candidates.extend(self._example_candidates())
        candidates.extend(self._figure_candidates())
        candidates.extend(self._prerequisite_candidates())
        candidates.extend(self._similarity_candidates())
        candidates.extend(self._proof_candidates())
        return candidates

    def _formula_candidates(self) -> list[CandidatePair]:
        formulas = self._unattached_targets("Formula", "has_formula")
        sources = self._nodes_of_type({"Concept", "Property", "Theorem"})
        return self._top_candidates("formula_linking", "has_formula", sources, formulas)

    def _property_candidates(self) -> list[CandidatePair]:
        properties = self._unattached_targets("Property", "has_property")
        sources = self._nodes_of_type({"Concept", "Theorem"})
        return self._top_candidates("property_linking", "has_property", sources, properties)

    def _example_candidates(self) -> list[CandidatePair]:
        examples = self._unattached_sources("Example", "example_of")
        targets = self._nodes_of_type({"Concept", "Property", "Theorem"})
        return self._top_candidates_by_source("example_linking", "example_of", examples, targets)

    def _figure_candidates(self) -> list[CandidatePair]:
        figures = self._unattached_targets("Figure", "illustrated_by")
        sources = self._nodes_of_type({"Concept", "Example", "Property", "Theorem"})
        explicit = self._explicit_reference_candidates("figure_linking", "illustrated_by", sources, figures)
        proximity = self._top_candidates("figure_linking", "illustrated_by", sources, figures)
        return dedupe_candidates([*explicit, *proximity])

    def _prerequisite_candidates(self) -> list[CandidatePair]:
        knowledge_units = self._nodes_of_type({"Concept", "Property", "Theorem"})
        return self._top_candidates_by_source("prerequisite_linking", "prerequisite_of", knowledge_units, knowledge_units)

    def _similarity_candidates(self) -> list[CandidatePair]:
        candidates: list[CandidatePair] = []
        for node_type in ["Concept", "Property", "Formula", "Theorem"]:
            typed_nodes = self._nodes_of_type({node_type})
            candidates.extend(self._top_candidates_by_source("similarity_linking", "similar_to", typed_nodes, typed_nodes))
        return dedupe_candidates(candidates)

    def _proof_candidates(self) -> list[CandidatePair]:
        proofs = self._unattached_targets("Proof", "proved_by")
        theorems = self._nodes_of_type({"Theorem"})
        return self._top_candidates("proof_linking", "proved_by", theorems, proofs)

    def _unattached_targets(self, node_type: str, rel_type: str) -> list[dict[str, Any]]:
        attached = {rel.get("target") for rel in self.relationships if rel.get("type") == rel_type}
        return [node for node in self.nodes if node.get("type") == node_type and node.get("id") not in attached]

    def _unattached_sources(self, node_type: str, rel_type: str) -> list[dict[str, Any]]:
        attached = {rel.get("source") for rel in self.relationships if rel.get("type") == rel_type}
        return [node for node in self.nodes if node.get("type") == node_type and node.get("id") not in attached]

    def _nodes_of_type(self, node_types: set[str]) -> list[dict[str, Any]]:
        return [node for node in self.nodes if node.get("type") in node_types]

    def _top_candidates(
        self,
        task_type: str,
        rel_type: str,
        sources: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> list[CandidatePair]:
        all_candidates: list[CandidatePair] = []
        for target in targets:
            scored: list[CandidatePair] = []
            for source in sources:
                if source["id"] == target["id"]:
                    continue
                if (rel_type, source["id"], target["id"]) in self.existing:
                    continue
                score, hits = candidate_score(source, target, rel_type, self.line_window)
                if score <= 0:
                    continue
                scored.append(
                    CandidatePair(
                        id=candidate_id(task_type, rel_type, source["id"], target["id"]),
                        task_type=task_type,
                        relation=rel_type,
                        source_id=source["id"],
                        target_id=target["id"],
                        score=score,
                        rule_hits=hits,
                        source_node=source,
                        target_node=target,
                    )
                )
            scored.sort(key=lambda item: item.score, reverse=True)
            all_candidates.extend(scored[: self.top_k])
        return all_candidates

    def _top_candidates_by_source(
        self,
        task_type: str,
        rel_type: str,
        sources: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> list[CandidatePair]:
        all_candidates: list[CandidatePair] = []
        for source in sources:
            scored: list[CandidatePair] = []
            for target in targets:
                if source["id"] == target["id"]:
                    continue
                if (rel_type, source["id"], target["id"]) in self.existing:
                    continue
                score, hits = candidate_score(source, target, rel_type, self.line_window)
                if score <= 0:
                    continue
                scored.append(
                    CandidatePair(
                        id=candidate_id(task_type, rel_type, source["id"], target["id"]),
                        task_type=task_type,
                        relation=rel_type,
                        source_id=source["id"],
                        target_id=target["id"],
                        score=score,
                        rule_hits=hits,
                        source_node=source,
                        target_node=target,
                    )
                )
            scored.sort(key=lambda item: item.score, reverse=True)
            all_candidates.extend(scored[: self.top_k])
        return all_candidates

    def _explicit_reference_candidates(
        self,
        task_type: str,
        rel_type: str,
        sources: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> list[CandidatePair]:
        all_candidates: list[CandidatePair] = []
        for target in targets:
            for source in sources:
                if source["id"] == target["id"]:
                    continue
                if (rel_type, source["id"], target["id"]) in self.existing:
                    continue
                hits = source_figure_reference_hits(source, target)
                if not hits:
                    continue
                base_score, base_hits = candidate_score(source, target, rel_type, self.line_window)
                merged_hits = list(dict.fromkeys([*base_hits, *hits]))
                all_candidates.append(
                    CandidatePair(
                        id=candidate_id(task_type, rel_type, source["id"], target["id"]),
                        task_type=task_type,
                        relation=rel_type,
                        source_id=source["id"],
                        target_id=target["id"],
                        score=max(base_score, 0.98),
                        rule_hits=merged_hits,
                        source_node=source,
                        target_node=target,
                    )
                )
        all_candidates.sort(key=lambda item: item.score, reverse=True)
        return all_candidates


def validate_linking_response(
    response: LinkingResponse,
    kg: dict[str, Any],
    candidate_by_id: dict[str, CandidatePair],
    existing_edge_keys: set[tuple[str, str, str]],
    seen_patch_keys: set[tuple[str, str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    node_by_id = {node["id"]: node for node in kg.get("nodes", [])}
    accepted: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = [item.model_dump(mode="json") for item in response.rejected_pairs]

    for bucket_name, proposals in [("accepted", response.accepted_edges), ("review", response.review_edges)]:
        for proposal in proposals:
            flags = validator_flags(proposal, node_by_id, candidate_by_id, existing_edge_keys, seen_patch_keys)
            proposal.validator_flags.extend(flag for flag in flags if flag not in proposal.validator_flags)
            edge = proposal_to_edge(proposal, kg, candidate_by_id)
            if flags:
                edge["validator_flags"] = proposal.validator_flags
                rejected.append(
                    {
                        "candidate_id": proposal.candidate_id,
                        "source_id": proposal.source_id,
                        "target_id": proposal.target_id,
                        "relation": proposal.relation,
                        "reason": "validator_rejected:" + ",".join(flags),
                    }
                )
                continue
            key = (proposal.relation, proposal.source_id, proposal.target_id)
            if bucket_name == "accepted" and proposal.confidence >= DIRECT_THRESHOLD and proposal.evidence_mode == "direct":
                seen_patch_keys.add(key)
                accepted.append(edge)
            elif proposal.confidence >= REVIEW_THRESHOLD:
                review.append(edge)
            else:
                rejected.append(
                    {
                        "candidate_id": proposal.candidate_id,
                        "source_id": proposal.source_id,
                        "target_id": proposal.target_id,
                        "relation": proposal.relation,
                        "reason": "below_review_threshold",
                    }
                )
    return accepted, review, rejected


def validator_flags(
    proposal: EdgeProposal,
    node_by_id: dict[str, dict[str, Any]],
    candidate_by_id: dict[str, CandidatePair],
    existing_edge_keys: set[tuple[str, str, str]],
    seen_patch_keys: set[tuple[str, str, str]],
) -> list[str]:
    flags: list[str] = []
    candidate = candidate_by_id.get(proposal.candidate_id or "")
    if candidate is None:
        flags.append("unknown_candidate_id")
    elif (
        candidate.source_id != proposal.source_id
        or candidate.target_id != proposal.target_id
        or candidate.relation != proposal.relation
    ):
        flags.append("candidate_mismatch")
    source = node_by_id.get(proposal.source_id)
    target = node_by_id.get(proposal.target_id)
    if source is None or target is None:
        flags.append("invalid_node_reference")
        return flags
    allowed_sources, allowed_targets = RELATION_SIGNATURES[proposal.relation]
    if source.get("type") not in allowed_sources or target.get("type") not in allowed_targets:
        flags.append("wrong_type_signature")
    if proposal.source_id == proposal.target_id:
        flags.append("self_loop")
    key = (proposal.relation, proposal.source_id, proposal.target_id)
    if key in existing_edge_keys or key in seen_patch_keys:
        flags.append("duplicate_edge")
    if proposal.evidence_mode != "direct":
        flags.append("hard_relation_requires_direct_evidence")
    if proposal.relation == "illustrated_by" and not figure_label_supported(proposal, source, target):
        flags.append("illustrated_by_requires_explicit_figure_reference")
    if proposal.confidence < REVIEW_THRESHOLD:
        flags.append("low_confidence")
    if not proposal.reason.strip():
        flags.append("missing_reason")
    if not proposal.support:
        flags.append("missing_support")
    return flags


def figure_label_supported(proposal: EdgeProposal, source: dict[str, Any], target: dict[str, Any]) -> bool:
    texts = [source_reference_text(source)]
    for support in proposal.support:
        if isinstance(support.source_anchor, dict):
            texts.append(str(support.source_anchor.get("text") or support.source_anchor.get("excerpt") or ""))
    return bool(source_figure_reference_hits(source, target, "\n".join(texts)))


def proposal_to_edge(
    proposal: EdgeProposal,
    kg: dict[str, Any],
    candidate_by_id: dict[str, CandidatePair],
) -> dict[str, Any]:
    candidate = candidate_by_id.get(proposal.candidate_id or "")
    evidence = edge_evidence(proposal, candidate, kg)
    rel_id = relationship_id(proposal.relation, proposal.source_id, proposal.target_id)
    return {
        "id": rel_id,
        "type": proposal.relation,
        "source": proposal.source_id,
        "target": proposal.target_id,
        "content": {
            "reason": proposal.reason,
            "evidence_mode": proposal.evidence_mode,
            "strength": proposal.strength,
            "semantic_linking_candidate_id": proposal.candidate_id,
            "rule_hits": proposal.rule_hits,
        },
        "properties": {},
        "quality": {
            "confidence": round(float(proposal.confidence), 4),
            "stage": "semantic_linking",
        },
        "evidence": evidence,
    }


def edge_evidence(proposal: EdgeProposal, candidate: CandidatePair | None, kg: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for support in proposal.support:
        for key in ["bridge_span", "source_anchor", "target_anchor"]:
            raw = getattr(support, key)
            if isinstance(raw, dict) and raw.get("start_line") is not None:
                items.append(normalize_support_span(raw, kg))
    if not items and candidate is not None:
        items.extend(first_evidence(candidate.source_node))
        items.extend(first_evidence(candidate.target_node))
    return unique_evidence(items)[:3]


def normalize_support_span(raw: dict[str, Any], kg: dict[str, Any]) -> dict[str, Any]:
    source_path = raw.get("source_path") or kg.get("source_path", "")
    start = int(raw.get("start_line") or 1)
    end = int(raw.get("end_line") or start)
    return {
        "source_path": source_path,
        "start_line": start,
        "end_line": max(start, end),
        "text": str(raw.get("text") or raw.get("excerpt") or ""),
        "chunk_id": raw.get("chunk_id"),
    }


def merge_patch(kg: dict[str, Any], accepted_edges: list[dict[str, Any]]) -> dict[str, Any]:
    merged = json.loads(json.dumps(kg, ensure_ascii=False))
    existing = existing_edges(merged)
    added = 0
    for edge in accepted_edges:
        key = (edge["type"], edge["source"], edge["target"])
        if key in existing:
            continue
        merged["relationships"].append(edge)
        existing.add(key)
        added += 1
    diagnostics = dict(merged.get("diagnostics", {}))
    diagnostics["semantic_linking_edges_added"] = added
    diagnostics["relationship_count"] = len(merged["relationships"])
    diagnostics["relationship_count_by_type"] = count_relationship_types(merged["relationships"])
    merged["diagnostics"] = diagnostics
    return merged


def dedupe_rejected_pairs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("relation"), item.get("source_id"), item.get("target_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def coverage_report(kg: dict[str, Any], accepted_edges: list[dict[str, Any]]) -> dict[str, Any]:
    rels = [*kg.get("relationships", []), *accepted_edges]
    nodes = kg.get("nodes", [])
    return {
        "formula_attachment_rate": attachment_rate(nodes, rels, "Formula", "has_formula", "target"),
        "property_attachment_rate": attachment_rate(nodes, rels, "Property", "has_property", "target"),
        "example_attachment_rate": attachment_rate(nodes, rels, "Example", "example_of", "source"),
        "figure_attachment_rate": attachment_rate(nodes, rels, "Figure", "illustrated_by", "target"),
        "proof_attachment_rate": attachment_rate(nodes, rels, "Proof", "proved_by", "target"),
        "prerequisite_attachment_rate": attachment_rate(nodes, rels, "Concept", "prerequisite_of", "source"),
    }


def attachment_rate(nodes: list[dict[str, Any]], rels: list[dict[str, Any]], node_type: str, rel_type: str, side: str) -> dict[str, Any]:
    ids = {node["id"] for node in nodes if node.get("type") == node_type}
    if side == "source":
        attached = {rel.get("source") for rel in rels if rel.get("type") == rel_type}
    else:
        attached = {rel.get("target") for rel in rels if rel.get("type") == rel_type}
    covered = len(ids & attached)
    total = len(ids)
    return {"covered": covered, "total": total, "rate": round(covered / total, 4) if total else 1.0}


def candidate_score(source: dict[str, Any], target: dict[str, Any], rel_type: str, line_window: int) -> tuple[float, list[str]]:
    hits: list[str] = []
    source_line = first_line(source)
    target_line = first_line(target)
    distance = abs(source_line - target_line) if source_line and target_line else 10**9
    score = 0.0
    if source_evidence_chunk(source) and source_evidence_chunk(source) == source_evidence_chunk(target):
        score += 0.35
        hits.append("same_chunk")
    if distance <= line_window:
        score += max(0.0, 0.35 * (1 - distance / max(1, line_window)))
        hits.append("near_source_lines")
    overlap = token_overlap(node_terms(source), node_terms(target))
    if overlap > 0:
        score += min(0.25, overlap * 0.12)
        hits.append("name_or_description_overlap")
    if rel_type == "has_formula" and latex_overlap(source, target):
        score += 0.25
        hits.append("latex_token_overlap")
    if rel_type == "has_property" and overlap >= 0.18:
        score += 0.18
        hits.append("property_overlap")
    if rel_type == "illustrated_by" and distance <= 40:
        score += 0.20
        hits.append("near_figure_caption")
    if rel_type == "illustrated_by":
        figure_hits = source_figure_reference_hits(source, target)
        if figure_hits:
            score += 0.75
            hits.extend(figure_hits)
    if rel_type == "example_of" and name_core(target.get("name", "")) in node_text(source):
        score += 0.25
        hits.append("example_mentions_target")
    if rel_type == "prerequisite_of":
        if source_line and target_line and source_line < target_line:
            score += 0.18
            hits.append("source_appears_earlier")
        if overlap >= 0.16:
            score += 0.18
            hits.append("prerequisite_overlap")
    if rel_type == "similar_to":
        if source.get("type") == target.get("type"):
            score += 0.18
            hits.append("same_type")
        if overlap >= 0.22:
            score += 0.28
            hits.append("similarity_overlap")
        if source.get("type") == "Formula" and target.get("type") == "Formula" and latex_overlap(source, target):
            score += 0.30
            hits.append("formula_similarity")
    if rel_type == "proved_by":
        if source_line and target_line and source_line < target_line:
            score += 0.20
            hits.append("theorem_before_proof")
        if overlap >= 0.14:
            score += 0.22
            hits.append("theorem_proof_overlap")
    return round(min(score, 1.0), 6), hits


def figure_labels(node: dict[str, Any]) -> list[str]:
    raw_items = [
        str((node.get("content") or {}).get("label") or ""),
        str(node.get("name") or ""),
    ]
    labels: list[str] = []
    for raw in raw_items:
        compact = re.sub(r"\s+", "", raw)
        if not compact:
            continue
        match = re.search(r"图\d+(?:-\d+)?", compact)
        if match:
            labels.append(match.group(0))
        else:
            labels.append(compact.split("：", 1)[0].split(":", 1)[0])
    return list(dict.fromkeys(label for label in labels if label))


def explicit_figure_reference_hits(text: str, figure: dict[str, Any]) -> list[str]:
    compact_text = re.sub(r"\s+", "", text)
    if not compact_text:
        return []
    hits: list[str] = []
    for label in figure_labels(figure):
        escaped = re.escape(label)
        patterns = [
            rf"（{escaped}）",
            rf"（{escaped}\)",
            rf"\({escaped}）",
            rf"\({escaped}\)",
            rf"如{escaped}",
            rf"见{escaped}",
            rf"参见{escaped}",
            rf"{escaped}所示",
            rf"所示{escaped}",
        ]
        if any(re.search(pattern, compact_text) for pattern in patterns):
            hits.append(f"explicit_figure_reference:{label}")
    return hits


def source_figure_reference_hits(source: dict[str, Any], figure: dict[str, Any], text: str | None = None) -> list[str]:
    labels = figure_labels(figure)
    if not labels:
        return []
    terms = source_anchor_terms(source)
    hits: list[str] = []
    for block in source_reference_blocks(source, text):
        segments = reference_segments(block)
        for index, segment in enumerate(segments):
            anchor_segment = normalize_anchor_text(segment)
            label_segment = re.sub(r"\s+", "", segment)
            if not anchor_segment or not label_segment:
                continue
            has_local_anchor = any(term in anchor_segment for term in terms)
            has_recent_anchor = recent_anchor_before(segments, index, terms)
            following_segments = segments[index + 1 : index + 2]
            has_following_anchor = any(any(term in normalize_anchor_text(following) for term in terms) for following in following_segments)
            has_following_naming_anchor = any(
                any(term in normalize_anchor_text(following) for term in terms) and is_naming_back_reference(following)
                for following in following_segments
            )
            if (
                not has_local_anchor
                and not has_recent_anchor
                and not (has_following_anchor and has_following_naming_anchor)
            ):
                continue
            for hit in explicit_figure_reference_hits(label_segment, figure):
                if hit not in hits:
                    hits.append(hit)
    return hits


def source_reference_blocks(source: dict[str, Any], text: str | None = None) -> list[str]:
    if text is not None:
        return [text]
    blocks: list[str] = []
    description = str(source.get("description") or "")
    if description:
        blocks.append(description)
    for ev in source.get("evidence", [])[:3]:
        ev_text = str(ev.get("text", ""))
        if ev_text:
            blocks.append(ev_text)
    return blocks


def source_reference_text(source: dict[str, Any]) -> str:
    parts = [str(source.get("name") or ""), str(source.get("description") or ""), *[str(item) for item in source.get("aliases", [])]]
    for ev in source.get("evidence", [])[:3]:
        parts.append(str(ev.get("text", "")))
    return "\n".join(part for part in parts if part)


def source_anchor_terms(source: dict[str, Any]) -> list[str]:
    raw_terms = [str(source.get("name") or ""), *[str(item) for item in source.get("aliases", [])]]
    name = str(source.get("name") or "")
    if "的" in name:
        prefix, suffix = name.split("的", 1)
        raw_terms.extend([prefix, suffix])
    terms: list[str] = []
    for term in raw_terms:
        compact = normalize_anchor_text(term)
        if len(compact) >= 2:
            terms.append(compact)
        if compact.endswith("性") and len(compact) >= 3:
            terms.append(compact[:-1])
    return list(dict.fromkeys(terms))


def reference_segments(text: str) -> list[str]:
    segments = re.split(r"[\n。；;]", text)
    return [segment.strip() for segment in segments if segment.strip()]


def dedupe_candidates(candidates: list[CandidatePair]) -> list[CandidatePair]:
    seen: set[str] = set()
    unique: list[CandidatePair] = []
    for candidate in candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        unique.append(candidate)
    return unique


def normalize_anchor_text(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\\\u4e00-\u9fff]+", "", text)


def contains_figure_reference(text: str) -> bool:
    return bool(re.search(r"图\s*\d+(?:\s*-\s*\d+)?", text))


def is_naming_back_reference(text: str) -> bool:
    return bool(re.search(r"(称为|称作|叫做|记作|简称)", text))


def recent_anchor_before(segments: list[str], index: int, terms: list[str], window: int = 4) -> bool:
    for previous in reversed(segments[max(0, index - window) : index]):
        normalized = normalize_anchor_text(previous)
        if any(term in normalized for term in terms):
            return True
        if is_topic_shift_segment(previous):
            return False
    return False


def is_topic_shift_segment(text: str) -> bool:
    compact = normalize_anchor_text(text)
    return bool(
        re.match(r"^[\u4e00-\u9fffA-Za-z0-9\\]+(的定义域|定义域为|函数定义为|称为)", compact)
    )


def compact_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "name": node.get("name"),
        "aliases": node.get("aliases", [])[:5],
        "description": node.get("description", "")[:600],
        "content": compact_content(node.get("content", {})),
        "evidence": node.get("evidence", [])[:2],
    }


def compact_content(content: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for key in ["latex", "approach", "steps", "label", "url", "source_latex"]:
        if key in content:
            keep[key] = content[key]
    return keep


def validate_kg(kg: dict[str, Any]) -> None:
    if kg.get("schema_version") != "maf-kg.agent.v5":
        raise ValueError("SemanticLinkingAgent requires maf-kg.agent.v5 input")
    node_ids = {node.get("id") for node in kg.get("nodes", [])}
    for rel in kg.get("relationships", []):
        if rel.get("source") not in node_ids or rel.get("target") not in node_ids:
            raise ValueError(f"Invalid relationship reference: {rel.get('id')}")


def resolve_kg_path(raw_path: str | Path | None) -> Path:
    if raw_path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    patterns = ["*_v5.json", "*_v4.json", "*.json"]
    for pattern in patterns:
        candidates = sorted(DEFAULT_KG_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
    raise FileNotFoundError(f"No JSON KG files found under {DEFAULT_KG_DIR}")


def default_patch_path(kg_path: Path) -> Path:
    return kg_path.with_name(kg_path.stem + "_semantic_linking_patch.json")


def default_enhanced_path(kg_path: Path) -> Path:
    return kg_path.with_name(kg_path.stem + "_enhanced.json")


def load_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def existing_edges(kg: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(rel.get("type"), rel.get("source"), rel.get("target")) for rel in kg.get("relationships", [])}


def candidate_id(task_type: str, rel_type: str, source_id: str, target_id: str) -> str:
    return "cand_" + stable_hash(task_type, rel_type, source_id, target_id)


def relationship_id(rel_type: str, source_id: str, target_id: str) -> str:
    return "rel_link_" + stable_hash(rel_type, source_id, target_id)


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def first_line(node: dict[str, Any]) -> int:
    evidence = node.get("evidence") or []
    if not evidence:
        return 0
    return int(evidence[0].get("start_line") or 0)


def first_evidence(node: dict[str, Any]) -> list[dict[str, Any]]:
    return list((node.get("evidence") or [])[:1])


def source_evidence_chunk(node: dict[str, Any]) -> str:
    evidence = node.get("evidence") or []
    if not evidence:
        return ""
    return str(evidence[0].get("chunk_id") or "")


def node_text(node: dict[str, Any]) -> str:
    content = node.get("content") or {}
    parts = [node.get("name", ""), node.get("description", ""), *node.get("aliases", [])]
    for value in content.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value[:8])
    for ev in node.get("evidence", [])[:2]:
        parts.append(str(ev.get("text", "")))
    return "\n".join(part for part in parts if part)


def node_terms(node: dict[str, Any]) -> set[str]:
    return set(tokenize(node_text(node)))


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z]+|\\[A-Za-z]+|[0-9]+|[\u4e00-\u9fff]{2,}", text)
    return [token.lower() for token in tokens if len(token.strip()) >= 2]


def token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def latex_overlap(source: dict[str, Any], target: dict[str, Any]) -> bool:
    target_latex = str((target.get("content") or {}).get("latex") or "")
    if not target_latex:
        return False
    target_tokens = set(tokenize(target_latex))
    return token_overlap(node_terms(source), target_tokens) >= 0.12


def name_core(name: str) -> str:
    text = re.sub(r"^(定义|公式|性质|例)\s*\d*[：:、.\s-]*", "", name)
    text = text.replace("的定义", "").replace("的公式", "").replace("的表达式", "")
    return re.sub(r"\s+", "", text)


def unique_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any, Any]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("source_path"), item.get("start_line"), item.get("end_line"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def batch_items(items: list[Any], batch_size: int) -> list[list[Any]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def count_by(items: list[Any], key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(key_fn(item))
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_relationship_types(rels: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rel in rels:
        rel_type = rel.get("type", "")
        counts[rel_type] = counts.get(rel_type, 0) + 1
    return dict(sorted(counts.items()))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Post-verification semantic linker for MaF-KG v5 JSON.")
    parser.add_argument("--json", default=None, help="Input KG v5 JSON. Defaults to the latest KG JSON, preferring *_v5.json.")
    parser.add_argument("--patch-output", default=None, help="Semantic linking patch path.")
    parser.add_argument("--enhanced-output", default=None, help="Enhanced KG output path when --merge is used.")
    parser.add_argument("--model", default=None, help="DeepSeek model. Defaults to DEEPSEEK_LINKING_MODEL/DS_LINKING_MODEL/deepseek-v4-flash.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--request-delay", type=float, default=3.0)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--line-window", type=int, default=80)
    parser.add_argument("--dry-run", action="store_true", help="Only build candidates and write an empty patch with diagnostics.")
    parser.add_argument("--merge", action="store_true", help="Write enhanced KG by appending accepted edges.")
    parser.add_argument("--no-save", action="store_true", help="Run in memory and do not write patch/enhanced files.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore cached semantic-linking batch responses.")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    agent = SemanticLinkingAgent(
        kg_path=args.json,
        patch_path=args.patch_output,
        enhanced_path=args.enhanced_output,
        model=args.model,
        temperature=args.temperature,
        max_retries=args.max_retries,
        request_timeout=args.request_timeout,
        max_tokens=args.max_tokens,
        request_delay=args.request_delay,
        no_proxy=args.no_proxy,
        batch_size=args.batch_size,
        top_k=args.top_k,
        line_window=args.line_window,
        dry_run=args.dry_run,
        merge=args.merge,
        save_patch=not args.no_save,
        resume=not args.no_resume,
        verbose=not args.quiet,
    )
    try:
        patch = agent.run()
    except (RuntimeError, ValidationError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"SemanticLinkingAgent failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(patch.diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
