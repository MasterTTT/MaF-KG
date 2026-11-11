"""Read-only checks of DSH artifacts; write only a separate Codex audit file."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "evaluation/www_evidence_supplement_2026-10-11"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frac(n, d):
    return {"numerator": n, "denominator": d, "value": n / d if d else None}


def classification(gold, predicted, known):
    tp = len(gold & predicted)
    fp = len((predicted & known) - gold)
    fn = len(gold - predicted)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def joined_e1():
    items, relations, duplicates = {}, {}, []
    for name in "ABCD":
        p = S / f"e1_content/review/judgements_E1_batch{name}.json"
        batch = read(p)
        for field, keyfield, target in (("item_rows", "item_id", items),
                                        ("relation_rows", "relation_id", relations)):
            for row in batch[field]:
                key = (row["run"], row[keyfield])
                if key in target:
                    duplicates.append({"field": field, "key": key})
                target[key] = dict(row)
    corr = read(S / "e1_content/review/judgements_E1_correction.json")
    for row in corr["condition_rows"]:
        key = row["run"], row["item_id"]
        if key in items:
            items[key]["conditions_complete"] = row["conditions_complete"]
    for row in corr["relation_rows"]:
        key = row["run"], row["relation_id"]
        if key in relations:
            for field in ("semantic", "strict", "native_signature"):
                if field in row:
                    relations[key][field] = row[field]
    return items, relations, duplicates


def content_counts(ref, rows, mapping, required_only):
    output = {}
    for run, meta in mapping.items():
        pool = [n for n in ref["nodes"] if n["section"] == meta["section"]
                and (not required_only or n["status"] == "M")]
        claims = [n for n in pool if n["type"] == "Claim"]
        full = sum(rows.get((run, n["id"]), {}).get("coverage") == "full" for n in pool)
        recognized = sum(rows.get((run, n["id"]), {}).get("coverage")
                         in {"full", "partial", "name_only"} for n in pool)
        conditions = sum(rows.get((run, n["id"]), {}).get("conditions_complete") is True
                         and rows.get((run, n["id"]), {}).get("coverage")
                         in {"full", "partial", "name_only"} for n in claims)
        output[run] = {"method": meta["method"], "section": meta["section"],
                       "repeat": meta["repeat"], "full": frac(full, len(pool)),
                       "recognized": frac(recognized, len(pool)),
                       "conditions": frac(conditions, len(claims))}
    pooled = {}
    for method in sorted({v["method"] for v in output.values()}):
        subset = [v for v in output.values() if v["method"] == method]
        pooled[method] = {field: frac(sum(v[field]["numerator"] for v in subset),
                                    sum(v[field]["denominator"] for v in subset))
                          for field in ("full", "recognized", "conditions")}
    return {"per_run": output, "per_method": pooled}


def run_audit():
    result = {"schema": "codex.www_supplement_acceptance.v1", "date": "2026-10-11",
              "scope": "Independent arithmetic and artifact audit, no semantic re-adjudication or API calls"}
    freeze = read(S / "freeze/freeze_record.json")
    bindings = []
    for path, meta in freeze["frozen_artifacts"].items():
        p = ROOT / path
        bindings.append({"path": path, "exists": p.is_file(),
                         "hash_matches": p.is_file() and digest(p).lower() == meta["sha256"].lower()})
    result["freeze_checks"] = bindings
    index = read(S / "reports/evidence_index.json")
    result["evidence_index"] = {"rows": len(index["items"]),
        "rows_with_sha256": sum("sha256" in x for x in index["items"]),
        "missing": [x["path"] for x in index["items"] if not (ROOT / x["path"]).is_file()]}

    ref = read(S / "references/runner_reference_v1.json")
    mapping = read(S / "e1_content/blinding_map_internal.json")["mapping"]
    items, relations, duplicates = joined_e1()
    result["e1_reference"] = {"statuses": dict(Counter(n["status"] for n in ref["nodes"])),
        "optional": [{k: n[k] for k in ("id", "name", "type", "section")} for n in ref["nodes"]
                     if n["status"] != "M"], "duplicate_review_keys": duplicates}
    result["e1_all_items"] = content_counts(ref, items, mapping, False)
    result["e1_required_only"] = content_counts(ref, items, mapping, True)
    document_scores = {}
    for method in ("maf", "direct", "edc", "itext2kg"):
        document_scores[method] = {}
        for section in ("input_I", "input_O"):
            group = [v for v in result["e1_required_only"]["per_run"].values()
                     if v["method"] == method and v["section"] == section]
            document_scores[method][section] = {
                field: frac(sum(v[field]["numerator"] for v in group),
                            sum(v[field]["denominator"] for v in group))
                for field in ("full", "conditions")}
    result["e1_per_document"] = document_scores

    e1_manifest = read(S / "e1_content/run_a2/manifest.json")
    result["e1_manifest_graph_matches"] = [{"method": j["method"],
        "section": j["section"], "repeat": j["repeat"], "status": j["status"],
        "hash_matches": digest(Path(j["directory"]) / "graph.json") == j["graph_sha256"]}
        for j in e1_manifest["jobs"]]

    graph_checks, omitted = [], []
    for run, meta in mapping.items():
        p = Path(meta["directory"]) / "graph.json"
        g = read(p)
        graph_checks.append({"run": run, "path": str(p), "sha256": digest(p)})
        if isinstance(g, dict) and "nodes" in g:
            omitted.append({"run": run, "method": meta["method"],
                "expressions": len(g.get("math_expressions", [])),
                "logical_forms": sum(bool(n.get("logical_form")) for n in g["nodes"]),
                "descriptions_over_600_chars": sum(len(n.get("description", "")) > 600 for n in g["nodes"])})
    result["graphs"] = graph_checks
    result["worksheet_missing_fields"] = omitted

    no = read(S / "e3_memory/review/judgements_E3_rev3.json")
    no_rows = {(r["run"], r["item_id"]): r for r in no["item_rows"]}
    no_map = read(S / "e3_memory/blinding_map_internal.json")["mapping"]
    result["e3_required_only"] = content_counts(ref, no_rows, no_map, True)
    missing_conditions = []
    for run, meta in no_map.items():
        sheet = read(S / f"e3_memory/review/worksheet_{run}.json")
        for node in ref["nodes"]:
            if node["section"] != meta["section"] or node["type"] != "Claim":
                continue
            reviewed = no_rows.get((run, node["id"]), {})
            row = next(r for r in sheet["rows"] if r["reference_id"] == node["id"])
            if reviewed.get("conditions_complete") is None:
                missing_conditions.append({"run": run, "section": meta["section"],
                    "reference_id": node["id"], "coverage": reviewed.get("coverage"),
                    "worksheet_conditions": row["reference"].get("conditions")})
    result["e3_unjudged_claim_conditions"] = missing_conditions
    changes = []
    for run, meta in no_map.items():
        with_run = next(k for k, v in mapping.items() if v["method"] == "maf"
                        and v["section"] == meta["section"] and v["repeat"] == meta["repeat"])
        pool = [n for n in ref["nodes"] if n["section"] == meta["section"] and n["status"] == "M"]
        gains, losses = [], []
        for n in pool:
            a = items.get((with_run, n["id"]), {}).get("coverage") == "full"
            b = no_rows.get((run, n["id"]), {}).get("coverage") == "full"
            if a and not b:
                gains.append(n["id"])
            if b and not a:
                losses.append(n["id"])
        changes.append({"section": meta["section"], "repeat": meta["repeat"],
                        "with_memory_only_full": gains, "without_memory_only_full": losses})
    result["memory_item_changes"] = changes

    common = read(S / "e1_prefix/common_pool.json")
    prefix_items = read(S / "e1_prefix/review/judgements_E1P_final.json")["item_rows"]
    prefix_rows = {(r["run"], r["item_id"]): r for r in prefix_items}
    prefix_map = read(S / "e1_prefix/blinding_map_internal.json")["mapping"]
    trajectory = []
    for run, meta in prefix_map.items():
        trajectory.append({"method": meta["method"], "repeat": meta["repeat"],
            "range": meta["section"], "blind": run,
            "full": frac(sum(prefix_rows.get((run, n["id"]), {}).get("coverage") == "full"
                             for n in common["items"]), len(common["items"]))})
    for run, meta in mapping.items():
        if meta["method"] in {"maf", "direct"} and meta["section"] == "input_I":
            trajectory.append({"method": meta["method"], "repeat": meta["repeat"],
                "range": "input_I3", "blind": run,
                "full": frac(sum(items.get((run, n["id"]), {}).get("coverage") == "full"
                                 for n in common["items"]), len(common["items"]))})
    result["prefix_with_i3_from_existing_e1_reviews"] = sorted(trajectory,
        key=lambda x: (x["method"], x["repeat"], x["range"]))

    main = read(ROOT / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/reference_v3.json")
    expanded = read(S / "e2_prerequisite/reference_v4.json")
    orig = {(p["target_ku"], p["prerequisite_ku"]): p for p in main["pairs"]}
    ext = {(p["target_ku"], p["prerequisite_ku"]): p for p in expanded["pairs"]}
    result["original_reference_preserved"] = all(k in ext and
        (v["role"], v.get("directness")) == (ext[k]["role"], ext[k].get("directness"))
        for k, v in orig.items())
    e2_scores = []
    for arm in ("P", "P_plus_S"):
        for repeat in (1, 2):
            d = S / f"e2_prerequisite/run_v3/runs/{arm}/r{repeat}"
            manifest = read(d / "run_manifest.json")
            predictions = read(d / "predictions.json")["verdicts"]
            predicted = {(p["source_ku"], p["target_ku"]) for p in predictions
                         if p["label"] == "required" and p.get("directness") == "direct"}
            subsets = {}
            for name, pool in (("original_four", main["pairs"]), ("expanded_nine", expanded["pairs"])):
                known = {(p["target_ku"], p["prerequisite_ku"]) for p in pool if p["role"] != "uncertain"}
                gold = {(p["target_ku"], p["prerequisite_ku"]) for p in pool
                        if p["role"] == "required" and p.get("directness") == "direct"}
                judged = {(p["source_ku"], p["target_ku"]) for p in predictions if p["label"] != "uncertain"}
                subsets[name] = {"direct": classification(gold, predicted, known),
                    "verdict_coverage": frac(len(judged & known), len(known))}
            saved = read(d / "scores_layered.json")["end_to_end_direct"]
            got = subsets["expanded_nine"]["direct"]
            e2_scores.append({"arm": arm, "repeat": repeat, "status": manifest["status"],
                "subsets": subsets, "saved_direct_counts_match": all(got[k] == saved[k] for k in ("tp", "fp", "fn"))})
    result["e2_scores"] = e2_scores

    packet = read(S / "reports/author_packet.json")
    pack_map = read(S / "reports/author_packet_mapping_internal.json")["mapping"]
    pack_checks = []
    for entry in packet["items"]:
        run = pack_map[entry["blind_id"]]["run"]
        worksheet = read(S / f"e1_content/review/worksheet_{run}.json")
        row = next(r for r in worksheet["rows"] if r["reference_id"] == entry["reference_id"])
        sent = {v.get("native_id") for v in entry.get("output_delivered", [])}
        native = {v.get("native_id") for v in row.get("native_candidates", [])}
        pack_checks.append({"id": entry["blind_id"], "stratum": entry["stratum"],
            "run": run, "reference": entry["reference_id"],
            "excerpt_empty": not bool(entry.get("source_excerpt")),
            "named_candidates_count": len(native),
            "named_candidates_in_packet": len(native & sent),
            "all_named_candidates_omitted": bool(native) and not bool(native & sent)})
    result["author_packet_audit"] = {"rows": len(pack_checks),
        "source_excerpt_empty": sum(x["excerpt_empty"] for x in pack_checks),
        "all_named_candidates_omitted": [x for x in pack_checks if x["all_named_candidates_omitted"]]}

    ledgers = []
    for p in S.rglob("*.json"):
        if p.name != "budget.json" and p.parent.name not in {"e1_ledgers_a2", "e1p_ledgers", "e3_ledgers"}:
            continue
        data = read(p)
        ledgers.append({"path": str(p.relative_to(ROOT)), "sha256": digest(p),
                        "usd": data.get("accounting_usd", 0) or 0})
    grouped = defaultdict(list)
    for row in ledgers:
        grouped[row["sha256"]].append(row)
    result["budget_arithmetic"] = {"ledgers": len(ledgers),
        "sum_usd": sum(x["usd"] for x in ledgers),
        "byte_identical_duplicate_groups": [v for v in grouped.values() if len(v) > 1],
        "duplicate_note": "Identical conservative reservations need not be duplicate API attempts; do not deduplicate by ledger hash alone."}
    return result


if __name__ == "__main__":
    result = run_audit()
    out = S / "codex_acceptance_2026-10-11"
    out.mkdir(exist_ok=True)
    (out / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8", newline="\n")
    brief = {k: result[k] for k in ("e1_per_document", "e3_unjudged_claim_conditions",
             "original_reference_preserved", "e2_scores", "budget_arithmetic")}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
