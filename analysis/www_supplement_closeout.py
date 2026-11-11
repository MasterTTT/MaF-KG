"""Versioned evaluation repair. No extraction or research API calls."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path

from www_supplement_e1_worksheets import enrich, native_items, read

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "evaluation/www_evidence_supplement_2026-10-11"
OUT = S / "closeout_v2"

PROTOCOL = {
    "version": "complete-carriers-source-review-v2",
    "provenance": "AI-assisted source review, not author adjudication or expert Gold",
    "reference_policy": "The pre-prediction evaluation contract is unchanged. Only the review view is repaired.",
    "rules": [
        "Review only the assigned blinded packets. Do not inspect method mapping, old judgements, scores, reports, or other reviewers.",
        "Judge only effective output content: native items, their logical_form and owned expressions, and valid native edges/triples. Source quotations orient the reviewer but never supply a missing output condition.",
        "A formula or a whole assertion literally present in an item name may carry meaning. A bare topic name alone cannot. Do not require a description field or a particular storage type.",
        "Combining split items is allowed only when the output explicitly connects them or they are representations of the same item. Do not assemble a new fact from unrelated neighbouring items or external mathematical knowledge.",
        "full means the output represents the substantive reference meaning, including all material restrictions. partial means some substantive meaning is present but a result or material restriction is absent. name_only means only the topic is identifiable. missing means no compatible output carrier.",
        "For Claim conditions use true or false, not null. A missing Claim has conditions_complete=false. For non-Claims conditions_complete=null. For full Claims material conditions must be complete.",
        "Separate material domain/form restrictions from derivation order, integration-constant bookkeeping and textbook meta-comments. Do not invent conditions the textbook does not state. Retain the author's prior decision that implicit y!=0 in the two annotated separation steps is not a required explicit condition under this source-based reference.",
        "Relation full means the effective output asserts the same mathematical relationship between the same content objects. A supported inverse predicate can fully preserve semantics despite opposite stored direction. partial means a weaker or only partly supported assertion. mentioned_only means the objects occur but the relation is not asserted. not_covered means at least one object has no carrier. uncertain preserves unresolved interpretation.",
        "Provide item_ids, edge_ids and expression_ids for content bindings; start_ids, end_ids and edge_ids for relation bindings. IDs must occur in the native output. Give a short source-based reason. Use uncertainty rather than pretending a binding is clear.",
        "Do not evaluate foreign predicates against a typed graph schema. Exact reference-label/storage matching and method-native structural validity will be computed separately from your bindings.",
        "Return every reference item and relation exactly once. Write the requested JSON directly, preserve UTF-8, and record model=gpt-6-luna and reasoning_effort=max. No research API calls or extraction.",
    ],
    "answer_schema": {
        "packet_id": "Cxx",
        "item_rows": [{"item_id": "reference ID", "coverage": "full|partial|name_only|missing",
                       "conditions_complete": "boolean for Claim, null otherwise",
                       "item_ids": [], "edge_ids": [], "expression_ids": [],
                       "missing_conditions": [], "reason": "short source-based reason"}],
        "relation_rows": [{"relation_id": "reference ID",
                           "semantic": "full|partial|mentioned_only|not_covered|uncertain",
                           "start_ids": [], "end_ids": [], "edge_ids": [],
                           "reason": "short source-based reason"}],
    },
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def binding(path):
    path = Path(path).resolve()
    rendered = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    return {"path": str(rendered).replace("\\", "/"), "sha256": sha(path)}


def literal_assignment(path, name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(x, ast.Name) and x.id == name for x in targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing frozen literal {name}")


def excerpt(node, lines):
    span = node.get("source_evidence") or node.get("evidence")
    if not span or len(span) != 2 or span[0] not in lines or span[1] not in lines:
        raise ValueError(f"Missing in-scope source excerpt for {node.get('id')}: {span}")
    return {"span": span, "text": "\n".join(f"{i}: {lines[i]}" for i in range(span[0], span[1] + 1))}


def make_packet(packet_id, graph, reference, source_lines):
    entities, edges = native_items(graph)
    entity_ids = [x["id"] for x in entities]
    edge_ids = [x["id"] for x in edges]
    if len(set(entity_ids)) != len(entity_ids) or len(set(edge_ids)) != len(edge_ids):
        raise ValueError("Native IDs must be unique in the review view")
    nodes = reference["nodes"]
    by_id = {n["id"]: n for n in nodes}
    items = [{"reference": n, "source": excerpt(n, source_lines)} for n in nodes]
    relations = []
    for relation in reference.get("relations", []) + reference.get("cross_section_relations", []):
        contexts = [excerpt(relation, source_lines)] if relation.get("source_evidence") or relation.get("evidence") else []
        relations.append({"reference": relation,
            "start": by_id[relation["start"]]["name"], "end": by_id[relation["end"]]["name"],
            "source": contexts or [excerpt(by_id[relation[k]], source_lines) for k in ("start", "end")]})
    return {"schema": "source_review.complete_carriers.v2", "packet_id": packet_id,
            "native_output": {"items": entities, "edges": edges},
            "reference_items": items, "reference_relations": relations,
            "rule": "Source context is not generated output; only native_output can carry retained content."}


def subreference(reference, section, common=None):
    if common:
        ids = {n["id"] for n in common["items"]}
        rel_ids = {r["id"] for r in common["relations"]}
    else:
        ids = {n["id"] for n in reference["nodes"] if n["section"] == section}
        rel_ids = None
    return {"nodes": [n for n in reference["nodes"] if n["id"] in ids],
            "relations": [r for r in reference["relations"] if r["start"] in ids and r["end"] in ids
                          and (rel_ids is None or r["id"] in rel_ids)]}


def prepare():
    OUT.mkdir(exist_ok=True)
    frozen = enrich(read(S / "references/runner_reference_v1.json"),
                    S / "references/reference_input_I_v2.json")
    write(OUT / "reference_view.json", frozen)
    write(OUT / "review_protocol.json", PROTOCOL)
    write(OUT / "review_protocol.md", "# Source-based Review Protocol\n\n" + "\n\n".join(PROTOCOL["rules"]))
    sources = {x["input_id"]: x for x in read(S / "e1_content/inputs/input_map.json")["inputs"]}
    source_tables = {}
    for key, meta in sources.items():
        p = Path(meta["source_file"])
        if sha(p).upper() != meta["source_sha256"].upper():
            raise ValueError("Original textbook source changed")
        source_tables[key] = {i + 1: text for i, text in enumerate(p.read_text(encoding="utf-8").splitlines())}
    jobs = []
    for family, mapping_path in (("E1", "e1_content/blinding_map_internal.json"),
                                 ("E3", "e3_memory/blinding_map_internal.json"),
                                 ("E1p", "e1_prefix/blinding_map_internal.json")):
        for old_id, meta in read(S / mapping_path)["mapping"].items():
            packet_id = f"C{len(jobs) + 1:02d}"
            section = "input_I" if meta["section"].startswith("input_I") else "input_O"
            common = read(S / "e1_prefix/common_pool.json") if family == "E1p" else None
            ref = subreference(frozen, section, common)
            graph_path = Path(meta["directory"]) / "graph.json"
            packet = make_packet(packet_id, read(graph_path), ref, source_tables[section[-1]])
            path = OUT / "blind_packets" / f"{packet_id}.json"
            if path.exists() and read(path) != packet:
                raise ValueError("Prepared packet changed; create a new closeout version")
            write(path, packet)
            jobs.append({"packet_id": packet_id, "family": family, "old_id": old_id, **meta,
                         "graph_binding": binding(graph_path), "packet_binding": binding(path)})
    protected = [ROOT / row["graph_binding"]["path"] for row in jobs]
    protected += [S / "references/runner_reference_v1.json", S / "references/reference_input_I_v2.json",
                  S / "references/reference_input_O_v1.json", S / "e1_prefix/common_pool.json",
                  S / "freeze/evaluation_contract.json", S / "e2_prerequisite/reference_v4.json",
                  ROOT / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/reference_v3.json"]
    record = {"schema": "evaluation.closeout.v2", "status": "prepared_review_pending", "jobs": jobs,
              "protected": [binding(p) for p in protected], "protocol": binding(OUT / "review_protocol.json"),
              "review_reference": binding(OUT / "reference_view.json"),
              "provenance": {"delegated_model": "gpt-6-luna", "reasoning_effort": "max",
                             "extractor_predictions_unchanged": True, "reference_labels_unchanged": True}}
    write(OUT / "manifest_internal.json", record)
    print(json.dumps({"packets": len(jobs), "families": dict(Counter(j["family"] for j in jobs)),
                      "packet_bytes": sum((OUT / j["packet_binding"]["path"].split("closeout_v2/")[-1]).stat().st_size for j in jobs)}, indent=2))


def validate_review(packet, review):
    if review.get("packet_id") != packet["packet_id"]:
        raise ValueError("Review packet ID mismatch")
    native = packet["native_output"]
    item_ids = {n["id"] for n in native["items"]}
    edge_ids = {r["id"] for r in native["edges"]}
    expression_ids = {e["id"] for n in native["items"] for e in n.get("owned_expressions", [])}
    refs = {row["reference"]["id"]: row["reference"] for row in packet["reference_items"]}
    relation_ids = {row["reference"]["id"] for row in packet["reference_relations"]}
    for field, key, expected in (("item_rows", "item_id", set(refs)),
                                  ("relation_rows", "relation_id", relation_ids)):
        values = [row[key] for row in review.get(field, [])]
        if len(values) != len(set(values)) or set(values) != expected:
            raise ValueError(f"Incomplete or duplicate {field} for {packet['packet_id']}")
    for row in review["item_rows"]:
        if row["coverage"] not in {"full", "partial", "name_only", "missing"}:
            raise ValueError("Invalid coverage")
        for field, allowed in (("item_ids", item_ids), ("edge_ids", edge_ids), ("expression_ids", expression_ids)):
            if not isinstance(row.get(field), list) or not set(row[field]) <= allowed:
                raise ValueError(f"{packet['packet_id']} {row['item_id']}: invalid {field} {row.get(field)}")
        if refs[row["item_id"]]["type"] == "Claim":
            if type(row.get("conditions_complete")) is not bool:
                raise ValueError("Claim conditions must be explicitly judged")
            if row["coverage"] == "full" and row["conditions_complete"] is not True:
                raise ValueError("Full Claim cannot omit material restrictions")
            if row["coverage"] == "missing" and row["conditions_complete"]:
                raise ValueError("Missing Claim cannot retain conditions")
        if row["coverage"] != "missing" and not any(row[field] for field in ("item_ids", "edge_ids", "expression_ids")):
            raise ValueError("A delivered item needs an effective-output carrier")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("Missing source-based reason")
    for row in review["relation_rows"]:
        if row["semantic"] not in {"full", "partial", "mentioned_only", "not_covered", "uncertain"}:
            raise ValueError("Invalid relation judgement")
        for field, allowed in (("start_ids", item_ids), ("end_ids", item_ids), ("edge_ids", edge_ids)):
            if not isinstance(row.get(field), list) or not set(row[field]) <= allowed:
                unknown = set(row.get(field) or []) - allowed
                raise ValueError(f"{packet['packet_id']} {row['relation_id']}: invalid {field}, unknown={sorted(unknown)}")
        if row["semantic"] in {"full", "partial", "mentioned_only"} and not (row["start_ids"] and row["end_ids"]):
            raise ValueError("Relation coverage needs both content objects")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("Missing relation reason")
    return True


def binding_issues(packet, review):
    native = packet["native_output"]
    allowed = {"item_ids": {n["id"] for n in native["items"]},
               "start_ids": {n["id"] for n in native["items"]},
               "end_ids": {n["id"] for n in native["items"]},
               "edge_ids": {e["id"] for e in native["edges"]},
               "expression_ids": {e["id"] for n in native["items"] for e in n.get("owned_expressions", [])}}
    result = []
    for section, key, fields in (("item_rows", "item_id", ("item_ids", "edge_ids", "expression_ids")),
                                 ("relation_rows", "relation_id", ("start_ids", "end_ids", "edge_ids"))):
        for row in review.get(section, []):
            for field in fields:
                values = row.get(field)
                if not isinstance(values, list):
                    result.append({"reference_id": row.get(key), "field": field, "error": "must be an ID array"})
                elif set(values) - allowed[field]:
                    result.append({"reference_id": row.get(key), "field": field,
                                   "unknown_ids": sorted(set(values) - allowed[field])})
    return result


def strict_from_bindings(reference, row, edges):
    connected = [edge for edge in edges if (edge["source"] in row["start_ids"] and edge["target"] in row["end_ids"])
                 or (edge["target"] in row["start_ids"] and edge["source"] in row["end_ids"])]
    if not connected:
        return "not_comparable"
    same_label = [edge for edge in connected if edge["type"] == reference["type"]]
    if not same_label:
        return "type_mismatch"
    if any(edge["source"] in row["start_ids"] and edge["target"] in row["end_ids"] for edge in same_label):
        return "conforms"
    return "direction_mismatch"


def native_structure(job, packet):
    items, edges = packet["native_output"]["items"], packet["native_output"]["edges"]
    by_id = {n["id"]: n for n in items}
    dangling = [r["id"] for r in edges if r["source"] not in by_id or r["target"] not in by_id]
    empty_predicates = [r["id"] for r in edges if not isinstance(r["type"], str) or not r["type"].strip()]
    result = {"generated_edges": len(edges), "dangling_endpoints": dangling,
              "empty_predicates": empty_predicates,
              "note": "Native structural validity is not mathematical correctness or reference-edge agreement."}
    if job["method"] in {"maf", "direct"}:
        snapshot = Path(job["directory"]).parents[3] / "snapshot/agents/Extractor.py"
        signatures = literal_assignment(snapshot, "RELATION_SIGNATURES")
        invalid = [r["id"] for r in edges if r["type"] not in signatures or
                   (by_id.get(r["source"], {}).get("type"), by_id.get(r["target"], {}).get("type"))
                   not in signatures.get(r["type"], ())]
        result.update(schema_binding=binding(snapshot), signature_scope="own frozen typed signatures",
                      invalid_signature_edges=invalid, signature_valid=len(edges) - len(invalid))
    elif job["method"] == "edc":
        audit = Path(job["directory"]) / "edc_audit.json"
        schema = read(audit)["target_schema"]
        invalid = [r["id"] for r in edges if r["type"] not in schema]
        result.update(schema_binding=binding(audit), signature_scope="own target-alignment predicate dictionary; no typed endpoint schema",
                      invalid_signature_edges=invalid, signature_valid=len(edges) - len(invalid))
    else:
        result.update(signature_scope="free predicate/entity representation without frozen typed signatures",
                      signature_valid=None, invalid_signature_edges=None,
                      signature_status="not_applicable; structural endpoint/predicate checks reported separately")
    return result


def load_validated_reviews():
    status = validate_all()
    if status["pending"] or status.get("invalid"):
        raise ValueError("Review pending; no final scores generated")
    result = []
    for job in read(OUT / "manifest_internal.json")["jobs"]:
        packet = read(ROOT / job["packet_binding"]["path"])
        review_path = OUT / "reviews" / f"{job['packet_id']}.json"
        review = read(review_path)
        relations = {r["reference"]["id"]: r["reference"] for r in packet["reference_relations"]}
        item_rows = [{**row, "run": job["packet_id"]} for row in review["item_rows"]]
        relation_rows = [{**row, "run": job["packet_id"],
            "strict": strict_from_bindings(relations[row["relation_id"]], row, packet["native_output"]["edges"])}
            for row in review["relation_rows"]]
        result.append({"job": job, "packet": packet, "item_rows": item_rows,
                       "relation_rows": relation_rows, "review_binding": binding(review_path),
                       "native_structure": native_structure(job, packet)})
    return result


def make_author_packet(reviews):
    by_old_id = {r["job"]["old_id"]: r for r in reviews if r["job"]["family"] == "E1"}
    original = read(S / "reports/author_packet.json")
    old_mapping = read(S / "reports/author_packet_mapping_internal.json")["mapping"]
    repaired, internal = [], {}
    for old in original["items"]:
        review = by_old_id[old_mapping[old["blind_id"]]["run"]]
        packet, item_id = review["packet"], old["reference_id"]
        relation = old["stratum"] == "relation_semantic"
        native = packet["native_output"]
        pool = packet["reference_relations"] if relation else packet["reference_items"]
        source = next(r for r in pool if r["reference"]["id"] == item_id)
        rows = review["relation_rows"] if relation else review["item_rows"]
        verdict = next(r for r in rows if r["relation_id" if relation else "item_id"] == item_id)
        bound_ids = set(verdict["start_ids"] + verdict["end_ids"] if relation else verdict["item_ids"])
        bound_edges = set(verdict["edge_ids"])
        output = {"items": [n for n in native["items"] if n["id"] in bound_ids],
                  "edges": [e for e in native["edges"] if e["id"] in bound_edges]}
        # Missing bindings still expose the complete effective output for author inspection.
        if not output["items"] and not output["edges"]:
            output = native
        contexts = source["source"] if relation else [source["source"]]
        old_ids = {n.get("native_id") for n in old.get("output_delivered", [])}
        fields = ("semantic", "strict") if relation else ("coverage", "conditions_complete")
        changed = any(verdict.get(k) != old.get("existing_judgement", {}).get(k) for k in fields)
        exposure_changed = not old.get("source_excerpt") or (bool(bound_ids) and not bound_ids <= old_ids)
        needs_recheck = changed or exposure_changed
        repaired.append({"blind_id": old["blind_id"], "stratum": old["stratum"],
            "reference_id": item_id, "reference": source["reference"], "source_context": contexts,
            "effective_output": output, "previous_judgement": old.get("existing_judgement"),
            "corrected_judgement": {k: v for k, v in verdict.items() if k != "run"},
            "status": "pending_author_recheck" if needs_recheck else "prior_confirmation_preserved",
            "reason_for_recheck": {"judgement_changed": changed, "material_exposure_changed": exposure_changed},
            "prior_confirmation": "Recorded in the old author_adjudication.json; applies only to the old packet.",
            "complete_output_binding": review["job"]["packet_binding"]})
        internal[old["blind_id"]] = review["job"]["packet_id"]
    result = {"schema": "author_review.repaired.v2", "sample_ids_unchanged": True,
              "items": repaired, "item_count": len(repaired),
              "pending_count": sum(r["status"] == "pending_author_recheck" for r in repaired),
              "assistant_cannot_confirm_for_author": True,
              "substitution": "The previously accepted condition-check substitution is retained; no generated-fact accuracy claim.",
              "old_packet_binding": binding(S / "reports/author_packet.json")}
    write(OUT / "author_packet.json", result)
    write(OUT / "author_mapping_internal.json", internal)
    lines = ["# Repaired Author Review Packet", "", "Original AR IDs are retained. New approvals are pending, not inferred from previous approval.", ""]
    for row in repaired:
        lines += [f"## {row['blind_id']} ({row['status']})", "",
                  f"Reference: {row['reference_id']} {row['reference'].get('name', row['reference'].get('type'))}", ""]
        for context in row["source_context"]:
            lines += ["```text", context["text"], "```", ""]
        lines += ["Effective output and current judgement:", "```json",
                  json.dumps({"output": row["effective_output"], "judgement": row["corrected_judgement"]}, ensure_ascii=False, indent=2),
                  "```", "", "Author answer: agree / change (give reason) / uncertain", ""]
    write(OUT / "author_packet.md", "\n".join(lines))
    return result


def score_closeout():
    from www_supplement_e1_aggregate import aggregate_reviews
    from www_supplement_scope_score import score_scoped
    reviews = load_validated_reviews()
    reference = read(OUT / "reference_view.json")
    common = read(S / "e1_prefix/common_pool.json")
    family_scores = {}
    for family in ("E1", "E3"):
        group = [r for r in reviews if r["job"]["family"] == family]
        mapping = {r["job"]["packet_id"]: {k: r["job"][k] for k in ("method", "section", "repeat")}
                   for r in group}
        items = [row for r in group for row in r["item_rows"]]
        relations = [row for r in group for row in r["relation_rows"]]
        # This is the same public aggregation entry exercised by its CLI tests.
        result = aggregate_reviews(reference, items, relations, mapping)
        result["native_structure"] = {r["job"]["packet_id"]: r["native_structure"] for r in group}
        result["native_schema_rule"] = "Use generated-edge native_structure; legacy reference-row signature fields are not interpreted as validity."
        family_scores[family] = result
        write(OUT / f"{family.lower()}_metrics.json", result)

    prefix_items, prefix_relations, mapping = [], [], {}
    pool_item_ids = {n["id"] for n in common["items"]}
    pool_rel_ids = {r["id"] for r in common["relations"]}
    for r in reviews:
        job = r["job"]
        is_i3 = job["family"] == "E1" and job["section"] == "input_I" and job["method"] in {"maf", "direct"}
        if job["family"] != "E1p" and not is_i3:
            continue
        key = f"I3_{job['packet_id']}" if is_i3 else job["packet_id"]
        mapping[key] = {"method": job["method"], "repeat": job["repeat"],
                        "section": "input_I3" if is_i3 else job["section"],
                        "reference_section": "input_I", "prediction_binding": job["graph_binding"],
                        "review_binding": r["review_binding"]}
        prefix_items += [{**row, "run": key} for row in r["item_rows"] if row["item_id"] in pool_item_ids]
        prefix_relations += [{**row, "run": key} for row in r["relation_rows"] if row["relation_id"] in pool_rel_ids]
    prefix = score_scoped(subreference(reference, "input_I", common), prefix_items, prefix_relations, mapping)
    prefix["trajectory"] = [{"run": run, "method": row["method"], "repeat": row["repeat"],
        "range": row["section"], "complete": row["complete"], "conditions": row["condition_retention"],
        "relation_full": row["relation_semantic"]["full"], "relation_partial": row["relation_semantic"]["partial"],
        "status": "completed"} for run, row in prefix["per_run"].items()]
    failure = S / "reports/e1p_input_I2_maf_r1_failure.json"
    prefix["trajectory"].append({"method": "maf", "repeat": 1, "range": "input_I2", "status": "failed",
                                 "complete": None, "failure_binding": binding(failure)})
    prefix["trajectory"].sort(key=lambda x: (x["method"], x["repeat"], x["range"]))
    write(OUT / "prefix_metrics.json", prefix)

    contrasts = []
    e1_by = {(r["job"]["section"], r["job"]["repeat"]): r for r in reviews
             if r["job"]["family"] == "E1" and r["job"]["method"] == "maf"}
    for no in [r for r in reviews if r["job"]["family"] == "E3"]:
        yes = e1_by[(no["job"]["section"], no["job"]["repeat"])]
        yes_rows = {r["item_id"]: r for r in yes["item_rows"]}
        no_rows = {r["item_id"]: r for r in no["item_rows"]}
        pool = [n for n in reference["nodes"] if n["section"] == no["job"]["section"] and n["status"] == "M"]
        gains, losses = [], []
        for n in pool:
            a, b = yes_rows[n["id"]]["coverage"] == "full", no_rows[n["id"]]["coverage"] == "full"
            if a and not b:
                gains.append(n["id"])
            if b and not a:
                losses.append(n["id"])
        contrasts.append({"section": no["job"]["section"], "repeat": no["job"]["repeat"],
                          "with_only_full": gains, "without_only_full": losses})
    write(OUT / "memory_item_contrasts.json", {"contrasts": contrasts,
        "rule": "Matched topic/repeat comparisons are descriptive stochastic observations, not proof of zero causal effect."})
    author = make_author_packet(reviews)
    outputs = {"E1": family_scores["E1"], "E3": family_scores["E3"], "prefix": prefix,
               "author_recheck_pending": author["pending_count"]}
    write(OUT / "master_metrics.json", outputs)
    return outputs, reviews


def metric_text(metric):
    value = metric.get("value")
    return f"{metric['numerator']}/{metric['denominator']} ({100 * value:.1f}%)" if value is not None else "pending / not applicable"


def finalize():
    from www_supplement_prerequisite_closeout import build_report, write_report
    outputs, reviews = score_closeout()
    prerequisite = build_report(ROOT)
    write_report(prerequisite, ROOT)
    outputs["prerequisite"] = prerequisite
    write(OUT / "master_metrics.json", outputs)

    # Preserve old judgements and explain each changed assessment alongside its new binding.
    from codex_www_supplement_acceptance_2026_10_11 import joined_e1
    old_items, old_relations, _ = joined_e1()
    changes = []
    for reviewed in [r for r in reviews if r["job"]["family"] == "E1"]:
        job = reviewed["job"]
        for key, id_field, fields, old in (("item_rows", "item_id", ("coverage", "conditions_complete"), old_items),
                                          ("relation_rows", "relation_id", ("semantic", "strict"), old_relations)):
            for row in reviewed[key]:
                previous = old[(job["old_id"], row[id_field])]
                changed = [f for f in fields if previous.get(f) != row.get(f)]
                if changed:
                    changes.append({"packet_id": job["packet_id"], "reference_id": row[id_field],
                        "changed_fields": changed, "previous": {f: previous.get(f) for f in fields},
                        "current": {f: row.get(f) for f in fields}, "reason": row.get("reason"),
                        "review_binding": reviewed["review_binding"],
                        "policy": "Frozen contract reapplied to complete effective output; no prediction or Gold edits."})
    write(OUT / "correction_log.json", {"changes": changes, "count": len(changes),
        "reference_and_prediction_unchanged": True, "original_judgements_retained": True})

    report = ["# WWW补充实验收口报告", "", "日期：2026-10-11。本版取代DSH旧汇总作为修正后的技术入口，旧产物全部保留。", "",
        "## 状态", "", "原生抽取与参考标签未改。27份完整载体视图已完成AI辅助源文补审及ID校验；必抽与Optional分开计数，前缀包含I3，先修原四与扩展九分表。",
        f"原64项作者包保持原AR编号；其中{outputs['author_recheck_pending']}项因判定或材料展示变化待作者复核。没有将旧确认冒称为新材料确认，也不称独立专家Gold。", "",
        "## E1内容与条件", "", "以下为同书两份连续输入、各两次技术运行的描述结果。完整覆盖包含实质条件；识别与完整性分列。", "",
        "| 方法 | 完整必抽 | 识别必抽 | 条件保留 | 共同语义full / partial |", "| --- | --- | --- | --- | --- |"]
    for method, row in outputs["E1"]["per_method"].items():
        report.append(f"| {method} | {metric_text(row['complete_required'])} | {metric_text(row['recognized_required'])} | {metric_text(row['condition_retention'])} | {row['relation_full']} / {row['relation_partial']}，分母{row['relation_denominator']} |")
    report += ["", "### 按输入分开", "", "| 输入 | 方法 | 完整必抽 | 条件 |", "| --- | --- | --- | --- |"]
    for section, doc in outputs["E1"]["per_document"].items():
        for method, row in doc["per_method"].items():
            report.append(f"| {section} | {method} | {metric_text(row['complete_required'])} | {metric_text(row['condition_retention'])} |")
    report += ["", "每方法的core/detail、Optional、每次运行和等权macro见e1_metrics.json。不同表示统一按有效数学内容评价，不因缺少description字段预设低分。", "",
        "## Memory对照", "", "| 视图 | 完整必抽 | 条件 | 共同语义full / partial |", "| --- | --- | --- | --- |"]
    yes = outputs["E1"]["per_method"]["maf"]
    no = outputs["E3"]["per_method"]["maf"]
    for label, row in (("有可见Memory", yes), ("无可见Memory", no)):
        report.append(f"| {label} | {metric_text(row['complete_required'])} | {metric_text(row['condition_retention'])} | {row['relation_full']} / {row['relation_partial']}，分母{row['relation_denominator']} |")
    report += ["", "逐项得失保存在memory_item_contrasts.json。该组净差不能证明贡献精确为零；关系总数不是关系正确性。也不能从整体构造优势单独归因Memory。", "",
        "## 独立前缀", "", "| 方法 | 重复 | 范围 | 完整共同项 | 关系full | 状态 |", "| --- | --- | --- | --- | --- | --- |"]
    for row in outputs["prefix"]["trajectory"]:
        report.append(f"| {row['method']} | {row['repeat']} | {row['range']} | {metric_text(row['complete']) if row['complete'] else '不评分'} | {row.get('relation_full', '不评分')} | {row['status']} |")
    report += ["", "同一9项池仅支持有限范围观察。I3复用已有独立完整输入产物，未重放早期回答生成新样本；失败保留，不作为零分或补成最好结果。", "",
        "## 先修模块", "", "原四目标来自同一九目标调用的子集，不称四目标独立实验；扩展参考的助手审阅与7个开放问题保留。", "",
        "| 参考范围 | 臂 | 直接P均值 | 直接R均值 | 直接F1每轮 | 实际判定覆盖 |", "| --- | --- | --- | --- | --- | --- |"]
    for key, scoped in prerequisite["scoring"].items():
        for arm in ("P", "P_plus_S"):
            rows = [r for r in scoped["rounds"] if r["arm"] == arm]
            summary = scoped["aggregate"]["by_arm"][arm]["mean_and_range"]
            f1s = " / ".join(f"{r['direct_required']['f1']:.3f}" for r in rows)
            report.append(f"| {key} | {arm} | {summary['direct_precision']['mean']:.3f} | {summary['direct_recall']['mean']:.3f} | {f1s} | {summary['actual_verdict_coverage']['mean']:.3f} |")
    report += ["", "均值不能掩盖一轮增加、一轮未增加或下降。精度仅针对已枚举确定参考；参考外接受边包括间接边单列，不自动算对或错。32000版与12000失败观察分开，不称必然失败。", "",
        "## 成本与结构", "", "未新增研究API抽取调用。历史研究账本约USD4.91含保守预留，不等于Go实际扣费；Codex辅助审阅消耗未获得可比usage，不能宣称免费。",
        "本轮E1原生运行MaF1429461 token、96请求；Direct263647 token、4请求，约5.42倍token。适配方法不是原论文完全复现。",
        "原生签名按各方法自身配置核查，见native_structure.json；iText没有冻结typed-signature体系，标not_applicable而非套用MaF词表。结构合法不能替代数学正确性。", "",
        "## 数据边界", "", "这些都是单一开发教材内的有限材料与技术重复，不报总体显著性、不声称全图人工准确率、跨教材泛化或学习路径收益。来源引文不能补齐输出未陈述的条件。生成项正确性独立抽查的既有偏离保持披露。", "",
        "## 复现", "", "```powershell", "python -B -X utf8 analysis/www_supplement_closeout.py finalize", "```", "",
        "生成前要求全部review通过覆盖、绑定与冻结hash验证；任何未完成或无效判定都会阻止定稿。"]
    write(OUT / "reports/experiment_report.md", "\n".join(report))
    write(ROOT / "docs/WWW_SUPPLEMENT_CLOSEOUT_2026-10-11.md", "\n".join(report))
    write(OUT / "reports/handoff.md", "# 收口交接\n\n技术修复和AI辅助补审完成，原抽取/Gold未改。\n\n"
          f"作者待核查{outputs['author_recheck_pending']}项，见../author_packet.md；不把它写成人工全部确认。\n\n"
          "当前数据入口为../master_metrics.json；旧reports/experiment_report.md、handoff.md、STATE.md仅历史记录。\n\n"
          "复现命令：python -B -X utf8 analysis/www_supplement_closeout.py finalize。\n\n"
          "后续只处理作者明确反馈、必要的源文歧义和论文入稿，不因分数重开抽取优化。")

    evidence = []
    source_map = read(S / "e1_content/inputs/input_map.json")
    sources = [binding(Path(x["source_file"])) for x in source_map["inputs"]]
    reference_bindings = [binding(S / p) for p in ("references/runner_reference_v1.json", "references/reference_input_I_v2.json", "references/reference_input_O_v1.json")]
    for family in ("E1", "E3"):
        for method, row in outputs[family]["per_method"].items():
            jobs = [r for r in reviews if r["job"]["family"] == family and r["job"]["method"] == method]
            configs = sorted({str(Path(r["job"]["directory"]).parents[3] / "manifest.json") for r in jobs})
            for field in ("complete_required", "recognized_required", "condition_retention"):
                evidence.append({"metric": f"{family}.{method}.{field}", "value": row[field], "source": sources,
                    "reference": reference_bindings, "prediction": [r["job"]["graph_binding"] for r in jobs],
                    "review": [r["review_binding"] for r in jobs], "config": [binding(Path(p)) for p in configs],
                    "scorer": [binding(ROOT / "analysis/www_supplement_scope_score.py"), binding(Path(__file__))],
                    "protocol": binding(OUT / "review_protocol.json")})
    for row in outputs["prefix"]["trajectory"]:
        if row["status"] == "failed":
            evidence.append({"metric": "prefix.failure", **row})
            continue
        mapping = next(r for r in reviews if r["job"]["packet_id"] == row["run"].removeprefix("I3_"))
        evidence.append({"metric": f"prefix.{row['method']}.r{row['repeat']}.{row['range']}", "value": row["complete"],
            "source": sources[:1], "reference": [binding(S / "e1_prefix/common_pool.json")],
            "prediction": [mapping["job"]["graph_binding"]], "review": [mapping["review_binding"]],
            "config": [binding(Path(mapping["job"]["directory"]).parents[3] / "manifest.json")],
            "scorer": [binding(Path(__file__)), binding(ROOT / "analysis/www_supplement_scope_score.py")]})
    write(OUT / "reports/evidence_index.json", {"schema": "metric_bound_evidence.v2", "entries": evidence,
        "prerequisite_index": binding(OUT / "prerequisite/www_supplement_prerequisite_closeout.json"),
        "author_status": binding(OUT / "author_packet.json"), "status": "technical_closeout_author_recheck_pending"})
    validate_all()
    print(json.dumps({"E1": {m: r["complete_required"] for m, r in outputs["E1"]["per_method"].items()},
                      "author_recheck_pending": outputs["author_recheck_pending"], "report": str(OUT / "reports/experiment_report.md")}, ensure_ascii=False, indent=2))
    return outputs


def validate_all():
    manifest = read(OUT / "manifest_internal.json")
    for b in manifest["protected"] + [manifest["protocol"], manifest["review_reference"]]:
        if sha(ROOT / b["path"]) != b["sha256"]:
            raise ValueError(f"Protected artifact changed: {b['path']}")
    pending, complete, invalid = [], [], []
    for job in manifest["jobs"]:
        if sha(ROOT / job["packet_binding"]["path"]) != job["packet_binding"]["sha256"]:
            raise ValueError(f"Blinded packet changed: {job['packet_id']}")
        path = OUT / "reviews" / f"{job['packet_id']}.json"
        if not path.exists():
            pending.append(job["packet_id"])
            continue
        packet = read(ROOT / job["packet_binding"]["path"])
        try:
            response = read(path)
            issues = binding_issues(packet, response)
            if issues:
                write(OUT / "binding_issues" / f"{job['packet_id']}.json", issues)
            validate_review(packet, response)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            archive = OUT / "review_attempts" / job["packet_id"] / f"{sha(path)}.json"
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                archive.write_bytes(path.read_bytes())
            invalid.append({"packet_id": job["packet_id"], "error": str(exc),
                            "archived_response": binding(archive)})
            continue
        complete.append(job["packet_id"])
    result = {"complete": complete, "pending": pending, "invalid": invalid,
              "protected_unchanged": True}
    write(OUT / "validation.json", result)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "validate", "finalize"))
    args = parser.parse_args()
    {"prepare": prepare, "validate": validate_all, "finalize": finalize}[args.stage]()
