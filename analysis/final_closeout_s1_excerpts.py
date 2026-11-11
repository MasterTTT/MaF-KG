#!/usr/bin/env python
"""S1 evidence extraction: resolve every span cited by the new 28-pair reference to the actual
textbook text, with file hash and line numbers, so that adjudication can be made on the source
rather than on the existing labels.

Read-only with respect to the textbook and the reference.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/.

Usage:
    python analysis/final_closeout_s1_excerpts.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication"

NEW_REF = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/reference_v2.json"
OLD_REF = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/reference.json"
CATALOG = REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json"

SOURCE_ROOTS = [
    REPO / "evaluation/prerequisite_real_ku_2026-10-05/run02/snapshot/input",
    REPO / "evaluation/prerequisite_new_targets_2026-10-07/run02/snapshot/input",
]

HEAD_LINES = 24
TAIL_LINES = 10
MAX_CHARS = 1500


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_source(name: str) -> Path | None:
    for root in SOURCE_ROOTS:
        candidate = root / name
        if candidate.is_file():
            return candidate
    matches = sorted(REPO.glob(f"evaluation/**/{name}"))
    return matches[0] if matches else None


_cache: dict[str, list[str]] = {}


def lines_of(path: Path) -> list[str]:
    key = str(path)
    if key not in _cache:
        _cache[key] = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return _cache[key]


def excerpt(name: str, start: int, end: int) -> dict:
    path = resolve_source(name)
    if path is None:
        return {"source": name, "resolved": False, "note": "source file not found"}
    all_lines = lines_of(path)
    start = max(1, int(start))
    end = min(len(all_lines), int(end))
    if end < start:
        return {"source": name, "resolved": True, "start_line": start, "end_line": end,
                "note": "empty span"}
    body = all_lines[start - 1 : end]
    record = {
        "source": name,
        "resolved": True,
        "resolved_path": path.relative_to(REPO).as_posix(),
        "source_sha256": sha256_file(path),
        "start_line": start,
        "end_line": end,
        "line_count": len(body),
    }
    if len(body) <= HEAD_LINES + TAIL_LINES:
        text = "\n".join(body)
    else:
        head = body[:HEAD_LINES]
        tail = body[-TAIL_LINES:]
        omitted = len(body) - HEAD_LINES - TAIL_LINES
        text = "\n".join(head + [f"...[{omitted} lines omitted]..."] + tail)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " ...[truncated]"
    record["excerpt"] = text
    return record


def build(pairs: list[dict], id_key: str = "id") -> list[dict]:
    records = []
    for pair in pairs:
        entry = {
            "id": pair.get(id_key),
            "target_name": pair.get("target_name"),
            "prerequisite_name": pair.get("prerequisite_name"),
            "existing_role": pair.get("role"),
            "existing_directness": pair.get("directness"),
            "existing_reason": pair.get("reason"),
            "required_scope": pair.get("required_scope"),
            "target_excerpts": [
                excerpt(s["source"], s["start_line"], s["end_line"])
                for s in pair.get("target_spans", [])
            ],
            "prerequisite_excerpts": [
                excerpt(s["source"], s["start_line"], s["end_line"])
                for s in pair.get("prerequisite_spans", [])
            ],
        }
        records.append(entry)
    return records


def markdown(records: list[dict], title: str) -> str:
    out = [f"# {title}", ""]
    for rec in records:
        out.append(f"## {rec['id']}  {rec['target_name']} -> {rec['prerequisite_name']}")
        out.append("")
        out.append(f"- 现有标签：role=`{rec['existing_role']}` directness=`{rec['existing_directness']}`")
        out.append(f"- 现有理由：{rec['existing_reason']}")
        out.append(f"- required_scope：{rec['required_scope']}")
        out.append("")
        out.append("### 目标端出处")
        out.append("")
        for ex in rec["target_excerpts"]:
            out.append(f"- `{ex.get('source')}` L{ex.get('start_line')}-{ex.get('end_line')}"
                       f" (sha256 {str(ex.get('source_sha256'))[:12]})")
            out.append("")
            out.append("```text")
            out.append(ex.get("excerpt", ex.get("note", "")))
            out.append("```")
            out.append("")
        out.append("### 先修端出处")
        out.append("")
        for ex in rec["prerequisite_excerpts"]:
            out.append(f"- `{ex.get('source')}` L{ex.get('start_line')}-{ex.get('end_line')}"
                       f" (sha256 {str(ex.get('source_sha256'))[:12]})")
            out.append("")
            out.append("```text")
            out.append(ex.get("excerpt", ex.get("note", "")))
            out.append("```")
            out.append("")
    return "\n".join(out)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new_ref = json.loads(NEW_REF.read_text(encoding="utf-8"))
    old_ref = json.loads(OLD_REF.read_text(encoding="utf-8"))

    new_records = build(new_ref["pairs"])
    old_records = build(old_ref["annotations"])

    payload = {
        "schema": "dsh.final_closeout.s1_evidence_excerpts.v1",
        "new_reference": NEW_REF.relative_to(REPO).as_posix(),
        "old_reference": OLD_REF.relative_to(REPO).as_posix(),
        "source_roots": [root.relative_to(REPO).as_posix() for root in SOURCE_ROOTS],
        "excerpt_policy": {
            "head_lines": HEAD_LINES,
            "tail_lines": TAIL_LINES,
            "max_chars": MAX_CHARS,
            "note": "long spans keep head and tail with an explicit omission marker",
        },
        "new_pairs": new_records,
        "old_pairs": old_records,
    }
    (OUT_DIR / "evidence_excerpts.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "evidence_excerpts_new28.md").write_text(
        markdown(new_records, "新 28 对：教材原文摘录"), encoding="utf-8"
    )
    (OUT_DIR / "evidence_excerpts_old27.md").write_text(
        markdown(old_records, "旧 27 对：教材原文摘录"), encoding="utf-8"
    )
    unresolved = [
        (rec["id"], ex.get("source"))
        for rec in new_records + old_records
        for ex in rec["target_excerpts"] + rec["prerequisite_excerpts"]
        if not ex.get("resolved")
    ]
    print(f"wrote evidence_excerpts.json / evidence_excerpts_new28.md / evidence_excerpts_old27.md")
    print(f"new pairs={len(new_records)} old pairs={len(old_records)} unresolved_spans={len(unresolved)}")
    if unresolved:
        print("UNRESOLVED:", unresolved[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
