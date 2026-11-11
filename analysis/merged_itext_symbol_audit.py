# -*- coding: utf-8 -*-
"""Audit symbol damage in the adapted iText2KG arms, separating upstream behaviour from adapter bugs.

Three symbol classes are checked against what the model actually returned:

  1. relationship predicate names  -> upstream DataHandler.process sanitises [^a-zA-Z0-9] to "_"
  2. hyphens / minus signs         -> upstream add_embeddings_as_property_batch maps "-" to " " in item names
  3. entity name case              -> the same helper lower-cases item names

For each class the audit compares the frozen source text, the raw model responses, and the final
graph, so a claim about "the method loses symbols" can be traced to a specific pinned line rather
than to our adapter.

Output: evaluation/external_baselines_merged_2026-10-09/itext_symbol_audit.json
"""
import json, re, sys
from pathlib import Path

NEW = Path(r'E:\MaF-KG\.worktrees\stable-main\evaluation\external_baselines_merged_2026-10-09')
sys.stdout.reconfigure(encoding='utf-8')
MINUS = '\u2212'
PLACEHOLDER = re.compile(r'^_+$')


def count_symbols(text):
    return {
        'ascii_hyphen': text.count('-'),
        'unicode_minus_u2212': text.count(MINUS),
        'fullwidth_hyphen_uff0d': text.count('\uff0d'),
        'underscore': text.count('_'),
    }


def model_names(directory):
    """Names the model actually returned, split by extraction stage."""
    entity_names, relation_names, parse_failures = [], [], []
    for path in sorted((directory / 'responses').glob('*.json')):
        stage = 'entities' if 'entities' in path.name else 'relationships' if 'relationships' in path.name else 'other'
        try:
            content = json.loads(path.read_text(encoding='utf-8'))['choices'][0]['message']['content']
            parsed = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            parse_failures.append({'file': path.name, 'error': type(exc).__name__})
            continue
        if stage == 'entities':
            entity_names += [str(r.get('name') or '') for r in (parsed.get('entities') or [])]
        elif stage == 'relationships':
            relation_names += [str(r.get('name') or '') for r in (parsed.get('relationships') or [])]
    return entity_names, relation_names, parse_failures


def graph_names(graph):
    entities = [str(e.get('name') or '') for e in (graph.get('entities') or [])]
    relations = [str(r.get('name') or '') for r in (graph.get('relationships') or [])]
    return entities, relations


def audit_arm(directory, label):
    graph_path = directory / 'graph.json'
    if not graph_path.exists():
        return {'status': 'graph_absent'}
    graph = json.loads(graph_path.read_text(encoding='utf-8'))
    final_entities, final_relations = graph_names(graph)
    model_entities, model_relations, parse_failures = model_names(directory)
    return {
        'label': label,
        'directory': str(directory),
        'entities': len(final_entities),
        'relationships': len(final_relations),
        'model_entity_names': count_symbols(' '.join(model_entities)),
        'final_entity_names': count_symbols(' '.join(final_entities)),
        'model_relation_names': count_symbols(' '.join(model_relations)),
        'final_relation_names': count_symbols(' '.join(final_relations)),
        'model_relation_name_count': len(model_relations),
        'final_relation_name_count': len(final_relations),
        'relation_names_placeholder_only': sum(1 for n in final_relations if PLACEHOLDER.match(n)),
        'relation_names_with_alnum': sum(1 for n in final_relations if re.search(r'[A-Za-z0-9]', n)),
        'model_responses_unparsed': parse_failures,
    }


def main() -> int:
    source = (NEW / 'input' / 'merged_ch2s5_ch3s1_ch3s2.md').read_text(encoding='utf-8')
    payload = {
        'source_text': count_symbols(source),
        'interpretation': {
            'relation_names': ('The pinned sanitiser rewrites relation predicates, so the model-side and '
                               'final counts are directly comparable: in the progress-bounded arms the model '
                               'returned readable predicates and the final graph contains hundreds of '
                               'underscores instead. The symbol-compatible arm shows no such conversion.'),
            'entity_names': ('Entity counts are NOT directly comparable: the model proposes one name per '
                             'section (63 here) while fusion keeps far fewer (26), so names that carry a '
                             'hyphen may simply not survive. Absence of a model name from the final graph is '
                             'not evidence of symbol loss; check whether a space- or underscore-substituted '
                             'variant appears before concluding anything.'),
        },
        'upstream_causes': [
            {
                'file': 'itext2kg/utils/data_handling.py',
                'line': 70,
                'code': 'data["name"] = re.sub(r\'[^a-zA-Z0-9]\', \'_\', data["name"]).replace("&", "and")',
                'effect': 'relationship predicate: every non-alphanumeric character (including every Chinese character) becomes "_"',
            },
            {
                'file': 'itext2kg/utils/data_handling.py',
                'line': 33,
                'code': 'item_name_key: item[item_name_key].lower().replace("_", " ").replace("-", " ")',
                'effect': 'entity/relation item names: lower-cased, "_" and "-" replaced by spaces before embedding',
            },
        ],
        'arms': {},
    }
    for label, directory in {
        'itext2kg-progress-bound-3-r1': NEW / 'guarded' / 'r1' / 'merged' / 'itext2kg',
        'itext2kg-progress-bound-3-r2': NEW / 'guarded' / 'r2' / 'merged' / 'itext2kg',
        'itext2kg-symbol-compatible-r1': NEW / 'itext_v3' / 'r1' / 'merged' / 'itext2kg',
        'itext2kg-symbol-compatible-r2': NEW / 'itext_v3' / 'r2' / 'merged' / 'itext2kg',
        'itext2kg-unguarded-r1': NEW / 'runs' / 'r1' / 'merged' / 'itext2kg',
    }.items():
        payload['arms'][label] = audit_arm(directory, label)
    target = NEW / 'itext_symbol_audit.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('source:', payload['source_text'])
    for label, info in payload['arms'].items():
        if info.get('status'):
            print('%-30s %s' % (label, info['status']))
            continue
        print('%-31s rel=%d placeholder_only=%d with_alnum=%d' % (
            label, info['relationships'], info['relation_names_placeholder_only'], info['relation_names_with_alnum']))
        print('    model relations :', info['model_relation_names'], '(%d names)' % info['model_relation_name_count'])
        print('    final relations :', info['final_relation_names'], '(%d names)' % info['final_relation_name_count'])
        print('    model entities  :', info['model_entity_names'])
        print('    final entities  :', info['final_entity_names'])
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
