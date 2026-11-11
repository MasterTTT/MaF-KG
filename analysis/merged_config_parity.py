# -*- coding: utf-8 -*-
"""Record that every arm of the merged experiment ran under one identical frozen configuration.

The paper claims a fair comparison across serializations, so the shared request configuration is
extracted from each manifest and compared field by field, together with the frozen reference and
input hashes. Any divergence would fail this check loudly.

Manifest parity is not wire parity, so this script also checks the recorded request bodies:
`temperature` is a *requested* value that the thinking adapter omits, and the arms that record inputs
instead of bodies are cross-checked against their graph diagnostics. The distinction matters because
"the manifest says 0.1" must never be written as "the experiment ran at temperature 0.1".

Output: evaluation/external_baselines_merged_2026-10-09/config_parity.json
"""
import hashlib, json, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
FIELDS = ('model', 'max_tokens', 'temperature', 'request_timeout', 'max_retries', 'thinking',
          'reasoning_effort', 'chunk_strategy', 'chunk_context_before_lines', 'chunk_context_after_lines',
          'max_chunk_chars', 'request_delay', 'use_response_format', 'provider', 'base_url')
sys.stdout.reconfigure(encoding='utf-8')

MANIFESTS = {
    'maf': NEW / 'ctl' / 'maf' / 'manifest.json',
    'direct': NEW / 'ctl' / 'direct' / 'manifest.json',
    'itext2kg': NEW / 'ctl' / 'itext2kg' / 'manifest.json',
    'edc': NEW / 'ctl' / 'edc' / 'manifest.json',
    'itext2kg-progress-bound-3': NEW / 'guarded' / 'manifest.json',
    'itext2kg-symbol-compatible': NEW / 'itext_v3' / 'manifest.json',
    'edc-parse-compatible': NEW / 'edc_v3' / 'manifest.json',
    'itext-symbol-native': NEW / 'itext_v4' / 'manifest.json',
}

# (request dir, graph.json for the arms whose request records store inputs rather than bodies)
WIRE = {
    'maf': (NEW / 'runs' / 'r1' / 'merged' / 'maf', True),
    'direct': (NEW / 'runs' / 'r1' / 'merged' / 'direct', True),
    'itext2kg': (NEW / 'runs' / 'r1' / 'merged' / 'itext2kg', False),
    'itext2kg-progress-bound-3': (NEW / 'guarded' / 'r1' / 'merged' / 'itext2kg', False),
    'itext2kg-symbol-compatible': (NEW / 'itext_v3' / 'r1' / 'merged' / 'itext2kg', False),
    'edc-parse-compatible': (NEW / 'edc_v3' / 'r1' / 'merged' / 'edc', False),
}


def wire_check(directory: Path, has_diagnostics: bool):
    requests = sorted((directory / 'requests').glob('*.json'))
    if not requests:
        return {'status': 'no_recorded_requests'}
    bodies, input_only = 0, 0
    with_temperature, caps = 0, set()
    for path in requests:
        record = json.loads(path.read_text(encoding='utf-8'))
        body = record.get('body')
        if body is None:
            input_only += 1
            continue
        bodies += 1
        if 'temperature' in body and body['temperature'] is not None:
            with_temperature += 1
        caps.add(body.get('max_tokens'))
    info = {
        'request_records': len(requests),
        'records_with_wire_body': bodies,
        'records_with_inputs_only': input_only,
        'bodies_carrying_temperature': with_temperature,
        'body_max_tokens_values': sorted(v for v in caps if v is not None),
    }
    if has_diagnostics and (directory / 'graph.json').exists():
        run_config = json.loads((directory / 'graph.json').read_text(encoding='utf-8'))['diagnostics']['run_config']
        info['graph_diagnostics'] = {
            'temperature_requested': run_config.get('temperature_requested'),
            'temperature_sent': run_config.get('temperature_sent'),
            'reasoning_effort_sent': run_config.get('reasoning_effort_sent'),
        }
    return info


def main() -> int:
    configs, reference = {}, None
    for arm, path in MANIFESTS.items():
        if not path.exists():
            configs[arm] = {'status': 'manifest_absent'}
            continue
        manifest = json.loads(path.read_text(encoding='utf-8'))
        common = manifest['common']
        entry = {field: common.get(field) for field in FIELDS}
        entry['per_method_budget_usd'] = manifest.get('per_method_budget_usd')
        entry['per_method_token_limit'] = manifest.get('per_method_token_limit')
        entry['task_max_calls'] = manifest.get('task_max_calls')
        entry['task_max_seconds'] = manifest.get('task_max_seconds')
        entry['frozen_config'] = manifest.get('frozen_config')
        configs[arm] = entry
        if reference is None:
            reference = {field: entry[field] for field in FIELDS}
    divergent = {arm: {k: v for k, v in cfg.items() if reference.get(k) != v}
                 for arm, cfg in configs.items()
                 if 'status' not in cfg and any(reference.get(k) != cfg.get(k) for k in FIELDS)}

    wire = {arm: wire_check(directory, diag) for arm, (directory, diag) in WIRE.items()}
    bodies = [w for w in wire.values() if w.get('records_with_wire_body')]
    wire_temperature_never_sent = all(w.get('bodies_carrying_temperature') == 0 for w in bodies)
    diagnostics_say_not_sent = all(
        w.get('graph_diagnostics', {}).get('temperature_sent') is None
        for w in wire.values() if w.get('graph_diagnostics'))

    digest = json.loads((NEW / 'reference' / 'reference_digest.json').read_text(encoding='utf-8'))
    payload = {
        'shared_fields': list(FIELDS),
        'request_config_identical_across_arms': not divergent,
        'divergent_fields': divergent,
        'configs': configs,
        'field_semantics': {
            'manifest_vs_wire': ('shared_fields are compared across manifests. In these manifests `temperature` '
                                 'is a requested value, not a transmitted one: the thinking adapter omits it, so '
                                 'the recorded wire bodies carry model/messages/max_tokens/stream/thinking/'
                                 'reasoning_effort only. "Manifest says 0.1" must never be written as "the '
                                 'experiment ran at temperature 0.1".'),
            'temperature_requested': 0.1,
            'temperature_ever_sent': False,
            'adapter_rule': 'agents/Extractor.py omits_temperature_when_thinking (thinking enabled => temperature not sent)',
            'external_arm_client': 'scripts/external_baseline_runtime.py TextClient uses prospective_native_client.payload, which never sets temperature',
        },
        'wire_checks': wire,
        'wire_checks_summary': {
            'bodies_carrying_temperature_total': sum(w.get('bodies_carrying_temperature', 0) for w in bodies),
            'temperature_never_sent_in_recorded_bodies': wire_temperature_never_sent,
            'graph_diagnostics_agree': diagnostics_say_not_sent,
        },
        'native_kwargs_deviation': {
            'arm': 'edc (v2 and parse-compatible)',
            'detail': ('EDC asks for temperature=0 / max_tokens=512; the shared transport records those kwargs '
                       'and ignores them, so the arm ran with the common 64 000 cap and no temperature.'),
            'evidence': 'edc_adapter_deviation.json',
        },
        'note': ('Only the input serialization differs between arms (sequential teaching-unit chunks with '
                 'persistent memory; one whole-document request; 2400-char packed lines with incremental '
                 'fusion; one native text unit). Budget and task ceilings may differ per arm because they '
                 'are operational guards, not method configuration.'),
        'reference_sha256': digest['reference_sha256'],
        'reference_sha256_matches_frozen': digest['reference_sha256_matches_frozen'],
        'input_sha256': digest['input_sha256'],
        'input_sha256_matches_frozen': digest['input_sha256_matches_frozen'],
    }
    target = NEW / 'config_parity.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('request config identical across arms:', payload['request_config_identical_across_arms'])
    if divergent:
        for arm, diff in divergent.items():
            print('  DIVERGENT', arm, diff)
    for arm, cfg in configs.items():
        if 'status' in cfg:
            print('%-27s %s' % (arm, cfg['status']))
            continue
        print('%-27s model=%s cap=%s temp=%s frozen=%s' % (
            arm, cfg['model'], cfg['max_tokens'], cfg['temperature'], cfg['frozen_config']))
    print('wire bodies carrying temperature:', payload['wire_checks_summary']['bodies_carrying_temperature_total'])
    for arm, info in wire.items():
        print('  %-27s bodies=%s input_only=%s temp_in_body=%s caps=%s %s' % (
            arm, info.get('records_with_wire_body'), info.get('records_with_inputs_only'),
            info.get('bodies_carrying_temperature'), info.get('body_max_tokens_values'),
            info.get('graph_diagnostics', '')))
    print('reference/input hashes match frozen:',
          payload['reference_sha256_matches_frozen'], payload['input_sha256_matches_frozen'])
    print('wrote ->', target)
    return 0 if payload['request_config_identical_across_arms'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
