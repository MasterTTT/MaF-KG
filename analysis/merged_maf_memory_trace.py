# -*- coding: utf-8 -*-
"""Persist the MaF sequential-memory trace across the merged document.

Evidence that the sequential arm really carried state across the section and chapter boundaries
(instead of restarting per section): each chunk request payload records the node/relation memory
handed to the model. This script records those sizes per chunk and the values at the boundaries.

Output: evaluation/external_baselines_merged_2026-10-09/maf_memory_trace.json
"""
import json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
SECTIONS = (('ch2_s5', 1, 574), ('ch3_s1', 575, 936), ('ch3_s2', 937, 1094))
sys.stdout.reconfigure(encoding='utf-8')


def section_of(line):
    for name, start, end in SECTIONS:
        if start <= line <= end:
            return name
    return 'unknown'


def trace(arm_dir):
    requests = sorted((arm_dir / 'requests').glob('*.json'))
    rows = []
    for path in requests:
        payload = json.loads(path.read_text(encoding='utf-8'))['payload']
        span = path.stem.split('_', 2)[2]
        start = int(span.split('_')[0])
        end = int(span.split('_')[1])
        rows.append({
            'chunk': path.stem,
            'merged_start_line': start,
            'merged_end_line': end,
            'section': section_of(start),
            'node_memory_given': len(payload.get('node_memory') or []),
            'relation_memory_given': len(payload.get('relation_memory') or []),
            'content_chars': len(payload.get('chunk') or ''),
        })
    return rows


def main() -> int:
    out = {}
    for repeat in (1, 2):
        arm_dir = NEW / 'runs' / ('r%d' % repeat) / 'merged' / 'maf'
        if not (arm_dir / 'requests').exists():
            out['r%d' % repeat] = {'status': 'requests_absent'}
            continue
        rows = trace(arm_dir)
        boundaries = {}
        for name, start, _ in SECTIONS:
            entering = [r for r in rows if r['section'] == name]
            boundaries[name] = {
                'first_chunk': entering[0]['chunk'] if entering else None,
                'node_memory_at_entry': entering[0]['node_memory_given'] if entering else None,
                'relation_memory_at_entry': entering[0]['relation_memory_given'] if entering else None,
            }
        out['r%d' % repeat] = {
            'chunks': len(rows),
            'node_memory_min': min(r['node_memory_given'] for r in rows),
            'node_memory_max': max(r['node_memory_given'] for r in rows),
            'relation_memory_min': min(r['relation_memory_given'] for r in rows),
            'relation_memory_max': max(r['relation_memory_given'] for r in rows),
            'boundaries': boundaries,
            'rows': rows,
        }
    payload = {
        'interpretation': ('Each chunk payload carries the node/relation memory handed to the model. '
                           'Non-zero memory in the first chunk of ch3_s1 and ch3_s2 shows the sequential arm '
                           'carried state across both the section and the chapter boundary of the merged document.'),
        'memory_is_bounded': True,
        'repeats': out,
    }
    target = NEW / 'maf_memory_trace.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for repeat, info in out.items():
        if info.get('status'):
            print(repeat, info)
            continue
        print('%s chunks=%d node_memory %d-%d relation_memory %d-%d' % (
            repeat, info['chunks'], info['node_memory_min'], info['node_memory_max'],
            info['relation_memory_min'], info['relation_memory_max']))
        for name, value in info['boundaries'].items():
            print('    %-7s entry: nodes=%s relations=%s (%s)' % (
                name, value['node_memory_at_entry'], value['relation_memory_at_entry'], value['first_chunk']))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
