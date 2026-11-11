# -*- coding: utf-8 -*-
"""Reclassify P03 (EDC ch3_s2 r2) from completed -> failed/no_output.

User-approved 2026-10-09: the run has a native graph of 40 all-null slots and a
packet with 0 items, so it must not be scored as zero required-content coverage.
Keeps a timestamped backup of mapping_private.json and writes an evidence note.
"""
import json, shutil, sys, os
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
B = r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_2026-10-08'
RV = os.path.join(B, 'review_live01')
MP = os.path.join(RV, 'mapping_private.json')

REASON = ('EDC target-alignment 运行完成（exit 0、2 calls、issues 空）但最终 graph.json 为 40 个槽位全 null，'
          '非 null 项 0；packet items=0，无可评原生输出。同方法同节 r1 为 45/73 非 null。'
          '按「失败/未完成不填零」纪律改记为 no-output，不参与必抽覆盖评分。')

mapping = json.load(open(MP, encoding='utf-8'))
target = [m for m in mapping if m['label'] == 'P03']
if not target:
    print('P03 not found in mapping; aborting')
    raise SystemExit(1)
entry = target[0]
if entry.get('status') == 'failed':
    print('P03 already marked failed:', json.dumps(entry, ensure_ascii=False)[:200])
    raise SystemExit(0)

stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
bak = MP + '.bak-before-p03-no-output-' + stamp
shutil.copy2(MP, bak)

entry['status'] = 'failed'
entry['failure_kind'] = 'no_output'
entry['not_a_zero_score'] = True
entry['failure_reason'] = REASON
entry['reclassified_from'] = 'completed'
entry['reclassified_at'] = stamp

json.dump(mapping, open(MP, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

note = {
    'label': 'P03',
    'reclassified_from': 'completed',
    'status': 'failed',
    'failure_kind': 'no_output',
    'reason': REASON,
    'evidence': {
        'run_directory': entry.get('directory'),
        'graph_sha256': entry.get('graph_sha256'),
        'graph_slots': 40,
        'graph_non_null': 0,
        'packet_items': 0,
        'packet_native_nulls': 40,
        'comparison_same_method_section_r1': {'graph_slots': 73, 'graph_non_null': 45},
        'extract_response_present': True,
        'define_response_present': True,
        'audit_status': 'completed',
        'audit_exit_code': 0,
        'audit_issues': [],
        'audit_quality_status': 'not_scored',
    },
    'handling': ('不生成覆盖评分；完成率按 no-output 单列（3 个 iText 失败 + 1 个 EDC 空输出）；'
                 '该包 packet 与空图保留为 EDC 适配版可靠性证据。'),
    'approved_by': 'user',
    'approved_at': stamp,
}
note_path = os.path.join(RV, 'P03_no_output_finding.json')
json.dump(note, open(note_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

print('backup  ->', bak)
print('mapping -> P03 status now:', entry['status'], '| failure_kind:', entry['failure_kind'])
print('note    ->', note_path)
print('mapping status counts:', {s: sum(1 for m in mapping if m['status'] == s) for s in {m['status'] for m in mapping}})
