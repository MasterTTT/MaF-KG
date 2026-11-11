# -*- coding: utf-8 -*-
"""Extract progress-guard provenance for the merged iText arms from the run logs.

The pinned upstream loop prints "[INFO] The isolated entities are" once per repair pass and
only exits when the pass returns no isolated entities. Comparison of the unguarded and
progress-bounded stdout logs therefore shows, per repeat:

  * how many isolated-entity passes ran,
  * which entity names were still isolated at each pass,
  * whether the bounded arm stopped on its own progress (loop emptied) or was cut by the guard.

Output: evaluation/external_baselines_merged_2026-10-09/guarded/guard_evidence.json
"""
import hashlib, json, re, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')
MARKER = '[INFO] The isolated entities are'
NAME_RE = re.compile(r"'name':\s*'((?:[^'\\]|\\.)*)'")


def scan(path):
    if not path.exists():
        return None
    raw = path.read_text(encoding='utf-8', errors='replace')
    lines = raw.splitlines()
    passes = []
    for index, line in enumerate(lines):
        if MARKER not in line:
            continue
        chunk = line
        # the entity dump can continue on following lines; collect until the next log tag
        for follow in lines[index + 1:index + 40]:
            if follow.startswith('[') or 'INFO' == follow[:4]:
                break
            chunk += follow
        names = []
        for name in NAME_RE.findall(chunk):
            name = name.replace("\\'", "'")
            if name not in names:
                names.append(name)
        passes.append({'log_line': index + 1, 'isolated_names': names})
    return {
        'path': str(path),
        'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
        'size_bytes': len(raw.encode('utf-8')),
        'isolated_entity_passes': len(passes),
        'passes': passes,
    }


def main() -> int:
    targets = {
        'itext2kg-r1-unguarded': NEW / 'runs' / 'r1' / 'merged' / 'itext2kg' / 'stdout.log',
        'itext2kg-r2-unguarded-cancelled': NEW / 'runs' / 'r2' / 'merged' / 'itext2kg' / 'stdout.log',
        'itext2kg-progress-bound-3-r1': NEW / 'guarded' / 'r1' / 'merged' / 'itext2kg' / 'stdout.log',
        'itext2kg-progress-bound-3-r2': NEW / 'guarded' / 'r2' / 'merged' / 'itext2kg' / 'stdout.log',
    }
    out = {}
    for key, path in targets.items():
        info = scan(path)
        if info is None:
            out[key] = {'status': 'log_absent'}
            continue
        out[key] = info
    payload = {
        'marker': MARKER,
        'interpretation': (
            'The pinned upstream loop repeats until a pass returns no isolated entities. The unguarded arm '
            'never reaches that state and dies at the 120-chat-call ceiling. The progress-bounded arm breaks '
            'out after 3 consecutive passes that do not reduce the isolated set, keeps those entities in the '
            'graph and continues. Pass counts below are direct log evidence of that behaviour.'
        ),
        'arms': out,
    }
    target = NEW / 'guarded' / 'guard_evidence.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for key, info in out.items():
        if info.get('status') == 'log_absent':
            print('%-34s log absent' % key)
        else:
            last = info['passes'][-1]['isolated_names'] if info['passes'] else []
            print('%-34s passes=%d  last_isolated=%d %s' % (key, info['isolated_entity_passes'], len(last), last[:3]))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
