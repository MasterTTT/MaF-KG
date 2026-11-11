"""Audit the prospective draft and parser reachability without model requests."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from run_memory_ablation import COMMON, load_module

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "evaluation/prospective_2026-10-04/reference_draft_v1.json"
ENDPOINTS = {
    "involves": ({"Claim"}, {"Concept"}),
    "uses": ({"Method"}, {"Concept", "Claim", "Method"}),
    "derived_from": ({"Claim"}, {"Claim"}),
    "special_case_of": ({"Concept"}, {"Concept"}),
}


def validate(reference: dict) -> None:
    sections = {row["id"]: row for row in reference["sections"]}
    nodes = {row["id"]: row for row in reference["nodes"]}
    if len(sections) != len(reference["sections"]) or len(nodes) != len(reference["nodes"]):
        raise ValueError("Duplicate section or node ID")
    for node in nodes.values():
        section = sections[node["section"]]
        first, last = node["evidence"]
        if not section["start_line"] <= first <= last <= section["end_line"]:
            raise ValueError(f"Evidence out of section: {node['id']}")
        if node["type"] not in {"Concept", "Claim", "Method"}:
            raise ValueError("Invalid node type")
        if node["status"] not in {"M", "O"} or not node["checklist"]:
            raise ValueError("Invalid requirement/checklist")
    seen = set()
    for relation in reference["relations_provisional"]:
        if relation["id"] in seen:
            raise ValueError("Duplicate relation ID")
        seen.add(relation["id"])
        start, end = nodes[relation["start"]], nodes[relation["end"]]
        allowed_start, allowed_end = ENDPOINTS[relation["type"]]
        if start["type"] not in allowed_start or end["type"] not in allowed_end:
            raise ValueError(f"Invalid relation endpoints: {relation['id']}")
        section = sections[relation["section"]]
        if start["section"] != section["id"] or end["section"] != section["id"]:
            raise ValueError("Relation section mismatch")
        first, last = relation["evidence"]
        if not section["start_line"] <= first <= last <= section["end_line"]:
            raise ValueError("Relation evidence out of section")
    for section_id, partitions in reference["ku_partitions"].items():
        required = {n["id"] for n in nodes.values()
                    if n["section"] == section_id and n["status"] == "M"}
        for partition in partitions["allowed"]:
            assigned = []
            for unit in partition:
                assigned.extend(unit["core_members"] + unit["support_members"])
            if set(assigned) != required or len(assigned) != len(set(assigned)):
                raise ValueError(f"Partition must assign each required node once: {section_id}")


def reachability(first: int, last: int, source_lines: list[str], selected: set[int]) -> dict:
    meaningful = {n for n in range(first, last + 1) if source_lines[n - 1].strip()}
    present = meaningful & selected
    status = "full" if meaningful <= selected else "partial" if present else "unreachable"
    return {"status": status, "missing_nonempty_source_lines": sorted(meaningful - selected)}


def audit(reference_path: Path, output: Path) -> dict:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    validate(reference)
    output.mkdir(parents=True, exist_ok=False)
    module = load_module(ROOT / "agents/Extractor.py")
    report = {
        "status": "offline-audited-not-frozen",
        "network_requests": 0,
        "reference_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        "extractor_sha256": hashlib.sha256((ROOT / "agents/Extractor.py").read_bytes()).hexdigest(),
        "required_nodes": sum(n["status"] == "M" for n in reference["nodes"]),
        "optional_nodes": sum(n["status"] == "O" for n in reference["nodes"]),
        "finite_reference_relations": len(reference["relations_provisional"]),
        "sections": [],
        "limitation": "Span coverage is an input diagnostic, not model recall. Broad evidence may contain proof lines; checklist items require manual evidence review.",
    }
    for section in reference["sections"]:
        source = Path(section["source"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != section["source_sha256"]:
            raise ValueError(f"Source hash mismatch: {source}")
        full_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
        target = output / f"{section['id']}.md"
        target.write_text("".join(full_lines[section["start_line"] - 1:section["end_line"]]),
                          encoding="utf-8", newline="\n")
        extractor = module.Extractor(input_path=target, verbose=False)
        state = extractor._chunk_document(extractor._load_document(
            {**COMMON, "input_path": str(target), "include_exercises": False, "diagnostics": {}}))
        selected = set()
        for chunk in state["chunks"]:
            for line in chunk.text.splitlines():
                number, separator, body = line.partition(": ")
                if not separator or not number.isdecimal():
                    raise ValueError("Unexpected chunk line format")
                local = int(number)
                if state["raw_lines"][local - 1] != body:
                    raise ValueError("Parser altered source evidence")
                selected.add(local + section["start_line"] - 1)
        spans = [{"id": n["id"], "requirement": n["status"],
                  **reachability(*n["evidence"], full_lines, selected)}
                 for n in reference["nodes"] if n["section"] == section["id"]]
        row = {"id": section["id"], "chunk_count": len(state["chunks"]),
               "selected_source_lines": sorted(selected), "node_span_reachability": spans,
               "required_span_status": dict(Counter(n["status"] for n in spans
                                                      if n["requirement"] == "M"))}
        if section["id"] == "ch4_s3":
            row["selection_guidance"] = reachability(1008, 1011, full_lines, selected)
        report["sections"].append(row)
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    args = parser.parse_args()
    report = audit(args.reference, args.output)
    print(json.dumps({"required_nodes": report["required_nodes"],
                      "optional_nodes": report["optional_nodes"],
                      "relations": report["finite_reference_relations"],
                      "sections": [{"id": r["id"], "chunks": r["chunk_count"],
                                    "required_span_status": r["required_span_status"],
                                    "selection_guidance": r.get("selection_guidance")}
                                   for r in report["sections"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
