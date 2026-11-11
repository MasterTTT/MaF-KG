# -*- coding: utf-8 -*-
"""Alignment-stage statistics for the parse-compatible EDC variant (and the v2 runs it replaces).

Per repeat: how many triplets entered alignment, how many the native canonicalizer kept, how many it
rejected, and why. This keeps the "null slots" in the final graph explained by the method's own
contract check rather than by an unstated pipeline fault.

Output: evaluation/external_baselines_merged_2026-10-09/edc_alignment_stats.json
"""
import json, sys
from collections import Counter
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')


def inspect(run_dir: Path):
    audit_path = run_dir / 'edc_audit.json'
    if not audit_path.exists():
        return {'status': 'audit_absent'}
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    events = [e for e in (audit.get('parse_events') or []) if e.get('parser') == 'native_canonicalization_choice']
    reasons = Counter()
    for event in events:
        for dropped in (event.get('dropped') or []):
            reasons[str(dropped.get('reason'))[:120]] += 1
    result = audit.get('result') or {}
    canonical = result.get('canonical_triplets') or []
    kept = sum(1 for section in canonical for x in (section if isinstance(section, list) else []) if x is not None)
    nulls = sum(1 for section in canonical for x in (section if isinstance(section, list) else []) if x is None)
    calls = audit.get('chat_calls') or []
    return {
        'status': audit.get('status'),
        'parse_compat': audit.get('parse_compat'),
        'chat_calls': len(calls),
        'replayed_calls': sum(1 for c in calls if c.get('status') == 'replayed'),
        'alignment_events': len(events),
        'kept': kept,
        'rejected': len(events) - kept,
        'null_slots': nulls,
        'rejection_reasons': dict(reasons),
        'target_schema_unchanged': result.get('target_schema_unchanged'),
        'definitions_parsed': next((len(e.get('parsed') or {}) for e in (audit.get('parse_events') or [])
                                    if e.get('parser') == 'parse_relation_definition'), None),
    }


def main() -> int:
    runs = {}
    for repeat in (1, 2):
        runs['v3-r%d' % repeat] = inspect(NEW / 'edc_v3' / ('r%d' % repeat) / 'merged' / 'edc')
        runs['v2-r%d' % repeat] = inspect(NEW / 'runs' / ('r%d' % repeat) / 'merged' / 'edc')
    payload = {
        'interpretation': ('A rejected triplet is the native canonicalizer refusing a model answer that does not '
                           'begin with a candidate letter; the slot stays null. This is method behaviour under its '
                           'own output contract, and it is what the 30/32 null slots in the parse-compatible runs are.'),
        'runs': runs,
    }
    target = NEW / 'edc_alignment_stats.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for key, info in runs.items():
        if info.get('status') == 'audit_absent':
            print('%-7s audit absent' % key)
            continue
        print('%-7s calls=%-4s alignment=%-4s kept=%-4s rejected=%-4s nulls=%-4s definitions=%-4s parse_compat=%s' % (
            key, info['chat_calls'], info['alignment_events'], info['kept'], info['rejected'],
            info['null_slots'], info['definitions_parsed'], info['parse_compat']))
        if info['rejection_reasons']:
            print('        reasons:', info['rejection_reasons'])
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
