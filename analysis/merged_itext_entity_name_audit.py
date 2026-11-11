# -*- coding: utf-8 -*-
"""Matched-pair audit of iText2KG name damage that survives into the final graph.

The first symbol audit (`merged_itext_symbol_audit.py`) compared symbol counts and had to caveat
entity names, because the model proposes many more names than fusion keeps, so an absent name is not
evidence of damage. This audit removes that caveat by comparing *present* pairs: for every name in
the final graph it looks up the model-returned names whose pinned retrieval key
(`lower().replace("_", " ").replace("-", " ")`) is identical, and reports whether the final string
equals the model string.

Classes reported per arm:
  entity_verbatim        final name equals the model string
  entity_key_mangled     final name equals the retrieval key but not the model string
                         (case lowered, "-" / "_" replaced by spaces)
  relation_verbatim / relation_key_mangled   the same test for relation predicate names
  endpoint_unbound       a relation endpoint that matches no final entity name verbatim

Output: evaluation/external_baselines_merged_2026-10-09/itext_entity_name_audit.json
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')
SAMPLE_LIMIT = 12


def retrieval_key(value: str) -> str:
    """The pinned upstream normalisation (utils/data_handling.py L33)."""
    return value.lower().replace("_", " ").replace("-", " ")


def model_stage_names(directory: Path):
    entities, relations, unparsed = [], [], []
    for path in sorted((directory / 'responses').glob('*.json')):
        if 'entities' in path.name:
            stage = 'entities'
        elif 'relationships' in path.name:
            stage = 'relationships'
        else:
            continue
        try:
            content = json.loads(path.read_text(encoding='utf-8'))['choices'][0]['message']['content']
            parsed = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            unparsed.append({'file': path.name, 'error': type(exc).__name__})
            continue
        if stage == 'entities':
            entities += [str(row.get('name') or '') for row in (parsed.get('entities') or [])]
        else:
            relations += [str(row.get('name') or '') for row in (parsed.get('relationships') or [])]
    return entities, relations, unparsed


def classify(final_names, model_names):
    """Compare present final names against model names through the retrieval key."""
    index = defaultdict(list)
    for name in model_names:
        index[retrieval_key(name)].append(name)
    verbatim, mangled, unmatched, samples = 0, 0, 0, []
    for final in final_names:
        candidates = index.get(retrieval_key(final), [])
        if final in candidates:
            verbatim += 1
            continue
        if candidates:
            mangled += 1
            if len(samples) < SAMPLE_LIMIT:
                samples.append({'model_returned': candidates[0], 'final_graph': final,
                                'key': retrieval_key(final)})
            continue
        unmatched += 1
    return {'verbatim': verbatim, 'key_mangled': mangled, 'no_model_counterpart': unmatched,
            'samples': samples}


def audit_arm(directory: Path, label: str):
    graph_path = directory / 'graph.json'
    if not graph_path.exists():
        return {'status': 'graph_absent', 'directory': str(directory)}
    graph = json.loads(graph_path.read_text(encoding='utf-8'))
    final_entities = [str(e.get('name') or '') for e in (graph.get('entities') or [])]
    final_relations = [str(r.get('name') or '') for r in (graph.get('relationships') or [])]
    model_entities, model_relations, unparsed = model_stage_names(directory)
    entity_final_set = set(final_entities)
    endpoints = []
    for row in (graph.get('relationships') or []):
        endpoints += [str(row.get('startNode') or ''), str(row.get('endNode') or '')]
    unbound = sorted({e for e in endpoints if e and e not in entity_final_set})
    return {
        'label': label,
        'directory': str(directory),
        'final_entity_count': len(final_entities),
        'final_relation_count': len(final_relations),
        'model_entity_name_count': len(model_entities),
        'model_relation_name_count': len(model_relations),
        'entities': classify(final_entities, model_entities),
        'relations': classify(final_relations, model_relations),
        'endpoints_unbound_to_final_entities': len(unbound),
        'endpoint_unbound_samples': unbound[:SAMPLE_LIMIT],
        'model_responses_unparsed': unparsed,
    }


def main() -> int:
    arms = {
        'itext2kg-progress-bound-3-r1': NEW / 'guarded' / 'r1' / 'merged' / 'itext2kg',
        'itext2kg-progress-bound-3-r2': NEW / 'guarded' / 'r2' / 'merged' / 'itext2kg',
        'itext2kg-symbol-compatible-r1': NEW / 'itext_v3' / 'r1' / 'merged' / 'itext2kg',
        'itext2kg-symbol-compatible-r2': NEW / 'itext_v3' / 'r2' / 'merged' / 'itext2kg',
        'itext-symbol-native-r1': NEW / 'itext_v4' / 'r1' / 'merged' / 'itext2kg',
        'itext-symbol-native-r2': NEW / 'itext_v4' / 'r2' / 'merged' / 'itext2kg',
    }
    payload = {
        'purpose': ('Separate upstream-induced name damage from adapter behaviour on pairs that are '
                    'actually present in the final graph (the first audit could not, because an absent '
                    'model name may simply not survive fusion).'),
        'method': ('For each final name, look up model-returned names with the identical pinned '
                   'retrieval key lower().replace("_"," ").replace("-"," "). If the final string equals '
                   'the model string the name is verbatim; if it equals the key instead, the pinned '
                   'normalisation is visible in the delivered graph.'),
        'upstream_cause': {
            'file': 'itext2kg/utils/data_handling.py',
            'line': 33,
            'code': 'item_name_key: item[item_name_key].lower().replace("_", " ").replace("-", " ")',
            'note': ('Called from ientities_extractor.extract_entities (L44) and irelations_extractor '
                     '(L55/L91); the returned items are what fusion and DataHandler.remove_duplicates '
                     '(identity = record["name"], L95) consume.'),
        },
        'arms': {label: audit_arm(directory, label) for label, directory in arms.items()},
    }
    target = NEW / 'itext_entity_name_audit.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for label, info in payload['arms'].items():
        if info.get('status'):
            print('%-32s %s' % (label, info['status']))
            continue
        print('%-32s entities verbatim=%d key_mangled=%d no_counterpart=%d | relations verbatim=%d key_mangled=%d | unbound endpoints=%d'
              % (label, info['entities']['verbatim'], info['entities']['key_mangled'],
                 info['entities']['no_model_counterpart'], info['relations']['verbatim'],
                 info['relations']['key_mangled'], info['endpoints_unbound_to_final_entities']))
        for sample in info['entities']['samples'][:3]:
            print('      model %r -> final %r' % (sample['model_returned'], sample['final_graph']))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
