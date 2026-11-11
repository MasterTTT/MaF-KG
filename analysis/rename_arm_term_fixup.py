# -*- coding: utf-8 -*-
"""Second pass on the closeout doc: the bare 臂 -> 组 rule also hit the ablation's context conditions.

In the prerequisite context ablation the branches are context conditions (A/B/C), not methods, so the
generic replacement produced "两种方法齐发" and "A/B/C 三种方法". This pass fixes those, inserts the
missing space after "条件 X", and leaves the genuinely method-level sentences (MaF vs Direct) alone.

Usage: python analysis/rename_arm_term_fixup.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
PATH = ROOT / 'docs' / 'paper_closeout_progress_2026-10-10.md'
FIXES = [
    ('因此 A/B/C 三种方法**只差新增的上下文**', '因此 A/B/C 三种条件**只差新增的上下文**'),
    ('不盲目两种方法齐发', '不盲目两种条件齐发'),
    ('**批大小由 8 改为 4（每种方法 7 批）**', '**批大小由 8 改为 4（每种条件 7 批）**'),
    ('### 8.1 固定候选池审核（两种方法，仅上下文不同）', '### 8.1 固定候选池审核（两种条件，仅上下文不同）'),
    ('- 两种方法逐对一致率 24/28', '- 两种条件逐对一致率 24/28'),
]
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    text = PATH.read_text(encoding='utf-8')
    before = text.count('条件')
    for old, new in FIXES:
        if old not in text:
            print('WARN not found:', old[:40])
        text = text.replace(old, new)
    # insert the missing space: 条件 X<CJK or bold marker>
    text = re.sub(r'条件 ([ABC])(?=[\u4e00-\u9fff*])', r'条件 \1 ', text)
    PATH.write_text(text, encoding='utf-8')
    print('conditions mentioned: %d -> %d' % (before, text.count('条件')))
    for line in text.splitlines():
        if '条件 ' in line or '条件 C' in line:
            stripped = line.strip()
            if '条件' in stripped and ('条件 A' in stripped or '条件 B' in stripped or '条件 C' in stripped):
                print('   ', stripped[:110])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
