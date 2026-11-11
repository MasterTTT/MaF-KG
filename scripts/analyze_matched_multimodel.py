"""Revalidate locked reviews, then describe availability-matched cells and pairs."""
import argparse
from collections import Counter
from pathlib import Path
from statistics import mean

from consolidate_multimodel_completion import consolidate
from merge_multimodel_prediction_audits import merge, AXES
from run_memory_ablation import write_json
from score_blinded_prediction_audit import digest, read, require

ORIGINAL = ('deepseek-v4-flash', 'glm-5.3-flash', 'gpt-6-luna',
            'mimo-v2.6-flash', 'kimi-k2.6')
SUPPLEMENT = 'longcat-2.0'
POSITIVE = {'node_source_support': 'supported', 'node_admission': 'eligible',
            'condition_completeness': 'complete', 'relation_source_support': 'supported',
            'relation_direction': 'correct'}
FINITE = {'typed': ('mandatory_typed_identity_hits', 'mandatory_typed_identity_total'),
          'edge': ('exact_finite_relation_hits', 'exact_finite_relation_total')}


def key(row):
    return row['model'], row['section'], row['name']


def revalidate(matrix_path, quality_path):
    matrix, quality = read(matrix_path), read(quality_path)
    require(matrix['manifest_path'] == quality['manifest_path'], 'Different manifests')
    require(matrix['manifest_sha256'] == quality['manifest_sha256'] ==
            digest(Path(matrix['manifest_path'])), 'Manifest changed')
    extras = set()
    for row in matrix['rows']:
        if 'finite_quality' in row:
            path = Path(row['finite_quality']['review_path'])
            if read(path).get('schema_version') == 'completion-finite-alignment.v1':
                extras.add(path)
    rebuilt = consolidate(Path(matrix['manifest_path']), sorted(extras))
    require(rebuilt == matrix, 'Finite summary differs from locked reviews')
    audits = [(Path(a['packet_dir']), Path(a['review_path'])) for a in quality['audit_sources']]
    require(merge(Path(quality['manifest_path']), audits) == quality,
            'Prediction summary differs from locked reviews')
    require(matrix['unreviewed_completed'] == quality['unreviewed_completed'] == 0,
            'Completed graphs must all be reviewed')
    return matrix, quality


def summarize_cells(cells):
    result = {'cells': len(cells)}
    for label, (hit, total) in FINITE.items():
        qs = [c['finite_quality'] for c in cells]
        ratios = [q[hit] / q[total] for q in qs if q[total]]
        result[label] = {'hits': sum(q[hit] for q in qs), 'total': sum(q[total] for q in qs),
                         'macro_ratio': mean(ratios) if ratios else None,
                         'applicable_cells': len(ratios)}
    for axis in AXES:
        values = [c['audit'][axis] for c in cells]
        applicable = [v for v in values if v['applicable']]
        counts = sum((Counter(v['counts']) for v in values), Counter())
        result[axis] = {'counts': dict(counts),
                       'applicable_predictions': sum(v['applicable'] for v in values),
                       'applicable_cells': len(applicable),
                       'macro_lower': mean(v['counts'].get(POSITIVE[axis], 0) / v['applicable']
                                           for v in applicable) if applicable else None,
                       'macro_upper': mean((v['counts'].get(POSITIVE[axis], 0) + v['uncertain']) /
                                           v['applicable'] for v in applicable) if applicable else None}
    return result


def analyze(matrix, quality):
    audits = {key(c): c for c in quality['cells']}
    require(len(audits) == len(quality['cells']), 'Duplicate audit coordinates')
    require(len({key(r) for r in matrix['rows']}) == len(matrix['rows']), 'Duplicate finite coordinates')
    require(set(audits) == {key(r) for r in matrix['rows']}, 'Coordinate sets differ')
    rows = {}
    for r in matrix['rows']:
        c = audits[key(r)]
        require(r['status'] == c['status'], 'Different cell status')
        if r['status'] == 'completed':
            require('finite_quality' in r and 'audit' in c, 'Unreviewed success')
            require(r['graph_sha256'] == c['audit']['graph_sha256'], 'Different graph')
            rows[key(r)] = {**r, 'audit': c['audit']}
    require(all(any(k[0] == m for k in audits) for m in ORIGINAL), 'Missing original model')
    universes = {m: {(s, a) for (model, s, a) in audits if model == m} for m in ORIGINAL}
    require(len({frozenset(v) for v in universes.values()}) == 1, 'Different planned cells')
    planned = universes[ORIGINAL[0]]
    successes = {m: {(s, a) for model, s, a in rows if model == m} for m in ORIGINAL}
    common = set.intersection(*(successes[m] for m in ORIGINAL))
    full_models = [m for m in ORIGINAL if successes[m] == planned]
    groups = []
    for name, models, coords in [('original_five_shared_successes', list(ORIGINAL), common),
                                 ('full_original_models', full_models, planned)]:
        summaries = []
        if coords:
            for m in models:
                summaries.append({'model': m, **summarize_cells([rows[m, s, a] for s, a in sorted(coords)])})
        group = {'name': name, 'models': models, 'coordinates': [list(c) for c in sorted(coords)],
                 'coordinate_count': len(coords), 'summaries': summaries}
        if name == 'original_five_shared_successes' and coords:
            supplement_rows = [rows.get((SUPPLEMENT, s, a)) for s, a in sorted(coords)]
            if all(supplement_rows):
                group['supplement'] = {'model': SUPPLEMENT, **summarize_cells(supplement_rows)}
        groups.append(group)
    pairs = []
    for model, section in sorted({(m, s) for m, s, _ in rows}):
        direct, sequential = rows.get((model, section, 'direct')), rows.get((model, section, 'sequential'))
        if direct is None or sequential is None:
            continue
        pair = {'model': model, 'section': section}
        for label, (hit, total) in FINITE.items():
            d, s = direct['finite_quality'], sequential['finite_quality']
            require(d[total] == s[total] > 0, 'Pair reference denominators differ or are empty')
            pair[label + '_ratio_delta'] = (s[hit] - d[hit]) / s[total]
            pair[label + '_direct_hits'] = d[hit]
            pair[label + '_sequential_hits'] = s[hit]
            pair[label + '_total'] = s[total]
        pairs.append(pair)
    paired_models = [{'model': m, 'pairs': len(ps),
                      **{label + '_macro_delta': mean(p[label + '_ratio_delta'] for p in ps)
                         for label in FINITE},
                      **{label + '_pooled': {
                          'direct_hits': sum(p[label + '_direct_hits'] for p in ps),
                          'sequential_hits': sum(p[label + '_sequential_hits'] for p in ps),
                          'total': sum(p[label + '_total'] for p in ps),
                          'ratio_delta': sum(p[label + '_sequential_hits'] - p[label + '_direct_hits']
                                             for p in ps) / sum(p[label + '_total'] for p in ps)}
                         for label in FINITE}}
                     for m in sorted({p['model'] for p in pairs})
                     if (ps := [p for p in pairs if p['model'] == m])]
    return {'schema_version': 'multimodel-matched-analysis.v1', 'groups': groups, 'pairs': pairs,
            'paired_models': paired_models, 'availability': matrix['model_summaries'],
            'caveat': 'Post-hoc descriptive availability matching, not pre-registered ranking. '
                      'Common-success cells have survivor bias. AI reviews are not expert Gold; '
                      'uncertainty bounds are not confidence intervals. No significance tests.'}


def percent(value):
    return '-' if value is None else f'{value * 100:.1f}%'


def render(result):
    lines = ['# 多模型同题与单元等权分析', '', result['caveat'], '',
             '同题表百分比对单张图取比例后等权平均；配对表同时列等权差与命中合计差。均不是全书质量。', '']
    lines += ['## 完成情况', '', '|模型|补齐前快照|累计完成|', '|---|---:|---:|']
    for row in result['availability']:
        lines.append(f"|{row['model']}|{row['first_completed']}/{row['attempted_cells']}|"
                     f"{row['eventual_completed']}/{row['attempted_cells']}|")
    for group in result['groups']:
        lines += [f"## {group['name']}", '',
                  f"共同单元：{group['coordinates']}；共{group['coordinate_count']}项。", '',
                  '|模型|类型命中|类型等权召回|关系命中|关系等权召回|准入等权下界|条件等权下界|',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for row in group['summaries'] + ([group['supplement']] if 'supplement' in group else []):
            t, e = row['typed'], row['edge']
            label = row['model'] + ('（补充）' if row['model'] == SUPPLEMENT else '')
            lines.append(f"|{label}|{t['hits']}/{t['total']}|{percent(t['macro_ratio'])}|"
                         f"{e['hits']}/{e['total']}|{percent(e['macro_ratio'])}|"
                         f"{percent(row['node_admission']['macro_lower'])}|"
                         f"{percent(row['condition_completeness']['macro_lower'])}|")
    lines += ['', '## 同模型配对', '', '差值为顺序减直抽；每个模型仅包含自身完整小节对，不跨模型排名。', '',
              '|模型|完整小节对|类型等权差|关系等权差|关系直抽→顺序命中|关系合计差|',
              '|---|---:|---:|---:|---:|---:|']
    for row in result['paired_models']:
        p = row['edge_pooled']
        lines.append(f"|{row['model']}|{row['pairs']}|{row['typed_macro_delta']*100:+.1f} pp|"
                     f"{row['edge_macro_delta']*100:+.1f} pp|{p['direct_hits']}/{p['total']}→"
                     f"{p['sequential_hits']}/{p['total']}|{p['ratio_delta']*100:+.1f} pp|")
    lines += ['', '|模型|小节|类型顺序减直抽|关系顺序减直抽|', '|---|---|---:|---:|']
    for row in result['pairs']:
        lines.append(f"|{row['model']}|{row['section']}|{row['typed_ratio_delta']*100:+.1f} pp|"
                     f"{row['edge_ratio_delta']*100:+.1f} pp|")
    lines += ['', '失败不作为语义零分；完整运行矩阵仍必须与此表一起展示。上下界及全部分轴分母见同名JSON。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--quality', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    matrix, quality = revalidate(args.matrix, args.quality)
    result = analyze(matrix, quality)
    protocol = Path(__file__).resolve().parents[1] / 'docs/multimodel_matched_analysis_protocol_2026-10-05.md'
    result['input_bindings'] = [{'path': str(p.resolve()), 'sha256': digest(p)}
                                for p in (args.matrix, args.quality, Path(__file__), protocol)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    args.output.with_suffix('.md').write_text(render(result), encoding='utf-8', newline='\n')
    print({'common_cells': result['groups'][0]['coordinate_count'], 'complete_pairs': len(result['pairs'])})
