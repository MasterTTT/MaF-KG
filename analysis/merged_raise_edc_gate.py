# -*- coding: utf-8 -*-
"""Raise the EDC gate only (user-approved 2026-10-09: +$0.75 / +1.25M tokens).

The frozen runner derives budget limits from the per-method control manifest, and
the ledger must record the same limits. Nothing else is touched: the shared
snapshot, plans, artifacts and the other three control manifests stay as frozen.
"""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
USD, TOKENS = 2.25, 3_750_000

ctl = NEW / 'ctl' / 'edc' / 'manifest.json'
mf = json.loads(ctl.read_text(encoding='utf-8'))
print('before: usd=%s tokens=%s' % (mf['per_method_budget_usd'], mf['per_method_token_limit']))
mf['per_method_budget_usd'] = USD
mf['per_method_token_limit'] = TOKENS
mf['gate_exception'] = ('EDC-only raise approved by the user on 2026-10-09 after r1 returned an empty aligned graph '
                        '(131/131 null slots) and r2 was stopped by the pre-request token gate; '
                        'other arms keep the frozen $1.50 / 2.5M gate.')
for job in mf['jobs']:
    if job['repeat'] == 2:
        job['status'] = 'pending'
        for k in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
            job.pop(k, None)
ctl.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding='utf-8')
print('after : usd=%s tokens=%s | jobs=%s' % (mf['per_method_budget_usd'], mf['per_method_token_limit'],
                                             [(j['repeat'], j['status']) for j in mf['jobs']]))

led = NEW / 'budgets' / 'edc.json'
d = json.loads(led.read_text(encoding='utf-8'))
print('ledger before: usd_limit=%s token_limit=%s accounting=%s/%s' % (
    d['usd_limit'], d['token_limit'], round(d['accounting_usd'], 3), d['accounting_tokens']))
d['usd_limit'], d['token_limit'] = USD, TOKENS
d['limit_raise'] = {'approved': 'user 2026-10-09', 'from': [1.50, 2_500_000], 'to': [USD, TOKENS],
                    'reason': 'EDC merged-input r1 produced an empty aligned graph and r2 was gated; '
                              'one more repeat to distinguish systematic failure from run variance.'}
led.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
print('ledger after : usd_limit=%s token_limit=%s' % (d['usd_limit'], d['token_limit']))

rec = NEW / 'freeze' / 'FREEZE_RECORD.md'
text = rec.read_text(encoding='utf-8')
if 'EDC 门禁例外' not in text:
    text += """

## 附：EDC 门禁例外（2026-10-09，用户批准）

合并输入下 EDC 第一轮 extract 成功（124 条三元组）、define 成功，但本地 Target Alignment 使 131 个槽位全部为 null（空图）；
第二轮在发送前被 token 门禁拦下（累计 2.38M/2.5M）。为区分"系统性失败"与"运行间波动"，经用户批准**仅对 EDC** 把门禁提升到
**$2.25 / 3,750,000 token**（其余三臂保持 $1.50 / 2.5M），重置 r2 后重跑一次；账本累计消费不清零。
"""
    rec.write_text(text, encoding='utf-8')
    print('freeze record updated with the EDC gate exception')
