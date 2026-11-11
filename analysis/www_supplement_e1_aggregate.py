"""Aggregate E1 review batches through the unified scoped scoring entry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from .www_supplement_scope_score import score_scoped
except ImportError:
    from www_supplement_scope_score import score_scoped

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
REVIEW = S / "e1_content/review"
REFERENCE = S / "references/runner_reference_v1.json"
MAPPING = S / "e1_content/blinding_map_internal.json"
DEFAULT_OUT = S / "e1_content/e1_metrics_scoped.json"

BATCHES = ["judgements_E1_batchA.json", "judgements_E1_batchB.json",
           "judgements_E1_batchC.json", "judgements_E1_batchD.json"]


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _mapping_payload(payload: dict) -> dict:
    return payload.get("mapping", payload)


def _unique_index(rows: list[dict], id_field: str, label: str) -> dict[tuple, dict]:
    indexed = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"{label}[{index}] must be an object")
        key = (row.get("run"), row.get(id_field))
        if not all(isinstance(part, str) and part for part in key):
            raise ValueError(f"{label}[{index}] needs non-empty run and {id_field}")
        if key in indexed:
            raise ValueError(f"duplicate {label} key {key!r}")
        indexed[key] = row
    return indexed


def _apply_correction(item_rows: list[dict], relation_rows: list[dict],
                      correction: dict, correction_name: str) -> dict:
    by_item = _unique_index(item_rows, "item_id", "item_rows")
    by_relation = _unique_index(relation_rows, "relation_id", "relation_rows")
    correction_relations = _unique_index(
        correction.get("relation_rows", []), "relation_id", "correction.relation_rows")
    correction_conditions = _unique_index(
        correction.get("condition_rows", []), "item_id", "correction.condition_rows")

    applied_relations = 0
    unmatched_relations = 0
    for key, row in correction_relations.items():
        target = by_relation.get(key)
        if target is None:
            unmatched_relations += 1
            continue
        for field in ("semantic", "strict", "native_signature"):
            if field in row:
                target[field] = row[field]
        applied_relations += 1

    applied_conditions = 0
    unmatched_conditions = 0
    for key, row in correction_conditions.items():
        target = by_item.get(key)
        if target is None:
            unmatched_conditions += 1
            continue
        if "conditions_complete" in row:
            target["conditions_complete"] = row["conditions_complete"]
            applied_conditions += 1

    return {
        "file": correction_name,
        "relation_rows_applied": applied_relations,
        "relation_rows_unmatched": unmatched_relations,
        "condition_rows_applied": applied_conditions,
        "condition_rows_unmatched": unmatched_conditions,
    }


def aggregate_reviews(reference: dict, item_rows: list[dict], relation_rows: list[dict],
                      mapping: dict, metadata: dict | None = None) -> dict:
    """Public aggregation entry used by the CLI and offline tests."""
    result = score_scoped(reference, item_rows, relation_rows, mapping)
    result["schema"] = "maf_kg.www_supplement.e1_metrics.v1"
    if metadata:
        result.update(metadata)
    return result


def _format(metric: dict) -> str:
    value = "unknown" if metric.get("value") is None else f"{metric['value']:.3f}"
    bounds = ""
    if metric.get("lower_bound") is not None:
        bounds = f" [{metric['lower_bound']:.3f}, {metric['upper_bound']:.3f}]"
    return f"{metric['numerator']}/{metric['denominator']} {value}{bounds}"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--mapping", type=Path, default=MAPPING)
    parser.add_argument("--review-dir", type=Path, default=REVIEW)
    parser.add_argument("--batch", type=str, nargs="+", default=BATCHES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true",
                        help="replace the selected output file explicitly")
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        raise FileExistsError(f"refusing to overwrite existing output: {args.out}; use --force")

    reference = read(args.reference)
    mapping = _mapping_payload(read(args.mapping))
    item_rows: list[dict] = []
    relation_rows: list[dict] = []
    loaded = []
    for name in args.batch:
        path = args.review_dir / name
        if not path.exists():
            print(f"  missing batch: {name}")
            continue
        payload = read(path)
        batch_items = payload.get("item_rows", [])
        batch_relations = payload.get("relation_rows", [])
        item_rows.extend(batch_items)
        relation_rows.extend(batch_relations)
        loaded.append({"name": name, "item_rows": len(batch_items),
                       "relation_rows": len(batch_relations)})

    for batch in loaded:
        print(f"  loaded {batch['name']}: {batch['item_rows']} item rows, "
              f"{batch['relation_rows']} relation rows")

    # Validate base keys before building indices for the intentional correction overlay.
    _unique_index(item_rows, "item_id", "item_rows")
    _unique_index(relation_rows, "relation_id", "relation_rows")
    correction_path = args.review_dir / "judgements_E1_correction.json"
    correction_note = None
    if correction_path.exists():
        correction_note = _apply_correction(
            item_rows, relation_rows, read(correction_path), correction_path.name)
        print("  applied correction: "
              f"{correction_note['relation_rows_applied']} relation rows, "
              f"{correction_note['condition_rows_applied']} condition rows")

    output = aggregate_reviews(reference, item_rows, relation_rows, mapping, {
        "date": "2026-10-11",
        "denominator_rule": "each run is scored against its mapped input reference subset; "
                            "relations require both endpoints in that subset",
        "conditions_policy": "only required M Claims enter condition retention; explicit "
                             "missing is failure, while unjudged conditions remain unknown",
        "batches_loaded": [batch["name"] for batch in loaded],
        "correction_applied": correction_note,
        "caveats": [
            "technical repeats are not independent samples",
            "reviewer uncertainty is preserved as unknown with bounds",
            "strict reference-edge agreement is a reference-pattern diagnostic, not schema validity",
        ],
    })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    print("\n== per method (pooled over mapped input documents) ==")
    print(f"{'method':10s} {'complete':>34s} {'recognized':>34s} {'conditions':>34s} "
          f"{'rel full':>9s} {'rel part':>9s}")
    for method, values in output["per_method"].items():
        print(f"{method:10s} {_format(values['complete_required']):>34s} "
              f"{_format(values['recognized_required']):>34s} "
              f"{_format(values['condition_retention']):>34s} "
              f"{values['relation_full']:>9d} {values['relation_partial']:>9d}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
