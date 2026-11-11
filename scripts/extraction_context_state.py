"""Organize extraction context from source metadata and committed graph facts."""
from __future__ import annotations

import copy
import hashlib
import json
import re


SCHEMA = "maf-kg.extraction-context.v1.1"
LAYOUT_SCHEMA = "maf-kg.extraction-context.layout.v1"
RECENT_CHUNKS = 2
UPDATE_BYTES = 4000
NODE_FIELDS = ("id", "type", "name", "aliases", "description", "claim_kind", "logical_form")
INPUT_FIELDS = {
    "chunk.evidence_line_numbers": "current_evidence.evidence_line_numbers",
    "chunk.defined_term_hints": "current_evidence.defined_term_hints",
    "chunk.source_latex_candidates": "current_evidence.source_latex_candidates",
    "chunk.teaching_blocks": "reading_context.teaching_blocks",
    "chunk.text": "current_evidence.text",
    "context_before_text": "reading_context.context_before_text",
    "context_after_text": "reading_context.context_after_text",
    "node_memory": "graph_state.nodes",
    "relation_memory": "graph_state.relations",
    "source_latex_candidates": "current_evidence.source_latex_candidates",
    "defined_term_hints": "current_evidence.defined_term_hints",
}
CONTEXT_CONTRACT = """Input context contract
- reading_context contains parser navigation and neighboring text, not evidence.
- current_evidence is the only source region for this request's extraction.
- graph_state is a relevant, possibly incomplete view of the committed graph.
  Its descriptions are not cropped. Recent updates describe successful graph
  mutations, not a claim that all mathematical content has been extracted.
  Missing items in this view are not proof of absence from the global graph.
- task lists permitted operations, not expected mathematical nodes or edges.
- Reusing an existing identity does not finish the passage: also check whether
  the current evidence supports updated semantics or additional relations.
- No reading note, generated mathematical summary, or new output field is used.

"""


def compact_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def data_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def structured_system_prompt(original: str) -> str:
    pattern = re.compile("|".join(re.escape(key) for key in sorted(INPUT_FIELDS, key=len, reverse=True)))
    return CONTEXT_CONTRACT + pattern.sub(lambda match: INPUT_FIELDS[match.group()], original)


def layout_system_prompt(original: str) -> str:
    contract = """Input context contract
- reading_context contains parser navigation and neighboring text, not evidence.
- current_evidence is the only source region for this request's extraction.
- graph_state contains the original selected node summaries and relations.
  It is a partial relevant view, not complete source evidence or full history.
  Summaries can omit conditions; absence here does not prove global absence.
- task states permitted operations, not expected mathematical nodes or edges.
- Reuse does not finish relation review of the current evidence.
- No reading note, recent update history, or new output field is used.

"""
    pattern = re.compile("|".join(re.escape(key) for key in sorted(INPUT_FIELDS, key=len, reverse=True)))
    return contract + pattern.sub(lambda match: INPUT_FIELDS[match.group()], original)


def graph_snapshot(nodes: dict, relations: dict) -> dict:
    projected = {}
    for node_id, node in nodes.items():
        row = node.model_dump(mode="json", exclude_none=True)
        if row["id"] != node_id:
            raise ValueError("Node store key differs from node identity")
        projected[node_id] = {key: copy.deepcopy(row[key]) for key in NODE_FIELDS if key in row}
        projected[node_id]["evidence_sha256"] = data_hash(row["evidence"])
    edges = []
    for relation in relations.values():
        row = relation.model_dump(mode="json", exclude_none=True)
        if row["source_id"] not in nodes or row["target_id"] not in nodes:
            raise ValueError("Graph snapshot has an unknown relation endpoint")
        edges.append((row["source_id"], row["relation_type"], row["target_id"]))
    if len(set(edges)) != len(edges):
        raise ValueError("Graph snapshot has duplicate relation identities")
    return {"nodes": projected, "relations": [list(edge) for edge in sorted(edges)]}


def commit_events(chunk_id: str, before: dict, after: dict) -> list[dict]:
    events = []
    for node_id, node in sorted(after["nodes"].items()):
        old = before["nodes"].get(node_id)
        if old is None:
            events.append({"chunk_id": chunk_id, "kind": "node_created", "node_id": node_id})
            continue
        changed = [key for key in NODE_FIELDS if old.get(key) != node.get(key)]
        if changed:
            events.append({"chunk_id": chunk_id, "kind": "node_semantics_updated",
                           "node_id": node_id, "changed_fields": changed})
        if old["evidence_sha256"] != node["evidence_sha256"]:
            events.append({"chunk_id": chunk_id, "kind": "node_evidence_updated", "node_id": node_id})
    if set(before["nodes"]) - set(after["nodes"]):
        raise ValueError("Unexpected node deletion in sequential extractor")
    previous = {tuple(edge) for edge in before["relations"]}
    current = {tuple(edge) for edge in after["relations"]}
    for kind, edges in (("relation_added", current - previous), ("relation_removed", previous - current)):
        for source, relation_type, target in sorted(edges):
            events.append({"chunk_id": chunk_id, "kind": kind, "source_id": source,
                           "relation_type": relation_type, "target_id": target})
    return events


class ContextState:
    def __init__(self, chunks: list[dict]):
        self.chunks = copy.deepcopy(chunks)
        self.by_id = {chunk["id"]: chunk for chunk in self.chunks}
        if not chunks or len(self.by_id) != len(chunks):
            raise ValueError("Context state requires a nonempty unique chunk plan")
        self.completed: list[str] = []
        self.commits: list[dict] = []
        self.graph = {"nodes": {}, "relations": []}

    def observe(self, snapshot: dict) -> None:
        if snapshot != self.graph:
            raise ValueError("Observed graph differs from successfully committed state")

    def commit(self, chunk_id: str, before: dict, after: dict) -> dict:
        if len(self.completed) >= len(self.chunks) or self.chunks[len(self.completed)]["id"] != chunk_id:
            raise ValueError("Graph commit is out of frozen chunk order")
        self.observe(before)
        record = {"chunk_id": chunk_id, "before": copy.deepcopy(before), "after": copy.deepcopy(after),
                  "events": commit_events(chunk_id, before, after)}
        self.commits.append(record)
        self.completed.append(chunk_id)
        self.graph = copy.deepcopy(after)
        return copy.deepcopy(record)

    def render(self, legacy: dict, *, preview: bool = False) -> dict:
        chunk = legacy["chunk"]
        chunk_id = chunk["chunk_id"]
        if chunk_id not in self.by_id:
            raise ValueError("Request chunk not present in frozen plan")
        if not preview and (len(self.completed) >= len(self.chunks)
                            or self.chunks[len(self.completed)]["id"] != chunk_id):
            raise ValueError("Request does not follow successful graph commits")
        planned = self.by_id[chunk_id]
        for key in ("text", "start_line", "end_line", "heading_path", "teaching_blocks",
                    "teaching_block_ids", "context_before_text", "context_after_text"):
            if chunk[key] != planned[key]:
                raise ValueError(f"Frozen source context changed: {key}")
        memory = []
        ids = set()
        for item in legacy["node_memory"]:
            row = copy.deepcopy(item)
            node_id = row["id"]
            if node_id in ids or node_id not in self.graph["nodes"]:
                raise ValueError("Memory identity is duplicate or not yet committed")
            description = row.pop("short_description")
            if description != self.graph["nodes"][node_id]["description"]:
                raise ValueError("Structured memory description is cropped or stale")
            row["description"] = description
            if any(row.get(key) != self.graph["nodes"][node_id].get(key) for key in NODE_FIELDS):
                raise ValueError("Structured memory semantics differ from committed node")
            memory.append(row)
            ids.add(node_id)
        relation_ids = set()
        for relation in legacy["relation_memory"]:
            identity = [relation["source_id"], relation["relation_type"], relation["target_id"]]
            if identity not in self.graph["relations"] or not {identity[0], identity[2]} <= ids:
                raise ValueError("Memory relation is stale or has an invisible endpoint")
            if tuple(identity) in relation_ids:
                raise ValueError("Duplicate memory relation identity")
            relation_ids.add(tuple(identity))
        relevant = [event for commit in self.commits[-RECENT_CHUNKS:] for event in commit["events"]
                    if (event.get("node_id") in ids or
                        (event.get("source_id") in ids and event.get("target_id") in ids))]
        updates = []
        for event in reversed(relevant):
            if compact_bytes([event, *updates]) <= UPDATE_BYTES:
                updates.insert(0, copy.deepcopy(event))
        return {
            "schema_version": SCHEMA,
            "reading_context": {
                "source_path": legacy["source_path"], "chunk_id": chunk_id,
                "chunk_index": next(i for i, c in enumerate(self.chunks, 1) if c["id"] == chunk_id),
                "chunk_count": len(self.chunks), "heading_path": copy.deepcopy(chunk["heading_path"]),
                "teaching_block_ids": copy.deepcopy(chunk["teaching_block_ids"]),
                "teaching_blocks": copy.deepcopy(chunk["teaching_blocks"]),
                "context_before_text": chunk["context_before_text"],
                "context_after_text": chunk["context_after_text"],
                "roles_are_navigation_not_mathematical_evidence": True,
            },
            "current_evidence": {key: copy.deepcopy(chunk[key]) for key in
                                 ("start_line", "end_line", "text", "evidence_line_numbers")} | {
                "defined_term_hints": copy.deepcopy(legacy["defined_term_hints"]),
                "source_latex_candidates": copy.deepcopy(legacy["source_latex_candidates"]),
            },
            "graph_state": {
                "scope": "partial_relevant_view", "nodes": memory,
                "relations": copy.deepcopy(legacy["relation_memory"]),
                "completed_chunk_count": len(self.completed),
                "last_completed_chunk_id": self.completed[-1] if self.completed else None,
                "processed_does_not_mean_complete_extraction": True,
                "recent_updates": updates,
                "recent_updates_scope": {"previous_chunks": RECENT_CHUNKS, "visible_identities_only": True,
                                         "byte_budget": UPDATE_BYTES,
                                         "omitted_relevant_events": len(relevant) - len(updates)},
            },
            "task": {
                "layer": "first_layer_mathematical_semantics",
                "operations": ["create_source_supported_identity", "reuse_supplied_identity",
                               "update_source_supported_semantics", "add_source_supported_relations"],
                "reuse_does_not_finish_relation_review": True,
                "unshown_identity_or_relation_is_not_proof_of_global_absence": True,
                "evidence_region": "current_evidence.text",
            },
        }


class LayoutContextState(ContextState):
    """Separate input roles without changing selected memory content."""

    def render(self, legacy: dict, *, preview: bool = False) -> dict:
        expanded = copy.deepcopy(legacy)
        for row in expanded["node_memory"]:
            stored = self.graph["nodes"].get(row["id"])
            if stored is None:
                raise ValueError("Memory identity is not yet committed")
            summary = row["short_description"]
            if not stored["description"].strip().startswith(summary):
                raise ValueError("Memory summary differs from committed description")
            row["short_description"] = stored["description"]
        rendered = super().render(expanded, preview=preview)
        rendered["schema_version"] = LAYOUT_SCHEMA
        rendered["graph_state"] = {
            "scope": "partial_relevant_view",
            "nodes": copy.deepcopy(legacy["node_memory"]),
            "relations": copy.deepcopy(legacy["relation_memory"]),
        }
        return rendered


CONTEXT_MODES = {
    "package": (ContextState, SCHEMA, structured_system_prompt),
    "layout_only": (LayoutContextState, LAYOUT_SCHEMA, layout_system_prompt),
}
