"""Describe rescue success separately from frozen benchmark completion."""
import argparse
from pathlib import Path
from run_memory_ablation import digest, write_json
from run_prospective_multimodel import read


def summarize(output):
    manifest = read(output / 'manifest.json')
    old = read(Path(manifest['previous_manifest']))
    if digest(Path(manifest['previous_manifest'])) != manifest['previous_manifest_sha256']:
        raise ValueError('Original manifest changed')
    successes = manifest.get('prior_successes', []) + [j for j in manifest['jobs'] if j['status'] == 'completed']
    rows = []
    for job in successes:
        attempt = job['attempts'][-1]
        graph_path = Path(attempt['directory']) / 'graph.json'
        if digest(graph_path) != attempt['graph_sha256']:
            raise ValueError('Rescue graph changed')
        graph = read(graph_path)
        ids = [n['id'] for n in graph['nodes']]
        dangling = [r['id'] for r in graph['relations'] if r['source_id'] not in ids or r['target_id'] not in ids]
        responses = []
        for path in sorted((graph_path.parent / 'native_responses').glob('*.json')):
            native = read(path)
            message = native['choices'][0]['message']
            responses.append({'path': str(path), 'finish_reason': native['choices'][0]['finish_reason'],
                              'content_chars': len(message.get('content') or ''),
                              'reasoning_chars': len(message.get('reasoning_content') or ''),
                              'usage': native.get('usage')})
        if dangling or len(set(ids)) != len(ids):
            raise ValueError('Invalid graph identity or endpoints')
        rows.append({'model': job['model'], 'section': job['section'], 'arm': job['name'],
                     'nodes': len(ids), 'relations': len(graph['relations']),
                     'successful_attempt_output_limit': attempt['max_tokens'],
                     'graph': str(graph_path), 'graph_sha256': digest(graph_path),
                     'native_responses': responses,
                     'semantic_quality': 'Not expert-adjudicated; execution success only'})
    costs = [read(p) for p in (output / 'budgets').glob('*.json')]
    report = {'original_primary_completed': sum(j['status'] == 'completed' for j in old['jobs'] if j['model'] != 'longcat-2.0'),
              'original_primary_total': 30, 'rescued_cells': len(rows), 'cells': rows,
              'adaptive_primary_completed': sum(j['status'] == 'completed' for j in old['jobs'] if j['model'] != 'longcat-2.0') + len(rows),
              'additional_conservative_accounting_usd': sum(b['accounting_usd'] for b in costs),
              'additional_conservative_accounting_tokens': sum(b['accounting_tokens'] for b in costs),
              'pending': [j for j in manifest['jobs'] if j['status'] in ('pending', 'running')],
              'failed': [j for j in manifest['jobs'] if j['status'] == 'failed'],
              'warning': 'Adaptive rescue results cannot replace fixed-configuration results or prove semantic accuracy.'}
    write_json(output / 'rescue_summary.json', report)
    text = ['# 多模型成功性补救结果', '',
            f"原五模型固定配置：{report['original_primary_completed']}/30；本轮自适应补救新增：{len(rows)}项；自适应配置合计{report['adaptive_primary_completed']}/30。", '',
            '|模型|小节|方式|节点|关系|', '|---|---|---|---:|---:|']
    text += [f"|{r['model']}|{r['section']}|{r['arm']}|{r['nodes']}|{r['relations']}|" for r in rows]
    text += ['', '成功图通过完整chunk覆盖、schema和端点检查；语义质量尚未专家审定。',
             '原始失败不删除，自适应推理配置及输出预算与原基线分开报告。',
             f"额外保守账本：{report['additional_conservative_accounting_usd']:.6f}美元，不是供应商账单。"]
    (output / 'rescue_summary.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    print({k: v for k, v in report.items() if k in ('rescued_cells', 'additional_conservative_accounting_usd')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    summarize(parser.parse_args().output.resolve())
