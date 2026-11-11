#!/usr/bin/env python
"""S2 aggregation: common semantic coverage and strict structural conformance, kept separate.

Reads the eight per-graph judgement files written under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review/ and produces one
machine table plus one readable table.  No model is called and no judgement is edited; the only
derived field is ``strict_normalised``, which records ``absent`` when the semantic column says the
relation was not covered at all (the frozen protocol has no strict value for "no native edge").

Usage:
    python analysis/final_closeout_s2_metrics.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review"
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"

SEMANTIC_VALUES = ["full", "partial", "mentioned_only", "not_covered", "uncertain"]
STRICT_VALUES = [
    "conforms",
    "direction_mismatch",
    "type_mismatch",
    "endpoint_identity_mismatch",
    "not_comparable",
    "uncertain",
]

GRAPHS = [
    ("P06", "maf", 1),
    ("P05", "maf", 2),
    ("P08", "direct", 1),
    ("P10", "direct", 2),
    ("Q04", "edc", 1),
    ("Q02", "edc", 2),
    ("V01", "itext2kg", 1),
    ("V02", "itext2kg", 2),
]

METHOD_ORDER = ["direct", "maf", "edc", "itext2kg"]

PACKET_DIRS = {
    "P06": "review_merged",
    "P05": "review_merged",
    "P08": "review_merged",
    "P10": "review_merged",
    "Q04": "review_v3",
    "Q02": "review_v3",
    "V01": "review_v4",
    "V02": "review_v4",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_judgements(out_dir: Path = OUT_DIR) -> dict[str, dict]:
    loaded = {}
    for label, method, repeat in GRAPHS:
        path = out_dir / f"judgements_{label}.json"
        if not path.is_file():
            continue
        data = read_json(path)
        rows = data["judgements"]
        if len(rows) != 47:
            raise ValueError(f"{label}: expected 47 judgements, found {len(rows)}")
        seen = [row["reference_id"] for row in rows]
        if len(set(seen)) != 47:
            raise ValueError(f"{label}: duplicate reference_id in judgements")
        loaded[label] = data
    return loaded


def validate_judgements(out_dir: Path = OUT_DIR) -> dict:
    """Check binding and enumeration legality without judging the semantics."""
    problems: list[str] = []
    checked = 0
    for label, method, repeat in GRAPHS:
        judgement_path = out_dir / f"judgements_{label}.json"
        worksheet_path = out_dir / f"worksheet_{label}.json"
        if not judgement_path.is_file():
            continue
        if not worksheet_path.is_file():
            problems.append(f"{label}: worksheet missing, cannot verify item binding")
            continue
        data = read_json(judgement_path)
        worksheet = read_json(worksheet_path)
        allowed_refs = {row["reference_id"] for row in worksheet["rows"]}
        packet = read_json(MERGED / PACKET_DIRS[label] / f"{label}.json")
        allowed_items = {item["item_id"] for item in packet["items"]}
        rows = data["judgements"]
        if len(rows) != len(allowed_refs):
            problems.append(f"{label}: {len(rows)} judgements for {len(allowed_refs)} reference rows")
        seen_refs = [row.get("reference_id") for row in rows]
        duplicates = sorted({ref for ref in seen_refs if seen_refs.count(ref) > 1})
        if duplicates:
            problems.append(f"{label}: duplicate reference_id in judgements: {duplicates[:5]}")
        missing_refs = sorted(allowed_refs - set(seen_refs))
        if missing_refs:
            problems.append(f"{label}: no judgement for reference_id: {missing_refs[:5]}")
        for row in rows:
            checked += 1
            ref = row.get("reference_id")
            if ref not in allowed_refs:
                problems.append(f"{label}/{ref}: reference_id not in this graph's reference set")
            if row.get("semantic") not in SEMANTIC_VALUES:
                problems.append(f"{label}/{ref}: invalid semantic value {row.get('semantic')!r}")
            if row.get("strict") not in STRICT_VALUES:
                problems.append(f"{label}/{ref}: invalid strict value {row.get('strict')!r}")
            for item_id in row.get("native_item_ids", []):
                if item_id not in allowed_items:
                    problems.append(f"{label}/{ref}: illegal native item_id {item_id!r}")
            if not row.get("verbatim_fragment"):
                problems.append(f"{label}/{ref}: empty verbatim_fragment")
        counted = data.get("counts")
        if isinstance(counted, dict):
            actual_semantic = count_values(rows, "semantic", SEMANTIC_VALUES)
            if counted.get("semantic") != actual_semantic:
                problems.append(f"{label}: counts.semantic does not match the judgements")
            actual_strict = count_values(rows, "strict", STRICT_VALUES)
            if counted.get("strict") != actual_strict:
                problems.append(f"{label}: counts.strict does not match the judgements")
    return {
        "graphs_checked": sorted(
            label for label, _, _ in GRAPHS if (out_dir / f"judgements_{label}.json").is_file()
        ),
        "judgements_checked": checked,
        "problems": problems,
        "ok": not problems,
    }


def normalise_strict(row: dict) -> str:
    """Record 'absent' when nothing carries the relation, without editing the raw verdict.

    ``not_comparable`` is preserved: when the representation has no node layer at all, the strict
    verdict is structural non-comparability, not a missing edge, and overwriting it would be read as
    a structural gap that does not exist.
    """
    if row.get("semantic") == "not_covered" and row.get("strict") != "not_comparable":
        return "absent"
    return row.get("strict")


def count_values(rows: list[dict], key: str, vocabulary: list[str]) -> dict:
    counts = {value: 0 for value in vocabulary}
    for row in rows:
        value = row.get(key)
        if value in counts:
            counts[value] += 1
        else:
            counts[value] = counts.get(value, 0) + 1
    return counts


def rates(counts: dict, total: int) -> dict:
    return {key: (value / total if total else None) for key, value in counts.items()}


def cross_run_agreement(loaded: dict[str, dict]) -> dict:
    """Agreement between the two repeats of the same method, on the same reference relation IDs.

    Important limitation, raised by the acceptance review: the two repeats are *different sampled
    graphs*, not the same graph judged twice.  The agreement figure therefore mixes variation from
    generation with variation from judging, and it must not be read as a judging-noise bound.  It
    describes how stable the "covered / not covered" state is across two runs of the same method.
    """
    pairs = {"direct": ("P08", "P10"), "maf": ("P06", "P05"), "edc": ("Q04", "Q02"),
             "itext2kg": ("V01", "V02")}
    out = {}
    for method, (left_label, right_label) in pairs.items():
        if left_label not in loaded or right_label not in loaded:
            continue
        left = {row["reference_id"]: row for row in loaded[left_label]["judgements"]}
        right = {row["reference_id"]: row for row in loaded[right_label]["judgements"]}
        shared = sorted(set(left) & set(right))
        semantic_same = [ref for ref in shared if left[ref]["semantic"] == right[ref]["semantic"]]
        semantic_binary_same = [
            ref
            for ref in shared
            if (left[ref]["semantic"] in {"full", "partial"})
            == (right[ref]["semantic"] in {"full", "partial"})
        ]
        strict_same = [ref for ref in shared if left[ref]["strict"] == right[ref]["strict"]]
        strict_norm_same = [
            ref
            for ref in shared
            if normalise_strict(left[ref]) == normalise_strict(right[ref])
        ]
        differing = [
            {
                "reference_id": ref,
                "left": {"semantic": left[ref]["semantic"], "strict": left[ref]["strict"]},
                "right": {"semantic": right[ref]["semantic"], "strict": right[ref]["strict"]},
            }
            for ref in shared
            if left[ref]["semantic"] != right[ref]["semantic"]
            or left[ref]["strict"] != right[ref]["strict"]
        ]
        out[method] = {
            "repeats": [left_label, right_label],
            "shared_reference_relations": len(shared),
            "semantic_exact_agreement": len(semantic_same) / len(shared) if shared else None,
            "covered_vs_not_covered_agreement": (
                len(semantic_binary_same) / len(shared) if shared else None
            ),
            "strict_exact_agreement": len(strict_same) / len(shared) if shared else None,
            "strict_normalised_agreement": (
                len(strict_norm_same) / len(shared) if shared else None
            ),
            "differing_relations": differing,
            "what_this_is": (
                "The stability of the covered/not-covered state across two independent runs of the "
                "same method, compared on the same frozen reference relation IDs."
            ),
            "what_this_is_not": (
                "Not a judging-noise bound and not inter-annotator agreement: the two runs produced "
                "different graphs, so generation variation and judging variation are mixed here, and "
                "neither review agent is a human annotator."
            ),
            "consequence": (
                "It cannot be used to argue that a between-method difference falls inside judging "
                "noise. Between-method differences in the semantic column are simply not established "
                "as a ranking; the structural facts (not_comparable counts, strict conforms) are "
                "unaffected."
            ),
        }
    return out


def summarise(rows: list[dict]) -> dict:
    total = len(rows)
    semantic = count_values(rows, "semantic", SEMANTIC_VALUES)
    strict_raw = count_values(rows, "strict", STRICT_VALUES)
    strict_norm = count_values(rows, "strict_normalised", STRICT_VALUES + ["absent"])
    comparable = total - strict_norm.get("not_comparable", 0) - strict_norm.get("absent", 0)
    return {
        "relations": total,
        "semantic_counts": semantic,
        "semantic_rates": rates(semantic, total),
        "semantic_covered_full_or_partial": semantic["full"] + semantic["partial"],
        "semantic_covered_rate": (semantic["full"] + semantic["partial"]) / total if total else None,
        "strict_counts_raw": strict_raw,
        "strict_counts_normalised": strict_norm,
        "strict_rates_normalised": rates(strict_norm, total),
        "comparable_denominator": comparable,
        "strict_conforms_over_comparable": (
            strict_norm["conforms"] / comparable if comparable else None
        ),
    }


def main() -> int:
    loaded = load_judgements()
    validation = validate_judgements()
    if not loaded:
        print("no judgement files found yet")
        print(f"validation: {validation}")
        return 0

    per_graph = {}
    for label, method, repeat in GRAPHS:
        if label not in loaded:
            continue
        rows = []
        for row in loaded[label]["judgements"]:
            enriched = dict(row)
            enriched["strict_normalised"] = normalise_strict(row)
            rows.append(enriched)
        per_graph[label] = {"method": method, "repeat": repeat, **summarise(rows)}

    per_method = {}
    for method in METHOD_ORDER:
        rows = []
        for label, m, repeat in GRAPHS:
            if m != method or label not in loaded:
                continue
            for row in loaded[label]["judgements"]:
                enriched = dict(row)
                enriched["strict_normalised"] = normalise_strict(row)
                rows.append(enriched)
        if rows:
            per_method[method] = summarise(rows)

    payload = {
        "schema": "dsh.final_closeout.s2_metrics.v1",
        "protocol": "relation_semantic_review/protocol.md (v1)",
        "no_new_model_calls": True,
        "derived_field_note": (
            "strict_normalised adds 'absent' when semantic == not_covered, because the frozen "
            "protocol has no strict value for 'no native edge carries this relation'. Raw verdicts "
            "are unchanged and both columns are reported."
        ),
        "graphs_loaded": sorted(loaded),
        "graphs_missing": [label for label, _, _ in GRAPHS if label not in loaded],
        "validation": validation,
        "per_graph": per_graph,
        "per_method": per_method,
        "cross_run_coverage_agreement": cross_run_agreement(loaded),
    }
    (OUT_DIR / "metrics_semantic_vs_strict.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# 关系评价：共同语义覆盖 与 严格结构符合（两列独立）",
        "",
        "判据见 `protocol.md`（v1）。两列不合成总分；`not_comparable` 与 `absent` 不进严格列的可比分母。",
        "",
        "## 按方法（两轮合并，各 94 条关系）",
        "",
        "| 方法 | 语义 full | 语义 partial | full+partial | mentioned_only | not_covered | 严格 conforms | 严格可比分母 | conforms/可比 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for method in METHOD_ORDER:
        if method not in per_method:
            continue
        block = per_method[method]
        semantic = block["semantic_counts"]
        strict = block["strict_counts_normalised"]
        total = block["relations"]

        def pct(value: int, denominator: int = total) -> str:
            return f"{value} ({value / denominator:.3f})" if denominator else str(value)

        ratio = block["strict_conforms_over_comparable"]
        ratio_text = f"{ratio:.3f}" if ratio is not None else "n/a"
        lines.append(
            f"| {method} | {pct(semantic['full'])} | {pct(semantic['partial'])} "
            f"| {pct(block['semantic_covered_full_or_partial'])} | {pct(semantic['mentioned_only'])} "
            f"| {pct(semantic['not_covered'])} | {pct(strict['conforms'])} "
            f"| {block['comparable_denominator']} | {ratio_text} |"
        )
    lines += [
        "",
        "## 严格列完整分布（归一化后，含不可比与缺失）",
        "",
        "| 方法 | conforms | direction_mismatch | type_mismatch | endpoint_identity_mismatch | not_comparable | absent |",
        "|---|---|---|---|---|---|---|",
    ]
    for method in METHOD_ORDER:
        if method not in per_method:
            continue
        strict = per_method[method]["strict_counts_normalised"]
        lines.append(
            f"| {method} | {strict.get('conforms', 0)} | {strict.get('direction_mismatch', 0)} "
            f"| {strict.get('type_mismatch', 0)} | {strict.get('endpoint_identity_mismatch', 0)} "
            f"| {strict.get('not_comparable', 0)} | {strict.get('absent', 0)} |"
        )
    lines += [
        "",
        "## 按图",
        "",
        "| 图 | 方法 | 轮次 | 语义 full+partial | 严格 conforms | 可比分母 |",
        "|---|---|---|---|---|---|",
    ]
    for label, method, repeat in GRAPHS:
        if label not in per_graph:
            continue
        block = per_graph[label]
        lines.append(
            f"| {label} | {method} | r{repeat} | {block['semantic_covered_full_or_partial']}/47 "
            f"| {block['strict_counts_normalised'].get('conforms', 0)}/47 "
            f"| {block['comparable_denominator']} |"
        )
    lines += [
        "",
        "## 跨运行覆盖状态一致率（**不是**评审噪声上界）",
        "",
        "同一方法的两次重复是**两次独立采样得到的不同图**，不是同一张图判两次。因此下表同时混有",
        "生成差异与判定差异，**不得**读作判定噪声，也**不得**用来论证方法间差异落在噪声内。",
        "",
        "| 方法 | 共同参考关系 | 语义完全一致 | 覆盖/未覆盖二值一致 | 严格列完全一致 | 不一致条数 |",
        "|---|---|---|---|---|---|",
    ]
    for method, block in (payload.get("cross_run_coverage_agreement") or {}).items():
        lines.append(
            f"| {method} | {block['shared_reference_relations']} "
            f"| {block['semantic_exact_agreement']:.3f} "
            f"| {block['covered_vs_not_covered_agreement']:.3f} "
            f"| {block['strict_exact_agreement']:.3f} "
            f"| {len(block['differing_relations'])} |"
        )
    lines += [
        "",
        "**由此得到的限制**：语义列上方法之间的差异**没有被建立为排名**——它既受上表的跨运行变化影响，",
        "也受已发现的判据分歧影响（见 `rule_changes.md` §8）。可以报告的只有结构性事实：",
        "EDC 的严格列 94/94 不可比、iText 的严格 `conforms` 为 0，以及「两列必须分离」这一结论本身。",
        "",
        "## 读法",
        "",
        "1. **语义列与严格列必须分开引用**：某方法语义覆盖高而严格符合低，说明它表达了数学内容但不符合目标",
        "   schema（或反之），这两种情况在旧口径下被混成一个数字。",
        "2. **EDC 的严格列大多为 `not_comparable`**：它的原生表示没有节点身份，结构上无法与目标 schema 比较。",
        "   这不是零分，旧口径把它压到接近 0 是口径问题。",
        "3. `absent` 是聚合阶段补的取值，只表示「没有任何原生边承载该关系」，不改变原始判定。",
        "4. 两列都受判定差异影响；两列之间的**结构性**差异（不可比 vs 可比）不受影响。",
    ]
    (OUT_DIR / "metrics_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"loaded graphs: {sorted(loaded)}")
    print(f"missing graphs: {payload['graphs_missing']}")
    print(f"validation: checked={validation['judgements_checked']} ok={validation['ok']} "
          f"problems={len(validation['problems'])}")
    for problem in validation["problems"][:10]:
        print(f"   ! {problem}")
    for method in METHOD_ORDER:
        if method not in per_method:
            continue
        block = per_method[method]
        print(f"  {method:9s} semantic full+partial={block['semantic_covered_full_or_partial']}/{block['relations']} "
              f"strict conforms={block['strict_counts_normalised'].get('conforms', 0)} "
              f"not_comparable={block['strict_counts_normalised'].get('not_comparable', 0)} "
              f"absent={block['strict_counts_normalised'].get('absent', 0)}")
    print(f"wrote {(OUT_DIR / 'metrics_semantic_vs_strict.json').relative_to(REPO).as_posix()}")
    print(f"wrote {(OUT_DIR / 'metrics_table.md').relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
