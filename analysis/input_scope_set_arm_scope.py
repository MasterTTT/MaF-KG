# -*- coding: utf-8 -*-
"""Make the input-scope review mapping pool by arm *and* scope.

`merged_score.py` pools reviewed repeats per `arm`. In the input-scope round each prefix has its own
frozen reference universe, so pooling S1 and S2 would mix two different denominators. Rewriting the
private mapping's `arm` to `"<method>:<scope>"` keeps the scorer's own aggregation correct without
touching any packet or review file.

Usage: python analysis/input_scope_set_arm_scope.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
REVIEW = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope' / 'review_scope'
sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    path = REVIEW / 'mapping_private.json'
    mapping = json.loads(path.read_text(encoding='utf-8'))
    for row in mapping:
        if ':' not in row['arm']:
            row['arm'] = '%s:%s' % (row['method'], row['section'])
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in mapping:
        print('%-4s arm=%-10s section=%-3s repeat=%s status=%s'
              % (row['label'], row['arm'], row['section'], row['repeat'], row['status']))
    print('rewrote ->', path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
