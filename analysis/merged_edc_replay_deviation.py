# -*- coding: utf-8 -*-
"""Record what the executed EDC v3 run actually did, including the replay deviation.

The approved plan was: reuse the frozen Extract/Define responses and run only the alignment stage
that never executed. The implemented hook searched for the frozen responses in the run root, while
they live in <run>/responses/, so it matched nothing and Extract/Define were re-sampled live. The
run is therefore a fresh end-to-end run under the parse-compatible adapter, not a replay.

This file records the deviation and the evidence, so no document claims a replay that did not happen.

Output: evaluation/external_baselines_merged_2026-10-09/edc_v3/replay_deviation.json
"""
import importlib.util, json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
VENDOR_LLM = (Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_2026-10-08')
              / 'vendor' / 'edc' / 'edc' / 'utils' / 'llm_utils.py')
sys.stdout.reconfigure(encoding='utf-8')


def load_parser():
    spec = importlib.util.spec_from_file_location('vendor_llm_utils_probe', VENDOR_LLM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stage_counts(run_dir: Path, parser):
    responses = run_dir / 'responses'
    extract = define = None
    for path in sorted(responses.glob('*.json')):
        content = json.loads(path.read_text(encoding='utf-8'))['choices'][0]['message']['content']
        if 'extract' in path.name:
            extract = len(parser.parse_raw_triplets(content))
        elif 'define' in path.name:
            fixed = content.replace('\uff1a', ':')
            define = len({k: v for k, v in parser.parse_relation_definition(fixed).items() if v.strip()})
    return {'triplets_from_extract': extract, 'definitions_after_separator_fix': define}


def main() -> int:
    parser = load_parser()
    payload = {
        'approved_plan': 'replay the frozen Extract/Define responses; run only the alignment stage live',
        'what_executed': ('Extract/Define were re-sampled live because the replay hook searched the run root '
                          'instead of <run>/responses/; the run is a fresh end-to-end run under the '
                          'parse-compatible adapter'),
        'code_fixed_after_the_fact': ('analysis/merged_prepare_v3.py now searches both the run root and '
                                      '<run>/responses/, so a future replay would match'),
        'evidence': {
            'audit_call_statuses': 'all "completed"; none "replayed"; no replayed_from field set',
            'request_files_exist_for_extract_and_define': True,
        },
        'runs': {},
        'consequences': [
            'the triplet set is a new sample, so v3 is not "v2 triplets plus alignment"; v2 is excluded from the comparison anyway',
            'Extract/Define cost is small (~55k tokens per repeat) and stays inside the approved gate',
            'no replay artefact can bias the alignment results, because nothing was replayed',
        ],
    }
    for repeat in (1, 2):
        run_dir = NEW / 'edc_v3' / ('r%d' % repeat) / 'merged' / 'edc'
        if not (run_dir / 'responses').exists():
            payload['runs']['r%d' % repeat] = {'status': 'responses_absent'}
            continue
        entry = stage_counts(run_dir, parser)
        for baseline_tag, baseline_dir in (('v2', NEW / 'runs' / ('r%d' % repeat) / 'merged' / 'edc'),):
            if (baseline_dir / 'responses').exists():
                entry['v2_' + baseline_tag] = stage_counts(baseline_dir, parser)
        payload['runs']['r%d' % repeat] = entry
    target = NEW / 'edc_v3' / 'replay_deviation.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for repeat, info in payload['runs'].items():
        print(repeat, info)
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
