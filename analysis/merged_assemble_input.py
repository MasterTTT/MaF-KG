# -*- coding: utf-8 -*-
"""Assemble the merged three-section input with line-level provenance.

Outputs (evaluation/external_baselines_merged_2026-10-09/input/):
  merged_ch2s5_ch3s1_ch3s2.md      concatenated document, section headings retained
  sections/<id>.md                  per-section slices (for reference / fallback arms)
  provenance.json                   merged line -> (source file, source line) mapping
  sections.json                     section descriptors (id, title, source, sha256, line ranges)
"""
import hashlib, json, os, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
RAW = Path(r'E:\MaF-KG\data\raw')
OUT = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09\input')
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'sections').mkdir(parents=True, exist_ok=True)

SECTIONS = [
    ('ch2_s5', '第二章第五节 函数的微分', '第二章_全章_无习题.md', 1702, 2275),
    ('ch3_s1', '第三章第一节 微分中值定理', '第三章_全章_无习题.md', 5, 366),
    ('ch3_s2', '第三章第二节 洛必达法则', '第三章_全章_无习题.md', 367, 524),
]


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


merged_lines, provenance, descriptors = [], [], []
for sid, title, fname, a, b in SECTIONS:
    src = RAW / fname
    lines = src.read_text(encoding='utf-8').splitlines()
    body = lines[a - 1:b]
    (OUT / 'sections' / (sid + '.md')).write_text('\n'.join(body) + '\n', encoding='utf-8')
    descriptors.append({
        'id': sid, 'title': title,
        'source': str(src).replace('\\', '/'), 'source_sha256': sha256_text(src),
        'start_line': a, 'end_line': b, 'lines': len(body), 'chars': len('\n'.join(body)),
        'merged_start_line': len(merged_lines) + 1,
    })
    for offset, text in enumerate(body):
        merged_lines.append(text)
        provenance.append({'merged_line': len(merged_lines), 'section': sid,
                           'source_line': a + offset})
    descriptors[-1]['merged_end_line'] = len(merged_lines)

merged_path = OUT / 'merged_ch2s5_ch3s1_ch3s2.md'
merged_path.write_text('\n'.join(merged_lines) + '\n', encoding='utf-8')
(OUT / 'provenance.json').write_text(json.dumps(
    {'merged_file': merged_path.name, 'merged_sha256': sha256_text(merged_path),
     'merged_lines': len(merged_lines), 'mapping': provenance}, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / 'sections.json').write_text(json.dumps(descriptors, ensure_ascii=False, indent=2), encoding='utf-8')

print('merged file :', merged_path.name, '| lines', len(merged_lines),
      '| chars', sum(len(x) for x in merged_lines), '| sha256', sha256_text(merged_path)[:16])
for d in descriptors:
    print('  %-7s %-24s src %-4d-%-4d -> merged %-4d-%-4d (%d lines)' % (
        d['id'], d['title'], d['start_line'], d['end_line'], d['merged_start_line'], d['merged_end_line'], d['lines']))
print('wrote ->', OUT)
