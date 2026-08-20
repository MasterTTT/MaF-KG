#!/usr/bin/env python
"""Slice a chapter out of the full textbook Markdown, strip exercise blocks,
and write a renumbered "无习题" chapter file (matches how 第二章_全章_无习题.md
was produced: cut by ORIGINAL line range, drop # 习题 / # 总习题 blocks, renumber).

An exercise block starts at a heading matching ^#{1,4} *习题 or ^#{1,4} *总习题
and ends at the next ^#{1,4} *第{X}节 section heading (or EOF). Everything else
(intro, theorem/property/example/figure-caption lines) is retained.

Usage:
  python scripts/build_chapter_noex.py \
    <full-textbook.md> <start_line> <end_line> \
    --out <data/raw/第三章_全章_无习题.md>
  (start_line/end_line are 1-based INCLUSIVE in the full-textbook file.)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_EXERCISE_RE = re.compile(r"^#{1,4}\s*(习题|总习题)")
_SECTION_RE = re.compile(r"^#{1,4}\s*第[一二三四五六七八九十]+节")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Slice chapter + strip exercises to a renumbered md.")
    ap.add_argument("textbook", help="Full textbook Markdown path.")
    ap.add_argument("start", type=int, help="1-based inclusive start line of the chapter.")
    ap.add_argument("end", type=int, help="1-based inclusive end line of the chapter.")
    ap.add_argument("--out", required=True, help="Output renumbered '无习题' Markdown path.")
    args = ap.parse_args()

    src = Path(args.textbook).read_text(encoding="utf-8")
    lines_in = src.split("\n")
    # normalize to simple \n list; remember whether source used CRLF for output
    crlf = "\r\n" in (src[:min(len(src), 4000)])
    body = lines_in[args.start - 1 : args.end]  # 0-based slice, end exclusive

    kept: list[str] = []
    in_exercise = False
    for line in body:
        if _EXERCISE_RE.match(line.strip()):
            in_exercise = True
            continue
        if _SECTION_RE.match(line.strip()):
            in_exercise = False
        if not in_exercise:
            kept.append(line)
    # trim leading/trailing blank lines
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    text = "\n".join(kept) + "\n"
    if crlf:
        text = text.replace("\n", "\r\n")

    Path(args.out).write_text(text, encoding="utf-8")
    n_sections = sum(1 for k in kept if _SECTION_RE.match(k.strip()))
    n_exercise_heads = sum(1 for k in body if _EXERCISE_RE.match(k.strip()))
    print(f"source lines {args.start}-{args.end} ({len(body)} lines) -> {len(kept)} kept lines")
    print(f"sections: {n_sections}  exercise headings removed: {n_exercise_heads}  CRLF: {crlf}")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
