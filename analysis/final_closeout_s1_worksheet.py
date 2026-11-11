#!/usr/bin/env python
"""S1 preparation: dump everything needed to adjudicate the prerequisite reference into one
readable worksheet, so that the judgement is made on the source text and the frozen catalogue
rather than on the existing labels.

Read-only.  Writes only the worksheet and a small JSON index under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/.

Usage:
    python analysis/final_closeout_s1_worksheet.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication"

NEW_REF = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/reference_v2.json"
OLD_REF = (
    REPO
    / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/reference.json"
)
CATALOG = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json"
REVIEW_SYSTEM = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/snapshot/review_system.txt"
SELECTION = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/selection.json"
ARM_KU_PAYLOAD = (
    REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/arm_ku/payload_manifest.json"
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def spans_text(spans) -> str:
    if not spans:
        return "    (no spans)"
    lines = []
    for span in spans:
        lines.append(
            f"    - {span.get('source')} L{span.get('start_line')}-{span.get('end_line')} "
            f"[{span.get('kind')}]"
        )
    return "\n".join(lines)


def catalogue_index(catalog: dict) -> dict:
    index = {}
    for unit in catalog.get("knowledge_units", []):
        key = unit.get("id") or unit.get("unit_id") or unit.get("ku_id")
        if key:
            index[key] = unit
    return index


def unit_block(unit: dict | None, fallback_name: str) -> str:
    if not unit:
        return f"  (catalogue entry not found for {fallback_name})"
    lines = []
    lines.append(f"  id: {unit.get('id')}")
    lines.append(f"  name: {unit.get('name')}")
    lines.append(f"  type: {unit.get('type')}   aggregation_kind: {unit.get('aggregation_kind')}")
    lines.append(f"  chapter: {unit.get('chapter')}")
    lines.append(f"  description: {unit.get('description')}")
    centres = unit.get("centers_on")
    if isinstance(centres, list):
        lines.append(f"  centers_on ({len(centres)}):")
        for centre in centres:
            desc = str(centre.get("description", ""))
            if len(desc) > 220:
                desc = desc[:220] + "..."
            lines.append(f"    - [{centre.get('type')}] {centre.get('name')} :: {desc}")
    contains = unit.get("contains")
    if isinstance(contains, list):
        names = [c.get("name") for c in contains if isinstance(c, dict)]
        lines.append(f"  contains ({len(names)}): {names[:40]}")
    evidence = unit.get("evidence")
    if isinstance(evidence, list):
        lines.append(f"  evidence spans ({len(evidence)}):")
        for span in evidence[:8]:
            lines.append(
                f"    - {span.get('source_path')} L{span.get('start_line')}-{span.get('end_line')}"
            )
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new_ref = load(NEW_REF)
    old_ref = load(OLD_REF)
    catalog = load(CATALOG)
    selection = load(SELECTION)
    review_system = REVIEW_SYSTEM.read_text(encoding="utf-8")
    index = catalogue_index(catalog)

    out = []
    out.append("# S1 先修参考审定输入清单（机器生成）")
    out.append("")
    out.append("本文件由 `analysis/final_closeout_s1_worksheet.py` 生成，只读汇总，供逐项审定使用。")
    out.append("")
    out.append("## 0. 实际发给审核模型的系统指令（冻结原文）")
    out.append("")
    out.append("```text")
    out.append(review_system.strip())
    out.append("```")
    out.append("")
    out.append(f"来源：`{REVIEW_SYSTEM.relative_to(REPO).as_posix()}`")
    out.append("")

    out.append("## 1. 四个新目标：目录条目、选择理由与标注目标类型")
    out.append("")
    sel_targets = {t["unit_id"]: t for t in selection.get("targets", [])}
    for target in new_ref.get("targets", []):
        unit_id = target["unit_id"]
        out.append(f"### {target.get('name')}  `{unit_id}`")
        out.append("")
        out.append(f"- reference_v2 记录的 goal_type：`{target.get('goal_type')}`")
        sel = sel_targets.get(unit_id)
        if sel:
            out.append(f"- selection 记录的 goal_type：`{sel.get('goal_type')}`")
            out.append(f"- selection 的选择理由：{sel.get('why')}")
        out.append(f"- 目录位置：{target.get('catalogue_position')}")
        out.append("")
        out.append("目录条目全文：")
        out.append("")
        out.append("```text")
        out.append(unit_block(index.get(unit_id), unit_id))
        out.append("```")
        out.append("")

    out.append("## 2. 新 28 对：现有标签、理由与两端出处")
    out.append("")
    for pair in new_ref.get("pairs", []):
        out.append(
            f"### {pair['id']}  {pair.get('target_name')} -> {pair.get('prerequisite_name')}"
        )
        out.append("")
        out.append(f"- target_ku: `{pair.get('target_ku')}`")
        out.append(f"- prerequisite_ku: `{pair.get('prerequisite_ku')}`")
        out.append(f"- 现有标签：role=`{pair.get('role')}` directness=`{pair.get('directness')}`")
        out.append(f"- 现有理由：{pair.get('reason')}")
        out.append(f"- required_scope：{pair.get('required_scope')}")
        out.append(f"- provenance：{pair.get('provenance')}")
        out.append("- target 出处：")
        out.append(spans_text(pair.get("target_spans")))
        out.append("- prerequisite 出处：")
        out.append(spans_text(pair.get("prerequisite_spans")))
        out.append("")
        out.append("prerequisite 目录条目：")
        out.append("")
        out.append("```text")
        out.append(unit_block(index.get(pair.get("prerequisite_ku")), pair.get("prerequisite_name", "")))
        out.append("```")
        out.append("")

    out.append("## 3. 旧 27 对：现有标签与理由（用于同类口径冲突检查）")
    out.append("")
    for ann in old_ref.get("annotations", []):
        out.append(
            f"### {ann['id']}  {ann.get('target_name')} -> {ann.get('prerequisite_name')}"
        )
        out.append("")
        out.append(f"- 现有标签：role=`{ann.get('role')}` directness=`{ann.get('directness')}`")
        out.append(f"- 现有理由：{ann.get('reason')}")
        out.append(f"- required_scope：{ann.get('required_scope')}")
        out.append(f"- evidence：{json.dumps(ann.get('evidence'), ensure_ascii=False)[:600]}")
        out.append("")
    if old_ref.get("bridges"):
        out.append("### 旧参考的 bridge 条目")
        out.append("")
        for bridge in old_ref["bridges"]:
            out.append(
                f"- {bridge['id']} {bridge.get('target_name')} -> {bridge.get('prerequisite_name')} "
                f"role=`{bridge.get('role')}` directness=`{bridge.get('directness')}`"
            )
            out.append(f"  - 理由：{bridge.get('reason')}")
        out.append("")
    out.append(f"旧参考 status：{old_ref.get('status')}")
    out.append(f"旧参考 unlisted_pairs：{old_ref.get('unlisted_pairs')}")
    out.append(f"旧参考 outside_scope：{old_ref.get('outside_scope')}")
    out.append("")

    out.append("## 4. 两种审核载荷实际发送的内容（arm_ku 第一批，节选）")
    out.append("")
    if ARM_KU_PAYLOAD.is_file():
        payload = load(ARM_KU_PAYLOAD)
        rendered = json.dumps(payload, ensure_ascii=False, indent=1)
        out.append("```json")
        out.append(rendered[:4000] + ("\n...[truncated]" if len(rendered) > 4000 else ""))
        out.append("```")
    else:
        out.append("(payload manifest not found)")
    out.append("")

    text = "\n".join(out)
    worksheet = OUT_DIR / "worksheet_input.md"
    worksheet.write_text(text, encoding="utf-8")

    summary = {
        "schema": "dsh.final_closeout.s1_worksheet.v1",
        "new_reference": NEW_REF.relative_to(REPO).as_posix(),
        "new_reference_counts": new_ref.get("counts"),
        "old_reference": OLD_REF.relative_to(REPO).as_posix(),
        "old_annotations": len(old_ref.get("annotations", [])),
        "old_bridges": len(old_ref.get("bridges", [])),
        "catalogue_units": len(catalog.get("knowledge_units", [])),
        "review_system": REVIEW_SYSTEM.relative_to(REPO).as_posix(),
        "worksheet": worksheet.relative_to(REPO).as_posix(),
    }
    (OUT_DIR / "worksheet_index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {worksheet.relative_to(REPO).as_posix()} ({len(text)} chars)")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
