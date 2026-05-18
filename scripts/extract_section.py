#!/usr/bin/env python3
"""Extract a chapter/section from the Gaoshu Shuxue markdown file.

Usage:
    python extract_section.py <input_md> <chapter> <section> <output_md>

Example:
    python extract_section.py data/raw/高等数学上册第七版.md 1 1 output/第一章第一节.md
"""

import re
import sys
import os


def chinese_to_int(s: str) -> int:
    """Convert Chinese numeral 1-10 to int."""
    mapping = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    return mapping.get(s)


def int_to_chinese(n: int) -> str:
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
               6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    return mapping.get(n, str(n))


def find_section_boundaries(lines, chapter_num, section_num):
    """Find start and end line indices for a given chapter and section."""
    ch_chapter = int_to_chinese(chapter_num)
    ch_section = int_to_chinese(section_num)
    ch_next_section = int_to_chinese(section_num + 1)
    ch_next_chapter = int_to_chinese(chapter_num + 1)

    chapter_pattern = re.compile(rf"^# 第{ch_chapter}章\s")
    section_pattern = re.compile(rf"^# 第{ch_section}节\s")
    next_section_pattern = re.compile(rf"^# 第{ch_next_section}节\s")
    next_chapter_pattern = re.compile(rf"^# 第{ch_next_chapter}章\s")

    # Find the second occurrence of chapter heading (first is TOC, second is content)
    chapter_indices = [i for i, line in enumerate(lines) if chapter_pattern.match(line)]
    if len(chapter_indices) < 2:
        raise ValueError(f"Could not find two occurrences of chapter {chapter_num} heading")
    chapter_start = chapter_indices[1]

    # Find section start after chapter start
    section_start = None
    for i in range(chapter_start, len(lines)):
        if section_pattern.match(lines[i]):
            section_start = i
            break

    if section_start is None:
        raise ValueError(f"Could not find section {section_num} in chapter {chapter_num}")

    # Find end: next section or next chapter, whichever comes first
    section_end = None
    for i in range(section_start + 1, len(lines)):
        if next_section_pattern.match(lines[i]) or next_chapter_pattern.match(lines[i]):
            section_end = i
            break

    if section_end is None:
        raise ValueError(f"Could not find end boundary for section {section_num}")

    return chapter_start, section_end


def main():
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <input_md> <chapter> <section> <output_md>")
        sys.exit(1)

    input_path = sys.argv[1]
    chapter_num = int(sys.argv[2])
    section_num = int(sys.argv[3])
    output_path = sys.argv[4]

    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start, end = find_section_boundaries(lines, chapter_num, section_num)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines[start:end])

    print(f"Extracted lines {start + 1}-{end} ({end - start} lines) → {output_path}")


if __name__ == "__main__":
    main()
