"""Validate new ratings and describe the adaptive five-model complete matrix."""
import argparse
from pathlib import Path
from collections import Counter
from analyze_matched_multimodel import revalidate, summarize_cells
from run_memory_ablation import digest, write_json
from run_prospective_multimodel import read
from score_blinded_prediction_audit import summarize
from summarize_prospective_quality_reviews import score_graph

PRIMARY = ('deepseek-v4-flash', 'gpt-6-luna', 'mimo-v2.6-flash', 'glm-5.3-flash', 'kimi-k2.6')


def build(directory):
    manifest_path = directory / 'manifest.json'
    manifest = read(manifest_path)
    alignment = read(directory / 'finite_alignment.json')
    if alignment['manifest_sha256'] != digest(manifest_path):
        raise ValueError('Finite evaluation manifest changed')
    ref_path = Path(alignment['reference_path'])
    if digest(ref_path) != alignment['reference_sha256']:
        raise ValueError('Finite reference changed')
    ref = read(ref_path)
    locked_review = read(directory / 'review.json')
    for binding in locked_review['review_sources']:
        if digest(Path(binding['path'])) != binding['sha256']:
            raise ValueError('Reviewer revision or coordinator decision changed')
    audit = summarize(directory / 'packets', directory / 'review.json')
    by_hash = {r['graph_sha256']: r for r in audit['rows']}
    rows = []
    for review in alignment['graph_reviews']:
        path = Path(review['graph_path'])
        if digest(path) != review['graph_sha256'] or digest(Path(review['source_path'])) != review['source_sha256']:
            raise ValueError('Bound source or graph changed')
        job = next(j for j in manifest['jobs'] if j['graph_sha256'] == review['graph_sha256'])
        if (job['model'], job['section'], job['name']) != (review['model'], review['section'], review['arm']):
            raise ValueError('Finite review coordinate mismatch')
        q = score_graph(ref, read(path), review['section'], review['mandatory_item_annotations'], review['finite_reference_relations'])
        rows.append({'model': review['model'], 'section': review['section'], 'name': review['arm'],
                     'graph_sha256': review['graph_sha256'], 'configuration': 'adaptive_rescue',
                     'finite_quality': q, 'audit': by_hash[review['graph_sha256']]})
    old_dir = directory.parent / 'multimodel_completion_2026-10-05'
    matrix, quality = revalidate(old_dir / 'final_review_matrix.json', old_dir / 'final_prediction_quality.json')
    old_audits = {(c['model'], c['section'], c['name']): c for c in quality['cells']}
    combined = []
    new = {(r['model'], r['section'], r['name']): r for r in rows}
    for cell in matrix['rows']:
        key = (cell['model'], cell['section'], cell['name'])
        if key[0] not in PRIMARY:
            continue
        if cell['status'] == 'completed':
            if key in new:
                raise ValueError('Rescue must never replace a prior successful cell')
            combined.append({**cell, 'audit': old_audits[key]['audit'], 'configuration': 'original_fixed'})
        else:
            combined.append(new[key])
    if len(combined) != 30 or len({(c['model'], c['section'], c['name']) for c in combined}) != 30:
        raise ValueError('Expected thirty unique primary cells')
    groups = [{'model': m, **summarize_cells([r for r in combined if r['model'] == m])} for m in PRIMARY]
    cases = []
    private = read(directory / 'packets/private/unblinding_map.json')
    ratings = read(directory / 'review.json')
    rating_map = {g['packet_id']: g for g in ratings['graphs']}
    for binding in private['packets']:
        graph = read(Path(binding['graph_path']))
        nodes = {n['id']: n for n in graph['nodes']}
        relations = {r['id']: r for r in graph['relations']}
        for kind, mapping_key, ratings_key in [('node', 'node_mapping', 'node_reviews'), ('relation', 'relation_mapping', 'relation_reviews')]:
            ids = {m['review_id']: m['original_id'] for m in binding[mapping_key]}
            for row in rating_map[binding['packet_id']][ratings_key]:
                flags = {k: v for k, v in row.items() if isinstance(v, str)
                         and v in ('unsupported', 'ineligible', 'incomplete', 'incorrect', 'uncertain', 'invalid')}
                if row.get('duplicate_group'):
                    flags['duplicate_group'] = row['duplicate_group']
                if flags:
                    oid = ids[row['review_id']]
                    cases.append({'model': binding['model'], 'section': binding['section_id'], 'arm': binding['arm'],
                                  'kind': kind, 'review_id': row['review_id'], 'flags': flags,
                                  'prediction': nodes[oid] if kind == 'node' else relations[oid],
                                  'reason': row['reason'], 'source_lines': row['source_lines']})
                    if kind == 'relation':
                        edge = relations[oid]
                        cases[-1]['endpoint_names'] = {
                            'source': nodes[edge['source_id']]['name'], 'target': nodes[edge['target_id']]['name']}
    result = {'new_graphs': rows, 'new_prediction_quality': audit,
              'primary_complete_adaptive_matrix': groups,
              'combined_primary_cells': combined,
              'flagged_predictions': cases,
              'hash_bindings': [ {'path': str(p), 'sha256': digest(p)} for p in
                                (manifest_path, directory/'review.json', directory/'finite_alignment.json', Path(__file__),
                                 old_dir/'final_review_matrix.json', old_dir/'final_prediction_quality.json')],
              'caveat': 'AI-assisted source judgments, not expert Gold; mixed native reasoning settings and adaptive rescue budgets preclude a controlled model ranking or causal reasoning claims.'}
    write_json(directory / 'quality_results.json', result)
    lines = ['# 新增五图质量评估', '', result['caveat'], '',
             '|模型|小节|方式|类型语义命中|有限关系命中|节点准入|条件完整|关系支持|',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        q, a = r['finite_quality'], r['audit']
        ratio = lambda axis, positive: f"{a[axis]['counts'].get(positive,0)}/{a[axis]['applicable']}（待定{a[axis]['uncertain']}）"
        lines.append(f"|{r['model']}|{r['section']}|{r['name']}|{q['mandatory_typed_identity_hits']}/{q['mandatory_typed_identity_total']}|{q['exact_finite_relation_hits']}/{q['exact_finite_relation_total']}|{ratio('node_admission','eligible')}|{ratio('condition_completeness','complete')}|{ratio('relation_source_support','supported')}|")
    lines += ['', '## 五模型完整集合的描述性结果', '',
              '每模型相同三节两臂，共6项。GLM四项、MiMo一项来自补救配置；不是统一推理预算的排名。', '',
              '|模型|类型语义命中|有限关系命中|节点准入|条件完整|', '|---|---:|---:|---:|---:|']
    for g in groups:
        pct = lambda axis, positive: f"{g[axis]['counts'].get(positive,0)}/{g[axis]['applicable_predictions']}"
        lines.append(f"|{g['model']}|{g['typed']['hits']}/{g['typed']['total']}|{g['edge']['hits']}/{g['edge']['total']}|{pct('node_admission','eligible')}|{pct('condition_completeness','complete')}|")
    lines += ['', f'新增审查48节点、38关系，{len(cases)}条预测被标记，标签可重叠，需人审；不等于已确认错误数。',
              '有限参考召回与全预测有效性分开报告，不拼成F1。', '']
    (directory / 'quality_results.md').write_text('\n'.join(lines), encoding='utf-8')
    print({'graphs':len(rows), 'flagged_predictions':len(cases), 'nodes':audit['total_nodes'], 'relations':audit['total_relations']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', required=True, type=Path)
    build(parser.parse_args().evaluation.resolve())
