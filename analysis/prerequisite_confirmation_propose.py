# -*- coding: utf-8 -*-
"""P1-B 5.2 step 5: the end-to-end candidate generation universe (four proposal calls).

The fixed-pool review asks "is this pair a prerequisite?"; this asks "which units are prerequisites at
all?" and is scored apart, never pooled with the fixed-pool numbers. One call per target, single
attempt, no repair; proposals are validated against the frozen unit list and the 8-proposal cap.

Usage: python analysis/prerequisite_confirmation_propose.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
LEDGER = OUT / 'budgets' / 'review.json'
sys.path.insert(0, str(MERGED / 'snapshot' / 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from prospective_native_client import Budget, payload, write  # noqa: E402

MODEL = 'deepseek-v4-flash'
ENDPOINT = 'https://opencode.ai/zen/go/v1/chat/completions'
USD_LIMIT, TOKEN_LIMIT, MAX_TOKENS = 0.60, 800_000, 16_000
# The first pass used MAX_TOKENS=4000; two of the four targets spent the entire completion budget on
# reasoning (finish_reason=length, reasoning_tokens=4000, empty content) and produced no answer at all.
# The cap is raised to 16000 and all four targets are re-run under that single configuration; the two
# superseded 4000-cap responses are kept under `proposal_*_cap4000/` as evidence and are not scored.
ROLES = {'required', 'proof_support', 'principle_support', 'related_or_none'}
SYSTEM = ('你是数学教材的先修关系分析员。给你一个目标知识单元和一份候选单元清单，'
          '请只挑选出**真正构成该目标先修条件**的单元，不要把所有相关单元都列上。'
          '先修指：不掌握它就无法理解或完成目标。直接先修指目标自身的定义/陈述/步骤直接用到它；'
          '间接先修指必须经过另一个单元才用到。'
          '严格只输出 JSON：{"proposals":[{"unit_id":"...","role":"required|proof_support|principle_support",'
          '"directness":"direct|indirect","reason":"..."}]}，最多 8 条，role 为 required 时必须给 directness。')


def validate(answer, task):
    """Validate one proposal set.

    Formatting-level redundancy is normalised by the harness rather than failing the call: a repeated
    unit_id is one proposal (first occurrence kept, the repeat is recorded) and a `directness` field on a
    non-required role is kept for audit as `directness_reported` instead of rejecting the answer. Out-of-
    scope units, more than the proposal cap, an unknown role, a missing reason and a required proposal
    without directness still fail. No semantic content is invented or dropped.
    """
    rows = answer.get('proposals')
    if not isinstance(rows, list):
        raise ValueError('missing proposals')
    allowed = {c['id'] for c in task['candidate_units']}
    if len(rows) > task['max_proposals']:
        raise ValueError('proposal cap exceeded: %d' % len(rows))
    seen, out, duplicates = set(), [], []
    for row in rows:
        unit_id = row.get('unit_id')
        if unit_id not in allowed:
            raise ValueError('out-of-scope unit %r' % unit_id)
        if unit_id in seen:
            duplicates.append(unit_id)
            continue
        seen.add(unit_id)
        role = row.get('role')
        if role not in ROLES:
            raise ValueError('invalid role %r' % role)
        if role == 'required' and row.get('directness') not in {'direct', 'indirect'}:
            raise ValueError('required proposal without directness')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('missing reason')
        entry = {'unit_id': unit_id, 'role': role, 'reason': row['reason']}
        if role == 'required':
            entry['directness'] = row['directness']
        elif row.get('directness') is not None:
            entry['directness_reported'] = row['directness']
        out.append(entry)
    return out, duplicates


def main() -> int:
    tasks = json.loads((OUT / 'proposal_payload.json').read_text(encoding='utf-8'))['tasks']
    key = os.environ.get('OPENCODE_API_KEY')
    if not key:
        raise SystemExit('OPENCODE_API_KEY missing; nothing sent')
    revalidate = '--revalidate' in sys.argv
    for index, task in enumerate(tasks, 1):
        label = 'target_%02d' % index
        if (OUT / 'proposals' / ('%s.json' % label)).exists():
            print('skip (already proposed):', label)
            continue
        if revalidate:
            stored = OUT / 'proposal_responses' / ('%s.json' % label)
            if not stored.is_file():
                print('%s: no stored response' % label)
                continue
            native = json.loads(stored.read_text(encoding='utf-8'))
            content = native['choices'][0]['message']['content']
            try:
                proposals, duplicates = validate(json.loads(content), task)
            except Exception as exc:  # noqa: BLE001
                print('%s still unparsable: %s' % (label, exc))
                continue
            write(OUT / 'proposals' / ('%s.json' % label),
                  {'target_id': task['target']['id'], 'target_name': task['target']['name'],
                   'proposals': proposals, 'normalised_duplicate_unit_ids': duplicates})
            print('%s %s: %d proposals (revalidated, dup %s)'
                  % (label, task['target']['name'], len(proposals), duplicates or 'none'))
            continue
        question = json.dumps(task, ensure_ascii=False)
        route, body = payload(MODEL, SYSTEM, question, MAX_TOKENS)
        body.update(stream=False)
        budget = Budget(LEDGER, MODEL, USD_LIMIT, TOKEN_LIMIT)
        slot = budget.reserve(body, MAX_TOKENS, label)
        write(OUT / 'proposal_requests' / ('%s.json' % label), {'endpoint': ENDPOINT, 'body': body})
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode('utf-8'),
                                         headers={'Authorization': 'Bearer ' + key,
                                                  'Content-Type': 'application/json',
                                                  'User-Agent': 'MaFKG-prereq-confirmation/1.0',
                                                  'x-opencode-session': 'prereq-confirmation-v2'}, method='POST')
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                raw = response.read().decode('utf-8')
            if key in raw:
                raise RuntimeError('credential echo; response not persisted')
            native = json.loads(raw)
            write(OUT / 'proposal_responses' / ('%s.json' % label), native)
            write(OUT / 'proposal_transport' / ('%s.json' % label), {
                'http_status': 200, 'elapsed_seconds': round(time.monotonic() - started, 2),
                'usage': native.get('usage'), 'attempts': 1})
            budget.settle(slot, native.get('usage'))
        except Exception as exc:  # noqa: BLE001
            error = {'type': type(exc).__name__, 'attempts': 1}
            if isinstance(exc, urllib.error.HTTPError):
                error.update(http_status=exc.code,
                             body=exc.read().decode('utf-8', errors='replace').replace(key, '[REDACTED]')[:2000])
            write(OUT / 'proposal_errors' / ('%s.json' % label), error)
            budget.settle(slot, None)
            raise SystemExit('proposal call failed, no retry: %s' % error['type'])
        content = native['choices'][0]['message']['content']
        try:
            proposals, duplicates = validate(json.loads(content), task)
        except Exception as exc:  # noqa: BLE001
            write(OUT / 'proposal_unparsed' / ('%s.json' % label), {'error': str(exc), 'content': content[:4000]})
            print('%s UNPARSABLE (%s)' % (label, exc))
            continue
        write(OUT / 'proposals' / ('%s.json' % label),
              {'target_id': task['target']['id'], 'target_name': task['target']['name'],
               'proposals': proposals, 'normalised_duplicate_unit_ids': duplicates})
        ledger = json.loads(LEDGER.read_text(encoding='utf-8'))
        print('%s %s: %d proposals | ledger %.4f USD / %d tokens'
              % (label, task['target']['name'], len(proposals), ledger['accounting_usd'],
                 ledger['accounting_tokens']), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
