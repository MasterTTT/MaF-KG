"""Export the two new E1 inputs and their source-to-input line mapping.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 3.1.

The inputs are slices of larger chapter files. Every downstream artifact must
carry BOTH the original source line numbers (for reviewers checking the
textbook) and the input-internal line numbers (for the extraction run). The
earlier round lost this mapping and produced evidence ranges outside the
document it was quoting, so the mapping is written once here and asserted.

Outputs (new files only):
  evaluation/www_evidence_supplement_2026-10-11/e1_content/inputs/input_I.md
  evaluation/www_evidence_supplement_2026-10-11/e1_content/inputs/input_O.md
  evaluation/www_evidence_supplement_2026-10-11/e1_content/inputs/input_map.json
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
ROOT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
INPUTS = ROOT / "e1_content" / "inputs"
SOURCE_REPO = Path("E:/MaF-KG")

SPECS = [
    {
        "input_id": "I",
        "file": "第四章_全章_无习题.md",
        "source_start": 5,
        "source_end": 1126,
        "label": "indefinite_integral",
        "sections": [
            {"section": "ch4_s1", "title": "第一节 不定积分的概念与性质"},
            {"section": "ch4_s2", "title": "第二节 换元积分法"},
            {"section": "ch4_s3", "title": "第三节 分部积分法"},
        ],
    },
    {
        "input_id": "O",
        "file": "第七章_全章_无习题.md",
        "source_start": 973,
        "source_end": 1809,
        "label": "differential_equation",
        "sections": [
            {"section": "ch7_s4", "title": "第四节 一阶线性微分方程"},
            {"section": "ch7_s5", "title": "第五节 可降阶的高阶微分方程"},
        ],
    },
]

# Headings that open a numbered subsection inside a section (一、二、三、).
SUBSECTION = re.compile(r"^\s*(?:#+\s*)?([一二三四五六七八九十]+)\s*[、.．]\s*(.+?)\s*$")
SECTION_HEADING = re.compile(r"^\s*#+\s*第([一二三四五六七八九十]+)节\s*(.*?)\s*$")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    INPUTS.mkdir(parents=True, exist_ok=True)
    mapping: dict = {
        "schema": "maf_kg.www_supplement.input_map.v1",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md",
        "convention": {
            "source_evidence": "1-based line number in the original chapter file",
            "input_evidence": "1-based line number in the exported input file",
            "relation": "input_line = source_line - offset",
            "text_encoding": "UTF-8, LF normalised, no BOM",
            "char_count_convention": "decoded characters including LF separators",
        },
        "inputs": [],
    }
    problems: list[str] = []

    for spec in SPECS:
        src = SOURCE_REPO / "data/raw" / spec["file"]
        raw = src.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n")
        lines = text.split("\n")
        lo, hi = spec["source_start"], spec["source_end"]
        seg = lines[lo - 1 : hi]
        offset = lo - 1

        out = INPUTS / f"input_{spec['input_id']}.md"
        out.write_text("\n".join(seg) + "\n", encoding="utf-8", newline="\n")

        # locate section and subsection headings inside the slice
        sections: list[dict] = []
        subsections: list[dict] = []
        for i, line in enumerate(seg):
            source_line = lo + i
            input_line = i + 1
            m = SECTION_HEADING.match(line)
            if m:
                sections.append({
                    "title": f"第{m.group(1)}节 {m.group(2)}".strip(),
                    "source_line": source_line,
                    "input_line": input_line,
                })
                continue
            m = SUBSECTION.match(line)
            if m and len(line.strip()) < 60:
                subsections.append({
                    "title": f"{m.group(1)}、{m.group(2)}".strip(),
                    "source_line": source_line,
                    "input_line": input_line,
                })

        entry = {
            "input_id": spec["input_id"],
            "label": spec["label"],
            "source_file": str(src),
            "source_sha256": hashlib.sha256(raw).hexdigest().upper(),
            "source_start_line": lo,
            "source_end_line": hi,
            "offset": offset,
            "input_file": str(out.relative_to(WORKSPACE)).replace("\\", "/"),
            "input_sha256": hashlib.sha256(out.read_bytes()).hexdigest().upper(),
            "input_lines": len(seg),
            "input_chars_incl_lf": len("\n".join(seg)),
            "input_chars_excl_lf": len("\n".join(seg)) - len(seg) + 1,
            "section_headings_found": sections,
            "subsection_headings_found": subsections,
            "declared_sections": spec["sections"],
            "first_line": seg[0][:100],
        }
        mapping["inputs"].append(entry)

        # --- assertions ---------------------------------------------------
        if len(seg) != (hi - lo + 1):
            problems.append(f"{spec['input_id']}: slice length {len(seg)} != {hi - lo + 1}")
        if not sections:
            problems.append(f"{spec['input_id']}: no 第X节 heading found inside the slice")
        if len(sections) != len(spec["sections"]):
            problems.append(
                f"{spec['input_id']}: found {len(sections)} section headings, "
                f"declared {len(spec['sections'])}"
            )
        for s in sections:
            if not (1 <= s["input_line"] <= len(seg)):
                problems.append(f"{spec['input_id']}: heading line out of range {s}")
            if s["source_line"] - s["input_line"] != offset:
                problems.append(f"{spec['input_id']}: offset broken at {s['title']}")
        for sub in subsections:
            if not (1 <= sub["input_line"] <= len(seg)):
                problems.append(f"{spec['input_id']}: subsection line out of range {sub}")
        if offset + len(seg) != hi:
            problems.append(f"{spec['input_id']}: offset+len != source_end")

    mapping["problems"] = problems
    mapping["ok"] = not problems

    (INPUTS / "input_map.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for e in mapping["inputs"]:
        print(f"input {e['input_id']}: {e['input_lines']} lines, "
              f"{e['input_chars_incl_lf']} chars (incl LF), offset={e['offset']}")
        print(f"  sections  : " + " | ".join(
            f"{s['title']} @in {s['input_line']}/src {s['source_line']}"
            for s in e["section_headings_found"]))
        print(f"  subsections ({len(e['subsection_headings_found'])}): " + " | ".join(
            f"{s['title']}@{s['input_line']}" for s in e["subsection_headings_found"][:14]))
    print(f"\nproblems: {len(problems)}")
    for p in problems:
        print(f"  - {p}")
    print(f"wrote {(INPUTS / 'input_map.json').relative_to(WORKSPACE)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
