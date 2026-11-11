"""Score E3 and prefix reviews through the unified input-scoped scorer."""

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
DEFAULT_OUT = S / "reports/e1_e3_metrics_scoped.json"


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mapping_payload(payload: dict) -> dict:
    return payload.get("mapping", payload)


def prefix_mapping(mapping: dict[str, dict], common_section: str = "ch4_s1") -> dict:
    """Keep every mapped prefix run while pointing each at the common pool."""
    return {
        run: {**meta, "reference_section": meta.get("reference_section", common_section)}
        for run, meta in mapping.items()
    }


def score(reference: dict, items: list[dict], rels: list[dict],
          mapping: dict[str, dict], label: str) -> dict:
    result = score_scoped(reference, items, rels, mapping)
    for row in result["per_run"].values():
        row["semantic"] = row["relation_semantic"]["counts"]
        row["strict"] = row["relation_strict"]["counts"]
        row["strict_conforms_over_comparable"] = row["relation_strict"][
            "conforms_over_comparable"]
    result["label"] = label
    return result


def _print_summary(label: str, metrics: dict) -> None:
    print(f"\n== {label} ==")
    for run, row in metrics["per_run"].items():
        condition = row["condition_retention"]
        condition_text = ("unknown" if condition["value"] is None
                          else f"{condition['value']:.6f}")
        print(f"  {run} ({row['reference_section']}): complete "
              f"{row['complete']['numerator']}/{row['complete']['denominator']} "
              f"({row['complete']['value']}) | conditions "
              f"{condition['numerator']}/{condition['denominator']} "
              f"({condition_text}, [{condition['lower_bound']}, {condition['upper_bound']}]) | "
              f"sem full {row['relation_semantic']['full']} "
              f"partial {row['relation_semantic']['partial']} | strict conforms "
              f"{row['relation_strict']['conforms_over_comparable']['numerator']}/"
              f"{row['relation_strict']['conforms_over_comparable']['denominator']}")
    pooled = metrics["pooled"]
    condition = pooled["condition_retention"]
    print(f"  POOLED: complete {pooled['complete_required']['numerator']}/"
          f"{pooled['complete_required']['denominator']} "
          f"({pooled['complete_required']['value']}) | conditions "
          f"{condition['numerator']}/{condition['denominator']} "
          f"({condition['value']}, [{condition['lower_bound']}, {condition['upper_bound']}])")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path,
                        default=S / "references/runner_reference_v1.json")
    parser.add_argument("--e3-review", type=Path,
                        default=S / "e3_memory/review/judgements_E3_rev3.json")
    parser.add_argument("--e3-mapping", type=Path,
                        default=S / "e3_memory/blinding_map_internal.json")
    parser.add_argument("--e1p-review", type=Path,
                        default=S / "e1_prefix/review/judgements_E1P_final.json")
    parser.add_argument("--e1p-mapping", type=Path,
                        default=S / "e1_prefix/blinding_map_internal.json")
    parser.add_argument("--common-pool", type=Path,
                        default=S / "e1_prefix/common_pool.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true",
                        help="replace the selected output file explicitly")
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        raise FileExistsError(f"refusing to overwrite existing output: {args.out}; use --force")

    reference = read(args.reference)
    output = {}
    if args.e3_review.exists():
        review = read(args.e3_review)
        mapping = mapping_payload(read(args.e3_mapping))
        label = "E3 memory ablation, rev3 review"
        output["E3"] = score(reference, review["item_rows"], review["relation_rows"],
                             mapping, label)
        _print_summary(label, output["E3"])

    if args.e1p_review.exists():
        review = read(args.e1p_review)
        pool = read(args.common_pool)
        common = {
            "nodes": pool["items"],
            "relations": pool["relations"],
            "cross_section_relations": [],
        }
        mapping = prefix_mapping(mapping_payload(read(args.e1p_mapping)))
        label = "E1p independent prefix review, common pool"
        output["E1p"] = score(common, review["item_rows"], review["relation_rows"],
                              mapping, label)
        _print_summary(label, output["E1p"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
