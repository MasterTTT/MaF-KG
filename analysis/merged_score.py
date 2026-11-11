# -*- coding: utf-8 -*-
"""Arm-aware scoring for the merged three-section experiment.

The frozen official scorer (scripts/score_external_baseline_reviews.py) pools by `method`,
which would merge the unguarded and progress-bounded iText arms. This scorer reuses the
official `validate()` as the sole authority on review validity and then aggregates by `arm`,
adding the merged-only dimensions:

  * content completeness  : required-content recall (full / recognized), tier split, Claim conditions
  * duplicates & merges   : duplicate_groups, false_merge_events out of the 15 must_not_merge pairs
  * cross-section relations: scored in their own universe (X-R1, X-R2), never folded into intra recall

Non-completed jobs are reported as no-output rows and are never zero-filled.
"""
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'analysis'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402
from validate_merged_one import check_merged_blocks  # noqa: E402


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ratio(counts, numerator, denominator):
    total = counts.get(denominator, 0)
    return counts.get(numerator, 0) / total if total else None


def load_adjudication(review_dir):
    """(label, 1-based group index) -> 'repetition' | 'split', from the tightened duplicate criterion."""
    path = Path(review_dir) / 'duplicate_adjudication.json'
    if not path.exists():
        return {}, None
    payload = json.loads(path.read_text(encoding='utf-8'))
    table = {(e['label'], e['index']): e['kind'] for e in payload.get('entries', [])}
    return table, payload


def load_overrides(review_dir):
    """Documented, uniform adjudications applied on top of a reviewer's judgment.

    The reviewer's own file stays untouched (audit trail); the override is recorded separately with
    its reason and applied here, so a cross-package consistency rule is visible rather than silent.
    """
    path = Path(review_dir) / 'status_overrides.json'
    if not path.exists():
        return {}, None
    payload = json.loads(path.read_text(encoding='utf-8'))
    table = {}
    for label, blocks in (payload.get('overrides') or {}).items():
        for block, entries in blocks.items():
            for reference_id, change in entries.items():
                table[(label, block, reference_id)] = change
    return table, payload


def apply_overrides(review, label, overrides):
    applied = []
    for block in ('node_coverage', 'relation_coverage', 'cross_relation_coverage'):
        for row in review.get(block, []):
            change = overrides.get((label, block, row.get('reference_id')))
            if not change:
                continue
            if row.get('status') == change.get('from'):
                row['status'] = change['to']
                row['reason'] = '%s [主代理口径裁定：%s]' % (row.get('reason', ''), change.get('reason', ''))
                applied.append({'block': block, 'reference_id': row['reference_id'],
                                'from': change.get('from'), 'to': change.get('to')})
    return applied


def score_one(label, packet, review, adjudication):
    # Both gates are enforced here, not left to the reviewing agent's self-check: the frozen
    # official validate() plus the merged-only blocks (cross-section relations, duplicate_groups,
    # false_merges restricted to the frozen must_not_merge pairs).
    validate(packet, review)
    check_merged_blocks(packet, review)
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
                condition = row.get('conditions_complete')
                key = 'complete' if condition is True else 'incomplete' if condition is False else 'unresolved'
                counts['claim_conditions_' + key] += 1
        elif ref['status'] == 'O':
            counts['optional'] += 1
            counts['optional_' + row['status']] += 1
    for row in review['relation_coverage']:
        counts['relations_reference'] += 1
        counts['relations_' + row['status']] += 1
    for row in review.get('cross_relation_coverage', []):
        counts['cross_reference'] += 1
        counts['cross_' + row['status']] += 1
    item_kind = {i['item_id']: i.get('kind', 'unknown') for i in packet['items']}
    for row in review['prediction_support']:
        counts['native_units_reviewed'] += 1
        counts['support_' + row['status']] += 1
        counts[item_kind[row['item_id']] + '_reviewed'] += 1
        counts['scope_admitted' if row['scope_admitted'] is True else
               'scope_excluded' if row['scope_admitted'] is False else 'scope_unresolved'] += 1
    groups = review.get('duplicate_groups', [])
    repetitions = splits = unadjudicated = 0
    for index, _group in enumerate(groups, 1):
        kind = adjudication.get((label, index))
        if kind == 'repetition':
            repetitions += 1
        elif kind == 'split':
            splits += 1
        else:
            unadjudicated += 1
    counts['duplicate_groups'] = repetitions          # metric counts genuine repetition only
    counts['duplicate_splits'] = splits               # legitimate decomposition, informational
    counts['duplicate_unadjudicated'] = unadjudicated  # never silently dropped
    counts['false_merge_events'] = len(review.get('false_merges', []))
    counts['must_not_merge_pairs_checked'] = len(packet['reference'].get('must_not_merge', []))
    recognized = sum(counts.get('required_' + s, 0) for s in ('full', 'partial', 'name_only'))
    cross_recognized = sum(counts.get('cross_' + s, 0) for s in ('full', 'partial', 'name_only'))
    return {
        'label': label, 'status': 'reviewed', 'counts': dict(counts),
        'full_required_recall': ratio(counts, 'required_full', 'required'),
        'recognized_required_recall': recognized / counts['required'] if counts.get('required') else None,
        'full_core_recall': ratio(counts, 'core_full', 'core_required'),
        'full_detail_recall': ratio(counts, 'detail_full', 'detail_required'),
        'full_optional_coverage': ratio(counts, 'optional_full', 'optional'),
        'full_relation_recall': ratio(counts, 'relations_full', 'relations_reference'),
        'recognized_relation_recall': (
            sum(counts.get('relations_' + s, 0) for s in ('full', 'partial', 'name_only')) /
            counts['relations_reference']) if counts.get('relations_reference') else None,
        'full_cross_relation_recall': ratio(counts, 'cross_full', 'cross_reference'),
        'recognized_cross_relation_recall': cross_recognized / counts['cross_reference'] if counts.get('cross_reference') else None,
        'claim_conditions_complete_ratio': ratio(counts, 'claim_conditions_complete', 'required_claims'),
        'native_support_yes_ratio': ratio(counts, 'support_yes', 'native_units_reviewed'),
        'false_merge_events': counts['false_merge_events'],
        'duplicate_groups': counts['duplicate_groups'],
        'duplicate_splits': counts['duplicate_splits'],
        'duplicate_unadjudicated': counts['duplicate_unadjudicated'],
        'provenance': review['reviewer_provenance'],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--review-dir', default=str(ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09' / 'review_merged'))
    ap.add_argument('--out', default=str(ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09' / 'semantic_scores.json'))
    args = ap.parse_args()
    review_dir = Path(args.review_dir)
    mapping = read(review_dir / 'mapping_private.json')
    adjudication, adjudication_payload = load_adjudication(review_dir)
    overrides, overrides_payload = load_overrides(review_dir)

    rows, totals = [], defaultdict(lambda: defaultdict(int))
    for job in mapping:
        arm, label = job['arm'], job['label']
        if job['status'] != 'completed':
            rows.append({'label': label, 'arm': arm, 'repeat': job['repeat'], 'status': job['status'],
                         'quality': None, 'not_a_zero_score': True})
            continue
        packet_path, review_path = review_dir / (label + '.json'), review_dir / (label + '_review.json')
        if not packet_path.exists():
            rows.append({'label': label, 'arm': arm, 'repeat': job['repeat'], 'status': 'packet_missing',
                         'quality': None, 'not_a_zero_score': True})
            continue
        packet = read(packet_path)
        if packet.get('status') and packet.get('status') != 'reviewable':
            # no-output packet (empty graph / failed / cancelled): recorded, never zero-filled
            rows.append({'label': label, 'arm': arm, 'repeat': job['repeat'], 'status': packet['status'],
                         'quality': None, 'not_a_zero_score': True})
            continue
        if not review_path.exists():
            rows.append({'label': label, 'arm': arm, 'repeat': job['repeat'], 'status': 'review_missing',
                         'quality': None, 'not_a_zero_score': True})
            continue
        review = read(review_path)
        if review.get('reviewer_provenance') in (None, 'pending', ''):
            # the template is still untouched: the review has not started or has not finished
            rows.append({'label': label, 'arm': arm, 'repeat': job['repeat'], 'status': 'review_pending',
                         'quality': None, 'not_a_zero_score': True})
            continue
        applied = apply_overrides(review, label, overrides)
        result = score_one(label, packet, review, adjudication)
        if applied:
            result['status_overrides_applied'] = applied
        result.update({'arm': arm, 'method': job['method'], 'section': job['section'], 'repeat': job['repeat']})
        rows.append(result)
        for key, value in result['counts'].items():
            totals[arm][key] += value

    def metrics(counts):
        recognized = sum(counts.get('required_' + s, 0) for s in ('full', 'partial', 'name_only'))
        rel_recognized = sum(counts.get('relations_' + s, 0) for s in ('full', 'partial', 'name_only'))
        cross_recognized = sum(counts.get('cross_' + s, 0) for s in ('full', 'partial', 'name_only'))
        return {
            'full_required_recall': ratio(counts, 'required_full', 'required'),
            'recognized_required_recall': recognized / counts['required'] if counts.get('required') else None,
            'full_core_recall': ratio(counts, 'core_full', 'core_required'),
            'full_detail_recall': ratio(counts, 'detail_full', 'detail_required'),
            'full_optional_coverage': ratio(counts, 'optional_full', 'optional'),
            'full_relation_recall': ratio(counts, 'relations_full', 'relations_reference'),
            'recognized_relation_recall': rel_recognized / counts['relations_reference'] if counts.get('relations_reference') else None,
            'full_cross_relation_recall': ratio(counts, 'cross_full', 'cross_reference'),
            'recognized_cross_relation_recall': cross_recognized / counts['cross_reference'] if counts.get('cross_reference') else None,
            'claim_conditions_complete_ratio': ratio(counts, 'claim_conditions_complete', 'required_claims'),
            'native_support_yes_ratio': ratio(counts, 'support_yes', 'native_units_reviewed'),
            'pooled_false_merge_events': counts.get('false_merge_events', 0),
            'pooled_duplicate_groups': counts.get('duplicate_groups', 0),
            'pooled_duplicate_splits': counts.get('duplicate_splits', 0),
            'pooled_duplicate_unadjudicated': counts.get('duplicate_unadjudicated', 0),
        }

    by_arm = {}
    for arm in sorted({j['arm'] for j in mapping}):
        selected = [r for r in rows if r['arm'] == arm]
        reviewed = [r for r in selected if r['status'] == 'reviewed']
        by_arm[arm] = {
            'planned_repeats': len(selected),
            'reviewed_repeats': len(reviewed),
            'noncompleted': [{'label': r['label'], 'repeat': r['repeat'], 'status': r['status']} for r in selected if r['status'] != 'reviewed'],
            'pooled_for_reviewed_outputs_only': metrics(totals[arm]),
            'per_repeat_full_required_recall': [r['full_required_recall'] for r in reviewed],
            'per_repeat_recognized_required_recall': [r['recognized_required_recall'] for r in reviewed],
            'per_repeat_cross_relation': [{'label': r['label'], 'full': r['full_cross_relation_recall'],
                                           'recognized': r['recognized_cross_relation_recall']} for r in reviewed],
            'all_planned_outputs_reviewed': bool(selected) and all(r['status'] == 'reviewed' for r in selected),
        }
    payload = {
        'rows': rows,
        'by_arm': by_arm,
        'pooled_counts_by_arm': {k: dict(v) for k, v in totals.items()},
        'arm_notes': {
            'itext2kg': 'unguarded adapted iText2KG: no-output (ceiling failure / user-cancelled repeat)',
            'itext2kg-progress-bound-3': 'separately labelled progress-bounded variant; never pooled with the unguarded arm',
        },
        'no_f1': 'Content recall, relation coverage, duplicate/false-merge counts and cross-section relation coverage are separate universes',
        'duplicate_criterion': ('duplicate_groups counts genuine repetition only (same content expressed redundantly). '
                                'Legitimate decomposition is reported separately as duplicate_splits and never counted. '
                                'Entries without an adjudication are reported as duplicate_unadjudicated.'),
        'adjudication_source': (adjudication_payload or {}).get('criterion') if adjudication_payload else None,
        'status_override_rule': (overrides_payload or {}).get('rule') if overrides_payload else None,
        'n_note': 'One merged three-section document; repeats are not independent samples',
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('scored', sum(r['status'] == 'reviewed' for r in rows), 'reviewed outputs of', len(rows), 'planned')
    for arm, info in by_arm.items():
        m = info['pooled_for_reviewed_outputs_only']
        print('  %-28s reviewed=%d/%d full_required=%s recognized=%s cross_full=%s dup=%d false_merge=%d' % (
            arm, info['reviewed_repeats'], info['planned_repeats'],
            m['full_required_recall'], m['recognized_required_recall'], m['full_cross_relation_recall'],
            m['pooled_duplicate_groups'], m['pooled_false_merge_events']))
    print('wrote ->', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
