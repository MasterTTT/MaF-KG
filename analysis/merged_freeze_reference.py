# -*- coding: utf-8 -*-
"""Freeze the merged reference: mark status, record sha256, append to the freeze record."""
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
P = NEW / 'reference' / 'reference_merged_v1.json'
ref = json.loads(P.read_text(encoding='utf-8'))
ref['status'] = 'FROZEN — user-approved 2026-10-09; do not modify; any change forces a full re-run'
ref['frozen_at'] = datetime.now(timezone.utc).isoformat()
ref['user_approval'] = ('User approved all seven adjudications: keep *-marked error-estimation items as M/detail; '
                        'accept the ch3_s2 split of iterative use (L-N4); accept the added L-N5/L-N12 as M; keep '
                        'D-N1/D-N2 in one teaching group; keep the auxiliary-function method as O; keep the '
                        'example-derived D-N13 as O; keep the finite-increment formula as a separate M/detail item.')
P.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding='utf-8')
sha = hashlib.sha256(P.read_bytes()).hexdigest().upper()
print('reference frozen:', P.name)
print('sha256:', sha)
print('counts:', json.dumps(ref['counts'], ensure_ascii=False))

rec = NEW / 'freeze' / 'FREEZE_RECORD.md'
text = rec.read_text(encoding='utf-8')
text = text.replace(
    '状态：**配置与输入已冻结并通过官方 `audit_prepared`；参考集待冻结（候选已产出，等用户审理）。参考集冻结前不得查看任何方法输出。**',
    '状态：**配置、输入与参考集全部冻结（2026-10-09 用户批准七条裁定后冻结）。冻结后不得修改参考；若必须修改，整轮重跑。**')
text = text.replace(
    '- 冻结后写入 `reference/reference_merged_v1.json`，sha256 记入本文件；**冻结后不得修改**，若必须修改则整轮重跑。',
    '- 冻结件：`reference/reference_merged_v1.json`，**sha256 = `%s`**（2, 见下）；冻结后不得修改，若必须修改则整轮重跑。\n'
    '- 规模：42 条目（35 M / 7 O）、45 条可解析节内关系、2 条跨节/跨章关系、15 对必须分离、14 个边界组、10 条范围外引用（只记录不评分）。\n'
    '- 用户批准的七条裁定见参考文件 `user_approval` 字段；与旧 `reference_v1.json` 不具可比性（ch3_s2 由 6 条扩到 12 条）。' % sha)
rec.write_text(text, encoding='utf-8')
print('freeze record updated ->', rec)
