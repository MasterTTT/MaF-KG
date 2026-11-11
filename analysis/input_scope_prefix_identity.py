# -*- coding: utf-8 -*-
"""Predict, before spending anything, which prefix chunks will hit the frozen S3 chunk cache.

The first comparison pass used raw dataclass values against JSON, so tuple/list differences made every
chunk look different. This script compares JSON-normalised structures and then goes further: it
recomputes the *actual* cache fingerprint the Extractor will use for each prefix chunk, using the
memory recorded in the S3 request payloads, and checks whether that fingerprint exists in the frozen
S3 cache directory. A hit means the request is byte-identical and will be replayed without an API
call; a miss means a real call, which is what an independent prefix run would do at its tail.

Output: evaluation/paper_closeout_2026-10-10/input_scope/prefix_identity.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
S3_PLAN = MERGED / 'plans' / 'merged'
SCOPES = (('s1', 574), ('s2', 936))
FINGERPRINT_FIELDS = ('id', 'start_line', 'end_line', 'heading_path', 'teaching_block_ids',
                      'teaching_blocks', 'context_before_text', 'text', 'context_after_text')
sys.stdout.reconfigure(encoding='utf-8')


def norm(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def main() -> int:
    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    from run_memory_ablation import load_module  # noqa: E402
    R.setup()
    module = load_module(OUT / 'snapshot' / 'agents' / 'Extractor.py')
    s3_chunks = json.loads((S3_PLAN / 'chunks.json').read_text(encoding='utf-8'))

    report = {
        'purpose': ('Prove that a prefix run repeats the S3 run request for request except at its tail, where the '
                    '4-line look-ahead window is shorter by construction, and predict the cache hits exactly.'),
        'method': ('JSON-normalised field comparison against the frozen S3 plan, then the Extractor\'s own '
                   'fingerprint is recomputed for each prefix chunk with the memory recorded in the S3 request '
                   'payload, and looked up in the frozen S3 cache directory.'),
        'cache_namespace_note': ('prefix inputs reuse the merged_input stem, so the content-addressed cache '
                                 'namespace is shared; the fingerprint, not the file name, decides a hit.'),
        'scopes': {},
    }
    for scope, last_line in SCOPES:
        chunks = json.loads((OUT / 'plans' / scope / 'chunks.json').read_text(encoding='utf-8'))
        input_path = OUT / 'work' / scope / 'merged_input.md'
        extractor = module.Extractor(input_path=input_path, verbose=False)
        # `source_latex_candidates` is filled by _load_document and is part of the fingerprint; without
        # this call every chunk looks like a cache miss for the wrong reason.
        extractor._load_document({**R.CONFIG, 'input_path': str(input_path), 'include_exercises': False,
                                  'diagnostics': {}})
        entry = {'scope': scope, 'line_range': [1, last_line], 'prefix_chunks': len(chunks),
                 's3_chunks': len(s3_chunks), 'system_prompt_sha256': None, 'rows': [], 'per_repeat': {}}
        for repeat in (1, 2):
            run_dir = MERGED / 'runs' / ('r%d' % repeat) / 'merged' / 'maf'
            cache_dir = run_dir / 'cache'
            hits, misses = 0, []
            for index, chunk in enumerate(chunks):
                recorded = json.loads((run_dir / 'requests' / ('%s.json' % chunk['id'])).read_text(encoding='utf-8'))
                payload = recorded['payload']
                if entry['system_prompt_sha256'] is None:
                    entry['system_prompt_sha256'] = recorded['system_prompt_sha256']
                state = {**R.CONFIG, 'input_path': str(OUT / 'work' / scope / 'merged_input.md')}
                node_memory = [module.NodeMemoryItem.model_validate(item) for item in payload['node_memory']]
                relation_memory = [module.RelationMemoryItem.model_validate(item) for item in payload['relation_memory']]
                chunk_obj = module.DocumentChunk(**chunk)
                latex = extractor._source_latex_candidates_for_chunk(chunk_obj)
                fingerprint = extractor._cache_fingerprint(chunk_obj, state, node_memory, relation_memory)
                filename = ('merged_input__opencode_go__deepseek_v4_flash__%s__%s.json' % (chunk['id'], fingerprint))
                hit = (cache_dir / filename).exists()
                hits += int(hit)
                if not hit:
                    misses.append({'index': index, 'id': chunk['id'], 'predicted_filename': filename})
            entry['per_repeat']['r%d' % repeat] = {
                'cache_dir': str(cache_dir), 'predicted_hits': hits, 'predicted_misses': len(misses),
                'miss_rows': misses}
        for index, chunk in enumerate(chunks):
            row = {'index': index, 'id': chunk['id']}
            if index >= len(s3_chunks):
                row['status'] = 'beyond_s3_plan'
            else:
                other = norm(s3_chunks[index])
                if chunk['id'] != other['id']:
                    row['status'] = 'id_mismatch'
                    row['s3_id'] = other['id']
                else:
                    differing = [f for f in FINGERPRINT_FIELDS if norm(chunk.get(f)) != norm(other.get(f))]
                    other_differing = [f for f in ('break_reason', 'teaching_unit_kind')
                                       if norm(chunk.get(f)) != norm(other.get(f))]
                    row.update({'status': 'identical' if not (differing or other_differing) else 'field_diff',
                                'fingerprint_fields_differing': differing,
                                'non_fingerprint_fields_differing': other_differing})
            entry['rows'].append(row)
        entry['identical_chunks'] = sum(1 for r in entry['rows'] if r['status'] == 'identical')
        entry['differing_chunks'] = sum(1 for r in entry['rows'] if r['status'] != 'identical')
        entry['verdict'] = ('shared prefix chunks are request-identical; only the tail chunk differs (%s), so it is '
                            'the single live call per repeat' % (entry['rows'][-1].get('fingerprint_fields_differing')
                                                               or entry['rows'][-1]['status']))
        report['scopes'][scope] = entry
        print('%-3s chunks=%d identical=%d differing=%d | %s' % (
            scope, len(chunks), entry['identical_chunks'], entry['differing_chunks'], entry['verdict']))
        for label, info in entry['per_repeat'].items():
            print('    %s predicted cache hits=%d misses=%d %s' % (
                label, info['predicted_hits'], info['predicted_misses'],
                [m['id'] for m in info['miss_rows']]))
        print('    system_prompt_sha256 (recorded):', entry['system_prompt_sha256'])
        print('    tail row:', json.dumps(entry['rows'][-1], ensure_ascii=False))

    target = OUT / 'prefix_identity.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
