# -*- coding: utf-8 -*-
"""Fallback: re-run an iText repeat whose isolated-entity repairs exhaust the 120-call ceiling.

The adapter's chat ceiling is its own parameter (DEFAULT_MAX_CHAT_CALLS = 120); the runner never
passes it, so raising the manifest's task ceiling alone would have no effect. This script therefore
patches the variant's runner to forward the manifest value, raises it, rebinds the artifact hashes
(the manifest audits them), clears the failed repeat, and re-queues it.

Run only when a repeat actually failed at the ceiling. Usage:
  python analysis/merged_itext_raise_ceiling.py [--repeat 2] [--calls 220]
"""
import argparse, json, shutil, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = NEW / 'itext_v3'
sys.stdout.reconfigure(encoding='utf-8')

ANCHOR = ("            result = run(vendor, texts, client, embed, directory,\n"
          "                         no_progress_limit=manifest.get(\"itext_no_progress_limit\"),\n"
          "                         preserve_symbols=bool(manifest.get(\"itext_preserve_symbols\")))")
PATCHED = ("            result = run(vendor, texts, client, embed, directory,\n"
           "                         max_chat_calls=int(manifest.get(\"task_max_calls\", 120)),\n"
           "                         no_progress_limit=manifest.get(\"itext_no_progress_limit\"),\n"
           "                         preserve_symbols=bool(manifest.get(\"itext_preserve_symbols\")))")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repeat', type=int, default=2, choices=(1, 2))
    ap.add_argument('--calls', type=int, default=220)
    args = ap.parse_args()

    runner = OUT / 'snapshot' / 'scripts' / 'run_external_baselines.py'
    text = runner.read_text(encoding='utf-8')
    if ANCHOR in text:
        text = text.replace(ANCHOR, PATCHED)
        runner.write_text(text, encoding='utf-8')
        print('runner patched to forward max_chat_calls')
    elif PATCHED in text:
        print('runner already patched')
    else:
        raise SystemExit('anchor not found in runner; refusing to guess')

    manifest_path = OUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    previous = manifest.get('task_max_calls')
    manifest['task_max_calls'] = args.calls
    manifest['task_ceiling_note'] = ('Isolated-entity repairs exhausted the 120-call ceiling in repeat %d; the '
                                     'runner now forwards the manifest ceiling and it is raised to %d. This is a task '
                                     'guard, not the budget gate (the shared iText ledger remains the budget gate).'
                                     % (args.repeat, args.calls))
    for job in manifest['jobs']:
        if job['repeat'] != args.repeat:
            continue
        directory = Path(job['directory'])
        if directory.exists():
            shutil.rmtree(directory)
            print('cleared failed repeat dir ->', directory.name)
        job['status'] = 'pending'
        for key in ('exit_code', 'elapsed_seconds', 'graph_sha256'):
            job.pop(key, None)

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    files = sorted((OUT / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'prompts').glob('*.txt')) + \
            [OUT / 'snapshot' / 'domain.json', OUT / 'snapshot' / 'embedding_lock.json'] + \
            sorted((NEW / 'input').glob('*.md')) + \
            [NEW / 'input' / 'provenance.json', NEW / 'input' / 'sections.json', NEW / 'work' / 'merged_input.md'] + \
            sorted((NEW / 'plans' / 'merged').glob('*.json'))
    manifest['common'] = R.CONFIG
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    R.setup()
    baseline.audit_prepared(manifest)
    print('ceiling %s -> %s | AUDIT OK | jobs %s' % (previous, args.calls,
                                                     [(j['repeat'], j['status']) for j in manifest['jobs']]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
