# -*- coding: utf-8 -*-
"""Smoke-test whether the frozen Go endpoint accepts a raised per-request output cap.

Sends exactly one minimal request at the requested limit and records the transport
evidence under the merged experiment's freeze directory. No prompt content beyond
a trivial probe is sent.
"""
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
OUT = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09\freeze')
BASE = 'https://opencode.ai/zen/go/v1'
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 64000


def main() -> int:
    key = os.environ.get('OPENCODE_API_KEY')
    if not key:
        print('OPENCODE_API_KEY missing in this process environment')
        return 2
    body = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "system", "content": "Reply with the single word OK."},
                     {"role": "user", "content": "Reply OK."}],
        "max_tokens": LIMIT,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    req = urllib.request.Request(
        BASE + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key,
                 'User-Agent': 'MaFKG-prospective-experiment/1.0',
                 'x-opencode-session': 'merged-three-section-cap-smoke'},
        method='POST')
    started = time.time()
    record = {'requested_max_tokens': LIMIT, 'endpoint': BASE + '/chat/completions', 'model': 'deepseek-v4-flash'}
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        status = e.code
    except Exception as e:  # noqa: BLE001
        print('transport error:', type(e).__name__, e)
        record.update({'error': '%s: %s' % (type(e).__name__, e)})
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / ('cap_smoke_%d.json' % LIMIT)).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return 1
    elapsed = time.time() - started
    record.update({'status': status, 'elapsed_seconds': round(elapsed, 2)})
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        obj = None
        record['raw_head'] = raw[:400]
    if isinstance(obj, dict):
        if 'error' in obj:
            record['error'] = obj['error']
        choice = (obj.get('choices') or [{}])[0]
        record['finish_reason'] = choice.get('finish_reason')
        record['content_head'] = ((choice.get('message') or {}).get('content') or '')[:80]
        record['usage'] = obj.get('usage')
        record['model_returned'] = obj.get('model')
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / ('cap_smoke_%d.json' % LIMIT)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2)[:900])
    print('wrote ->', path)
    return 0 if status == 200 and not record.get('error') else 1


if __name__ == '__main__':
    raise SystemExit(main())
