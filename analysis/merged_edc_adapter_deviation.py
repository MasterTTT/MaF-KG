# -*- coding: utf-8 -*-
"""Machine evidence for the EDC adapter deviation: native generation kwargs are recorded, not sent.

EDC's own code asks the shared transport for `temperature=0` and `max_tokens=512`. The shared client
(`external_baseline_runtime.TextClient.__call__(messages, label, **kwargs)`) stores those kwargs as
`wrapper_options` for audit and builds the body from `prospective_native_client.payload()`, which
carries only model/messages/max_tokens/stream/thinking/reasoning_effort. So the EDC arm ran with the
shared 64 000 output cap instead of its own 512, and with no temperature.

This script reads every recorded EDC request and reports the gap, so the disclosure in the paper can
point at machine evidence rather than at a hand-written claim.

Output: evaluation/external_baselines_merged_2026-10-09/edc_adapter_deviation.json
"""
import json
import sys
from collections import Counter
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')

RUNS = {
    'edc-v2-r1': NEW / 'runs' / 'r1' / 'merged' / 'edc',
    'edc-v2-r2': NEW / 'runs' / 'r2' / 'merged' / 'edc',
    'edc-parse-compatible-r1': NEW / 'edc_v3' / 'r1' / 'merged' / 'edc',
    'edc-parse-compatible-r2': NEW / 'edc_v3' / 'r2' / 'merged' / 'edc',
}
TRACKED = ('temperature', 'max_tokens', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty')


def audit(directory: Path):
    requests = sorted((directory / 'requests').glob('*.json')) if (directory / 'requests').is_dir() else []
    if not requests:
        return {'status': 'no_recorded_requests', 'directory': str(directory)}
    requested = Counter()
    sent = Counter()
    dropped = Counter()
    for path in requests:
        record = json.loads(path.read_text(encoding='utf-8'))
        options = record.get('wrapper_options') or {}
        body = record.get('body') or {}
        for key in TRACKED:
            if key in options:
                requested[key] += 1
                if key in body and body[key] == options[key]:
                    sent[key] += 1
                else:
                    dropped[key] += 1
        sent['__body_max_tokens=' + str(body.get('max_tokens'))] += 1
        sent['__body_has_temperature=' + str('temperature' in body)] += 1
    return {
        'directory': str(directory),
        'requests': len(requests),
        'native_kwargs_requested_by_method': dict(requested),
        'native_kwargs_actually_sent': dict(sent),
        'native_kwargs_dropped': dict(dropped),
        'effective_body_parameters': {
            'max_tokens': {k.split('=', 1)[1]: v for k, v in sent.items() if k.startswith('__body_max_tokens=')},
            'temperature_present_in_body': {k.split('=', 1)[1]: v for k, v in sent.items()
                                            if k.startswith('__body_has_temperature=')},
        },
    }


def main() -> int:
    payload = {
        'finding': ('The EDC arm did not run under its own generation parameters: the shared transport records '
                    'the method\'s native kwargs and then ignores them, so the arm used the common 64 000 output '
                    'cap and sent no temperature. This is an adaptation difference in the external method\'s '
                    'favour (a 512-token cap would have truncated triple extraction) and must be disclosed with '
                    'the four-arm table.'),
        'client': 'scripts/external_baseline_runtime.py TextClient.__call__ -> prospective_native_client.payload',
        'runs': {label: audit(directory) for label, directory in RUNS.items()},
    }
    target = NEW / 'edc_adapter_deviation.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for label, info in payload['runs'].items():
        if info.get('status'):
            print('%-26s %s' % (label, info['status']))
            continue
        print('%-26s requests=%d requested=%s dropped=%s body_max_tokens=%s'
              % (label, info['requests'], info['native_kwargs_requested_by_method'],
                 info['native_kwargs_dropped'], info['effective_body_parameters']['max_tokens']))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
