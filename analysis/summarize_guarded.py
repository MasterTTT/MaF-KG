# -*- coding: utf-8 -*-
"""Per-task summary for the guarded02 (no-progress-protection) subset.

Deliberately does NOT reuse the scorer's section-level `planned_repeats` metadata
(fixed at 2, which is wrong for this 3-task subset). Reports per-task counts and
ratios only, plus a subset total, and marks which runs actually triggered the guard.
"""
import json, os, sys
from collections import defaultdict

ROOT = r'E:\MaF-KG\.worktrees\stable-main'
BASE = os.path.join(ROOT, 'evaluation', 'external_baselines_2026-10-08')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate, ratios  # noqa: E402

GUARDED = os.path.join(BASE, 'guarded02')


def guard_hits(job_dir):
    """Read call_trace.json / no_progress_exits if present."""
    for name in ('call_trace.json', 'execution.json'):
        p = os.path.join(job_dir, name)
        if os.path.exists(p):
            try:
                obj = json.load(open(p, encoding='utf-8'))
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, dict):
                ex = obj.get('no_progress_exits')
                if ex is not None:
                    return len(ex) if isinstance(ex, list) else ex
                for v in obj.values():
                    if isinstance(v, dict) and 'no_progress_exits' in v:
                        e = v['no_progress_exits']
                        return len(e) if isinstance(e, list) else e
    return None


def main() -> int:
    review_dir = os.path.join(BASE, 'review_guarded02')
    mapping = json.load(open(os.path.join(review_dir, 'mapping_private.json'), encoding='utf-8'))
    audit = json.load(open(os.path.join(GUARDED, 'execution_audit.json'), encoding='utf-8'))
    jobs = {('%s|%s|%s' % (j['method'], j['section'], j['repeat'])): j for j in audit['jobs']}

    print('=' * 100)
    print('guarded02 per-task summary (no section-level planned_repeats claims)')
    totals = defaultdict(int)
    for m in sorted(mapping, key=lambda x: x['label']):
        if m['status'] != 'completed':
            print('  %-4s %-9s %-7s r%s  %s' % (m['label'], m['method'], m['section'], m['repeat'], m['status']))
            continue
        packet = json.load(open(os.path.join(review_dir, m['label'] + '.json'), encoding='utf-8'))
        review = json.load(open(os.path.join(review_dir, m['label'] + '_review.json'), encoding='utf-8'))
        validate(packet, review)
        reference = {n['id']: n for n in packet['reference']['nodes']}
        counts = defaultdict(int)
        for row in review['node_coverage']:
            ref = reference[row['reference_id']]
            if ref['status'] == 'M':
                counts['required'] += 1
                counts['required_' + row['status']] += 1
                counts[ref['tier'] + '_required'] += 1
                if row['status'] == 'full':
                    counts[ref['tier'] + '_full'] += 1
                if ref.get('type') == 'Claim':
                    counts['required_claims'] += 1
                    k = 'complete' if row.get('conditions_complete') is True else 'incomplete' if row.get('conditions_complete') is False else 'unresolved'
                    counts['claim_conditions_' + k] += 1
            elif ref['status'] == 'O':
                counts['optional'] += 1
                counts['optional_' + row['status']] += 1
        for row in review['relation_coverage']:
            counts['relations_reference'] += 1
            counts['relations_' + row['status']] += 1
        kind = {i['item_id']: i.get('kind', 'unknown') for i in packet['items']}
        for row in review['prediction_support']:
            counts['native_units_reviewed'] += 1
            counts['support_' + row['status']] += 1
            counts['scope_admitted' if row['scope_admitted'] is True else
                   'scope_excluded' if row['scope_admitted'] is False else 'scope_unresolved'] += 1
            counts[kind[row['item_id']] + '_reviewed'] += 1
        r = ratios(counts)
        j = jobs.get('%s|%s|%s' % (m['method'], m['section'], m['repeat']), {})
        gh = guard_hits(j.get('directory', '')) if j else None
        print('  %-4s %-9s %-7s r%s | full_req_recall=%s recognized=%s | full_core=%s full_detail=%s | '
              'claim_cond_complete=%s/%s | rel_full=%s/%s | support_yes=%s/%s | scope_excl=%s | guard_hits=%s' % (
                  m['label'], m['method'], m['section'], m['repeat'],
                  _f(r['full_required_recall']), _f(r['recognized_required_recall']),
                  _f(r['full_core_recall']), _f(r['full_detail_recall']),
                  counts.get('claim_conditions_complete', 0), counts.get('required_claims', 0),
                  counts.get('relations_full', 0), counts.get('relations_reference', 0),
                  counts.get('support_yes', 0), counts.get('native_units_reviewed', 0),
                  counts.get('scope_excluded', 0), gh))
        for k, v in counts.items():
            totals[k] += v
    print()
    print('  SUBSET TOTAL ratios (per-task counts pooled; not a per-section repeat claim):')
    for k, v in sorted(ratios(totals).items()):
        print('    %-38s %s' % (k, _f(v) if isinstance(v, float) else v))
    print('    raw counts:', dict(sorted(totals.items())))
    return 0


def _f(x):
    return 'n/a' if x is None else round(x, 4)


if __name__ == '__main__':
    raise SystemExit(main())
