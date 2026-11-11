"""Score explicit native-semantic judgments; never score by entity-name similarity."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from prospective_native_client import write


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(packet, review):
    if review["label"] != packet["label"] or review.get("reviewer_provenance") in {None, "pending", ""}:
        raise ValueError("Missing reviewer provenance")
    known = {i["item_id"] for i in packet["items"]}
    node_reference = {n["id"]: n for n in packet["reference"]["nodes"]}
    for kind in ("node", "relation"):
        rows = review[f"{kind}_coverage"]
        references = packet["reference"]["nodes" if kind == "node" else "relations"]
        if len(rows) != len(references) or {r["reference_id"] for r in rows} != {r["id"] for r in references}:
            raise ValueError("Reference judgments are missing or repeated")
        for row in rows:
            if row["status"] not in {"full", "partial", "name_only", "missing", "not_applicable"}:
                raise ValueError("Unfinished semantic judgment")
            if set(row["item_ids"]) - known or row["status"] in {"full", "partial", "name_only"} and not row["item_ids"]:
                raise ValueError("Judgment does not link actual output")
            if not row["reason"].strip():
                raise ValueError("Source-grounded rationale required")
            if kind == "node" and row["status"] == "full" and row.get("conditions_complete") is False:
                raise ValueError("Incomplete conditions cannot count as full mathematical coverage")
            if (kind == "node" and row["status"] == "full" and
                    node_reference[row["reference_id"]].get("type") == "Claim" and
                    row.get("conditions_complete") is not True):
                raise ValueError("Full Claim coverage needs an explicit condition judgment")
    support = review["prediction_support"]
    if len(support) != len(known) or {r["item_id"] for r in support} != known:
        raise ValueError("Prediction support review incomplete")
    for row in support:
        if row["status"] not in {"yes", "partial", "no", "undecided"} or not row["reason"].strip():
            raise ValueError("Unfinished source support review")
        if row["scope_admitted"] not in {True, False, None}:
            raise ValueError("Invalid scope admission")


def ratios(counts):
    def fraction(numerator, denominator):
        total = counts.get(denominator, 0)
        return counts.get(numerator, 0) / total if total else None

    required = counts.get("required", 0)
    recognized = sum(counts.get(f"required_{s}", 0) for s in ("full", "partial", "name_only"))
    return {
        "full_required_recall": fraction("required_full", "required"),
        "recognized_required_recall": recognized / required if required else None,
        "full_core_recall": fraction("core_full", "core_required"),
        "full_detail_recall": fraction("detail_full", "detail_required"),
        "full_optional_coverage": fraction("optional_full", "optional"),
        "full_reference_relation_recall": fraction("relations_full", "relations_reference"),
        "native_support_yes_ratio": fraction("support_yes", "native_units_reviewed"),
        "required_claim_conditions_complete_ratio": fraction("claim_conditions_complete", "required_claims"),
    }


def score(directory, output):
    mapping = read(directory / "mapping_private.json")
    rows, totals = [], defaultdict(lambda: defaultdict(int))
    for job in mapping:
        if job["status"] != "completed":
            rows.append({"label": job["label"], "method": job["method"], "status": job["status"], "quality": None})
            continue
        packet, review = read(directory / f"{job['label']}.json"), read(directory / f"{job['label']}_review.json")
        validate(packet, review)
        reference = {n["id"]: n for n in packet["reference"]["nodes"]}
        counts = defaultdict(int)
        for row in review["node_coverage"]:
            ref = reference[row["reference_id"]]
            if ref["status"] == "M":
                counts["required"] += 1
                counts[f"required_{row['status']}"] += 1
                counts[f"{ref['tier']}_required"] += 1
                if row["status"] == "full":
                    counts[f"{ref['tier']}_full"] += 1
                if ref.get("type") == "Claim":
                    counts["required_claims"] += 1
                    condition = row.get("conditions_complete")
                    key = "complete" if condition is True else "incomplete" if condition is False else "unresolved"
                    counts[f"claim_conditions_{key}"] += 1
            elif ref["status"] == "O":
                counts["optional"] += 1
                counts[f"optional_{row['status']}"] += 1
        for row in review["relation_coverage"]:
            counts["relations_reference"] += 1
            counts[f"relations_{row['status']}"] += 1
        item_kind = {i["item_id"]: i.get("kind", "unknown") for i in packet["items"]}
        for row in review["prediction_support"]:
            counts["native_units_reviewed"] += 1
            counts[f"support_{row['status']}"] += 1
            counts[f"{item_kind[row['item_id']]}_reviewed"] += 1
            counts[f"{item_kind[row['item_id']]}_support_{row['status']}"] += 1
            counts["scope_admitted" if row["scope_admitted"] is True else
                   "scope_excluded" if row["scope_admitted"] is False else "scope_unresolved"] += 1
        counts["duplicate_groups"] = len(review.get("duplicate_groups", []))
        counts["false_merge_events"] = len(review.get("false_merges", []))
        result = {"label": job["label"], "method": job["method"], "section": job["section"],
                  "repeat": job["repeat"], "status": "reviewed", "counts": dict(counts),
                  **ratios(counts),
                  "provenance": review["reviewer_provenance"]}
        rows.append(result)
        for key, value in counts.items():
            totals[job["method"]][key] += value
    by_method = {}
    for method in sorted({j["method"] for j in mapping}):
        selected = [r for r in rows if r["method"] == method]
        sections = {}
        for section in sorted({j["section"] for j in mapping if j["method"] == method}):
            reviewed = [r for r in selected if r.get("section") == section and r["status"] == "reviewed"]
            values = [r["full_required_recall"] for r in reviewed]
            sections[section] = {"reviewed_repeats": len(reviewed), "planned_repeats": 2,
                                 "full_required_recall_by_repeat": values,
                                 "mean": sum(values) / len(values) if values else None}
        section_means = [v["mean"] for v in sections.values() if v["mean"] is not None]
        by_method[method] = {"planned": len(selected),
            "reviewed": sum(r["status"] == "reviewed" for r in selected),
            "noncompleted": [{"label": j["label"], "section": j["section"], "repeat": j["repeat"],
                              "status": j["status"]} for j in mapping
                             if j["method"] == method and j["status"] != "completed"],
            "ratios_for_reviewed_outputs_only": ratios(totals[method]),
            "sections": sections,
            "equal_section_mean_for_observed_outputs": sum(section_means) / len(section_means) if section_means else None,
            "all_planned_outputs_reviewed": all(r["status"] == "reviewed" for r in selected)}
    write(output, {"rows": rows, "pooled_counts": dict(totals), "by_method": by_method,
        "n_note": "Three exposed sections; repeats and facts are not independent samples",
        "no_f1": "Native-unit support and required-content recall are different universes",
        "support_limit": "Native units differ across representations; do not infer equivalent precision from their sizes"})
    print(f"Scored {sum(r['status']=='reviewed' for r in rows)} reviewed outputs")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    score(args.directory.resolve(), args.output.resolve())
