#!/usr/bin/env python
"""S4: build the bounded author review packet and the targeted question list.

The packet is a stratified sample of at most 48 items (8 final graphs x 3 content items x 3 relation
items), with the sampling record kept so the sample can be reproduced.  It is explicitly not a
full-population accuracy estimate, and the disputed prerequisite items are kept in a separate
targeted list instead of being mixed into the random sample.

No model is called.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/author_review_packet/.

Usage:
    python analysis/final_closeout_s4_review_packet.py
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"
ADJ = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication"
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/author_review_packet"
INPUT_MD = MERGED / "work/merged_input.md"
REFERENCE = MERGED / "reference/reference_merged_v1.json"

SEED = 20261010
CONTENT_PER_GRAPH = 3
RELATION_PER_GRAPH = 3

# The author's answer to the S1 targeted questions, received 2026-10-10.
AUTHOR_ANSWERS = {
    "decided_at": "2026-10-10",
    "author_statement_verbatim": "首先，选A吧 其余九对接受",
    "prerequisite_reference_revision": {
        "status": "answered",
        "decision": (
            "N04 选 A：目标范围按目录 description，不含「能判断给定函数是否为原函数」，"
            "故 N04 记为未决；其余九对（N01/N09/N10/N19/N20/N23/N25/N27 及 N04 本身）全部接受。"
        ),
        "per_item_verdict": "agree",
        "effect": "主参考不变（选项 A 即既有状态），全部数字与裁决前一致；敏感性变体仅作敏感性保留。",
    },
}

# The author's answer to the 48-item stratified sample, received 2026-10-10.
SAMPLE_CONFIRMATION = {
    "decided_at": "2026-10-10",
    "author_statement_verbatim": "我全部同意了",
    "verdict": "agree",
    "scope": "all 48 sampled items (8 outputs x 3 content + 3 relation)",
    "what_this_establishes": (
        "The author reviewed the assistant's judgement on every sampled item and agreed with it, "
        "including the items judged as not covered."
    ),
    "what_this_does_not_establish": (
        "It is not independent double annotation, not inter-annotator agreement, and not a "
        "full-population accuracy: the sample is stratified and the remaining items were not "
        "re-checked. The paper must not describe the whole judgement set as human-verified."
    ),
}

GRAPHS = [
    ("P06", "review_merged", "maf", 1),
    ("P05", "review_merged", "maf", 2),
    ("P08", "review_merged", "direct", 1),
    ("P10", "review_merged", "direct", 2),
    ("Q04", "review_v3", "edc", 1),
    ("Q02", "review_v3", "edc", 2),
    ("V01", "review_v4", "itext2kg", 1),
    ("V02", "review_v4", "itext2kg", 2),
]

MAX_EXCERPT = 600

# Intra-section reference relations carry *source-file* line numbers, while the excerpt is read from
# the concatenated merged document.  Without this mapping every relation excerpt is misaligned.
SECTION_OFFSETS = {"ch2_s5": -1701, "ch3_s1": 570, "ch3_s2": 570}
MERGED_LINES = 1094


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def excerpt(start: int, end: int) -> str:
    lines = INPUT_MD.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start))
    end = min(len(lines), int(end))
    text = "\n".join(lines[start - 1 : end])
    if len(text) > MAX_EXCERPT:
        text = text[:MAX_EXCERPT] + " ...[truncated]"
    return text


def stratified_sample(rows: list[dict], count: int, rng: random.Random, key: str) -> list[dict]:
    """Round-robin over status strata, then fill from the remainder; deterministic under SEED."""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row.get(key)), []).append(row)
    for values in buckets.values():
        values.sort(key=lambda r: str(r.get("reference_id")))
        rng.shuffle(values)
    order = sorted(buckets)
    picked: list[dict] = []
    index = 0
    while len(picked) < count and any(buckets.values()):
        for status in order:
            values = buckets[status]
            if values and len(picked) < count:
                picked.append(values.pop(0))
        index += 1
        if index > 50:
            break
    return picked


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reference = read_json(REFERENCE)
    nodes = {n["id"]: n for n in reference["nodes"]}
    relations = {r["id"]: r for r in reference["relations"] + reference["cross_section_relations"]}
    rng = random.Random(SEED)

    items: list[dict] = []
    sampling_record: list[dict] = []
    counter = 0

    for label, packet_dir, method, repeat in GRAPHS:
        packet = read_json(MERGED / packet_dir / f"{label}.json")
        review = read_json(MERGED / packet_dir / f"{label}_review.json")
        native = {item["item_id"]: item["native"] for item in packet["items"]}

        def verbatim(item_ids) -> list:
            out = []
            for item_id in item_ids:
                value = native.get(item_id)
                if value is None:
                    continue
                rendered = json.dumps(value, ensure_ascii=False)
                out.append(rendered[:400])
            return out

        content_rows = review.get("node_coverage", [])
        relation_rows = review.get("relation_coverage", []) + review.get("cross_relation_coverage", [])

        for dimension, rows, count, pool_size in (
            ("content", content_rows, CONTENT_PER_GRAPH, len(nodes)),
            ("relation", relation_rows, RELATION_PER_GRAPH, len(relations)),
        ):
            picked = stratified_sample(rows, count, rng, "status")
            sampling_record.append(
                {
                    "graph": label,
                    "method": method,
                    "repeat": repeat,
                    "dimension": dimension,
                    "population": len(rows),
                    "population_note": (
                        "the 42 frozen reference items" if dimension == "content"
                        else "the 47 frozen reference relations (45 intra + 2 cross)"
                    ),
                    "sampled": [row["reference_id"] for row in picked],
                    "stratified_by": "existing review status",
                    "status_mix_in_sample": sorted({str(row.get("status")) for row in picked}),
                }
            )
            for row in picked:
                counter += 1
                ref_id = row["reference_id"]
                if dimension == "content":
                    node = nodes.get(ref_id, {})
                    span = node.get("merged_evidence") or node.get("evidence") or [1, 1]
                    textbook = {
                        "source": "merged_input.md (ch2_s5 + ch3_s1 + ch3_s2)",
                        "start_line": span[0],
                        "end_line": span[-1],
                        "excerpt": excerpt(span[0], span[-1]),
                    }
                    question = (
                        "该输出项是否确实完整承载了参考条目所要求的数学内容"
                        "（含该条目成立所需的限制条件）？"
                    )
                    suggestion = row.get("reason")
                    target_meaning = node.get("meaning")
                    target_name = node.get("name")
                else:
                    relation = relations.get(ref_id, {})
                    span = relation.get("evidence") or [1, 1]
                    section = relation.get("section")
                    offset = SECTION_OFFSETS.get(section, 0)
                    merged_start = int(span[0]) + offset
                    merged_end = (int(span[-1]) if len(span) > 1 else int(span[0])) + offset
                    if not (1 <= merged_start <= MERGED_LINES):
                        raise SystemExit(
                            f"{ref_id}: merged line {merged_start} out of range "
                            f"(section={section}, source={span}, offset={offset})"
                        )
                    textbook = {
                        "source": "merged_input.md (ch2_s5 + ch3_s1 + ch3_s2)",
                        "start_line": merged_start,
                        "end_line": merged_end,
                        "source_file_lines": span,
                        "section": section,
                        "offset_applied": offset,
                        "excerpt": excerpt(merged_start, merged_end),
                    }
                    question = (
                        "该原生输出是否表达了这条参考关系的数学语义、角色与方向？"
                        "（若方向相反但教材以相反语序表述同一事实，请注明）"
                    )
                    suggestion = row.get("reason")
                    target_name = (
                        f"{nodes.get(relation.get('start'), {}).get('name')} "
                        f"-[{relation.get('type')}]-> "
                        f"{nodes.get(relation.get('end'), {}).get('name')}"
                    )
                    target_meaning = relation.get("justification")

                items.append(
                    {
                        "item_id": f"AR-{counter:02d}",
                        "graph": {"label": label, "method": method, "repeat": repeat},
                        "dimension": dimension,
                        "reference_id": ref_id,
                        "reference_label": target_name,
                        "reference_meaning": target_meaning,
                        "native_binding": {
                            "item_ids": row.get("item_ids", []),
                            "verbatim": verbatim(row.get("item_ids", [])),
                        },
                        "existing_review": {
                            "status": row.get("status"),
                            "reason": row.get("reason"),
                            "conditions_complete": row.get("conditions_complete"),
                        },
                        "textbook": textbook,
                        "question_to_author": question,
                        "assistant_suggestion": suggestion,
                        "assistant_confidence": "medium",
                        "review_status": "author_confirmed",
                        "author_answer": {
                            "verdict": "agree",
                            "allowed": ["agree", "disagree", "uncertain"],
                            "decided_at": SAMPLE_CONFIRMATION["decided_at"],
                            "note": "作者已确认（全部 48 项同意）",
                        },
                    }
                )

    # ---- targeted list: disputed prerequisite items and cross-representation questions ----
    adjudications = read_json(ADJ / "adjudications.json")
    changed = [row for row in adjudications["items"] if row["changed"]]
    unchanged_medium = [
        row
        for row in adjudications["items"]
        if not row["changed"] and row.get("confidence") in {"medium", "low"}
    ]
    false_merges = []
    for label, packet_dir, method, repeat in GRAPHS:
        review = read_json(MERGED / packet_dir / f"{label}_review.json")
        for entry in review.get("false_merges", []):
            false_merges.append({"graph": label, "method": method, "repeat": repeat, "entry": entry})

    targeted = {
        "schema": "dsh.final_closeout.s4_targeted_questions.v1",
        "purpose": (
            "Disputed or high-impact items are listed here rather than mixed into the stratified "
            "sample, so the sample stays a sample and these are not presented as unbiased draws."
        ),
        "author_answers": AUTHOR_ANSWERS,
        "groups": [
            {
                "group_id": "prerequisite_reference_revision",
                "question": (
                    "这 10 对先修参考在 S1 中被改判；请确认改判方向（必需 / 原理支持 / 证明支持 / "
                    "非必需 / 未决）是否符合你的教学判断。"
                ),
                "items": [
                    {
                        "id": row["id"],
                        "pair": f"{row['target_name']} -> {row['prerequisite_name']}",
                        "v2": f"{row['v2_role']}/{row['v2_directness']}",
                        "v3": f"{row['v3_role']}/{row['v3_directness']}",
                        "adjudication_source": row["adjudication_source"],
                        "reason": row["reason"],
                        "denominator_effect": row["denominator_effect"],
                        "author_answer": {
                            "verdict": "agree",
                            "allowed": ["agree", "disagree", "uncertain"],
                            "decided_at": AUTHOR_ANSWERS["decided_at"],
                            "note": "作者已接受（N04 按选项 A 记为未决）",
                        },
                    }
                    for row in changed
                ],
            },
            {
                "group_id": "prerequisite_reference_kept_but_uncertain",
                "question": (
                    "这些对未改判但置信度为 medium/low；若你认为其中任何一条应改，请指出。"
                ),
                "items": [
                    {
                        "id": row["id"],
                        "pair": f"{row['target_name']} -> {row['prerequisite_name']}",
                        "role": f"{row['v3_role']}/{row['v3_directness']}",
                        "reason": row["reason"],
                        "confidence": row["confidence"],
                        "author_answer": {"verdict": None, "allowed": ["agree", "disagree", "uncertain"]},
                    }
                    for row in unchanged_medium
                ],
            },
            {
                "group_id": "cross_representation_relation_equivalence",
                "question": (
                    "X-R1 与 X-R2 两条跨节关系在不同表示下的等价性判定，是否接受"
                    "「逆谓词等价 + 自由短语端点对齐」这一规则？"
                ),
                "items": [
                    {
                        "id": ref_id,
                        "relation": (
                            f"{nodes.get(rel.get('start'), {}).get('name')} "
                            f"-[{rel.get('type')}]-> {nodes.get(rel.get('end'), {}).get('name')}"
                        ),
                        "scope": rel.get("scope", rel.get("section")),
                        "justification": rel.get("justification"),
                        "author_answer": {"verdict": None, "allowed": ["agree", "disagree", "uncertain"]},
                    }
                    for ref_id, rel in relations.items()
                    if ref_id.startswith("X-R")
                ],
            },
            {
                "group_id": "false_merges",
                "question": "这些误合并是否确实是把两个不同数学对象并成一项？",
                "items": false_merges,
            },
        ],
    }

    packet_payload = {
        "schema": "dsh.final_closeout.s4_author_review_packet.v1",
        "sampling": {
            "seed": SEED,
            "sampling_unit": "one (graph, dimension, reference item) triple",
            "population": {
                "content": "the 42 frozen reference items per graph",
                "relation": "the 47 frozen reference relations per graph",
            },
            "stratified_by": "existing review status",
            "target_size": len(GRAPHS) * (CONTENT_PER_GRAPH + RELATION_PER_GRAPH),
            "actual_size": len(items),
            "not_a_population_estimate": (
                "This is a stratified sample. Passing items do not upgrade the remaining AI labels "
                "to expert Gold, and the sample cannot be read as a full-population accuracy."
            ),
        },
        "sampling_record": sampling_record,
        "review_status": "author_confirmed",
        "author_confirmation": SAMPLE_CONFIRMATION,
        "confirmed_verdict_distribution": {},
        "disclosure": (
            "Suggestions are assistant source review, confirmed item by item by the author on the "
            "sampled items. There is still no second human annotator, so no inter-annotator "
            "agreement is reported and the paper must not claim independent human annotation."
        ),
        "items": items,
    }
    distribution: dict[str, int] = {}
    for item in items:
        key = f"{item['dimension']}:{item['existing_review']['status']}"
        distribution[key] = distribution.get(key, 0) + 1
    packet_payload["confirmed_verdict_distribution"] = dict(sorted(distribution.items()))
    (OUT_DIR / "packet.json").write_text(
        json.dumps(packet_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "targeted_questions.json").write_text(
        json.dumps(targeted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "sampling_record.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "generator": "analysis/final_closeout_s4_review_packet.py",
                "generator_sha256": sha256_file(Path(__file__)),
                "record": sampling_record,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# 作者复核包（有限分层样本）",
        "",
        f"- 样本量：{len(items)} 项（{len(GRAPHS)} 份图 × 3 内容项 × 3 关系项）",
        f"- 随机种子：{SEED}；抽样单位：(图, 维度, 参考条目)；分层：旧评审状态",
        "- **这是分层样本，不是全量总体正确率**；通过项不能把其余 AI 标签升级为专家 Gold。",
        f"- 评审状态：**author_confirmed**（作者 {SAMPLE_CONFIRMATION['decided_at']} 确认全部 {len(items)} 项，"
        f"原话「{SAMPLE_CONFIRMATION['author_statement_verbatim']}」）。",
        "- 这不构成独立双标注、人际一致性，也不构成全量正确率。",
        "",
        "| 编号 | 图 | 维度 | 参考条目 | 旧判 | 作者 |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| {item['item_id']} | {item['graph']['label']} ({item['graph']['method']} r{item['graph']['repeat']}) "
            f"| {item['dimension']} | {item['reference_id']} {str(item['reference_label'])[:34]} "
            f"| {item['existing_review']['status']} | {item['author_answer']['verdict']} |"
        )
    lines += [
        "",
        "## 逐项明细",
        "",
    ]
    for item in items:
        lines += [
            f"### {item['item_id']}  {item['graph']['label']} / {item['dimension']} / {item['reference_id']}",
            "",
            f"- 参考：{item['reference_label']}",
            f"- 参考含义：{str(item['reference_meaning'])[:400]}",
            f"- 原生绑定：{item['native_binding']['item_ids']}",
            f"- 原生逐字：{item['native_binding']['verbatim']}",
            f"- 旧判：`{item['existing_review']['status']}` —— {item['existing_review']['reason']}",
            f"- 教材出处：{item['textbook']['start_line']}-{item['textbook']['end_line']}",
            "",
            "```text",
            item["textbook"]["excerpt"],
            "```",
            "",
            f"- 待答：{item['question_to_author']}",
            f"- 助手建议：{item['assistant_suggestion']}",
            f"- 作者回答：verdict=____ comment=____",
            "",
        ]
    (OUT_DIR / "packet.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {OUT_DIR.relative_to(REPO).as_posix()}/packet.json ({len(items)} items)")
    print(f"  sampling: {len(GRAPHS)} graphs x 3 content + 3 relation")
    print(f"  targeted groups: {[g['group_id'] for g in targeted['groups']]}")
    print(f"  targeted items: changed={len(changed)} medium_kept={len(unchanged_medium)} "
          f"false_merges={len(false_merges)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
