#!/usr/bin/env python
"""S2 worksheet v2: correct the source-text alignment and add the native entity descriptions.

The v1 worksheet read the reference relation line numbers against the merged document, but those
numbers are *source-file* lines for intra-section relations.  Every intra-section excerpt was
therefore misaligned.  This script rebuilds the excerpts with the correct section offset, adds the
native entity descriptions (so that "the name only appears inside a description" can be judged), and
leaves the v1 worksheets untouched as the record of what was first produced.

Offset derivation (from reference nodes, which carry both source and merged evidence):
    ch2_s5: merged = source - 1701   (D-N1 source 1737-1755 -> merged 36-54)
    ch3_s1: merged = source + 570    (M-4    source   82-94  -> merged 652-664)
    ch3_s2: merged = source + 570    (L-N2   source 375-389 -> merged 945-959)
Cross-section relations already carry merged line numbers.

Read-only with respect to the experiments.  Writes only worksheet_v2_*.json and the defect note.

Usage:
    python analysis/final_closeout_s2_worksheet_v2.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review"
INPUT_MD = MERGED / "work/merged_input.md"
REFERENCE = MERGED / "reference/reference_merged_v1.json"

SECTION_OFFSETS = {"ch2_s5": -1701, "ch3_s1": 570, "ch3_s2": 570}
MAX_EXCERPT = 700

GRAPHS = [
    ("P06", "review_merged"),
    ("P05", "review_merged"),
    ("P08", "review_merged"),
    ("P10", "review_merged"),
    ("Q04", "review_v3"),
    ("Q02", "review_v3"),
    ("V01", "review_v4"),
    ("V02", "review_v4"),
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def excerpt(start: int, end: int) -> str:
    lines = INPUT_MD.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start))
    end = min(len(lines), int(end))
    text = "\n".join(lines[start - 1 : end])
    if len(text) > MAX_EXCERPT:
        text = text[:MAX_EXCERPT] + " ...[truncated]"
    return text


def main() -> int:
    reference = read_json(REFERENCE)
    relations = {
        r["id"]: r for r in reference["relations"] + reference["cross_section_relations"]
    }

    fixed_rows = 0
    for label, packet_dir in GRAPHS:
        v1_path = OUT_DIR / f"worksheet_{label}.json"
        v1 = read_json(v1_path)
        packet = read_json(MERGED / packet_dir / f"{label}.json")

        entities = []
        for item in packet["items"]:
            if item.get("kind") != "entity":
                continue
            native = item.get("native")
            if not isinstance(native, dict):
                continue
            entities.append(
                {
                    "item_id": item["item_id"],
                    "native_id": native.get("id"),
                    "type": native.get("type"),
                    "label": native.get("label"),
                    "name": native.get("name"),
                    "description": str(native.get("description", ""))[:600],
                }
            )

        for row in v1["rows"]:
            relation = relations.get(row["reference_id"])
            if not relation:
                continue
            section = relation.get("section")
            evidence = relation.get("evidence") or []
            if section in SECTION_OFFSETS and evidence:
                offset = SECTION_OFFSETS[section]
                merged_start = int(evidence[0]) + offset
                merged_end = (int(evidence[-1]) if len(evidence) > 1 else int(evidence[0])) + offset
                row["source_text_v1_misaligned"] = row.get("source_text")
                row["source_reference"] = {
                    "section": section,
                    "source_file_lines": evidence,
                    "merged_lines": [merged_start, merged_end],
                    "offset_applied": offset,
                }
                row["source_text"] = excerpt(merged_start, merged_end)
                fixed_rows += 1
            else:
                row["source_reference"] = {
                    "section": section,
                    "source_file_lines": evidence,
                    "merged_lines": evidence,
                    "offset_applied": 0,
                    "note": "cross-section relation already carries merged line numbers",
                }

        v1["worksheet_version"] = "v2"
        v1["v2_changes"] = [
            "source_text recomputed with the correct section offset for intra-section relations",
            "native_entities_resolved added so description-level content can be judged",
        ]
        v1["native_entities_resolved"] = entities
        v2_path = OUT_DIR / f"worksheet_v2_{label}.json"
        v2_path.write_text(json.dumps(v1, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {label}: entities={len(entities)} rows={len(v1['rows'])} -> {v2_path.name}")

    note = [
        "# 工作表 v1 缺陷记录（S2）",
        "",
        "## 缺陷",
        "",
        "`worksheet_<label>.json`（v1）里 `source_text` 用的是参考关系的 `evidence` 行号，但**节内关系**的",
        "`evidence` 是**源文件行号**，而摘录取自拼接后的 `merged_input.md`。两者系统错位：",
        "",
        "| 节 | 映射 | 依据 |",
        "|---|---|---|",
        "| ch2_s5 | merged = source − 1701 | 参考节点 D-N1：source 1737-1755 → merged 36-54 |",
        "| ch3_s1 | merged = source + 570 | 参考节点 M-4：source 82-94 → merged 652-664 |",
        "| ch3_s2 | merged = source + 570 | 参考节点 L-N2：source 375-389 → merged 945-959 |",
        "",
        "跨节关系 X-R1/X-R2 的 `evidence` 本来就是 merged 行号，不受影响。",
        "",
        "## 处理",
        "",
        "1. v1 文件**不修改**，作为「最初产出」的留痕；",
        "2. 新增 `worksheet_v2_<label>.json`：`source_text` 按上表偏移重算，并写入 `source_reference`",
        "   记录源文件行号、merged 行号与所用偏移；v1 的错误摘录保留在 `source_text_v1_misaligned` 字段；",
        "3. v2 另加 `native_entities_resolved`（原生实体的 name/type/description），使「名称只出现在描述里」",
        "   这一类判断有据可依；",
        "4. 判定未受影响：各图判定以 `native_item_ids` 与 `verbatim_fragment` 绑定，P08 的评审已明确记录",
        "   未把 v1 的 `source_text` 当证据；其余各图在合并时按 v2 复核一次绑定。",
        "",
        "## 已知残余限制",
        "",
        "- 参考关系 M-R8 的 justification 自注是教材两处显式关系的**传递推断**，要求输出表达教材未直接写出的",
        "  事实，与协议 §2 硬约束 1 存在张力；这一条在合并报告中单列。",
        "- 严格列在「两端齐备但没有任何原生边」时协议未给取值。合并阶段统一归一化为 `absent`",
        "  （仅当语义列为 `not_covered` 时），原始判定值不改。",
    ]
    (OUT_DIR / "worksheet_v1_defect_note.md").write_text("\n".join(note) + "\n", encoding="utf-8")
    print(f"  rows realigned: {fixed_rows}")
    print(f"wrote {(OUT_DIR / 'worksheet_v1_defect_note.md').relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
