#!/usr/bin/env python
"""Layer-2 KnowledgeUnit experiment (Chapter 1).

Deterministic MVP organizer + a small layer-1 merge demonstration.

Pipeline:
  1. layer-1 merge: collapse a curated list of granularity fragments
     (redirect relations, drop the fragment node).
  2. build the source outline: (section, subsection) -> line ranges, from the
     chapter-1 merged markdown files.
  3. assign each first-layer node to a (section, subsection) via its evidence
     source_path + start_line.
  4. one KnowledgeUnit per subsection: ``centers_on`` = definitional Concepts,
     ``contains`` = the remaining nodes in that subsection.
  5. ``depends_on`` between units, derived from derived_from edges crossing
     unit boundaries.
  6. semantic reference: each member records (id, type, name, description), so
     a later re-extraction can re-resolve by semantic identity instead of the
     content-hash id.

Usage:
  python scripts/build_knowledge_units.py <kg.json> --out <ku.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "maf-kg.knowledge-unit.v1"

# Source files that produced the Chapter-1 graph (merged, exercise-free).
SRC_FILES = [
    "E:/MaF-KG/data/raw/第一章前五节_合并_无习题.md",
    "E:/MaF-KG/data/raw/第一章后五节_合并_无习题.md",
]

# Curated layer-1 granularity fragments: canonical name -> fragment names to absorb.
FRAGMENT_MERGES: dict[str, list[str]] = {
    "函数的增量": ["增量"],
    "复合函数构成条件": ["多个函数顺次构成复合函数的条件"],
}


# ---------------------------------------------------------------------------
# layer-1 merge
# ---------------------------------------------------------------------------

def merge_fragments(data: dict) -> list[dict]:
    """Merge curated fragments in place; return a log of what was merged."""
    nodes = data["nodes"]
    relations = data.setdefault("relations", [])
    by_name: dict[str, str] = {n["name"]: n["id"] for n in nodes}
    merged: list[dict] = []

    for canon_name, frag_names in FRAGMENT_MERGES.items():
        canon_id = by_name.get(canon_name)
        if canon_id is None:
            merged.append({"canonical": canon_name, "fragments": frag_names,
                           "status": "canonical_missing"})
            continue
        for frag_name in frag_names:
            frag_id = by_name.get(frag_name)
            if frag_id is None:
                merged.append({"canonical": canon_name, "fragment": frag_name,
                               "status": "fragment_missing"})
                continue
            # redirect relations: fragment -> canonical
            for r in relations:
                if r["source_id"] == frag_id:
                    r["source_id"] = canon_id
                if r["target_id"] == frag_id:
                    r["target_id"] = canon_id
            # drop the fragment node
            nodes[:] = [n for n in nodes if n["id"] != frag_id]
            merged.append({"canonical": canon_name, "fragment": frag_name,
                           "status": "merged"})
    return merged


# ---------------------------------------------------------------------------
# source outline
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^#\s*第[一二三四五六七八九十]+节")
_SUB_RE = re.compile(r"^#\s*[一二三四五六七八九十]+、")


def build_outline() -> dict[str, list[dict]]:
    """Return {section_name: [{'name': sub, 'start': L, 'end': R}, ...]}."""
    outline: dict[str, list[dict]] = defaultdict(list)
    for path in SRC_FILES:
        lines = Path(path).read_text(encoding="utf-8").split("\n")
        cur_section: str | None = None
        cur_sub: dict | None = None
        for i, line in enumerate(lines, start=1):
            if _SECTION_RE.match(line):
                cur_section = re.sub(r"^#\s*", "", line.strip())
                cur_sub = None
            elif _SUB_RE.match(line) and cur_section is not None:
                if cur_sub is not None:
                    cur_sub["end"] = i - 1
                cur_sub = {"name": re.sub(r"^#\s*", "", line.strip()), "start": i, "end": len(lines)}
                outline[cur_section].append(cur_sub)
            if cur_sub is not None:
                cur_sub["end"] = i
        if cur_section is not None and cur_sub is not None:
            cur_sub["end"] = len(lines)
    return dict(outline)


def source_basename(node: dict) -> str:
    for ev in node.get("evidence", []):
        return Path(ev.get("source_path", "")).name
    return ""


def start_line_of(node: dict, basename: str) -> int | None:
    for ev in node.get("evidence", []):
        if Path(ev.get("source_path", "")).name == basename:
            return ev.get("start_line")
    return None


def heading_section(node: dict) -> str | None:
    for ev in node.get("evidence", []):
        hp = ev.get("heading_path", [])
        if len(hp) > 1:
            return hp[1]
    return None


# ---------------------------------------------------------------------------
# KnowledgeUnit building
# ---------------------------------------------------------------------------

def build_knowledge_units(data: dict) -> dict:
    nodes = data["nodes"]
    relations = data["relations"]
    nid = {n["id"]: n for n in nodes}
    outline = build_outline()

    # source_path (normalized basename) -> which merged file
    def section_key(ev_src: str) -> str:
        for path in SRC_FILES:
            if Path(path).name in ev_src:
                return path
        return ""

    # assign each node to (section_name, subsection_name)
    node_unit: dict[str, tuple[str, str]] = {}
    for n in nodes:
        base = source_basename(n)
        sl = start_line_of(n, base) if base else None
        section = heading_section(n)
        sub = None
        if base and sl is not None and section is not None:
            for s in outline.get(section, []):
                if s["start"] <= sl <= s["end"]:
                    sub = s["name"]
                    break
        if section is None:
            section = "(未定位)"
        if sub is None:
            # sections without 一、二、… subsections fall back to one unit per section
            sub = section
        node_unit[n["id"]] = (section, sub)

    # group nodes by unit
    units: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node_id, (sec, sub) in node_unit.items():
        units[(sec, sub)].append(node_id)

    # build KnowledgeUnits
    kus: list[dict] = []
    ku_id_by_key: dict[tuple[str, str], str] = {}
    counter = 0
    for (sec, sub), member_ids in sorted(units.items()):
        counter += 1
        ku_id = f"ku_{counter:03d}"
        ku_id_by_key[(sec, sub)] = ku_id
        centers = []
        contains = []
        for mid in member_ids:
            n = nid[mid]
            ref = {"id": mid, "type": n["type"], "name": n["name"],
                   "description": (n.get("description", "") or "")[:80]}
            # centers_on: definitional Concepts (evidence discourse_role == definition)
            roles = {ev.get("discourse_role") for ev in n.get("evidence", [])}
            if n["type"] == "Concept" and "definition" in roles:
                centers.append(ref)
            else:
                contains.append(ref)
        kus.append({
            "id": ku_id,
            "type": "KnowledgeUnit",
            "name": sub if sub != "(未定位小节)" else f"{sec} / {sub}",
            "aliases": [],
            "description": f"{sec} · {sub}",
            "evidence": [],
        })
        # attach centers_on/contains as top-level relations (v1 schema style)
        kus[-1]["_centers_on"] = centers
        kus[-1]["_contains"] = contains

    # depends_on: derived_from edges crossing units
    depends: list[dict] = []
    for r in relations:
        if r["type"] != "derived_from":
            continue
        a = node_unit.get(r["source_id"])
        b = node_unit.get(r["target_id"])
        if a and b and a != b:
            depends.append({"source_ku": ku_id_by_key.get(a, a),
                            "target_ku": ku_id_by_key.get(b, b)})

    return {
        "schema_version": SCHEMA,
        "source_graphs": ["第一章前五节_合并_semantic_v1_7_0", "第一章后五节_合并_semantic_v1_7_0"],
        "knowledge_units": kus,
        "relations_centers_on": [],
        "relations_contains": [],
        "relations_depends_on": depends,
        "diagnostics": {"unit_count": len(kus), "note": "centers_on/contains stored inline per unit as _centers_on/_contains"},
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Layer-2 KnowledgeUnit experiment (Chapter 1).")
    ap.add_argument("input", help="Chapter-1 KG JSON.")
    ap.add_argument("--out", required=True, help="Output KnowledgeUnit JSON.")
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(f"input: {len(data['nodes'])} nodes / {len(data['relations'])} relations")

    merge_log = merge_fragments(data)
    print(f"layer-1 merge: {len([m for m in merge_log if m['status']=='merged'])} merged")
    for m in merge_log:
        print(f"  [{m['status']}] {m.get('canonical','')} <- {m.get('fragment','') or m.get('fragments','')}")
    print(f"after merge: {len(data['nodes'])} nodes / {len(data['relations'])} relations")

    ku = build_knowledge_units(data)
    Path(args.out).write_text(json.dumps(ku, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nKnowledgeUnits: {ku['diagnostics']['unit_count']}")
    for k in ku["knowledge_units"]:
        c = len(k["_centers_on"])
        s = len(k["_contains"])
        print(f"  {k['id']}  centers={c} contains={s}  {k['name']}")
    print(f"depends_on edges: {len(ku['relations_depends_on'])}")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
