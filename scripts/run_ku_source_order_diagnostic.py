"""Isolated source-order diagnostic with fixed L1 semantics, not a new L1 run."""
import argparse
import copy
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_ku_prerequisite_phase import audit, load, read, sha, write
from run_ku_multilevel_heading import NUMBERED, execute, multilevel_objectives
from run_ku_boundary_challenge import score


def segments(start, end, mapping):
    values = sorted(mapping[i] for i in range(start, end + 1))
    groups = []
    for value in values:
        if groups and value == groups[-1][1] + 1:
            groups[-1][1] = value
        else:
            groups.append([value, value])
    return groups


def paths_for(lines):
    paths, current = {}, []
    for i, line in enumerate(lines, 1):
        title = re.sub(r'^#+\s*', '', line.strip())
        if line.startswith('#'):
            if title.startswith('第一节'):
                current = [title]
            elif re.match(r'^[一二三四五六七八九十]+、', title):
                current = current[:1] + [title]
            elif NUMBERED.match(title):
                current = current[:2] + [title]
        paths[i] = list(current)
    return paths


def split_headings(start, end, paths):
    groups = []
    for i in range(start, end + 1):
        if groups and paths[i] == paths[groups[-1][0]]:
            groups[-1][1] = i
        else:
            groups.append([i, i])
    return groups


def prepare(base, preview, out):
    original = read(base / 'manifest.json')
    audit(original)
    mapping_data = read(preview / 'line_mapping.json')
    if sha(mapping_data['source']) != mapping_data['source_sha256']:
        raise ValueError('Original source changed')
    if sha(mapping_data['output']) != mapping_data['output_sha256']:
        raise ValueError('Corrected preview changed')
    mapping = {int(k): v for k, v in mapping_data['old_to_new'].items()}
    out.mkdir(parents=True, exist_ok=False)
    snap = out / 'snapshot'
    shutil.copytree(base / 'snapshot', snap)
    shutil.copy2(Path(__file__), snap / 'scripts' / Path(__file__).name)
    shutil.copy2(preview / 'line_mapping.json', out / 'line_mapping.json')
    old = next(j for j in original['jobs'] if j['id'] == 'ch1_s1')
    gp = snap / Path(old['graph']).relative_to(base / 'snapshot')
    sp = snap / Path(old['source']).relative_to(base / 'snapshot')
    if sha(sp) != mapping_data['source_sha256']:
        raise ValueError('Baseline source does not match reviewed permutation')
    sp.write_bytes(Path(mapping_data['output']).read_bytes())
    lines = sp.read_text(encoding='utf-8').splitlines()
    paths = paths_for(lines)
    graph = read(gp)
    fixed_semantics = [{k: v for k, v in n.items() if k != 'evidence'} for n in graph['nodes']]
    spans = []
    for old_span in graph['diagnostics']['chunk_spans']:
        for start, end in segments(old_span['start_line'], old_span['end_line'], mapping):
            for a, b in split_headings(start, end, paths):
                span = copy.deepcopy(old_span)
                span.update(id=f"order_{old_span['id']}_{a}_{b}", start_line=a, end_line=b,
                            heading_path=paths[a], source_order_parent=old_span['id'])
                spans.append(span)
    spans.sort(key=lambda x: x['start_line'])

    def remap(obj):
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if key == 'evidence' and isinstance(value, list):
                    updated = []
                    for ev in value:
                        if Path(ev.get('source_path', '')).name != sp.name:
                            updated.append(ev)
                            continue
                        for a, b in segments(ev['start_line'], ev['end_line'], mapping):
                            for x, y in split_headings(a, b, paths):
                                item = copy.deepcopy(ev)
                                item.update(source_path=str(sp), start_line=x, end_line=y,
                                            heading_path=['第一章 函数与极限'] + paths[x],
                                            text='\n'.join(lines[x-1:y]))
                                matches = [s for s in spans if s['start_line'] <= x <= s['end_line']]
                                if not matches:
                                    raise ValueError('Evidence outside mapped chunks')
                                item['chunk_id'] = matches[0]['id']
                                item.pop('teaching_block_id', None)
                                updated.append(item)
                    obj[key] = updated
                else:
                    remap(value)
        elif isinstance(obj, list):
            for value in obj:
                remap(value)
    remap(graph)
    assert fixed_semantics == [{k: v for k, v in n.items() if k != 'evidence'} for n in graph['nodes']]
    graph['diagnostics']['chunk_spans'] = spans
    graph['diagnostics']['input_path'] = str(sp)
    graph['diagnostics']['source_order_diagnostic'] = {
        'fixed_node_semantics': True, 'new_extraction': False,
        'method': 'reversible line permutation; old chunks split at discontinuities and numbered headings',
        'baseline_graph_sha256': sha(old['graph']), 'mapping_sha256': sha(out / 'line_mapping.json')}
    write(gp, graph)
    module = load(snap / 'scripts/build_knowledge_units.py', 'order_prepare')
    outline = module.build_outline([str(sp)])
    cards = module.build_planner_cards(old['section'], graph['nodes'], outline, {sp.name: graph})
    goals = multilevel_objectives(old['section'], cards)
    candidates = module._node_candidate_goals(old['section'], graph['nodes'], cards, goals)
    pp = out / 'plans/ch1_s1.json'
    write(pp, {'nodes': graph['nodes'], 'cards': cards, 'objectives': goals, 'member_candidates': candidates})
    reference = read(base / 'reference.json')
    reference['jobs'] = {'ch1_s1': reference['jobs']['ch1_s1']}
    for group in reference['jobs']['ch1_s1']['groups']:
        group['original_lines'] = group['lines']
        group['corrected_spans'] = segments(*group['lines'], mapping)
        group['source_excerpt'] = '\n'.join(lines[mapping[i]-1] for i in range(group['lines'][0], group['lines'][1]+1))
    write(out / 'reference.json', reference)
    bindings = [{'path': str(p), 'sha256': sha(p)} for p in snap.rglob('*') if p.is_file()]
    bindings += [{'path': str(p), 'sha256': sha(p)} for p in (pp, out/'reference.json', out/'line_mapping.json')]
    manifest = {**original, 'status': 'prepared', 'snapshot': str(snap), 'bindings': bindings,
        'jobs': [{**old, 'graph': str(gp), 'source': str(sp), 'plan': str(pp),
                  'heading_count': len(goals), 'status': 'pending'}],
        'session': str(uuid.uuid4()), 'deadline': (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
        'baseline': str(base), 'reference_sha256': sha(out/'reference.json'),
        'changed_factor': 'source order and mechanically derived evidence/Card/heading alignment',
        'scope': 'single-section source-repair diagnostic; fixed L1 semantics; not blind validation or re-extraction'}
    write(out/'manifest.json', manifest)
    print('Prepared', len(cards), 'Cards;', len(goals), 'goals;', len(graph['nodes']), 'fixed nodes')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('prepare', 'execute', 'score'))
    p.add_argument('out', type=Path)
    p.add_argument('--base', type=Path)
    p.add_argument('--preview', type=Path)
    args = p.parse_args()
    if args.stage == 'prepare':
        prepare(args.base.resolve(), args.preview.resolve(), args.out.resolve())
    else:
        globals()[args.stage](args.out.resolve())
