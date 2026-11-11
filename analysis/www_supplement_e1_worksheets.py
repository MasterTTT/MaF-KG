"""Build method-blinded review worksheets from E1 outputs.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 6.

The review is AI-assisted and source-based: a reviewer sees, for one output,
every reference item and relation together with (a) the source excerpt that
defines it and (b) whatever the output actually delivered near it. Method names
are replaced by blind labels; the mapping is written to a separate file.

Two hard rules from the contract are enforced here:

  * A source excerpt is supplied for the reviewer's orientation only. It is NOT
    the output. The reviewer must judge what the output itself says, so each row
    states the rule explicitly rather than relying on the reviewer to remember it.
  * The endpoint-carrier rule is v2: an endpoint appears if some native item
    denotes the SAME mathematical object. Similar content is not the same object.
    Each relation row therefore lists candidate native edges separately for
    same-direction, reverse-direction and one-endpoint-touching cases, instead of
    pre-deciding the binding.

Outputs, per run: e1_content/review/worksheet_<blind>.json
Plus: e1_content/review/blinding_map.json and a summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"

BLIND_LABELS = ["R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08",
                "R09", "R10", "R11", "R12", "R13", "R14", "R15", "R16"]


def norm(text: str) -> str:
    return re.sub(r"[\s\\${}_^]+", "", str(text)).lower()


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def graph_shape(payload) -> str:
    """The four arms emit three different shapes; detect without guessing."""
    if isinstance(payload, dict) and "nodes" in payload and "relations" in payload:
        return "maf_graph"
    if isinstance(payload, dict) and "entities" in payload and "relationships" in payload:
        return "entity_relation"
    if isinstance(payload, list):
        # EDC emits a NESTED list: [[triple_or_null, triple_or_null, ...]].
        # Unwrap so the inner elements are read as the triples.
        if payload and isinstance(payload[0], list) and not (
                len(payload[0]) == 3 and all(isinstance(x, str) for x in payload[0])):
            return "nested_triple_list"
        return "triple_list"
    return "unknown"


def native_items(payload) -> tuple[list, list]:
    """Return (entities, relations) in a shape-neutral way."""
    shape = graph_shape(payload)
    if shape == "maf_graph":
        expressions = {}
        for expression in payload.get("math_expressions", []):
            expressions.setdefault(expression.get("owner_id"), []).append({
                k: expression[k] for k in ("id", "owner_id", "role", "latex") if k in expression})
        ents = [{"id": n.get("id"), "name": n.get("name"),
                 "aliases": n.get("aliases", []), "type": n.get("type"),
                 "description": n.get("description", ""),
                 "logical_form": n.get("logical_form"),
                 "owned_expressions": expressions.get(n.get("id"), [])}
                for n in payload["nodes"]]
        rels = [{"id": r.get("id") or f"edge_{i}",
                 "source": r.get("source_id"), "target": r.get("target_id"),
                 "type": r.get("type"), "description": r.get("description", "")}
                for i, r in enumerate(payload["relations"])]
        return ents, rels
    if shape == "entity_relation":
        ents = []
        for i, e in enumerate(payload["entities"]):
            if isinstance(e, dict):
                ents.append({"id": e.get("id") or e.get("name") or f"e{i}",
                             "name": e.get("name") or e.get("entity") or str(e),
                             "aliases": e.get("aliases", []),
                             "type": e.get("type") or e.get("label"),
                             "description": e.get("description", ""),
                             "logical_form": e.get("logical_form"),
                             "owned_expressions": e.get("owned_expressions", []),
                             "properties": {k: v for k, v in e.get("properties", {}).items()
                                            if k not in {"embeddings", "embedding", "evidence"}}})
            else:
                ents.append({"id": f"e{i}", "name": str(e), "aliases": [], "type": None,
                             "description": ""})
        rels = []
        def endpoint(value):
            return (value.get("id") or value.get("name")) if isinstance(value, dict) else value

        for i, r in enumerate(payload["relationships"]):
            if isinstance(r, dict):
                # iText2KG names these startNode / endNode / name; other adapters
                # use source / target / type. Without the iText keys every edge
                # came out as an all-null placeholder.
                rels.append({"id": r.get("id") or f"edge_{i}",
                             "source": endpoint(r.get("startNode") or r.get("source")
                                                or r.get("head") or r.get("entity1")),
                             "target": endpoint(r.get("endNode") or r.get("target")
                                                or r.get("tail") or r.get("entity2")),
                             "type": r.get("name") or r.get("type") or r.get("relation"),
                             "description": r.get("description", ""),
                             "properties": {k: v for k, v in r.get("properties", {}).items()
                                            if k not in {"embeddings", "embedding", "evidence"}}})
        return ents, rels
    if shape == "nested_triple_list":
        flat = []
        for block in payload:
            if isinstance(block, list):
                flat.extend(x for x in block if x is not None)
            elif block is not None:
                flat.append(block)
        payload = flat
        shape = "triple_list"
    if shape == "triple_list":
        ents, rels = {}, []
        for i, t in enumerate(payload):
            if not isinstance(t, (list, tuple)) or len(t) < 3:
                continue
            s, p, o = t[0], t[1], t[2]
            for x in (s, o):
                if isinstance(x, str):
                    ents.setdefault(x, {"id": x, "name": x, "aliases": [], "type": None,
                                              "description": ""})
            rels.append({"id": f"edge_{i}",
                         "source": s if isinstance(s, str) else json.dumps(s, ensure_ascii=False),
                         "target": o if isinstance(o, str) else json.dumps(o, ensure_ascii=False),
                         "type": p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)})
        return list(ents.values()), rels
    return [], []


def candidates_for(name: str, aliases: list, ents: list, limit: int = 40) -> list:
    """Native items whose name or alias plausibly denotes the reference item."""
    keys = {norm(name)} | {norm(a) for a in (aliases or []) if a}
    keys = {k for k in keys if len(k) >= 2}
    hits = []
    for e in ents:
        ek = {norm(e["name"])} | {norm(a) for a in (e.get("aliases") or []) if a}
        if keys & ek:
            hits.append({"match": "exact_name_or_alias", "native_id": e["id"],
                         "native_name": e["name"], "native_type": e.get("type"),
                         "native_description": e.get("description") or "",
                         "logical_form": e.get("logical_form"),
                         "owned_expressions": e.get("owned_expressions", [])})
            continue
        if any(k in norm(e["name"]) or norm(e["name"]) in k for k in keys if len(k) >= 3):
            hits.append({"match": "name_contains", "native_id": e["id"],
                         "native_name": e["name"], "native_type": e.get("type"),
                         "native_description": e.get("description") or "",
                         "logical_form": e.get("logical_form"),
                         "owned_expressions": e.get("owned_expressions", [])})
    return hits[:limit]


def enrich(reference: dict, source_reference: Path | None) -> dict:  # noqa: D401
    """Merge meaning/conditions/conclusion/justification from the full source
    reference. The runner-facing reference carries only what the runner needs, so
    without this the reviewer sees empty semantics and must infer them."""
    if not source_reference or not source_reference.exists():
        return reference
    # Read every sibling source reference: the runner reference spans both inputs,
    # so enriching from only one drops the other's conditions entirely.
    paths = [source_reference]
    for sib in ("reference_input_O_v1.json", "reference_input_I_v2.json"):
        cand = source_reference.parent / sib
        if cand.exists() and cand != source_reference:
            paths.append(cand)
    src = {"nodes": [], "relations": [], "cross_section_relations": []}
    for pth in paths:
        d2 = read(pth)
        for k in src:
            src[k] = src[k] + d2.get(k, [])
    by_id = {}
    for r in src.get("nodes", []) + src.get("items", []):
        by_id[r.get("id")] = r
    for r in src.get("relations", []) + src.get("cross_section_relations", []):
        by_id.setdefault(r.get("id"), r)
    ref = dict(reference)
    for key in ("nodes", "relations", "cross_section_relations"):
        out = []
        for row in reference.get(key, []):
            s = by_id.get(row.get("pool_id") or row.get("id"))
            if s:
                merged = dict(row)
                for f in ("meaning", "conditions", "conclusion", "symbol_convention",
                          "justification", "tier", "type"):
                    if f not in merged or merged.get(f) in (None, "", []):
                        if s.get(f) not in (None, "", []):
                            merged[f] = s[f]
                out.append(merged)
            else:
                out.append(row)
        ref[key] = out
    return ref


def dedupe(reference: dict) -> dict:
    """A prefix reference carries one entry per (pool item, range) so the frozen
    reachability check works for every range. For review we want each item once."""
    def uniq(rows):
        seen, out = set(), []
        for r in rows:
            key = r.get("pool_id") or r.get("id")
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out
    ref = dict(reference)
    ref["nodes"] = uniq(reference.get("nodes", []))
    ref["relations"] = uniq(reference.get("relations", []))
    ref["cross_section_relations"] = uniq(reference.get("cross_section_relations", []))
    return ref


def build_worksheet(blind: str, run_dir: Path, reference: dict, source_lines: dict,
                    run_section: str | None = None) -> dict:
    reference = dedupe(reference)
    graph = read(run_dir / "graph.json")
    table = source_lines.get(run_section, {}) if run_section else {}
    ents, rels = native_items(graph)
    by_id = {e["id"]: e for e in ents}

    rows = []
    for n in reference["nodes"]:
        se = n.get("source_evidence") or n.get("evidence")
        in_scope = (n.get("section") == run_section) if run_section else True
        excerpt, excerpt_source = "", None
        if in_scope and se and table and se[0] in table and se[1] in table:
            excerpt_source = run_section
            excerpt = "\n".join(f"{i}: {table.get(i, '')}" for i in range(se[0], se[1] + 1))
        rows.append({
            "reference_id": n["id"],
            "kind": "content",
            "reference": {"type": n.get("type"), "name": n.get("name"),
                          "status": n.get("status"), "tier": n.get("tier"),
                          "meaning": n.get("meaning", ""),
                          "conditions": n.get("conditions"),
                          "conclusion": n.get("conclusion")},
            "in_scope": in_scope,
            "source_excerpt": excerpt,
            "source_excerpt_file": excerpt_source,
            "out_of_scope_note": (None if in_scope else
                                  "this item belongs to the other input, which this run never "
                                  "saw; it is not a miss and must be excluded from the denominator"),
            "source_evidence": se,
            "native_candidates": candidates_for(n.get("name", ""), n.get("aliases", []), ents),
            "all_native_entities": [
                {"native_id": e["id"], "native_name": e["name"], "native_type": e.get("type"),
                 "native_description": e.get("description") or "",
                 "logical_form": e.get("logical_form"),
                 "owned_expressions": e.get("owned_expressions", []),
                 "properties": e.get("properties", {})} for e in ents],
            "all_native_edges": rels,
            "questions": {
                "coverage": "full | partial | name_only | missing | not_applicable",
                "conditions_complete": "true only if the OUTPUT ITSELF states the material "
                                       "restrictions; a condition present only in the source "
                                       "excerpt does not count",
                "source_support": "yes | partial | no | undecided",
                "source_support_gloss": "is what the OUTPUT delivered backed by the source excerpt; use "
                                       "undecided when nothing was delivered or the excerpt is unavailable",
            },
            "rule": "judge what the output delivered, not what the source says",
        })

    for r in reference.get("relations", []) + reference.get("cross_section_relations", []):
        s_name = next((n.get("name") for n in reference["nodes"] if n["id"] == r.get("start")), "")
        e_name = next((n.get("name") for n in reference["nodes"] if n["id"] == r.get("end")), "")
        same, rev, one = [], [], []
        for nr in rels:
            s_hit = any(c["native_id"] == nr["source"] for c in candidates_for(s_name, [], ents))
            e_hit = any(c["native_id"] == nr["target"] for c in candidates_for(e_name, [], ents))
            row = {"native_type": nr["type"],
                   "native_source": by_id.get(nr["source"], {}).get("name", nr["source"]),
                   "native_target": by_id.get(nr["target"], {}).get("name", nr["target"])}
            if s_hit and e_hit:
                same.append(row)
            elif any(c["native_id"] == nr["target"] for c in candidates_for(s_name, [], ents)) and \
                    any(c["native_id"] == nr["source"] for c in candidates_for(e_name, [], ents)):
                rev.append(row)
            elif s_hit or e_hit:
                one.append(row)
        r_in_scope = True
        if run_section:
            ends = [n2.get("section") for n2 in reference["nodes"]
                    if n2["id"] in (r.get("start"), r.get("end"))]
            r_in_scope = bool(ends) and all(e == run_section for e in ends)
        rows.append({
            "reference_id": r["id"],
            "kind": "relation",
            "in_scope": r_in_scope,
            "reference": {"type": r.get("type"), "start": r.get("start"), "start_name": s_name,
                          "end": r.get("end"), "end_name": e_name,
                          "justification": r.get("justification", "")},
            "native_same_direction": same,
            "native_reverse_direction": rev,
            "native_touching_one_endpoint": one,
            "all_native_edges": [
                {"native_type": r["type"],
                 "native_source_id": r["source"],
                 "native_source": by_id.get(r["source"], {}).get("name", r["source"]),
                 "native_source_type": by_id.get(r["source"], {}).get("type"),
                 "native_target_id": r["target"],
                 "native_target": by_id.get(r["target"], {}).get("name", r["target"]),
                 "native_target_type": by_id.get(r["target"], {}).get("type")} for r in rels],
            "matching_note": "native_same_direction / reverse / one_endpoint are produced by a "
                             "name-matching heuristic and UNDER-REPORT; all_native_edges is the "
                             "complete list and is the one to judge from",
            "questions": {"semantic": "full | partial | mentioned_only | not_covered | uncertain",
                          "strict": "conforms | direction_mismatch | type_mismatch | "
                                    "endpoint_identity_mismatch | not_comparable | uncertain",
                          "native_signature": "valid | out_of_signature"},
            "rule": "endpoint-carrier v2: an endpoint appears only if a native item denotes the "
                    "SAME mathematical object; similar content is not the same object",
        })

    return {
        "worksheet_version": "e1.v2-complete-carriers",
        "blind_label": blind,
        "run_section": run_section,
        "scope_note": "this worksheet covers ONE input section. Score it against that section's "
                      "reference items only; items belonging to the other input were never visible "
                      "to this run and are NOT misses.",
        "run_id_sha256": (hashlib.sha256((run_dir / "graph.json").read_bytes()).hexdigest()[:16]
                          if (run_dir / "graph.json").exists() else None),
        "graph_shape": graph_shape(graph),
        "native_entity_count": len(ents),
        "native_relation_count": len(rels),
        "reference_item_count": len(reference["nodes"]),
        "reference_relation_count": len(reference.get("relations", []))
                                   + len(reference.get("cross_section_relations", [])),
        "rows": rows,
        "blinding_note": "method name is withheld; the native representation may still reveal "
                         "it, and that residual is reported rather than hidden",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True, help="an e1 run directory")
    ap.add_argument("--reference", type=Path,
                    default=S / "references/runner_reference_v1.json")
    ap.add_argument("--source-reference", type=Path, default=None,
                    help="full source reference supplying meaning/conditions/justification")
    ap.add_argument("--out", type=Path, default=S / "e1_content/review")
    args = ap.parse_args()

    reference = read(args.reference)
    source_ref = args.source_reference
    if source_ref is None:
        # E1 uses the full reference already; a prefix reference needs the source one.
        candidate = S / "references/reference_input_I_v2.json"
        source_ref = candidate if candidate.exists() else None
    reference = enrich(reference, source_ref)
    inputs = read(S / "e1_content/inputs/input_map.json")
    # source lines keyed by the reference section label used in the runner reference
    # Key the source text by FILE and absolute line number. The earlier version
    # keyed it by input id, but a prefix reference labels its nodes with the
    # runner-facing range id (input_I1/input_I2), so every lookup missed and the
    # excerpt came out empty on all 126 rows.
    # Key source text by RUNNER SECTION ID so a run only ever gets excerpts from
    # the input it saw. Keying by file and matching on line numbers was wrong:
    # chapter 7 lines 977-1053 also fall inside chapter 4's range 5-1126, so the
    # first file to match won and seven input_O items got chapter 4 excerpts.
    source_lines = {}
    for sec in reference.get("sections", []):
        f = Path(sec["source"])
        text = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = text.split("\n")
        lo, hi = sec["start_line"], sec["end_line"]
        source_lines[sec["id"]] = {n: lines[n - 1] for n in range(lo, min(hi, len(lines)) + 1)}

    manifest = read(args.run / "manifest.json")
    args.out.mkdir(parents=True, exist_ok=True)
    mapping, sheets = {}, []
    for idx, job in enumerate([j for j in manifest["jobs"] if j["status"] == "completed"]):
        blind = BLIND_LABELS[idx % len(BLIND_LABELS)]
        d = Path(job["directory"])
        if not (d / "graph.json").exists():
            continue
        sheet = build_worksheet(blind, d, reference, source_lines, job.get("section"))
        (args.out / f"worksheet_{blind}.json").write_text(
            json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        mapping[blind] = {"method": job["method"], "section": job["section"],
                          "repeat": job["repeat"], "directory": str(d)}
        sheets.append({"blind": blind, "method": job["method"], "section": job["section"],
                       "repeat": job["repeat"], "shape": sheet["graph_shape"],
                       "native_entities": sheet["native_entity_count"],
                       "native_relations": sheet["native_relation_count"],
                       "rows": len(sheet["rows"])})

    # The blinding map and the summary both carry the method name. They must NEVER
    # be written into a directory a reviewer is handed: the first E1p review found
    # exactly that leak, because an earlier version of this script put them in
    # review/. They now go beside the run, and the review directory keeps
    # worksheets only.
    internal = args.out.parent if args.out.name == "review" else args.out
    (internal / "blinding_map_internal.json").write_text(
        json.dumps({"schema": "maf_kg.www_supplement.blinding_map.v1",
                    "note": "hidden mapping; must not be given to a reviewer",
                    "mapping": mapping}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (internal / "worksheet_summary_internal.json").write_text(
        json.dumps({"schema": "maf_kg.www_supplement.worksheet_summary.v1",
                    "run": str(args.run), "completed_outputs": len(sheets),
                    "note": "internal bookkeeping; carries method names and must not be "
                            "placed in a review directory",
                    "sheets": sheets}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    # A reviewer must be able to hand a directory to another reviewer safely.
    leaked = [p.name for p in args.out.glob("*.json")
              if p.name not in {"blinding_map_internal.json"} and "worksheet_" not in p.name]
    if leaked:
        print(f"WARNING: review dir contains non-worksheet files: {leaked}")

    print(f"completed outputs : {len(sheets)}")
    for s in sheets:
        print(f"  {s['blind']}  {s['section']:9s} r{s['repeat']}  {s['shape']:16s} "
              f"entities={s['native_entities']:4d} relations={s['native_relations']:4d} rows={s['rows']}")
    print(f"wrote {args.out.relative_to(WORKSPACE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
