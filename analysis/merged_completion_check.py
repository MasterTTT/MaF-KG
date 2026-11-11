# -*- coding: utf-8 -*-
"""Final completion gate for the merged three-section experiment.

Checks, from files on disk only, that the objective is actually achieved before anything is declared
complete or the machine is shut down:

  1. both fixed variants ran to completion in both repeats
  2. every reviewable packet of the v3 round exists and passes both validation gates
  3. the v3 round is scored and all four comparison arms carry numbers
  4. the four-arm table is complete and the superseded arms are excluded
  5. the documents carry the required sections and the corrected classifications

Exit code 0 means every check passed.
"""
import json, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'analysis'))
sys.stdout.reconfigure(encoding='utf-8')

from score_external_baseline_reviews import validate  # noqa: E402
from validate_merged_one import check_merged_blocks  # noqa: E402

CHECKS = []


def check(name, ok, detail=''):
    CHECKS.append((name, bool(ok), detail))
    print('%-46s %s %s' % (name, 'PASS' if ok else 'FAIL', detail))
    return bool(ok)


def main() -> int:
    # 1. runs
    for variant in ('edc_v3', 'itext_v3'):
        manifest_path = NEW / variant / 'manifest.json'
        if not manifest_path.exists():
            check('%s manifest present' % variant, False)
            continue
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        states = [(j['repeat'], j['status']) for j in manifest['jobs']]
        check('%s both repeats completed' % variant,
              all(s == 'completed' for _, s in states), str(states))

    # 2. reviews
    review_dir = NEW / 'review_v3'
    if not review_dir.exists():
        check('review_v3 exists', False)
    else:
        mapping = json.loads((review_dir / 'mapping_private.json').read_text(encoding='utf-8'))
        reviewable = valid = 0
        problems = []
        for job in mapping:
            packet_path = review_dir / (job['label'] + '.json')
            packet = json.loads(packet_path.read_text(encoding='utf-8')) if packet_path.exists() else None
            if packet is None:
                problems.append('%s packet missing' % job['label'])
                continue
            if packet.get('status') and packet.get('status') != 'reviewable':
                continue
            reviewable += 1
            review_path = review_dir / (job['label'] + '_review.json')
            if not review_path.exists():
                problems.append('%s review missing' % job['label'])
                continue
            review = json.loads(review_path.read_text(encoding='utf-8'))
            try:
                validate(packet, review)
                check_merged_blocks(packet, review)
                valid += 1
            except Exception as exc:  # noqa: BLE001
                problems.append('%s %s' % (job['label'], exc))
        check('all v3 packets reviewed and valid', reviewable > 0 and valid == reviewable and not problems,
              '%d/%d valid %s' % (valid, reviewable, problems or ''))

    # 3. scores
    scores_path = NEW / 'semantic_scores_v3.json'
    if not scores_path.exists():
        check('v3 scores exist', False)
    else:
        scores = json.loads(scores_path.read_text(encoding='utf-8'))
        for arm in ('edc-parse-compatible', 'itext2kg-symbol-compatible'):
            info = scores['by_arm'].get(arm)
            ok = bool(info) and info['reviewed_repeats'] == info['planned_repeats'] and info['reviewed_repeats'] > 0
            value = None if not info else info['pooled_for_reviewed_outputs_only']['full_required_recall']
            check('%s scored both repeats' % arm, ok,
                  'reviewed %s/%s full_required=%s' % (info['reviewed_repeats'], info['planned_repeats'],
                                                       None if value is None else round(value, 4)) if info else '')

    # 4. four-arm table
    table_path = NEW / 'four_arm_table.json'
    if not table_path.exists():
        check('four-arm table exists', False)
    else:
        table = json.loads(table_path.read_text(encoding='utf-8'))
        check('four-arm table complete', table.get('complete') is True,
              str([(a['arm'], a['status']) for a in table['arms']]))
        excluded = table.get('excluded', {})
        check('superseded arms excluded', {'edc', 'itext2kg-progress-bound-3'} <= set(excluded),
              ','.join(sorted(excluded)))

    # 5. documents
    doc = ROOT / 'docs' / 'www_merged_experiment_2026-10-09.md'
    text = doc.read_text(encoding='utf-8') if doc.exists() else ''
    check('experiment doc carries four-arm results', 'EDC（解析兼容）' in text or 'edc-parse-compatible' in text)
    check('experiment doc records EDC parse reclassification', 'parse_compatibility_failure' in text)
    check('experiment doc records iText sanitiser attribution', 'data_handling.py' in text)
    check('experiment doc limits the cross-section claim', '不能' in text and 'X-R2' in text)
    claim = ROOT / 'docs' / 'www_claim_evidence_table_2026-10-09.md'
    ctext = claim.read_text(encoding='utf-8') if claim.exists() else ''
    check('claim table drops zero-coverage sensitivity', '不得' in ctext and '零覆盖' in ctext)

    failed = [name for name, ok, _ in CHECKS if not ok]
    print()
    print('%d checks, %d passed, %d failed' % (len(CHECKS), len(CHECKS) - len(failed), len(failed)))
    for name in failed:
        print('  FAILED:', name)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
