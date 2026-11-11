# -*- coding: utf-8 -*-
"""Write the 5.2 confirmation manifest (review system prompt + gates) so the shared runner can drive it.

Usage: python analysis/prerequisite_confirmation_manifest.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
SYSTEM = (ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'snapshot'
          / 'review_system.txt')
FROZEN = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'frozen_inputs01'
sys.stdout.reconfigure(encoding='utf-8')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    reference = json.loads((OUT / 'reference_v2.json').read_text(encoding='utf-8'))
    manifest = {
        'experiment': 'prerequisite-new-target-confirmation-v2',
        'status': 'prepared',
        'purpose': ('Confirm the prerequisite pipeline on four targets that took no part in the earlier tuning: '
                    'the same review prompt is used on both arms, only the context changes, and the frozen pool '
                    'review is reported apart from the end-to-end candidate generation.'),
        'reference': str(OUT / 'reference_v2.json'), 'reference_sha256': sha(OUT / 'reference_v2.json'),
        'catalog': str(FROZEN / 'catalog.json'), 'catalog_sha256': sha(FROZEN / 'catalog.json'),
        'review_system': str(SYSTEM), 'review_system_sha256': sha(SYSTEM),
        'arms': {'ku': 'catalogue entries only', 'ctx': 'catalogue entries + real Card text'},
        'locked_arm': 'ctx',
        'locked_arm_basis': ('P1-B 5.1: arm B (source text) scored 1.000 necessity recall against 0.933 for arm A '
                             'and identical directness, so the context arm is carried forward.'),
        'counts': reference['counts'],
        'frozen_before_prediction': True,
        'budget_gate': {'usd': 0.60, 'tokens': 800_000,
                        'note': ('Approved P1-B 5.2 gate. Fourteen review calls (7 batches x 2 arms) plus four '
                                 'proposal calls; the fixed-pool review and the end-to-end proposals are separate '
                                 'universes and are never pooled.')},
        'stop_condition': ('One confirmation round only: no prompt chasing, no extra targets, no chapter expansion '
                           'after seeing the numbers.'),
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for arm in ('ku', 'ctx'):
        folder = OUT / ('arm_%s' % arm)
        for path in sorted(folder.glob('batch_*.json')):
            (folder / (path.stem + '_skeleton.json')).write_text(json.dumps(
                {'label': path.stem, 'arm': arm, 'reviewer_provenance': 'pending'}, ensure_ascii=False, indent=2),
                encoding='utf-8')
    print('manifest ->', OUT / 'manifest.json', '| system', SYSTEM.name, '| pairs', reference['counts']['pairs'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
