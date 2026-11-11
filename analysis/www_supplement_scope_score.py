"""Unified, input-scoped scoring for WWW supplement review judgements."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

try:
    from .www_supplement_e1_score import score as score_reference
except ImportError:
    from www_supplement_e1_score import score as score_reference


IMPLEMENTATION_PROVENANCE = {
    "model": "gpt-6-luna",
    "reasoning_effort": "max",
    "role": "scoring-chain subtask",
}

COVERAGE = ("full", "partial", "name_only", "missing", "not_applicable")
RECOGNIZED = ("full", "partial", "name_only")
TIERS = ("core", "detail")


def _check_unique_rows(rows: list[dict], id_field: str, label: str) -> dict[tuple, dict]:
    indexed = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"{label}[{index}] must be an object")
        run = row.get("run")
        row_id = row.get(id_field)
        if not isinstance(run, str) or not run:
            raise ValueError(f"{label}[{index}] has no non-empty run")
        if not isinstance(row_id, str) or not row_id:
            raise ValueError(f"{label}[{index}] has no non-empty {id_field}")
        key = (run, row_id)
        if key in indexed:
            raise ValueError(f"duplicate {label} key {key!r}")
        indexed[key] = row
    return indexed


def _metric(nodes: list[dict], item_rows: dict[str, dict], kinds: tuple[str, ...]) -> dict:
    denominator = len(nodes)
    numerator = 0
    unknown = 0
    for node in nodes:
        row = item_rows.get(node["id"])
        coverage = (row or {}).get("coverage")
        if coverage in kinds:
            numerator += 1
        if row is None or coverage not in COVERAGE:
            unknown += 1
    lower = round(numerator / denominator, 6) if denominator else None
    upper = round((numerator + unknown) / denominator, 6) if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": lower if denominator and not unknown else None,
        "lower_bound": lower,
        "upper_bound": upper,
        "unknown_count": unknown,
        "judged_count": denominator - unknown,
        "determinate": bool(denominator) and unknown == 0,
    }


def _condition_metric(claims: list[dict], item_rows: dict[str, dict]) -> dict:
    retained = 0
    missing = 0
    no_condition = 0
    unjudged_conditions = 0
    unjudged_review = 0
    per_claim = []
    for node in claims:
        row = item_rows.get(node["id"])
        coverage = (row or {}).get("coverage")
        conditions = (row or {}).get("conditions_complete")
        if conditions is not None and type(conditions) is not bool:
            raise ValueError("conditions_complete must be a boolean or null")

        delivered = coverage in RECOGNIZED
        if coverage == "missing":
            missing += 1
        elif delivered and conditions is True:
            retained += 1
        elif delivered and conditions is False:
            no_condition += 1
        elif delivered:
            unjudged_conditions += 1
        elif row is None or coverage not in COVERAGE:
            unjudged_review += 1

        per_claim.append({
            "item_id": node["id"],
            "name": node.get("name"),
            "coverage": coverage,
            "conditions_complete": conditions,
        })

    denominator = len(claims)
    unknown = unjudged_conditions + unjudged_review
    lower = round(retained / denominator, 6) if denominator else None
    upper = round((retained + unknown) / denominator, 6) if denominator else None
    return {
        "numerator": retained,
        "denominator": denominator,
        "value": lower if denominator and not unknown else None,
        "lower_bound": lower,
        "upper_bound": upper,
        "complete": bool(denominator) and unknown == 0,
        "unknown_count": unknown,
        "claims_with_unjudged_conditions": unjudged_conditions,
        "claims_unjudged_review": unjudged_review,
        "claims_not_delivered": missing,
        "claims_delivered_without_complete_conditions": no_condition,
        "rule": "a required Claim explicitly judged missing counts as conditions not retained",
        "per_claim": per_claim,
    }


def _bounded_ratio(numerator: int, denominator: int, unknown: int) -> dict:
    lower = round(numerator / denominator, 6) if denominator else None
    upper = round((numerator + unknown) / denominator, 6) if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": lower if denominator and not unknown else None,
        "lower_bound": lower,
        "upper_bound": upper,
        "unknown_count": unknown,
        "determinate": bool(denominator) and unknown == 0,
    }


def _relation_metrics(raw: dict, total: int) -> tuple[dict, dict]:
    semantic = dict(raw["relation_semantic"])
    semantic_counts = semantic["counts"]
    semantic["judged"] = total - semantic_counts.get("unjudged", 0)
    semantic["unjudged"] = semantic_counts.get("unjudged", 0)

    strict = dict(raw["relation_strict"])
    strict_counts = strict["counts"]
    unknown_all = strict_counts.get("uncertain", 0) + strict_counts.get("unjudged", 0)
    strict["conforms_over_all_relations"] = _bounded_ratio(
        strict_counts.get("conforms", 0), total, unknown_all)
    comparable = strict["comparable_denominator"]
    unknown_comparable = strict_counts.get("uncertain", 0)
    strict["conforms_over_comparable"] = _bounded_ratio(
        strict_counts.get("conforms", 0), comparable, unknown_comparable)
    strict["unknown_outside_comparable"] = strict_counts.get("unjudged", 0)
    return semantic, strict


def _scope_table(nodes: list[dict], item_rows: dict[str, dict]) -> dict:
    complete = _metric(nodes, item_rows, ("full",))
    recognized = _metric(nodes, item_rows, RECOGNIZED)
    by_tier = {}
    for tier in TIERS:
        pool = [node for node in nodes if node.get("tier") == tier]
        by_tier[tier] = {
            "count": len(pool),
            "complete": _metric(pool, item_rows, ("full",)),
            "recognized": _metric(pool, item_rows, RECOGNIZED),
        }
    return {
        "count": len(nodes),
        "complete": complete,
        "recognized": recognized,
        "by_tier": by_tier,
        "uncategorized_tier_count": sum(node.get("tier") not in TIERS for node in nodes),
    }


def _sum_counts(rows: list[dict], field: str) -> dict:
    counts = Counter()
    for row in rows:
        counts.update(row.get(field, {}).get("counts", {}))
    return dict(counts)


def _pool_content(rows: list[dict], field: str) -> dict:
    metrics = [row[field] for row in rows]
    numerator = sum(metric["numerator"] for metric in metrics)
    denominator = sum(metric["denominator"] for metric in metrics)
    unknown = sum(metric.get("unknown_count", 0) for metric in metrics)
    result = _bounded_ratio(numerator, denominator, unknown)
    result["judged_count"] = denominator - unknown
    return result


def _pool_conditions(rows: list[dict]) -> dict:
    metrics = [row["condition_retention"] for row in rows]
    numerator = sum(metric["numerator"] for metric in metrics)
    denominator = sum(metric["denominator"] for metric in metrics)
    unknown = sum(metric.get("unknown_count", 0) for metric in metrics)
    result = _bounded_ratio(numerator, denominator, unknown)
    result.update({
        "complete": bool(denominator) and unknown == 0,
        "claims_with_unjudged_conditions": sum(
            metric["claims_with_unjudged_conditions"] for metric in metrics),
        "claims_unjudged_review": sum(metric["claims_unjudged_review"] for metric in metrics),
        "claims_not_delivered": sum(metric["claims_not_delivered"] for metric in metrics),
        "claims_delivered_without_complete_conditions": sum(
            metric["claims_delivered_without_complete_conditions"] for metric in metrics),
        "rule": "a required Claim explicitly judged missing counts as conditions not retained",
    })
    return result


def _pool_semantic(rows: list[dict]) -> dict:
    counts = _sum_counts(rows, "relation_semantic")
    denominator = sum(row["relation_semantic"]["denominator"] for row in rows)
    return {
        "counts": counts,
        "full": counts.get("full", 0),
        "partial": counts.get("partial", 0),
        "full_plus_partial": counts.get("full", 0) + counts.get("partial", 0),
        "denominator": denominator,
        "judged": denominator - counts.get("unjudged", 0),
        "unjudged": counts.get("unjudged", 0),
        "note": "full and partial are reported separately; their sum is never called correctness",
    }


def _pool_strict(rows: list[dict]) -> dict:
    counts = _sum_counts(rows, "relation_strict")
    total = sum(row["relation_strict"]["conforms_over_all_relations"]["denominator"]
                for row in rows)
    comparable = sum(row["relation_strict"]["conforms_over_comparable"]["denominator"]
                     for row in rows)
    unknown_all = counts.get("uncertain", 0) + counts.get("unjudged", 0)
    return {
        "counts": counts,
        "conforms_over_all_relations": _bounded_ratio(counts.get("conforms", 0), total, unknown_all),
        "conforms_over_comparable": _bounded_ratio(
            counts.get("conforms", 0), comparable, counts.get("uncertain", 0)),
        "comparable_denominator": comparable,
        "unknown_outside_comparable": counts.get("unjudged", 0),
        "rule": "both denominators are printed; comparable excludes not_comparable, absent and unjudged",
    }


def _pool_scope(rows: list[dict], status: str) -> dict:
    scopes = [row["reference_by_status"][status] for row in rows]
    complete = _pool_content(scopes, "complete")
    recognized = _pool_content(scopes, "recognized")
    by_tier = {}
    for tier in TIERS:
        tier_scopes = [scope["by_tier"][tier] for scope in scopes]
        by_tier[tier] = {
            "count": sum(scope["count"] for scope in tier_scopes),
            "complete": _pool_content(tier_scopes, "complete"),
            "recognized": _pool_content(tier_scopes, "recognized"),
        }
    return {
        "count": sum(scope["count"] for scope in scopes),
        "complete": complete,
        "recognized": recognized,
        "by_tier": by_tier,
        "uncategorized_tier_count": sum(scope["uncategorized_tier_count"] for scope in scopes),
    }


def _aggregate_group(rows: list[dict]) -> dict:
    result = {
        "runs": len(rows),
        "complete_required": _pool_content(rows, "complete"),
        "recognized_required": _pool_content(rows, "recognized"),
        "complete_core": _pool_content(rows, "complete_core"),
        "complete_detail": _pool_content(rows, "complete_detail"),
        "recognized_core": _pool_content(rows, "recognized_core"),
        "recognized_detail": _pool_content(rows, "recognized_detail"),
        "condition_retention": _pool_conditions(rows),
        "relation_semantic": _pool_semantic(rows),
        "relation_strict": _pool_strict(rows),
        "relation_full": sum(row["relation_semantic"]["full"] for row in rows),
        "relation_partial": sum(row["relation_semantic"]["partial"] for row in rows),
        "relation_denominator": sum(row["relation_semantic"]["denominator"] for row in rows),
        "strict_conforms_over_comparable": _pool_strict(rows)["conforms_over_comparable"],
        "per_run_complete": [
            f"{row['complete']['numerator']}/{row['complete']['denominator']}" for row in rows
        ],
        "reference_by_status": {
            status: _pool_scope(rows, status) for status in ("M", "O")
        },
        "native_signature": {},
    }
    signature_counts = Counter()
    for row in rows:
        signature_counts.update(row["native_signature"])
    result["native_signature"] = dict(signature_counts)
    result["optionals"] = result["reference_by_status"]["O"]
    return result


def _macro_metric(metrics: list[dict]) -> dict:
    eligible = [metric for metric in metrics if metric.get("denominator", 0) > 0]
    if not eligible:
        return {
            "run_count": 0,
            "runs_without_scope": len(metrics),
            "unknown_runs": 0,
            "value": None,
            "lower_bound": None,
            "upper_bound": None,
            "rule": "equal-weight mean over runs with a non-empty denominator",
        }
    lowers = [metric.get("lower_bound", metric.get("value")) for metric in eligible]
    uppers = [metric.get("upper_bound", metric.get("value")) for metric in eligible]
    values = [metric.get("value") for metric in eligible]
    unknown_runs = sum(value is None for value in values)
    mean = lambda values: round(sum(values) / len(values), 6)
    return {
        "run_count": len(eligible),
        "runs_without_scope": len(metrics) - len(eligible),
        "unknown_runs": unknown_runs,
        "determinate_runs": len(eligible) - unknown_runs,
        "unknown_count": sum(metric.get("unknown_count", 0) for metric in eligible),
        "value": mean(values) if not unknown_runs else None,
        "lower_bound": mean(lowers),
        "upper_bound": mean(uppers),
        "rule": "equal-weight mean over runs with a non-empty denominator",
    }


def _macro(rows: list[dict]) -> dict:
    names = ("complete", "recognized", "complete_core", "complete_detail",
             "recognized_core", "recognized_detail", "condition_retention")
    result = {name: _macro_metric([row[name] for row in rows]) for name in names}
    for label, category in (("relation_semantic_full", "full"),
                            ("relation_semantic_partial", "partial")):
        metrics = []
        for row in rows:
            semantic = row["relation_semantic"]
            counts = semantic["counts"]
            metrics.append(_bounded_ratio(
                counts.get(category, 0), semantic["denominator"],
                counts.get("uncertain", 0) + counts.get("unjudged", 0)))
        result[label] = _macro_metric(metrics)
    result["strict_conforms_over_all_relations"] = _macro_metric([
        row["relation_strict"]["conforms_over_all_relations"] for row in rows])
    result["strict_conforms_over_comparable"] = _macro_metric([
        row["relation_strict"]["conforms_over_comparable"] for row in rows])
    result["optionals"] = {
        "complete": _macro_metric([row["optionals"]["complete"] for row in rows]),
        "recognized": _macro_metric([row["optionals"]["recognized"] for row in rows]),
        "complete_core": _macro_metric([
            row["optionals"]["by_tier"]["core"]["complete"] for row in rows]),
        "complete_detail": _macro_metric([
            row["optionals"]["by_tier"]["detail"]["complete"] for row in rows]),
        "recognized_core": _macro_metric([
            row["optionals"]["by_tier"]["core"]["recognized"] for row in rows]),
        "recognized_detail": _macro_metric([
            row["optionals"]["by_tier"]["detail"]["recognized"] for row in rows]),
    }
    return result


def _method_document_summaries(per_run: dict[str, dict]) -> tuple[dict, dict]:
    by_method: dict[str, list[dict]] = defaultdict(list)
    by_document: dict[str, list[dict]] = defaultdict(list)
    for row in per_run.values():
        by_method[row["method"]].append(row)
        by_document[row["reference_section"]].append(row)
    per_method = {}
    for method, rows in sorted(by_method.items()):
        summary = _aggregate_group(rows)
        summary["method"] = method
        summary["macro"] = _macro(rows)
        per_method[method] = summary
    per_document = {}
    for section, rows in sorted(by_document.items()):
        summary = _aggregate_group(rows)
        summary["reference_section"] = section
        summary["methods"] = sorted({row["method"] for row in rows})
        summary["per_method"] = {
            method: _aggregate_group([row for row in rows if row["method"] == method])
            for method in summary["methods"]
        }
        per_document[section] = summary
    return per_method, per_document


def score_scoped(reference: dict, item_rows: list[dict], relation_rows: list[dict],
                 mapping: dict[str, dict]) -> dict:
    """Score mapped runs against only their input reference section.

    `section` is the run metadata; `reference_section` optionally names the
    section id used in the supplied reference. Relations enter the pool only
    when both endpoint ids occur in that run's scoped node pool.
    """
    if not isinstance(reference, dict) or not isinstance(reference.get("nodes"), list):
        raise TypeError("reference must contain a nodes list")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("mapping must be a non-empty {run: metadata} object")
    if any(not isinstance(run, str) or not run for run in mapping):
        raise ValueError("mapping run keys must be non-empty strings")

    nodes = reference["nodes"]
    relations = list(reference.get("relations", [])) + list(
        reference.get("cross_section_relations", []))
    node_by_id = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ValueError(f"reference.nodes[{index}] has no string id")
        if node["id"] in node_by_id:
            raise ValueError(f"duplicate reference node id {node['id']!r}")
        node_by_id[node["id"]] = node
    relation_by_id = {}
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict) or not isinstance(relation.get("id"), str):
            raise ValueError(f"reference relations[{index}] has no string id")
        if relation["id"] in relation_by_id:
            raise ValueError(f"duplicate reference relation id {relation['id']!r}")
        relation_by_id[relation["id"]] = relation

    item_index = _check_unique_rows(item_rows, "item_id", "item_rows")
    relation_index = _check_unique_rows(relation_rows, "relation_id", "relation_rows")
    for (run, item_id) in item_index:
        if run not in mapping:
            raise ValueError(f"item_rows contains unmapped run {run!r}")
        if item_id not in node_by_id:
            raise ValueError(f"item_rows contains unknown item {item_id!r} for {run!r}")
    for (run, relation_id) in relation_index:
        if run not in mapping:
            raise ValueError(f"relation_rows contains unmapped run {run!r}")
        if relation_id not in relation_by_id:
            raise ValueError(f"relation_rows contains unknown relation {relation_id!r} for {run!r}")

    per_run = {}
    for run, meta in sorted(mapping.items()):
        if not isinstance(run, str) or not run or not isinstance(meta, dict):
            raise ValueError("each mapping entry must be {non-empty run: metadata object}")
        if not isinstance(meta.get("method"), str) or not meta["method"]:
            raise ValueError(f"mapping[{run!r}].method must be a non-empty string")
        if not isinstance(meta.get("section"), str) or not meta["section"]:
            raise ValueError(f"mapping[{run!r}].section must be a non-empty string")
        reference_section = meta.get("reference_section", meta["section"])
        if not isinstance(reference_section, str) or not reference_section:
            raise ValueError(f"mapping[{run!r}].reference_section must be a non-empty string")
        scoped_nodes = [node for node in nodes if node.get("section") == reference_section]
        if not scoped_nodes:
            raise ValueError(f"mapping[{run!r}] selects an empty reference section "
                             f"{reference_section!r}")
        scoped_ids = {node["id"] for node in scoped_nodes}
        scoped_relations = [
            relation for relation in relations
            if relation.get("start") in scoped_ids and relation.get("end") in scoped_ids
        ]
        scoped_relation_ids = {relation["id"] for relation in scoped_relations}
        run_items_all = {item_id: row for (row_run, item_id), row in item_index.items()
                         if row_run == run}
        run_rels_all = {relation_id: row for (row_run, relation_id), row in relation_index.items()
                        if row_run == run}
        scoped_item_rows = {item_id: row for item_id, row in run_items_all.items()
                            if item_id in scoped_ids}
        scoped_relation_rows = {relation_id: row for relation_id, row in run_rels_all.items()
                                if relation_id in scoped_relation_ids}

        # The base scorer derives run ids from judgement rows. A harmless
        # non-reference sentinel keeps fully unreviewed mapped runs visible.
        scorer_items = list(scoped_item_rows.values())
        scorer_rels = list(scoped_relation_rows.values())
        if not scorer_items and not scorer_rels:
            scorer_items = [{"run": run, "item_id": "__scope_presence__"}]
        base = score_reference(
            {"nodes": scoped_nodes, "relations": scoped_relations},
            scorer_items,
            scorer_rels,
        )["runs"][run]

        required = [node for node in scoped_nodes if node.get("status") == "M"]
        optional = [node for node in scoped_nodes if node.get("status") == "O"]
        required_scope = _scope_table(required, scoped_item_rows)
        optional_scope = _scope_table(optional, scoped_item_rows)
        all_status = {
            "M": required_scope,
            "O": optional_scope,
        }
        condition_claims = [node for node in required if node.get("type") == "Claim"]
        condition = _condition_metric(condition_claims, scoped_item_rows)
        semantic, strict = _relation_metrics(base, len(scoped_relations))

        signature = Counter()
        for relation in scoped_relations:
            judgement = scoped_relation_rows.get(relation["id"])
            signature[str(judgement.get("native_signature")) if judgement and
                      "native_signature" in judgement else "unjudged"] += 1

        per_run[run] = {
            "method": meta["method"],
            "section": meta["section"],
            "reference_section": reference_section,
            "repeat": meta.get("repeat"),
            "pool_items": len(scoped_nodes),
            "pool_relations": len(scoped_relations),
            "complete": required_scope["complete"],
            "recognized": required_scope["recognized"],
            "complete_required": required_scope["complete"],
            "recognized_required": required_scope["recognized"],
            "complete_core": required_scope["by_tier"]["core"]["complete"],
            "complete_detail": required_scope["by_tier"]["detail"]["complete"],
            "recognized_core": required_scope["by_tier"]["core"]["recognized"],
            "recognized_detail": required_scope["by_tier"]["detail"]["recognized"],
            "condition_retention": condition,
            "relation_semantic": semantic,
            "relation_strict": strict,
            "native_signature": dict(signature),
            "native_signature_validity": base["native_signature_validity"],
            "reference_by_status": all_status,
            "optionals": optional_scope,
            "scope_diagnostics": {
                "out_of_scope_item_rows_ignored": len(run_items_all) - len(scoped_item_rows),
                "out_of_scope_relation_rows_ignored": len(run_rels_all) - len(scoped_relation_rows),
                "items_without_review": required_scope["complete"]["unknown_count"]
                + optional_scope["complete"]["unknown_count"],
                "relations_without_review": semantic["unjudged"],
            },
        }

    per_method, per_document = _method_document_summaries(per_run)
    pooled = _aggregate_group(list(per_run.values()))
    macro = _macro(list(per_run.values()))
    optional_table = {
        "per_run": {run: row["optionals"] for run, row in per_run.items()},
        "per_method": {method: row["optionals"] for method, row in per_method.items()},
        "per_document": {section: row["optionals"] for section, row in per_document.items()},
        "macro": macro["optionals"],
        "note": "O items are reported separately and never enter required M denominators",
    }
    return {
        "schema": "maf_kg.www_supplement.scoped_metrics.v1",
        "implementation_provenance": IMPLEMENTATION_PROVENANCE,
        "scope_rule": "each run uses nodes in its reference_section and only relations whose two endpoints are in that node pool",
        "unknown_rule": "unreviewed rows and null review judgements remain unknown, with bounds; explicit missing is a negative judgement",
        "reference": {"nodes": len(nodes), "relations": len(relations)},
        "per_run": per_run,
        "per_method": per_method,
        "per_document": per_document,
        "pooled": pooled,
        "macro": macro,
        "optionals": optional_table,
    }
