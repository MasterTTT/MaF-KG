# -*- coding: utf-8 -*-
"""Fix the replay-cache wiring: the hook receives a synthetic hook job, not the manifest job.

`run_external_baselines.production()` calls `install_hooks(module, section, hook_job, directory)` with
`hook_job = {"name": ..., "memory_node_bytes": ..., "memory_relation_bytes": ...}`, so a per-job
`replay_cache` lookup would always be None and the frozen-cache replay would silently never activate.
The section dict is passed through unchanged, so the replay directory belongs there. Sections are
therefore split per scope *and* repeat (s1r1, s1r2, s2r1, s2r2), each carrying its own replay
directory, and the hook reads it from the section.

No API calls, no ledger changes.
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
SCOPES = (('s1', 574), ('s2', 936))
sys.stdout.reconfigure(encoding='utf-8')

HOOK_OLD = '    replay_cache = job.get("replay_cache")'
HOOK_NEW = ('    # The hook job is synthetic (name/memory only); the replay directory travels on the\n'
            '    # section, which install_hooks receives unchanged.\n'
            '    replay_cache = section.get("replay_cache")')


def patch(path: Path, replacements):
    text = path.read_text(encoding='utf-8')
    for anchor, replacement in replacements:
        if replacement in text:
            print('already patched ->', anchor.strip()[:60])
            continue
        if text.count(anchor) != 1:
            raise SystemExit('anchor not unique in %s: %r (%d)' % (path.name, anchor[:60], text.count(anchor)))
        text = text.replace(anchor, replacement)
    path.write_text(text, encoding='utf-8')
    print('patched ->', path.relative_to(OUT))


def main() -> int:
    patch(OUT / 'snapshot' / 'scripts' / 'run_direct_extraction_baseline.py', [(HOOK_OLD, HOOK_NEW)])

    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    base_sections = {s['id']: s for s in manifest['sections']}
    sections = []
    for repeat in (1, 2):
        for scope, last_line in SCOPES:
            entry = dict(base_sections[scope])
            entry['id'] = '%sr%d' % (scope, repeat)
            entry['scope'] = scope
            entry['repeat'] = repeat
            entry['replay_cache'] = str(MERGED / 'runs' / ('r%d' % repeat) / 'merged' / 'maf' / 'cache')
            sections.append(entry)
    manifest['sections'] = sections
    for job in manifest['jobs']:
        job['section'] = '%sr%d' % (job['section'], job['repeat'])
        job.pop('replay_cache', None)
    manifest['replay_cache_note'] = ('Sections are split per scope and repeat so each MaF job replays from the '
                                     'matching repeat\'s frozen S3 chunk cache. The hook job passed to install_hooks '
                                     'is synthetic, so the replay directory must travel on the section.')

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    R.setup()
    manifest['common'] = R.CONFIG
    files = sorted((OUT / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'prompts').glob('*.txt')) + \
            [OUT / 'snapshot' / 'domain.json', OUT / 'snapshot' / 'embedding_lock.json'] + \
            sorted((OUT / 'work').glob('*/merged_input.md')) + \
            sorted((OUT / 'plans').glob('*/chunks.json')) + sorted((OUT / 'plans').glob('*/direct_chunk.json')) + \
            sorted((OUT / 'reference').glob('*.json'))
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    baseline.audit_prepared(manifest)
    print('AUDIT OK | sections %s' % [(s['id'], Path(s['replay_cache']).parent.parent.name) for s in sections])
    print('jobs:')
    for job in manifest['jobs']:
        section = next(s for s in sections if s['id'] == job['section'])
        print('  r%d %-4s %-6s -> %s | replay=%s' % (job['repeat'], section['scope'], job['method'],
                                                     Path(job['directory']).name,
                                                     bool(section.get('replay_cache'))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
