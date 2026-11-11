# -*- coding: utf-8 -*-
"""Record every EDC run whose Define stage was lost to the ASCII-colon parser, across both experiments.

The pinned upstream parse_relation_definition (edc/utils/llm_utils.py L143-145) accepts only ASCII
":" while the model sometimes answers with the fullwidth U+FF1A. When that happens every definition
line is dropped as "missing colon", the target schema stays empty, and the alignment stage emits one
null slot per extracted triplet. Such a run is a parser-compatibility failure: it must not be read as
"the method found nothing", and it must not enter any zero-coverage sensitivity analysis.

Output: evaluation/external_baselines_2026-10-08/edc_parse_failure_evidence.json
"""
import json, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
OLD = ROOT / 'evaluation' / 'external_baselines_2026-10-08'
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
sys.stdout.reconfigure(encoding='utf-8')


def inspect(audit_path: Path):
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    events = audit.get('parse_events') or []
    defs = [e for e in events if e.get('parser') == 'parse_relation_definition']
    trips = [e for e in events if e.get('parser') == 'parse_raw_triplets']
    result = audit.get('result') or {}
    canonical = result.get('canonical_triplets') or []
    nulls = sum(1 for section in canonical for x in (section if isinstance(section, list) else []) if x is None)
    nonnull = sum(1 for section in canonical for x in (section if isinstance(section, list) else []) if x is not None)
    missing_colon = sum(1 for e in defs for x in (e.get('dropped') or []) if x.get('reason') == 'missing colon')
    definitions = len(defs[0].get('parsed') or {}) if defs else None
    triplets = len(trips[0].get('parsed') or []) if trips else None
    calls = len(audit.get('chat_calls') or [])
    return {
        'path': str(audit_path.parent),
        'chat_calls': calls,
        'definitions_parsed': definitions,
        'lines_dropped_missing_colon': missing_colon,
        'triplets_parsed': triplets,
        'canonical_null_slots': nulls,
        'canonical_non_null': nonnull,
        'parse_failure': bool(missing_colon) and not definitions,
        'alignment_ran': calls > 2,
    }


def main() -> int:
    runs = {}
    for audit in sorted(OLD.glob('formal01/*/*/edc/edc_audit.json')):
        runs['formal01/' + audit.parent.parent.parent.name + '/' + audit.parent.parent.name] = inspect(audit)
    for audit in sorted(NEW.glob('runs/*/merged/edc/edc_audit.json')):
        runs['merged-v2/' + audit.parent.parent.parent.name] = inspect(audit)
    for audit in sorted(NEW.glob('edc_v3/*/merged/edc/edc_audit.json')):
        runs['merged-v3/' + audit.parent.parent.parent.name] = inspect(audit)
    failed = {k: v for k, v in runs.items() if v['parse_failure']}
    payload = {
        'cause': ('pinned edc/utils/llm_utils.py parse_relation_definition requires ASCII ":"; the model '
                  'answered with fullwidth U+FF1A, so every definition line was dropped as "missing colon"'),
        'consequence': ('empty target schema -> alignment emits one null slot per extracted triplet; the run is '
                        'a parser-compatibility failure, not evidence that the method found nothing'),
        'scoring_rule': ('such runs are never scored as zero coverage and never enter a zero-coverage sensitivity '
                         'analysis; they are reported as execution/parse failures and, where possible, re-run '
                         'with the separator normalised for parsing only'),
        'runs': runs,
        'parse_failures': failed,
        'n_runs': len(runs),
        'n_parse_failures': len(failed),
    }
    target = OLD / 'edc_parse_failure_evidence.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for key, info in runs.items():
        flag = 'PARSE FAILURE' if info['parse_failure'] else ('ok' if info['definitions_parsed'] else 'no definitions')
        print('%-28s calls=%-3d defs=%-4s missing_colon=%-3d triples=%-4s nulls=%-3d nonnull=%-3d %s' % (
            key, info['chat_calls'], info['definitions_parsed'], info['lines_dropped_missing_colon'],
            info['triplets_parsed'], info['canonical_null_slots'], info['canonical_non_null'], flag))
    print('parse failures: %d of %d runs' % (payload['n_parse_failures'], payload['n_runs']))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
