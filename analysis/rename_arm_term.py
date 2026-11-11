# -*- coding: utf-8 -*-
"""Replace the jargon "臂" with readable Chinese in the docs we actually work from.

"臂" was a literal translation of the English experimental-design word *arm*. It is jargon, and in this
project it ended up meaning three different things at once (a method variant, a context condition, and
the pooling key in the scoring code), which made the docs hard to read. This script applies an ordered,
context-aware table to the current paper-facing docs:

    method variant  -> 方法        (Direct / MaF / EDC / iText)
    context condition -> 条件 A/B/C (the prerequisite context ablation)
    table headers   -> | 方法 |     (was | 臂 |)

Historical documents from earlier sessions are left untouched, and a glossary in the closeout doc maps
the old word to the new ones. The code keeps `arm` internally; it is an identifier, not prose.

Usage: python analysis/rename_arm_term.py [--check]
"""
import re
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
TARGETS = [
    ROOT / 'docs' / 'paper_closeout_progress_2026-10-10.md',
    ROOT / 'docs' / 'www_merged_experiment_2026-10-09.md',
    ROOT / 'docs' / 'www_claim_evidence_table_2026-10-09.md',
    ROOT / 'docs' / 'DSH_ALIGNMENT_REVIEW_2026-10-09.md',
    ROOT / 'docs' / 'www_experiment_readiness_2026-10-08.md',
]
# Longest / most specific first; the bare 臂 fallback runs last.
TABLE = [
    ('| 臂 |', '| 方法 |'),
    ('四臂对照表', '四方法对照表'),
    ('四臂对照', '四方法对照'),
    ('四臂', '四种方法'),
    ('三臂', '三种方法'),
    ('四个臂', '四个方法'),
    ('两臂', '两种方法'),
    ('每臂', '每种方法'),
    ('各臂', '各方法'),
    ('单臂', '单一方法'),
    ('同一臂', '同一方法'),
    ('2 臂', '2 种方法'),
    ('补跑臂', '补跑方法'),
    ('顺序臂', '顺序方法'),
    ('直接臂', '单次直读方法'),
    ('可见记忆臂', '可见记忆条件'),
    ('记忆臂', '记忆条件'),
    ('标题/Card臂', '标题/Card 方案'),
    ('标题臂', '标题方案'),
    ('结构臂', '结构方案'),
    ('诊断臂', '诊断条件'),
    ('适配臂', '适配方法'),
    ('实验臂', '实验条件'),
    ('审核臂', '审核条件'),
    ('A 臂', '条件 A'),
    ('B 臂', '条件 B'),
    ('C 臂', '条件 C'),
    ('臂', '组'),
]
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    check = '--check' in sys.argv
    for path in TARGETS:
        if not path.is_file():
            print('%-52s MISSING' % path.name)
            continue
        text = path.read_text(encoding='utf-8')
        before = text.count('臂')
        if check:
            print('%-52s %3d 处' % (path.name, before))
            continue
        for old, new in TABLE:
            text = text.replace(old, new)
        path.write_text(text, encoding='utf-8')
        after = text.count('臂')
        print('%-52s %3d -> %d' % (path.name, before, after))
        for line in text.splitlines():
            if '臂' in line:
                print('    LEFT:', line.strip()[:120])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
