# -*- coding: utf-8 -*-
"""P1-A: build the controlled input-scope experiment (S1 / S2 / S3) without any API call.

Question: with the same material, extractor and evaluation protocol, does the early content stay
fully expressed when the context is extended? S3 (all 1 094 lines) already exists; this script builds
the two prefixes:

    S1 = merged lines 1..574   (ch2_s5 alone)
    S2 = merged lines 1..936   (ch2_s5 + ch3_s1)
    S3 = merged lines 1..1094  (already run, reused as the frozen reference point)

Design notes that matter for honesty:

* The prefixes are byte-exact prefixes of the frozen merged document (same CRLF, no re-flowing).
* Chunking is done with the same frozen Extractor; the resulting plan is compared field by field
  against the S3 plan, and that comparison is written to `prefix_identity.json` rather than assumed.
* The MaF arm replays the shared prefix from the S3 run's own content-addressed chunk cache: the cache
  fingerprint covers chunk text, memory, LaTeX candidates and model configuration, so a hit *proves*
  the request is identical. Only a chunk whose fingerprint is absent (expected: the prefix tail,
  whose 4-line look-ahead window is shorter) makes a real call, exactly as an independent prefix run
  would. `resume_cache` in the manifest turns this on; the replay directory is per job and per repeat.
* Reference subsets are filtered from the frozen reference by merged line range *before* any prefix
  prediction exists, so the evaluation universe cannot be chosen after seeing results.
* Budgets: fresh per-method ledgers for this experiment root, each at the approved gate
  (0.15 USD / 200 000 tokens); the merged experiment's ledgers are untouched.

Usage: python analysis/input_scope_prepare.py
"""
import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
SCOPES = (('s1', 574), ('s2', 936))
S3_PLAN = MERGED / 'plans' / 'merged'
REFERENCE = MERGED / 'reference' / 'reference_merged_v1.json'
USD_LIMIT = 0.15
TOKEN_LIMIT = 200_000
sys.stdout.reconfigure(encoding='utf-8')

CHUNK_FIELDS = ('start_line', 'end_line', 'heading_path', 'text', 'context_before_text',
                'context_after_text', 'teaching_block_ids', 'teaching_blocks', 'break_reason',
                'teaching_unit_kind', 'anchor_block_ids')


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def clone_snapshot(target: Path, source: Path):
    target.mkdir(parents=True)
    for name in ('agents', 'prompts'):
        shutil.copytree(source / name, target / name)
    (target / 'scripts').mkdir()
    for src in sorted((source / 'scripts').glob('*.py')):
        shutil.copy2(src, target / 'scripts' / src.name)
    for name in ('domain.json', 'embedding_lock.json', 'requirements.lock.txt'):
        if (source / name).exists():
            shutil.copy2(source / name, target / name)
    for name in ('vendor', 'runtime'):
        origin = source / name
        if origin.exists():
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(target / name), str(origin)],
                           check=False, capture_output=True, text=True)


def patch(path: Path, replacements):
    text = path.read_text(encoding='utf-8')
    for anchor, replacement in replacements:
        if text.count(anchor) != 1:
            raise SystemExit('anchor not unique in %s: %r (%d)' % (path.name, anchor[:70], text.count(anchor)))
        text = text.replace(anchor, replacement)
    path.write_text(text, encoding='utf-8')
    print('patched ->', path.relative_to(OUT))


RESUME_OLD = '        "output_path": directory / "graph.json", "resume": False, "verbose": True,'
RESUME_NEW = ('        "output_path": directory / "graph.json",\n'
              '        "resume": bool(manifest.get("resume_cache", False)), "verbose": True,')

CACHE_OLD = '    module.Extractor._cache_dir = lambda self: directory / "cache"'
CACHE_NEW = '''    module.Extractor._cache_dir = lambda self: directory / "cache"
    replay_cache = job.get("replay_cache")
    if replay_cache:
        # Read-only replay of a frozen run's content-addressed chunk cache: the fingerprint covers
        # chunk text, memory, LaTeX candidates and model configuration, so a hit proves the request
        # is byte-identical. Writes still land in this run's own cache directory.
        original_cache_path = module.Extractor._cache_path

        def cache_path(self, chunk_id, fingerprint, state):
            run_path = original_cache_path(self, chunk_id, fingerprint, state)
            frozen = Path(replay_cache) / run_path.name
            return frozen if frozen.exists() else run_path

        module.Extractor._cache_path = cache_path'''


def build_prefix_files():
    source = MERGED / 'input' / 'merged_ch2s5_ch3s1_ch3s2.md'
    with source.open('r', encoding='utf-8', newline='') as handle:
        lines = handle.read().splitlines(keepends=True)
    if len(lines) != 1094:
        raise SystemExit('unexpected merged line count: %d' % len(lines))
    written = {}
    for scope, last_line in SCOPES:
        target = OUT / 'work' / scope / 'merged_input.md'   # same stem: shares the chunk-cache namespace
        target.parent.mkdir(parents=True, exist_ok=True)
        text = ''.join(lines[:last_line])
        target.write_text(text, encoding='utf-8', newline='')
        written[scope] = {'path': str(target), 'lines': last_line, 'chars': len(text)}
        print('%-3s lines 1..%d -> %s (%d chars)' % (scope, last_line, target.relative_to(OUT), len(text)))
    return written


def chunk_prefix(scope, module, path: Path):
    extractor = module.Extractor(input_path=path, verbose=False)
    state = extractor._chunk_document(extractor._load_document(
        {**COMMON, 'input_path': str(path), 'include_exercises': False, 'diagnostics': {}}))
    chunks = [asdict(c) for c in state['chunks']]
    once = baseline.direct_chunk(module, chunks, state['raw_lines'])
    plan_dir = OUT / 'plans' / scope
    plan_dir.mkdir(parents=True, exist_ok=True)
    selected = baseline.shared_evidence(chunks, state['raw_lines'])
    write_json(plan_dir / 'chunks.json', chunks)
    write_json(plan_dir / 'direct_chunk.json', asdict(once))
    write_json(plan_dir / 'templates.json', {
        'sequential': [json.loads(extractor._chunk_extraction_prompt(c, str(path), [], [])) for c in state['chunks']],
        'direct': [json.loads(extractor._chunk_extraction_prompt(once, str(path), [], []))]})
    write_json(plan_dir / 'evidence_map.json', {
        'selected_local_lines': list(selected),
        'source_chars': sum(len(b) for b in selected.values()),
        'note': 'Byte-exact prefix of the frozen merged document; line numbers are merged line numbers.'})
    print('%-3s chunks=%d | direct_chunk lines=%d | core lines=%d' % (
        scope, len(chunks), len(once.text.splitlines()), len(selected)))
    return chunks, once


def compare_with_s3(scope, chunks):
    s3 = json.loads((S3_PLAN / 'chunks.json').read_text(encoding='utf-8'))
    rows, identical, differing = [], 0, 0
    for index, chunk in enumerate(chunks):
        if index >= len(s3):
            rows.append({'index': index, 'id': chunk['id'], 'status': 'beyond_s3_plan'})
            differing += 1
            continue
        other = s3[index]
        if chunk['id'] != other['id']:
            rows.append({'index': index, 'id': chunk['id'], 's3_id': other['id'], 'status': 'id_mismatch'})
            differing += 1
            continue
        fields = [f for f in CHUNK_FIELDS if chunk.get(f) != other.get(f)]
        if fields:
            differing += 1
            rows.append({'index': index, 'id': chunk['id'], 'status': 'field_diff', 'fields': fields,
                         'context_after_prefix': chunk.get('context_after_text'),
                         'context_after_s3': other.get('context_after_text')})
        else:
            identical += 1
            rows.append({'index': index, 'id': chunk['id'], 'status': 'identical'})
    tail = rows[-1] if rows else {}
    verdict = ('shared prefix requests identical; only the tail chunk differs (%s) and therefore makes the '
               'one live call per repeat' % (tail.get('fields') or tail.get('status'))) \
        if differing == 1 and tail.get('status') == 'field_diff' else \
        'prefix comparison has %d non-identical chunks; read the row list before reusing any cache entry' % differing
    return {'scope': scope, 'prefix_chunks': len(chunks), 's3_chunks': len(s3),
            'identical_chunks': identical, 'differing_chunks': differing,
            'rows': rows, 'verdict': verdict}


def reference_subsets():
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))
    nodes = reference['nodes']
    by_id = {n['id']: n for n in nodes}
    subsets = {}
    for scope, last_line in SCOPES:
        keep = [n for n in nodes if n['merged_evidence'][1] <= last_line]
        keep_ids = {n['id'] for n in keep}
        intra = [r for r in reference['relations'] if r['start'] in keep_ids and r['end'] in keep_ids]
        cross = [r for r in reference['cross_section_relations']
                 if r['start'] in keep_ids and r['end'] in keep_ids]
        pairs = [p for p in reference['must_not_merge'] if p['pair'][0] in keep_ids and p['pair'][1] in keep_ids]
        groups = [g for g in reference['boundary_groups'] if all(m in keep_ids for m in g.get('members', []))]
        subsets[scope] = {
            'version': 'reference_%s_v1' % scope,
            'derived_from': 'reference_merged_v1.json (sha256 unchanged; nothing edited)',
            'rule': ('node kept iff merged_evidence[1] <= %d; intra relation kept iff both endpoints kept; '
                     'cross-section relation kept iff both endpoints kept; must_not_merge pair and boundary '
                     'group kept iff all members kept' % last_line),
            'line_range': [1, last_line],
            'nodes': keep, 'relations': intra, 'cross_section_relations': cross,
            'must_not_merge': pairs, 'boundary_groups': groups,
            'counts': {'nodes': len(keep), 'required': sum(1 for n in keep if n['status'] == 'M'),
                       'optional': sum(1 for n in keep if n['status'] == 'O'),
                       'core': sum(1 for n in keep if n.get('tier') == 'core'),
                       'detail': sum(1 for n in keep if n.get('tier') == 'detail'),
                       'claims': sum(1 for n in keep if n['type'] == 'Claim'),
                       'required_claims': sum(1 for n in keep if n['type'] == 'Claim' and n['status'] == 'M'),
                       'relations': len(intra), 'cross_section_relations': len(cross),
                       'must_not_merge': len(pairs), 'boundary_groups': len(groups)},
            'node_ids': sorted(keep_ids),
            'external_references': [e for e in reference['external_references']],
        }
        write_json(OUT / 'reference' / ('reference_%s.json' % scope), subsets[scope])
        print('%-3s reference: nodes=%d (M %d / O %d) | intra relations=%d | cross=%d | must_not_merge=%d'
              % (scope, len(keep), subsets[scope]['counts']['required'], subsets[scope]['counts']['optional'],
                 len(intra), len(cross), len(pairs)))
    common = dict(subsets['s1'])
    common['version'] = 'reference_common_ch2s5_v1'
    common['rule'] = ('the ch2_s5 node set only (identical to reference_s1); used for the paired '
                      'across-scope comparison, where every scope must be scored on the same items')
    common['equals_reference_s1'] = True
    write_json(OUT / 'reference' / 'reference_common_ch2s5.json', common)
    print('common early subset == reference_s1 node set (%d nodes)' % common['counts']['nodes'])
    return subsets


def main() -> int:
    if OUT.exists():
        raise SystemExit('exists: %s' % OUT)
    OUT.mkdir(parents=True)
    written = build_prefix_files()
    subsets = reference_subsets()

    clone_snapshot(OUT / 'snapshot', MERGED / 'snapshot')
    patch(OUT / 'snapshot' / 'scripts' / 'run_external_baselines.py', [(RESUME_OLD, RESUME_NEW)])
    patch(OUT / 'snapshot' / 'scripts' / 'run_direct_extraction_baseline.py', [(CACHE_OLD, CACHE_NEW)])

    global COMMON, baseline
    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline_module  # noqa: E402
    from run_memory_ablation import COMMON as COMMON_IMPORT, load_module  # noqa: E402
    baseline = baseline_module
    R.setup()
    COMMON = R.CONFIG

    module = load_module(OUT / 'snapshot' / 'agents' / 'Extractor.py')
    identity = {'purpose': ('Prove, before spending anything, that a prefix run repeats the S3 run request for '
                            'request except at its tail, where the 4-line look-ahead window is shorter by '
                            'construction.'),
                'merged_input': str(MERGED / 'input' / 'merged_ch2s5_ch3s1_ch3s2.md'),
                'scopes': {}, 'cache_namespace_note': ('prefix inputs reuse the merged_input stem so the '
                                                       'content-addressed chunk cache namespace is shared; the '
                                                       'fingerprint, not the name, decides a hit.')}
    plans = {}
    for scope, last_line in SCOPES:
        chunks, once = chunk_prefix(scope, module, Path(written[scope]['path']))
        plans[scope] = (chunks, once)
        identity['scopes'][scope] = compare_with_s3(scope, chunks)
        print('    prefix vs S3: identical=%d differing=%d | %s' % (
            identity['scopes'][scope]['identical_chunks'], identity['scopes'][scope]['differing_chunks'],
            identity['scopes'][scope]['verdict']))
    write_json(OUT / 'prefix_identity.json', identity)

    sections = [{'id': scope, 'title': 'merged lines 1..%d' % last_line,
                 'input': written[scope]['path'], 'plan_dir': str(OUT / 'plans' / scope),
                 'chunk_count': len(plans[scope][0]), 'core_line_count': None,
                 'merged_source': str(MERGED / 'input' / 'merged_ch2s5_ch3s1_ch3s2.md'),
                 'reference': str(OUT / 'reference' / ('reference_%s.json' % scope))}
                for scope, last_line in SCOPES]
    for section in sections:
        section['core_line_count'] = len(json.loads(
            (Path(section['plan_dir']) / 'evidence_map.json').read_text(encoding='utf-8'))['selected_local_lines'])

    jobs = []
    for repeat in (1, 2):
        for scope, _ in SCOPES:
            replay = MERGED / 'runs' / ('r%d' % repeat) / 'merged' / 'maf' / 'cache'
            for method in ('maf', 'direct'):
                job = {'method': method, 'section': scope, 'repeat': repeat,
                       'directory': str(OUT / 'runs' / ('r%d' % repeat) / scope / method),
                       'status': 'pending'}
                if method == 'maf':
                    job['replay_cache'] = str(replay)
                jobs.append(job)

    manifest = {
        'experiment': 'paper-closeout-input-scope-s1-s2',
        'status': 'prepared',
        'snapshot': str(OUT / 'snapshot'),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'common': R.CONFIG,
        'reference': str(REFERENCE),
        'sections': sections,
        'jobs': jobs,
        'methods': ['maf', 'direct'],
        'shared_domain_instruction': json.loads((OUT / 'snapshot' / 'domain.json').read_text(encoding='utf-8'))['instruction'],
        'embedding_lock': str(OUT / 'snapshot' / 'embedding_lock.json'),
        'per_method_budget_usd': USD_LIMIT,
        'per_method_token_limit': TOKEN_LIMIT,
        'budget_root': str(OUT / 'budgets'),
        'task_max_calls': 120,
        'task_max_seconds': 7200,
        'session': str(uuid.uuid4()),
        'resume_cache': True,
        'resume_cache_note': ('MaF jobs read the matching repeat\'s frozen S3 chunk cache; a fingerprint hit means '
                              'the request is byte-identical, so replaying it is equivalent to re-sending it. '
                              'Any miss becomes a real call, which is the correct behaviour for the prefix tail.'),
        'scope': ('Controlled input-scope diagnostic on byte-exact prefixes of the frozen merged document; '
                  'S3 is reused from the merged experiment. Not a generalisation claim and not a pure '
                  'character-length causal test: added content and task scope change together.'),
        'budget_note': ('Fresh per-method ledgers for this root at the approved P1-A gate (0.15 USD / 200 000 '
                        'tokens each); the merged experiment ledgers are untouched.'),
    }
    files = sorted((OUT / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'prompts').glob('*.txt')) + \
            [OUT / 'snapshot' / 'domain.json', OUT / 'snapshot' / 'embedding_lock.json'] + \
            sorted((OUT / 'work').glob('*/merged_input.md')) + \
            sorted((OUT / 'plans').glob('*/chunks.json')) + sorted((OUT / 'plans').glob('*/direct_chunk.json')) + \
            sorted((OUT / 'reference').glob('*.json'))
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    write_json(OUT / 'manifest.json', manifest)
    baseline.audit_prepared(manifest)
    print('AUDIT OK | jobs %d | %s' % (len(jobs), [(j['repeat'], j['section'], j['method'], j['status']) for j in jobs]))
    print('manifest ->', OUT / 'manifest.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
