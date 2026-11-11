"""Revalidate masked audits and join all rated predictions to a settled matrix."""
import argparse
import json
from pathlib import Path

from score_blinded_prediction_audit import summarize, digest, read, require
from run_memory_ablation import write_json


AXES = ('node_source_support', 'node_admission', 'condition_completeness',
        'relation_source_support', 'relation_direction')


def merge(manifest_path, audits):
    manifest = read(manifest_path)
    require(all(j['status'] in ('completed', 'failed') for j in manifest['jobs']), 'Matrix not settled')
    reviewed = {}
    sources = []
    for packet_dir, review_path in audits:
        result = summarize(packet_dir, review_path)
        sources.append({'packet_dir': str(packet_dir), 'review_path': str(review_path),
                        'review_sha256': result['review_sha256']})
        for row in result['rows']:
            sha = row['graph_sha256']
            require(sha not in reviewed, 'Repeated graph audit; choose one locked review explicitly')
            reviewed[sha] = row
    cells = []
    for job in manifest['jobs']:
        cell = {k: job[k] for k in ('model', 'section', 'name', 'status')}
        if job['status'] == 'completed':
            path = Path(job['directory']) / 'graph.json'
            require(digest(path) == job['graph_sha256'], 'Completed graph changed')
            row = reviewed.get(job['graph_sha256'])
            cell['audit_status'] = 'reviewed' if row else 'unreviewed'
            if row:
                require((row['model'], row['section'], row['arm']) ==
                        (job['model'], job['section'], job['name']), 'Audit coordinates differ')
                cell['audit'] = row
        else:
            cell['audit_status'] = 'failed_not_scored'
        cells.append(cell)
    require(set(reviewed) <= {j.get('graph_sha256') for j in manifest['jobs'] if j['status'] == 'completed'},
            'Audited graph not part of current successful matrix')
    return {'manifest_path': str(manifest_path), 'manifest_sha256': digest(manifest_path),
            'audit_sources': sources, 'cells': cells,
            'reviewed_graphs': sum(c['audit_status'] == 'reviewed' for c in cells),
            'unreviewed_completed': sum(c['audit_status'] == 'unreviewed' for c in cells),
            'reviewed_nodes': sum(c.get('audit', {}).get('nodes', 0) for c in cells),
            'reviewed_relations': sum(c.get('audit', {}).get('relations', 0) for c in cells),
            'caveat': 'AI-assisted full-prediction validity axes, not independent expert accuracy. Uncertainty bounds are not confidence intervals. Failure and unreviewed cells are never quality zeros.'}


def render(result):
    lines = ['# 多模型逐预测质量矩阵', '', result['caveat'], '',
             '|模型|小节|方式|状态|节点来源|节点准入|条件完整|关系来源|方向|',
             '|---|---|---|---|---:|---:|---:|---:|---:|']
    for c in result['cells']:
        values = []
        for axis in AXES:
            if 'audit' not in c:
                values.append('-')
                continue
            row = c['audit'][axis]
            yes = sum(v for k, v in row['counts'].items() if k in ('supported', 'eligible', 'complete', 'correct'))
            values.append(f"{yes}/{row['applicable']}，{row['uncertain']}待定")
        lines.append('|' + '|'.join([c['model'], c['section'], c['name'], c['audit_status']] + values) + '|')
    lines += ['', f"已审查{result['reviewed_graphs']}张图，{result['reviewed_nodes']}节点，{result['reviewed_relations']}关系；未审成功图{result['unreviewed_completed']}。", '']
    return '\n'.join(lines)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--audit', nargs=2, action='append', type=Path, required=True,
                   metavar=('PACKET_DIR', 'REVIEW_JSON'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = merge(args.manifest.resolve(), args.audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    args.output.with_suffix('.md').write_text(render(result), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('reviewed_graphs', 'unreviewed_completed', 'reviewed_nodes', 'reviewed_relations')}))
