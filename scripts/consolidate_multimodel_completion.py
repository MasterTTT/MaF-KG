"""Join validated finite-reference reviews to every cell, preserving retry outcomes."""
import argparse
import json
from collections import Counter
from pathlib import Path

import summarize_prospective_quality_reviews as finite
from run_memory_ablation import write_json


def failure_kind(directory):
    log = (Path(directory) / 'stdout.log').read_text(encoding='utf-8')
    markers = [('budget_gate', 'Pre-request'), ('output_truncated', 'CompletionTruncated'),
               ('schema_invalid', 'Chunk response failed schema validation'),
               ('json_invalid', 'JsonParseFailure'), ('merge_invalid', 'ChunkMergeError'),
               ('timeout', 'TimeoutError')]
    for label, marker in markers:
        if marker in log:
            return label
    if 'Native request failed' in log:
        return 'native_client_error'
    return 'other_recorded_failure'


def validate_embedded(review, review_path):
    specs = finite._graph_specs(review, 'graph_local')
    spec = specs[0]
    graph_path = finite.resolve_path(spec['graph_path'])
    bindings, reference_path, source_path, reference = finite.verify_bindings(review, [graph_path])
    sha = finite.sha256_file(graph_path)
    finite.require(finite._graph_hash_claims(review, spec, graph_path, bindings)[0] == sha,
                   'Embedded review graph hash mismatch')
    manifest_path, _ = finite.verify_manifest_job(review, spec, graph_path, sha)
    _, model, section, arm = finite.graph_coordinates(graph_path)
    finite.require(review['scope']['section_id'] == section, 'Section mismatch')
    score = finite.score_graph(reference, finite.read_json(graph_path), section,
                               review['mandatory_item_annotations'], review['finite_reference_relations'])
    return dict(model=model, section=section, arm=arm, graph_path=str(graph_path),
                graph_sha256=sha, review_path=str(review_path), manifest_path=str(manifest_path),
                reference_sha256=bindings[reference_path], source_sha256=bindings[source_path], **score)


def collect_reviews(extra):
    # Only this process's validator allow-list is extended; historical files and
    # the 19-row historical summary are not overwritten.
    finite.MODEL_ALIASES.update({'longcat-2.0': 'longcat', 'glm-5.3-flash': 'glm'})
    rows = finite.build_summary()['rows']
    root = finite.EVALUATION_ROOT
    rows += finite.validate_review_file(root / 'preliminary_taylor_review/longcat_direct/review.json', 'graph_local')
    bundle_path = root / 'longcat_supplement_review/review.json'
    rows += [validate_embedded(r, bundle_path) for r in finite.read_json(bundle_path)['graph_reviews']]
    for path in extra:
        review = finite.read_json(path)
        if review['schema_version'] != 'completion-finite-alignment.v1':
            raise ValueError('Unrecognized supplementary review schema')
        rows += [validate_embedded(r, path) for r in review['graph_reviews']]
    indexed = {r['graph_sha256']: r for r in rows}
    finite.require(len(indexed) == len(rows), 'Duplicate graph review')
    return indexed


def consolidate(manifest_path, extra):
    manifest = finite.read_json(manifest_path)
    finite.require(all(j['status'] in ('completed', 'failed') for j in manifest['jobs']),
                   'Cannot close out running or pending matrix')
    reviews = collect_reviews(extra)
    rows = []
    used = set()
    for job in manifest['jobs']:
        row = {k: job[k] for k in ('model', 'section', 'name', 'status')}
        previous = job.get('previous_failed_attempt') if job.get('attempt_number', 1) >= 2 else None
        earliest = previous
        while earliest and earliest.get('previous_failed_attempt'):
            earliest = earliest['previous_failed_attempt']
        row['first_outcome'] = 'failed' if previous else job['status']
        row['completion_second_attempt'] = job.get('attempt_number') == 2
        row['attempt_number'] = job.get('attempt_number', 1)
        row['budget_admin_continuation'] = row['attempt_number'] >= 3
        row['first_attempt_directory'] = earliest['directory'] if earliest else job['directory']
        row['latest_attempt_directory'] = job['directory']
        if job['status'] == 'completed':
            graph_path = Path(job['directory']) / 'graph.json'
            finite.require(finite.sha256_file(graph_path) == job['graph_sha256'], 'Manifest graph changed')
            graph = finite.read_json(graph_path)
            row.update(nodes=len(graph['nodes']), relations=len(graph['relations']),
                       graph_sha256=job['graph_sha256'])
            quality = reviews.get(job['graph_sha256'])
            if quality is None:
                row['finite_review_status'] = 'unreviewed'
            else:
                finite.require((quality['model'], quality['section'], quality['arm']) ==
                               (job['model'], job['section'], job['name']), 'Review coordinate mismatch')
                row['finite_review_status'] = 'reviewed'
                row['finite_quality'] = quality
                used.add(job['graph_sha256'])
        else:
            row['finite_review_status'] = 'failed_not_scored'
            row['failure_kind'] = failure_kind(job['directory'])
        rows.append(row)
    summaries = []
    for model in manifest['models']:
        subset = [r for r in rows if r['model'] == model]
        scored = [r['finite_quality'] for r in subset if 'finite_quality' in r]
        summaries.append(dict(model=model, first_completed=sum(r['first_outcome'] == 'completed' for r in subset),
                              eventual_completed=sum(r['status'] == 'completed' for r in subset),
                              attempted_cells=len(subset), reviewed_graphs=len(scored),
                              typed_hits=sum(r['mandatory_typed_identity_hits'] for r in scored),
                              typed_total=sum(r['mandatory_typed_identity_total'] for r in scored),
                              edge_hits=sum(r['exact_finite_relation_hits'] for r in scored),
                              edge_total=sum(r['exact_finite_relation_total'] for r in scored)))
    paired = []
    for model in manifest['models']:
        for section in manifest['sections']:
            cells = [r for r in rows if r['model'] == model and r['section'] == section['id']]
            if len(cells) != 2 or not all('finite_quality' in r for r in cells):
                continue
            arms = {r['name']: r['finite_quality'] for r in cells}
            delta = lambda field: arms['sequential'][field] - arms['direct'][field]
            paired.append(dict(model=model, section=section['id'],
                               typed_hit_delta=delta('mandatory_typed_identity_hits'),
                               edge_hit_delta=delta('exact_finite_relation_hits')))
    budgets = [finite.read_json(path) for path in (Path(manifest_path).parent / 'budgets').glob('*.json')]
    return dict(manifest_path=str(manifest_path), manifest_sha256=finite.sha256_file(manifest_path),
                rows=rows, model_summaries=summaries,
                complete_pairs=paired,
                failure_categories=dict(Counter(r['failure_kind'] for r in rows if r['status'] == 'failed')),
                estimated_cumulative_usd=sum(b['accounting_usd'] for b in budgets),
                status_counts=dict(Counter(r['status'] for r in rows)),
                unreviewed_completed=sum(r['finite_review_status'] == 'unreviewed' for r in rows),
                caveat='Reference matches are AI-assisted finite typed/edge recall, not precision/F1 or independent expert Gold. First_outcome means pre-completion-wave outcome, not necessarily the earliest HTTP attempt in run01-07. Unequal successful-cell denominators preclude aggregate ranking.')


def markdown(result):
    lines = ['# 多模型完整单元矩阵', '', result['caveat'], '',
             '|模型|小节|方式|补跑前|最新|节点/关系|类型语义命中|有限关系命中|',
             '|---|---|---|---|---|---:|---:|---:|']
    for row in result['rows']:
        q = row.get('finite_quality')
        hits = f"{q['mandatory_typed_identity_hits']}/{q['mandatory_typed_identity_total']}" if q else row['finite_review_status']
        edges = f"{q['exact_finite_relation_hits']}/{q['exact_finite_relation_total']}" if q else '-'
        size = f"{row['nodes']}/{row['relations']}" if q or row['status'] == 'completed' else '-'
        lines.append(f"|{row['model']}|{row['section']}|{row['name']}|{row['first_outcome']}|{row['status']}|{size}|{hits}|{edges}|")
    lines += ['', '|模型|补跑前完成|累计完成|已评分图|类型语义命中合计|有限关系命中合计|',
              '|---|---:|---:|---:|---:|---:|']
    for row in result['model_summaries']:
        lines.append(f"|{row['model']}|{row['first_completed']}/{row['attempted_cells']}|{row['eventual_completed']}/{row['attempted_cells']}|{row['reviewed_graphs']}|{row['typed_hits']}/{row['typed_total']}|{row['edge_hits']}/{row['edge_total']}|")
    lines += ['', '失败不按语义零分计入；模型汇总的分母随成功单元改变，不用于直接排名。补跑前完成率是run07快照，不是原始请求第一次成功率。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extra-review', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = consolidate(args.manifest.resolve(), args.extra_review)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    args.output.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps({'cells': len(result['rows']), 'unreviewed_completed': result['unreviewed_completed']}))
