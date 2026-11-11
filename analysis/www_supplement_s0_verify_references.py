"""S0.5 independent acceptance check of the two new source references.

This is the lead's own verification, not a rerun of the builder's script. It
checks the things that would silently corrupt E1 if they were wrong:

  * line-number arithmetic (input_evidence == source_evidence - offset),
  * evidence ranges inside the exported input,
  * relation endpoints resolving to real nodes,
  * counts matching the actual lists,
  * the native signature rules,
  * whether the footnote-borne basic integral table reached input I's reference,
  * whether dangling footnote markers were recorded rather than invented.

Output: evaluation/www_evidence_supplement_2026-10-11/references/reference_acceptance.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SUPPLEMENT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
REFS = SUPPLEMENT / "references"
INPUTS = SUPPLEMENT / "e1_content" / "inputs"

ALLOWED_TYPES = {
    "involves", "uses", "establishes", "derived_from", "equivalent_to",
    "special_case_of", "defined_in_terms_of",
}

CASES = [
    {"input_id": "I", "file": "reference_input_I_v1.json", "offset": 4,
     "input_lines": 1122, "source_lo": 5, "source_hi": 1126},
    {"input_id": "O", "file": "reference_input_O_v1.json", "offset": 972,
     "input_lines": 837, "source_lo": 973, "source_hi": 1809},
]

SOURCE_FILES = {
    "I": "第四章_全章_无习题.md",
    "O": "第七章_全章_无习题.md",
}
SOURCE_REPO = Path("E:/MaF-KG/data/raw")

# The basic integral table appears as footnote bodies in chapter 4 and is core
# reusable content; it must be present in input I's reference.
INTEGRAL_TABLE_PROBES = [
    r"k\s*x\s*\+\s*C", r"x\^?\{?\\?mu", r"\\ln\s*\|?x", r"\\arctan", r"\\arcsin",
    r"\\sec", r"\\csc", r"a\^x",
]


MARKER = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")


def dangling_footnotes(source_file: Path, lo: int, hi: int) -> dict:
    """In-text footnote markers whose footnote body is absent from the slice."""
    lines = source_file.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    seg = lines[lo - 1 : hi]
    in_text, bodies = [], []
    for i, line in enumerate(seg, 1):
        if MARKER.search(line):
            if MARKER.match(line.strip()):
                bodies.append({"input_line": i, "text": line.strip()[:90]})
            else:
                in_text.append({"input_line": i, "text": line.strip()[:90]})
    return {
        "in_text_markers": len(in_text),
        "footnote_bodies": len(bodies),
        "dangling": in_text if not bodies else [],
        "examples": in_text[:5],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report: dict = {"schema": "maf_kg.www_supplement.reference_acceptance.v1",
                    "checker": "analysis/www_supplement_s0_verify_references.py",
                    "inputs": [], "problems": [], "warnings": []}

    for case in CASES:
        path = REFS / case["file"]
        entry: dict = {"input_id": case["input_id"], "file": case["file"]}
        src = SOURCE_REPO / SOURCE_FILES[case["input_id"]]
        dangling = dangling_footnotes(src, case["source_lo"], case["source_hi"])
        entry["footnote_scan"] = {k: v for k, v in dangling.items() if k != "examples"}
        if not path.exists():
            report["problems"].append(f"{case['file']} missing")
            report["inputs"].append(entry)
            continue

        ref = json.loads(path.read_text(encoding="utf-8"))
        nodes = ref.get("nodes", [])
        relations = ref.get("relations", [])
        cross = ref.get("cross_section_relations", [])
        entry["top_level_keys"] = sorted(ref.keys())
        entry["node_count"] = len(nodes)
        entry["relation_count"] = len(relations)
        entry["cross_section_relation_count"] = len(cross)
        entry["must_not_merge_count"] = len(ref.get("must_not_merge", []))
        entry["boundary_group_count"] = len(ref.get("boundary_groups", []))
        entry["uncertainty_count"] = len(ref.get("uncertainties", []))
        entry["approval_basis"] = ref.get("approval_basis")
        entry["status"] = ref.get("status")
        entry["by_type"] = dict(Counter(n.get("type") for n in nodes))
        entry["by_status"] = dict(Counter(n.get("status") for n in nodes))
        entry["by_tier"] = dict(Counter(n.get("tier") for n in nodes))
        entry["relations_by_type"] = dict(Counter(r.get("type") for r in relations))
        entry["by_input_section"] = dict(Counter(n.get("section") for n in nodes))

        off = case["offset"]
        ids = [n.get("id") for n in nodes]
        entry["duplicate_node_ids"] = sorted({i for i in ids if ids.count(i) > 1})
        rel_ids = [r.get("id") for r in relations]
        entry["duplicate_relation_ids"] = sorted({i for i in rel_ids if rel_ids.count(i) > 1})

        # --- line arithmetic ------------------------------------------------
        bad_range, bad_offset, missing_evidence = [], [], []
        for n in nodes + relations:
            se = n.get("source_evidence")
            ie = n.get("input_evidence")
            if not se or not ie:
                missing_evidence.append(n.get("id"))
                continue
            if not (case["source_lo"] <= se[0] <= se[1] <= case["source_hi"]):
                bad_range.append({"id": n.get("id"), "source_evidence": se})
            if not (1 <= ie[0] <= ie[1] <= case["input_lines"]):
                bad_range.append({"id": n.get("id"), "input_evidence": ie})
            if [se[0] - off, se[1] - off] != list(ie):
                bad_offset.append({"id": n.get("id"), "source_evidence": se,
                                   "input_evidence": ie, "expected": [se[0] - off, se[1] - off]})
        entry["evidence_range_violations"] = bad_range
        entry["offset_violations"] = bad_offset
        entry["missing_evidence"] = missing_evidence

        # --- endpoints ------------------------------------------------------
        idset = set(ids)
        bad_endpoints = []
        for r in relations + cross:
            for key in ("start", "end"):
                if r.get(key) not in idset:
                    bad_endpoints.append({"relation": r.get("id"), "key": key,
                                          "value": r.get(key)})
        entry["unresolved_endpoints"] = bad_endpoints

        # --- signature rules ------------------------------------------------
        by_id = {n["id"]: n for n in nodes}
        violations = []
        for r in relations + cross:
            if r.get("type") not in ALLOWED_TYPES:
                violations.append({"relation": r.get("id"), "rule": "unknown type",
                                   "value": r.get("type")})
                continue
            s, e = by_id.get(r.get("start")), by_id.get(r.get("end"))
            if not s or not e:
                continue
            if r["type"] == "special_case_of" and not (s.get("type") == e.get("type") == "Concept"):
                violations.append({"relation": r["id"], "rule": "special_case_of not Concept->Concept",
                                   "start": s.get("type"), "end": e.get("type")})
            if r["type"] == "uses" and s.get("type") != "Method":
                violations.append({"relation": r["id"], "rule": "uses does not start from Method",
                                   "start": s.get("type")})
        entry["signature_violations"] = violations
        declared = ref.get("signature_exceptions", []) or []
        declared_ids = {d.get("relation_id") for d in declared if isinstance(d, dict)}
        undeclared = [v for v in violations if v.get("relation") not in declared_ids]
        entry["declared_signature_exceptions"] = declared
        entry["declared_exception_count"] = len(declared_ids)
        entry["undeclared_signature_violations"] = undeclared

        # --- cross-section classification ----------------------------------
        # The frozen policy defines a cross-section relation as one whose two
        # endpoints belong to different source sections. The builder filed some
        # relations that only cross a subsection boundary, so recompute from the
        # node section labels rather than trusting the filing.
        section_of = {n["id"]: n.get("section") for n in nodes}
        true_cross, misfiled = [], []
        for r in cross:
            s, e = section_of.get(r.get("start")), section_of.get(r.get("end"))
            row = {"id": r.get("id"), "type": r.get("type"),
                   "start": r.get("start"), "end": r.get("end"),
                   "start_section": s, "end_section": e}
            if s and e and s != e:
                true_cross.append(row)
            else:
                misfiled.append(row)
        entry["cross_section_true"] = len(true_cross)
        entry["cross_section_rows"] = true_cross
        entry["cross_section_misfiled"] = misfiled
        if misfiled:
            report["warnings"].append(
                f"{case['input_id']}: {len(misfiled)} rows filed under cross_section_relations "
                f"but both endpoints share one section ({[m['id'] for m in misfiled]})")

        # --- counts ---------------------------------------------------------
        declared = ref.get("counts", {})
        entry["declared_counts"] = declared
        entry["count_mismatches"] = [
            k for k, v in declared.items()
            if isinstance(v, int) and k in ("items", "nodes") and v != len(nodes)
        ]

        # --- content probes -------------------------------------------------
        blob = json.dumps(ref, ensure_ascii=False)
        if case["input_id"] == "I":
            # The JSON writes plain mathematical text, not LaTeX escapes, so probe
            # for the content rather than for one notation.
            entry["integral_table_probes"] = {
                p: bool(re.search(p, blob)) for p in INTEGRAL_TABLE_PROBES}
            hits = sum(1 for v in entry["integral_table_probes"].values() if v)
            entry["integral_table_probe_hits"] = f"{hits}/{len(INTEGRAL_TABLE_PROBES)}"
            if hits < 3:
                report["problems"].append(
                    "input I reference shows almost no basic-integral-table content; "
                    "the footnote-borne table at 第四章 L241+ may have been dropped")

        # dangling footnote markers were scanned at the top of this case; here we
        # only check whether the reference recorded them.
        entry["uncertainties_mentioning_footnote"] = [
            u for u in ref.get("uncertainties", [])
            if "脚注" in json.dumps(u, ensure_ascii=False)]

        report["inputs"].append(entry)

    # --- aggregate problems ------------------------------------------------
    for entry in report["inputs"]:
        tag = entry["input_id"]
        for key, label in (("duplicate_node_ids", "duplicate node ids"),
                           ("duplicate_relation_ids", "duplicate relation ids"),
                           ("evidence_range_violations", "evidence out of range"),
                           ("offset_violations", "offset arithmetic broken"),
                           ("missing_evidence", "missing evidence pair"),
                           ("unresolved_endpoints", "unresolved endpoints"),
                           ("undeclared_signature_violations", "undeclared signature violation")):
            if entry.get(key):
                report["problems"].append(f"{tag}: {label} ({len(entry[key])})")
        if entry.get("count_mismatches"):
            report["problems"].append(f"{tag}: counts mismatch {entry['count_mismatches']}")
        if entry.get("declared_exception_count"):
            report["warnings"].append(
                f"{tag}: {entry['declared_exception_count']} relation(s) are out of the native "
                f"signature and are declared in signature_exceptions; they must be scored in a "
                f"separate column and never counted as schema-valid")
        if entry.get("footnote_scan", {}).get("dangling") and \
                not entry.get("uncertainties_mentioning_footnote"):
            report["warnings"].append(
                f"{tag}: has {len(entry['footnote_scan']['dangling'])} dangling footnote "
                f"marker(s) and no uncertainty records them")

    report["ok"] = not report["problems"]

    out = REFS / "reference_acceptance.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for e in report["inputs"]:
        print(f"=== input {e['input_id']} ===")
        print(f"  nodes={e.get('node_count')} relations={e.get('relation_count')} "
              f"cross={e.get('cross_section_relation_count')} "
              f"must_not_merge={e.get('must_not_merge_count')} groups={e.get('boundary_group_count')} "
              f"uncertainties={e.get('uncertainty_count')}")
        print(f"  by type      : {e.get('by_type')}")
        print(f"  by status    : {e.get('by_status')}  tier {e.get('by_tier')}")
        print(f"  relations    : {e.get('relations_by_type')}")
        print(f"  node sections: {e.get('by_input_section')}")
        print(f"  violations   : range={len(e.get('evidence_range_violations',[]))} "
              f"offset={len(e.get('offset_violations',[]))} "
              f"endpoints={len(e.get('unresolved_endpoints',[]))} "
              f"signature={len(e.get('signature_violations',[]))}")
        if e["input_id"] == "I":
            print(f"  integral table probes: {e.get('integral_table_probe_hits')}")
        print(f"  cross-section: filed={e.get('cross_section_relation_count')} "
              f"true={e.get('cross_section_true')} misfiled={len(e.get('cross_section_misfiled',[]))}")
        print(f"  signature: violations={len(e.get('signature_violations',[]))} "
              f"declared={e.get('declared_exception_count')} "
              f"undeclared={len(e.get('undeclared_signature_violations',[]))}")
        print(f"  footnotes: {e.get('footnote_scan')} "
              f"| uncertainties mentioning 脚注: {len(e.get('uncertainties_mentioning_footnote',[]))}")
    print(f"\nproblems: {len(report['problems'])}")
    for p in report["problems"]:
        print(f"  - {p}")
    print(f"warnings: {len(report['warnings'])}")
    for w in report["warnings"]:
        print(f"  - {w}")
    print(f"\nwrote {out.relative_to(WORKSPACE)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
