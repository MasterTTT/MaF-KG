"""Recompute explicit source-reviewed alignments; no automatic semantic matching."""
import argparse
from pathlib import Path
from run_memory_ablation import digest, write_json
from run_prospective_multimodel import read
from summarize_prospective_quality_reviews import score_graph

# These are review decisions, not extraction rules or a name-matching evaluator.
ALIGNMENTS = {
    ('mimo-v2.6-flash', 'ch3_s3', 'direct'): {
        'T-N1': '泰勒多项式', 'T-N2': '带佩亚诺余项的泰勒中值定理',
        'T-N3': '带拉格朗日余项的泰勒中值定理', 'T-N4': '泰勒公式余项的误差估计式',
        'T-N5': '麦克劳林公式', 'T-N6': '麦克劳林公式'},
    ('glm-5.3-flash', 'ch3_s3', 'sequential'): {
        'T-N1': '泰勒多项式', 'T-N2': '泰勒中值定理1', 'T-N3': '泰勒中值定理2',
        'T-N4': '泰勒公式余项的误差估计式',
        'T-N5': '带有佩亚诺余项的麦克劳林公式', 'T-N6': '带有拉格朗日余项的麦克劳林公式'},
    ('glm-5.3-flash', 'ch3_s3', 'direct'): {
        'T-N1': 'n次泰勒多项式', 'T-N2': '泰勒中值定理1（带佩亚诺余项）',
        'T-N3': '泰勒中值定理2（带拉格朗日余项）', 'T-N4': '拉格朗日余项的误差估计式',
        'T-N5': '麦克劳林公式', 'T-N6': '麦克劳林公式'},
    ('glm-5.3-flash', 'ch4_s3', 'direct'): {'I-N1': '分部积分公式', 'I-N2': '分部积分法'},
    ('glm-5.3-flash', 'ch7_s5', 'sequential'): {
        'D-N1': None, 'D-N2': 'y^(n)=f(x) 型微分方程', 'D-N3': '逐次积分降阶法',
        'D-N4': "y''=f(x,y') 型微分方程", 'D-N5': "降阶法（设y'=p）",
        'D-N6': "y''=f(y,y') 型微分方程", 'D-N7': "降阶法（设y'=p，化为p对y的方程）"},
}


def evaluate(directory):
    manifest_path = directory / 'manifest.json'
    manifest = read(manifest_path)
    reference_path = Path(manifest['reference'])
    expected = {str(Path(a['path']).resolve()): a['sha256'] for a in manifest['artifacts']}
    if digest(reference_path) != expected[str(reference_path.resolve())]:
        raise ValueError('Frozen reference changed')
    reference = read(reference_path)
    reviews, results = [], []
    for job in manifest['jobs']:
        key = (job['model'], job['section'], job['name'])
        selected = ALIGNMENTS[key]
        section = next(s for s in manifest['sections'] if s['id'] == job['section'])
        source = Path(section['source'])
        if digest(source) != section['source_sha256']:
            raise ValueError('Authoritative textbook changed')
        graph_path = Path(job['directory']) / 'graph.json'
        if digest(graph_path) != job['graph_sha256']:
            raise ValueError('Rescue graph changed')
        graph = read(graph_path)
        names = {}
        for node in graph['nodes']:
            if node['name'] in names:
                raise ValueError('Ambiguous alignment name')
            names[node['name']] = node
        refs = [r for r in reference['nodes'] if r['section'] == job['section'] and r['status'] == 'M']
        if set(selected) != {r['id'] for r in refs}:
            raise ValueError('Reference coverage changed')
        rows = []
        for ref in refs:
            node = names[selected[ref['id']]] if selected[ref['id']] is not None else None
            status = 'matched' if node and node['type'] == ref['type'] else 'wrong_type' if node else 'missing'
            reason = ('源文定义的高阶微分方程总概念未独立抽取，三个方程族不能替代总概念。' if node is None else
                      '逐描述核对符号对象及正文含义对应此参考语义；身份命中不保证条件完整。' if status == 'matched' else
                      '公式内容有载体，但预测标为Concept而参考为Claim，不计带类型身份命中。合并麦克劳林节点承载两个变体，也不计两个独立Claim。')
            rows.append({'item_id': ref['id'], 'reference_type': ref['type'], 'reference_name': ref['name'],
                         'identity_id': node['id'] if node else None, 'identity_status': status,
                         'graph_type': node['type'] if node else None,
                         'content_ids': [node['id']] if node else [],
                         'reason': reason, 'source_lines': list(ref['evidence'])})
        edges = [{'reference_id': r['id']} for r in reference['relations_provisional'] if r['section'] == job['section']]
        score = score_graph(reference, graph, job['section'], rows, edges)
        reviews.append({'model': job['model'], 'section': job['section'], 'arm': job['name'],
                        'graph_path': str(graph_path), 'graph_sha256': job['graph_sha256'],
                        'source_path': str(source), 'source_sha256': digest(source),
                        'mandatory_item_annotations': rows, 'finite_reference_relations': edges})
        results.append({'model': job['model'], 'section': job['section'], 'arm': job['name'],
                        'graph_sha256': job['graph_sha256'], **score})
    write_json(directory / 'finite_alignment.json', {
        'reviewer': 'Codex source-reviewed assistant; exposed to identities and prior reference',
        'provenance': 'Explicit semantic alignments, not expert Gold. Candidate names only resolve already-reviewed decisions.',
        'reference_path': str(reference_path), 'reference_sha256': digest(reference_path),
        'manifest_sha256': digest(manifest_path), 'scoring_script_sha256': digest(Path(__file__)),
        'graph_reviews': reviews})
    write_json(directory / 'finite_scores.json', {'rows': results,
               'typed_hits': sum(r['mandatory_typed_identity_hits'] for r in results),
               'typed_total': sum(r['mandatory_typed_identity_total'] for r in results),
               'edge_hits': sum(r['exact_finite_relation_hits'] for r in results),
               'edge_total': sum(r['exact_finite_relation_total'] for r in results),
               'caveat': 'Finite source-reviewed reference recall only; not expert accuracy or F1.'})
    print([(r['model'], r['section'], r['arm'], r['mandatory_typed_identity_hits'], r['exact_finite_relation_hits']) for r in results])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', type=Path, required=True)
    evaluate(parser.parse_args().evaluation.resolve())
