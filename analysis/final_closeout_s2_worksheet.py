#!/usr/bin/env python
"""S2 preparation: build the per-graph worksheet that lets every one of the 47 reference relations be
judged twice -- once for common mathematical semantics, once for strict structural conformance.

The worksheet resolves, for each of the eight final outputs:
  * the reference relation and the source sentence that justifies it
  * which native entity each reference endpoint was bound to (from the existing node review)
  * the native relations that actually connect those two native entities, verbatim, in both directions
  * the status and reason the earlier review recorded

Read-only with respect to the experiments.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review/.

Usage:
    python analysis/final_closeout_s2_worksheet.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review"
REFERENCE = MERGED / "reference/reference_merged_v1.json"
INPUT_MD = MERGED / "work/merged_input.md"

# The eight final outputs in the main comparison, with the review packet that covers each.
GRAPHS = [
    {"label": "P06", "method": "maf", "repeat": 1, "packet_dir": "review_merged"},
    {"label": "P05", "method": "maf", "repeat": 2, "packet_dir": "review_merged"},
    {"label": "P08", "method": "direct", "repeat": 1, "packet_dir": "review_merged"},
    {"label": "P10", "method": "direct", "repeat": 2, "packet_dir": "review_merged"},
    {"label": "Q04", "method": "edc", "repeat": 1, "packet_dir": "review_v3"},
    {"label": "Q02", "method": "edc", "repeat": 2, "packet_dir": "review_v3"},
    {"label": "V01", "method": "itext2kg", "repeat": 1, "packet_dir": "review_v4"},
    {"label": "V02", "method": "itext2kg", "repeat": 2, "packet_dir": "review_v4"},
]

MAX_SOURCE_CHARS = 700


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_sentence(evidence) -> str:
    if not evidence:
        return "(reference relation carries no line evidence)"
    start = int(evidence[0])
    end = int(evidence[1]) if len(evidence) > 1 else start
    lines = INPUT_MD.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start)
    end = min(len(lines), end)
    text = "\n".join(lines[start - 1 : end])
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS] + " ...[truncated]"
    return text


def native_summary(item: dict, entities: dict) -> dict:
    native = item.get("native", {})
    if isinstance(native, list):
        # EDC native shape: [subject, predicate, object] as free-text phrases.
        subject = native[0] if len(native) > 0 else None
        predicate = native[1] if len(native) > 1 else None
        obj = native[2] if len(native) > 2 else None
        return {
            "item_id": item["item_id"],
            "kind": "assertion",
            "shape": "edc_triple",
            "type": predicate,
            "source_label": subject,
            "target_label": obj,
        }
    if not isinstance(native, dict):
        return {"item_id": item["item_id"], "kind": item.get("kind"), "shape": type(native).__name__}
    if "source_id" in native or "target_id" in native:
        source_id = native.get("source_id")
        target_id = native.get("target_id")
        return {
            "item_id": item["item_id"],
            "kind": "assertion",
            "shape": "node_edge",
            "id": native.get("id"),
            "type": native.get("type"),
            "source_id": source_id,
            "target_id": target_id,
            "source_label": (entities.get(source_id) or {}).get("name"),
            "target_label": (entities.get(target_id) or {}).get("name"),
        }
    return {
        "item_id": item["item_id"],
        "kind": "assertion",
        "shape": "free_phrase_edge",
        "type": native.get("name"),
        "retrieval_key": native.get("retrieval_key"),
        "source_label": native.get("startNode"),
        "target_label": native.get("endNode"),
    }


def entity_summary(item: dict) -> dict:
    native = item.get("native", {})
    if not isinstance(native, dict):
        return {"item_id": item["item_id"], "kind": "entity", "shape": type(native).__name__}
    return {
        "item_id": item["item_id"],
        "kind": "entity",
        "id": native.get("id"),
        "type": native.get("type"),
        "label": native.get("label"),
        "name": native.get("name"),
        "description": str(native.get("description", ""))[:300],
    }


def build_graph_worksheet(spec: dict, reference: dict, relations: list[dict]) -> dict:
    packet = read_json(MERGED / spec["packet_dir"] / f"{spec['label']}.json")
    review = read_json(MERGED / spec["packet_dir"] / f"{spec['label']}_review.json")

    items = {item["item_id"]: item for item in packet["items"]}
    node_by_id = {node["id"]: node for node in reference["nodes"]}
    entities = {}
    for item in packet["items"]:
        native = item.get("native")
        if item.get("kind") == "entity" and isinstance(native, dict):
            key = native.get("id") or native.get("name") or item["item_id"]
            entities[key] = native

    # endpoint binding from the existing node review
    endpoint_binding: dict[str, dict] = {}
    for row in review.get("node_coverage", []):
        names = []
        for item_id in row.get("item_ids", []):
            item = items.get(item_id)
            if item and isinstance(item.get("native"), dict):
                names.append(item["native"].get("name"))
        endpoint_binding[row["reference_id"]] = {
            "status": row.get("status"),
            "item_ids": row.get("item_ids", []),
            "native_names": names,
        }

    native_relations = [
        native_summary(item, entities)
        for item in packet["items"]
        if item.get("kind") != "entity"
    ]

    def labels_of(binding: dict) -> set:
        out = {name for name in binding.get("native_names", []) if name}
        for item_id in binding.get("item_ids", []):
            item = items.get(item_id)
            if not item:
                continue
            native = item.get("native")
            if isinstance(native, dict):
                if native.get("name"):
                    out.add(native["name"])
                if native.get("startNode"):
                    out.add(native["startNode"])
                if native.get("endNode"):
                    out.add(native["endNode"])
            elif isinstance(native, list):
                # EDC triple: only the two endpoints count as labels, never the predicate.
                for index in (0, 2):
                    if index < len(native) and isinstance(native[index], str):
                        out.add(native[index])
        return out

    review_by_ref = {row["reference_id"]: row for row in review.get("relation_coverage", [])}
    review_by_ref.update({row["reference_id"]: row for row in review.get("cross_relation_coverage", [])})

    rows = []
    for relation in relations:
        start_node = node_by_id.get(relation["start"], {})
        end_node = node_by_id.get(relation["end"], {})
        bound_start = endpoint_binding.get(relation["start"], {})
        bound_end = endpoint_binding.get(relation["end"], {})
        start_labels = labels_of(bound_start)
        end_labels = labels_of(bound_end)

        def side_matches(rel: dict, labels: set) -> bool:
            return bool(labels) and (
                rel.get("source_label") in labels or rel.get("target_label") in labels
            )

        def direction(rel: dict) -> str:
            forward = rel.get("source_label") in start_labels and rel.get("target_label") in end_labels
            backward = rel.get("source_label") in end_labels and rel.get("target_label") in start_labels
            if forward and backward:
                return "both"
            if forward:
                return "same_direction"
            if backward:
                return "reverse_direction"
            return "one_endpoint_only"

        forward, backward, other = [], [], []
        for rel in native_relations:
            if side_matches(rel, start_labels) or side_matches(rel, end_labels):
                bucket = direction(rel)
                rel = {**rel, "direction_vs_reference": bucket}
                if bucket in {"same_direction", "both"}:
                    forward.append(rel)
                elif bucket == "reverse_direction":
                    backward.append(rel)
                else:
                    other.append(rel)

        existing = review_by_ref.get(relation["id"], {})
        rows.append(
            {
                "reference_id": relation["id"],
                "scope": relation.get("scope", relation.get("section")),
                "reference_relation": {
                    "type": relation["type"],
                    "start_node": relation["start"],
                    "start_name": start_node.get("name"),
                    "start_type": start_node.get("type"),
                    "start_meaning": str(start_node.get("meaning", ""))[:200],
                    "end_node": relation["end"],
                    "end_name": end_node.get("name"),
                    "end_type": end_node.get("type"),
                    "end_meaning": str(end_node.get("meaning", ""))[:200],
                    "justification": relation.get("justification"),
                },
                "source_text": source_sentence(relation.get("evidence")),
                "existing_review": {
                    "status": existing.get("status"),
                    "item_ids": existing.get("item_ids", []),
                    "reason": existing.get("reason"),
                },
                "endpoint_binding": {
                    "start": {"node": relation["start"], **bound_start},
                    "end": {"node": relation["end"], **bound_end},
                },
                "native_edges_same_direction": forward,
                "native_edges_reverse_direction": backward,
                "native_edges_touching_one_endpoint": other[:8],
                "all_native_relations_in_this_graph": len(native_relations),
            }
        )

    return {
        "label": spec["label"],
        "method": spec["method"],
        "repeat": spec["repeat"],
        "packet_dir": spec["packet_dir"],
        "prediction_sha256": packet.get("prediction_sha256"),
        "native_relation_count": len(native_relations),
        "native_relations_resolved": native_relations,
        "reference_relation_count": len(rows),
        "rows": rows,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reference = read_json(REFERENCE)
    relations = reference["relations"] + reference["cross_section_relations"]

    combined = {
        "schema": "dsh.final_closeout.s2_worksheet.v1",
        "reference": REFERENCE.relative_to(REPO).as_posix(),
        "reference_relation_count": len(relations),
        "graphs": [],
    }
    for spec in GRAPHS:
        worksheet = build_graph_worksheet(spec, reference, relations)
        combined["graphs"].append(worksheet)
        path = OUT_DIR / f"worksheet_{spec['label']}.json"
        path.write_text(json.dumps(worksheet, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {spec['label']} ({spec['method']} r{spec['repeat']}): "
              f"{worksheet['reference_relation_count']} reference relations, "
              f"{worksheet['native_relation_count']} native relations")

    (OUT_DIR / "worksheet_all.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {OUT_DIR.relative_to(REPO).as_posix()}/worksheet_*.json")
    print(f"total judgements required: {len(relations) * len(GRAPHS)} "
          f"({len(relations)} relations x {len(GRAPHS)} graphs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
