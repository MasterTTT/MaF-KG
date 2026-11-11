# -*- coding: utf-8 -*-
"""P1-B 5.1 runner: send the frozen context-arm batches and store validated verdicts.

One call per batch, single attempt, no retry, no JSON repair — the same discipline as the other rounds.
Arm B runs first; its measured usage decides whether arm C is started at all, so the approved gate
cannot be overshot by launching both arms blindly.

Usage:
  python analysis/prerequisite_context_run.py --arm B            # send arm B (4 calls)
  python analysis/prerequisite_context_run.py --arm C            # send arm C if the gate still allows
  python analysis/prerequisite_context_run.py --report           # ledger and packet sizes only
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_context'
LEDGER = OUT / 'budgets' / 'review.json'
sys.path.insert(0, str(MERGED / 'snapshot' / 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from prospective_native_client import Budget, payload, write  # noqa: E402

MODEL = 'deepseek-v4-flash'
ENDPOINT = 'https://opencode.ai/zen/go/v1/chat/completions'
USD_LIMIT = 0.20
TOKEN_LIMIT = 300_000
MAX_TOKENS = 12000
ROLES = {'required', 'proof_support', 'principle_support', 'related_or_none', 'uncertain'}


def pair_names(base):
    """Endpoint names for either payload shape: 5.1 rows use source/target, 5.2 rows target/prerequisite."""
    if 'source' in base and 'target' in base:
        return base['source'].get('name'), base['target'].get('name')
    return base.get('target', {}).get('name'), base.get('prerequisite', {}).get('name')


def validate(answer, batch):
    """Validate one batch and match verdicts to rows.

    Batches carry global indices, but the model sometimes renumbers 0..n-1; when the returned index set is
    exactly the batch's positional range the verdicts are matched by order instead. That is a harness fix
    and it edits no model output. Both payload shapes are accepted (5.1 source/target, 5.2
    target/prerequisite).
    """
    rows = answer.get('verdicts')
    if not isinstance(rows, list) or len(rows) != len(batch):
        raise ValueError('review returned %s verdicts for %d rows'
                         % (len(rows) if isinstance(rows, list) else '?', len(batch)))
    by_index = {row['index']: row for row in batch}
    returned = [r.get('index') for r in rows]
    if set(returned) == set(by_index):
        resolve = lambda row: by_index[row['index']]                      # noqa: E731
    elif set(returned) == set(range(len(batch))):
        resolve = lambda row: batch[row['index']]                         # noqa: E731
    else:
        raise ValueError('review indices do not match the batch: %s vs %s'
                         % (sorted(map(str, returned)), sorted(by_index)))
    out = []
    for row in rows:
        role = row.get('role')
        if role not in ROLES:
            raise ValueError('invalid role %r' % role)
        if role == 'required' and row.get('directness') not in {'direct', 'indirect'}:
            raise ValueError('missing directness for a required verdict')
        if role != 'required' and row.get('directness') is not None:
            raise ValueError('unexpected directness for role %r' % role)
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('missing reason')
        base = resolve(row)
        target_name, prerequisite_name = pair_names(base)
        entry = {'index': base['index'], 'target': target_name, 'prerequisite': prerequisite_name,
                 'label': role, 'reason': row['reason']}
        if role == 'required':
            entry['directness'] = row['directness']
        out.append(entry)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--arm', choices=('B', 'C', 'ku', 'ctx'))
    ap.add_argument('--dir', default=None, help='experiment root (defaults to the 5.1 context folder)')
    ap.add_argument('--system', default=None, help='review system prompt path (defaults to the 5.1 manifest)')
    ap.add_argument('--only', nargs='*', help='batch stems to send (default: every batch without a verdict yet)')
    ap.add_argument('--revalidate', action='store_true',
                    help='re-validate already stored responses instead of sending anything (no ledger entries)')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()

    global OUT, LEDGER, USD_LIMIT, TOKEN_LIMIT
    if args.dir:
        OUT = Path(args.dir).resolve()
        LEDGER = OUT / 'budgets' / 'review.json'
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    gate = manifest.get('budget_gate') or {}
    if gate.get('usd') and gate.get('tokens'):
        USD_LIMIT, TOKEN_LIMIT = float(gate['usd']), int(gate['tokens'])
    system_path = Path(args.system or manifest['review_system'])
    system = system_path.read_text(encoding='utf-8')
    if not args.system and manifest['review_system_sha256'] != __import__('hashlib').sha256(
            Path(manifest['review_system']).read_bytes()).hexdigest():
        raise SystemExit('review system prompt changed; refusing to run')

    if args.report or not args.arm:
        ledger = json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else None
        print('gate: %.2f USD / %d tokens' % (USD_LIMIT, TOKEN_LIMIT))
        if ledger:
            print('spent: %.4f USD / %d tokens | requests %d'
                  % (ledger['accounting_usd'], ledger['accounting_tokens'], len(ledger['requests'])))
        for arm in ('B', 'C', 'ku', 'ctx'):
            folder = OUT / ('arm_%s' % arm if arm in ('ku', 'ctx') else 'arm%s' % arm)
            sizes = [p.stat().st_size for p in sorted(folder.glob('batch_*.json'))]
            print('arm %s: %d batches, %d bytes total' % (arm, len(sizes), sum(sizes)))
        return 0

    folder = OUT / ('arm_%s' % args.arm if args.arm in ('ku', 'ctx') else 'arm%s' % args.arm)
    batches = sorted(folder.glob('batch_*.json'))
    failures = []

    if args.revalidate:
        recovered = 0
        for path in batches:
            response = folder / 'responses' / ('%s.json' % path.stem)
            if not response.is_file():
                print('%s %s: no stored response' % (args.arm, path.stem))
                continue
            batch = json.loads(path.read_text(encoding='utf-8'))
            native = json.loads(response.read_text(encoding='utf-8'))
            try:
                verdicts = validate(json.loads(native['choices'][0]['message']['content']), batch)
            except Exception as exc:  # noqa: BLE001
                print('%s %s: still unparsable (%s)' % (args.arm, path.stem, exc))
                continue
            write(folder / 'verdicts' / ('%s.json' % path.stem), {'verdicts': verdicts})
            recovered += 1
            print('%s %s: revalidated %d verdicts' % (args.arm, path.stem, len(verdicts)))
        merged = []
        for path in sorted((folder / 'verdicts').glob('batch_*.json')):
            merged += json.loads(path.read_text(encoding='utf-8'))['verdicts']
        write(folder / 'predictions.json', {'verdicts': merged, 'revalidated': True})
        print('arm %s: %d batches revalidated, %d verdicts merged' % (args.arm, recovered, len(merged)))
        return 0
    key = os.environ.get('OPENCODE_API_KEY')
    if not key:
        raise SystemExit('OPENCODE_API_KEY missing; nothing sent')
    session = manifest.get('session') or 'prereq-context-arms'
    for path in batches:
        if args.only and path.stem not in args.only:
            continue
        if (folder / 'verdicts' / ('%s.json' % path.stem)).exists():
            print('skip (already judged): %s' % path.stem, flush=True)
            continue
        batch = json.loads(path.read_text(encoding='utf-8'))
        route, body = payload(MODEL, system, json.dumps(batch, ensure_ascii=False), MAX_TOKENS)
        body.update(stream=False)
        budget = Budget(LEDGER, MODEL, USD_LIMIT, TOKEN_LIMIT)
        index = budget.reserve(body, MAX_TOKENS, '%s/%s' % (args.arm, path.stem))
        write(folder / 'requests' / ('%s.json' % path.stem), {'endpoint': ENDPOINT, 'body': body})
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode('utf-8'),
                                         headers={'Authorization': 'Bearer ' + key,
                                                  'Content-Type': 'application/json',
                                                  'User-Agent': 'MaFKG-prerequisite-context/1.0',
                                                  'x-opencode-session': session}, method='POST')
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                raw = response.read().decode('utf-8')
            if key in raw:
                raise RuntimeError('credential echo; response not persisted')
            native = json.loads(raw)
            write(folder / 'responses' / ('%s.json' % path.stem), native)
            write(folder / 'transport' / ('%s.json' % path.stem), {
                'http_status': 200, 'elapsed_seconds': round(time.monotonic() - started, 2),
                'usage': native.get('usage'), 'attempts': 1})
            budget.settle(index, native.get('usage'))
        except Exception as exc:  # noqa: BLE001
            error = {'type': type(exc).__name__, 'attempts': 1}
            if isinstance(exc, urllib.error.HTTPError):
                error.update(http_status=exc.code,
                             body=exc.read().decode('utf-8', errors='replace').replace(key, '[REDACTED]')[:2000])
            write(folder / 'errors' / ('%s.json' % path.stem), error)
            budget.settle(index, None)
            raise SystemExit('call failed, no retry: %s' % error['type'])
        choices = native.get('choices', [])
        if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
            raise SystemExit('non-complete response in %s' % path.name)
        try:
            content = choices[0]['message']['content']
            verdicts = validate(json.loads(content), batch)
        except Exception as exc:  # noqa: BLE001
            # A contract violation is recorded per batch and the remaining batches still run, so one bad
            # reply cannot cost the whole arm. The reply is never repaired or re-asked automatically.
            write(folder / 'unparsed' / ('%s.json' % path.stem), {'error': str(exc), 'content': content[:8000]})
            print('%s %s: UNPARSABLE (%s) -- recorded, continuing' % (args.arm, path.stem, exc), flush=True)
            failures.append(path.stem)
            continue
        write(folder / 'verdicts' / ('%s.json' % path.stem), {'verdicts': verdicts})
        ledger = json.loads(LEDGER.read_text(encoding='utf-8'))
        print('%s %s: %d verdicts | ledger %.4f USD / %d tokens'
              % (args.arm, path.stem, len(verdicts), ledger['accounting_usd'], ledger['accounting_tokens']),
              flush=True)

    merged = []
    for path in sorted((folder / 'verdicts').glob('batch_*.json')):
        merged += json.loads(path.read_text(encoding='utf-8'))['verdicts']
    write(folder / 'predictions.json', {'verdicts': merged, 'unparsable_batches': failures})
    print('arm %s done: %d verdicts, %d unparsable batches %s -> %s'
          % (args.arm, len(merged), len(failures), failures, folder / 'predictions.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
