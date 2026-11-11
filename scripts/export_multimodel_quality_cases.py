"""Export every flagged locked audit row with source excerpts for human checking."""
import argparse
import json
from pathlib import Path

from score_blinded_prediction_audit import summarize, read, digest
from run_memory_ablation import write_json


def export(merged_path):
    merged = read(merged_path)
    items = []
    for audit in merged['audit_sources']:
        directory, review_path = Path(audit['packet_dir']), Path(audit['review_path'])
        summarize(directory, review_path)
        if digest(review_path) != audit['review_sha256']:
            raise ValueError('Locked review changed')
        private = read(directory / 'private/unblinding_map.json')
        ratings = {g['packet_id']: g for g in read(review_path)['graphs']}
        for binding in private['packets']:
            graph = read(binding['graph_path'])
            nodes = {n['id']: n for n in graph['nodes']}
            edges = {e['id']: e for e in graph['relations']}
            source = Path(binding['source_path']).read_text(encoding='utf-8').splitlines()
            for kind, field, mapping in [('node', 'node_reviews', 'node_mapping'),
                                         ('relation', 'relation_reviews', 'relation_mapping')]:
                ids = {r['review_id']: r['original_id'] for r in binding[mapping]}
                for row in ratings[binding['packet_id']][field]:
                    labels = {k: row[k] for k in ('source_support', 'ontology_admission',
                              'condition_completeness', 'direction_correctness', 'schema_signature') if k in row}
                    flagged = any(v in ('unsupported', 'uncertain', 'ineligible', 'incomplete', 'incorrect', 'invalid')
                                  for v in labels.values()) or row.get('duplicate_group') is not None
                    if not flagged:
                        continue
                    original_id = ids[row['review_id']]
                    prediction = nodes[original_id] if kind == 'node' else edges[original_id]
                    items.append(dict(model=binding['model'], section=binding['section_id'], arm=binding['arm'],
                                      kind=kind, original_id=original_id, graph_sha256=binding['graph_sha256'],
                                      graph_path=binding['graph_path'], review_id=row['review_id'],
                                      prediction=prediction, labels=labels, duplicate_group=row.get('duplicate_group'),
                                      reason=row['reason'], source_path=binding['source_path'],
                                      source_excerpts=[dict(line=i, text=source[i-1]) for i in row['source_lines']]))
    return {'provenance': 'All flagged AI-assisted audit rows; pending human adjudication, no labels changed',
            'merged_quality_path': str(merged_path), 'merged_quality_sha256': digest(merged_path),
            'cases': items}


def render(result):
    lines = ['# 多模型质量人工复核材料', '', result['provenance'], '',
             '评级来自锁定AI审查，以下不是已由专家确认的错误；原文与预测分开列出。', '']
    for index, item in enumerate(result['cases'], 1):
        name = item['prediction'].get('name', item['original_id'])
        fields = ('type', 'name', 'description', 'logical_form') if item['kind'] == 'node' else ('type', 'source_id', 'target_id')
        prediction = {k: item['prediction'].get(k) for k in fields}
        lines += [f"## Q{index}: {item['model']} / {item['section']} / {item['arm']} / {name}", '',
                  f"预测ID：{item['original_id']}；标签：{json.dumps(item['labels'], ensure_ascii=False)}", '',
                  '模型预测：', '```json', json.dumps(prediction, ensure_ascii=False, indent=2), '```', '',
                  f"AI审查理由：{item['reason']}", '', f"原文文件：{item['source_path']}", '']
        lines += [f"- L{span['line']}：{span['text']}" for span in item['source_excerpts']]
        lines += ['']
    return '\n'.join(lines)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--quality', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = export(args.quality.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    args.output.with_suffix('.md').write_text(render(result), encoding='utf-8')
    print(json.dumps({'flagged_cases': len(result['cases'])}))
