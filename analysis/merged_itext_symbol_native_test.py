# -*- coding: utf-8 -*-
"""Offline symbol test for the itext_v4 name-preserving adapter (no API calls, no ledger entries).

Checks the adapter's own code path rather than the delivered file, because "the JSON decodes" is not
evidence that symbols survived. Coverage required by the review:

  case-distinct variables (A vs a), minus sign, subscript, Chinese relation predicate,
  approximation sign, non-zero condition, and entity/relation endpoint binding.

For each class the test asserts three things:
  1. the delivered name equals the model string exactly (round-trip);
  2. `retrieval_key` equals the pinned normalisation `lower().replace("_"," ").replace("-"," ")`;
  3. embeddings are computed from the retrieval key, so pair-selection geometry is unchanged.

A positive control runs the same inputs through the unpatched pinned handler and shows the damage,
so a passing test cannot be an artefact of the test being unable to detect the difference.

Output: evaluation/external_baselines_merged_2026-10-09/itext_symbol_native_test.json
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
V4 = NEW / 'itext_v4' / 'snapshot'
VENDOR = (NEW.parent / 'external_baselines_2026-10-08' / 'vendor' / 'itext2kg' / 'snapshot'
          / 'itext2kg-43bc581c99a3a35ace99dc4699acdabb0f1a98f7')
sys.stdout.reconfigure(encoding='utf-8')

# (label, model-returned string) -- one row per symbol class the review asked for.
ENTITY_CASES = [
    ('case_distinct_variables', 'Absolute error: |A - a|'),
    ('minus_sign', 'Relative error: |A - a| / |a|'),
    ('subscript', 'Absolute error limit: \u03b4_A if |A - a| \u2264 \u03b4_A'),
    ('approximation_sign', 'Approximation: sin x \u2248 x for small |x|'),
    ('non_zero_condition', "Derivative of the differential: dy = f'(x) \u0394x when f'(x\u2080) \u2260 0"),
    ('greek_delta_pair', '\u0394y is the increment, \u03b4y the error limit'),
    ('chinese_and_latin_mix', '\u5f53 f\'(x\u2080)\u22600 \u65f6\uff0c\u5fae\u5206 dy \u662f\u589e\u91cf \u0394y \u7684\u7ebf\u6027\u4e3b\u90e8'),
]
RELATION_CASES = [
    ('chinese_predicate', '\u662f\u524d\u63d0'),
    ('chinese_predicate_2', '\u7528\u4e8e\u8ba1\u7b97'),
    ('hyphenated_predicate', 'is-proved-using'),
]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def retrieval_key(value):
    return str(value).lower().replace("_", " ").replace("-", " ")


def capture_embeddings():
    seen = []

    def embeddings_function(texts):
        seen.append(list(texts))
        return [[float(len(t))] for t in texts]

    return seen, embeddings_function


def run_handler(handler_class, cases, key_name):
    seen, embeddings_function = capture_embeddings()
    items = [{key_name: text, 'label': 'Definition'} for _, text in cases]
    out = handler_class().add_embeddings_as_property_batch(
        embeddings_function=embeddings_function, items=items, property_name='properties',
        item_name_key=key_name, embeddings=True)
    return out, seen


def main() -> int:
    external = load_module('v4_external_itext2kg', V4 / 'scripts' / 'external_itext2kg.py')
    data_handling = load_module('pinned_data_handling', VENDOR / 'itext2kg' / 'utils' / 'data_handling.py')
    pinned = data_handling.DataHandler

    # exactly the composition the adapter applies when both flags are on
    named = external._name_preserving_data_handler(pinned)
    both = external._symbol_preserving_data_handler(named)

    report = {
        'purpose': ('Prove the itext_v4 adapter delivers the model strings verbatim and keeps the pinned '
                    'normalisation only as a retrieval key, before any API call is made.'),
        'adapter_composition': 'symbol_preserving(name_preserving(pinned DataHandler))',
        'entity_cases': [],
        'relation_cases': [],
        'endpoint_binding': {},
        'positive_control': {'handler': 'pinned DataHandler (unpatched)', 'cases': []},
        'checks': {},
    }

    delivered, embedded = run_handler(both, ENTITY_CASES, 'name')
    for (label, model_text), row in zip(ENTITY_CASES, delivered):
        record = {
            'class': label,
            'model_returned': model_text,
            'delivered_name': row['name'],
            'retrieval_key': row['retrieval_key'],
            'round_trip_ok': row['name'] == model_text,
            'key_matches_pinned_normalisation': row['retrieval_key'] == retrieval_key(model_text),
            'delivered_differs_from_pinned_normalisation': row['name'] != retrieval_key(model_text),
        }
        report['entity_cases'].append(record)
    report['checks']['entity_embeddings_used_retrieval_keys'] = (
        embedded == [[retrieval_key(text) for _, text in ENTITY_CASES]])

    rel_delivered, rel_embedded = run_handler(both, RELATION_CASES, 'name')
    for (label, model_text), row in zip(RELATION_CASES, rel_delivered):
        report['relation_cases'].append({
            'class': label,
            'model_returned': model_text,
            'delivered_name': row['name'],
            'retrieval_key': row['retrieval_key'],
            'round_trip_ok': row['name'] == model_text,
        })
    report['checks']['relation_embeddings_used_retrieval_keys'] = (
        rel_embedded == [[retrieval_key(text) for _, text in RELATION_CASES]])

    # relation branch: endpoints and predicate must stay verbatim
    relation = {'startNode': 'Absolute error: |A - a|', 'endNode': 'Relative error: |A - a| / |a|',
                'name': '\u662f\u524d\u63d0'}
    processed = both().process(dict(relation), 'relation')
    report['relation_process'] = {
        'input': relation, 'output': processed,
        'endpoints_and_predicate_verbatim': processed == relation,
    }
    pinned_relation = pinned().process(dict(relation), 'relation')
    report['positive_control']['relation_process'] = {
        'output': pinned_relation,
        'damaged': pinned_relation != relation,
    }

    # entity/relation endpoint binding: a relation whose endpoints are entity names must stay bound
    entities = [{'name': text, 'label': 'Definition'} for _, text in ENTITY_CASES]
    entity_rows = both().handle_data(entities, 'entity')
    names = [row['name'] for row in entity_rows]
    relations = [{'startNode': ENTITY_CASES[0][1], 'endNode': ENTITY_CASES[1][1], 'name': '\u662f\u524d\u63d0'}]
    relation_rows = both().handle_data(relations, 'relation')
    report['endpoint_binding'] = {
        'entity_names_delivered': names,
        'relation_endpoints': [relation_rows[0]['startNode'], relation_rows[0]['endNode']],
        'both_endpoints_present_in_entities': all(
            endpoint in names for endpoint in (relation_rows[0]['startNode'], relation_rows[0]['endNode'])),
    }

    # positive control: the same inputs through the pinned handler
    pinned_rows, _ = run_handler(pinned, ENTITY_CASES, 'name')
    for (label, model_text), row in zip(ENTITY_CASES, pinned_rows):
        report['positive_control']['cases'].append({
            'class': label,
            'model_returned': model_text,
            'pinned_delivered_name': row['name'],
            'damaged': row['name'] != model_text,
        })

    # dedup semantics: pinned identity is key-based, so document what still collapses
    dupes = [
        {'name': 'Absolute error: |A - a|', 'label': 'Definition'},
        {'name': 'absolute error: |a   a|', 'label': 'Definition'},
        {'name': '\u0394y is the increment', 'label': 'Definition'},
        {'name': '\u03b4y is the increment', 'label': 'Definition'},
    ]
    deduped = both().handle_data(dupes, 'entity')
    report['dedup_semantics'] = {
        'input_names': [row['name'] for row in dupes],
        'kept_names': [row['name'] for row in deduped],
        'rule': ('identity prefers retrieval_key, so the pinned case/underscore/hyphen-insensitive '
                 'semantics are preserved'),
        'residual_conflation': ('names that differ only by case, separator or Greek capital/lowercase '
                                'still share one identity; this is the pinned identity rule, kept on '
                                'purpose and reported as a residual, not silently repaired'),
    }

    report['checks']['all_entity_names_round_trip'] = all(r['round_trip_ok'] for r in report['entity_cases'])
    report['checks']['all_relation_names_round_trip'] = all(r['round_trip_ok'] for r in report['relation_cases'])
    report['checks']['relation_branch_verbatim'] = report['relation_process']['endpoints_and_predicate_verbatim']
    report['checks']['endpoints_stay_bound'] = report['endpoint_binding']['both_endpoints_present_in_entities']
    report['checks']['positive_control_detects_damage'] = all(
        r['damaged'] for r in report['positive_control']['cases'])
    report['checks']['all_passed'] = all(report['checks'].values())

    target = NEW / 'itext_symbol_native_test.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in report['entity_cases']:
        print('%-26s %s' % (row['class'], 'OK' if row['round_trip_ok'] else 'FAIL'))
        print('    model     : %s' % row['model_returned'])
        print('    delivered : %s' % row['delivered_name'])
        print('    pinned    : %s' % retrieval_key(row['model_returned']))
    for row in report['positive_control']['cases']:
        print('    control   : %-26s damaged=%s -> %s' % (row['class'], row['damaged'], row['pinned_delivered_name']))
    print('checks:', json.dumps(report['checks'], ensure_ascii=False))
    print('wrote ->', target)
    return 0 if report['checks']['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
