# -*- coding: utf-8 -*-
"""Diagnose why predicted prefix cache hits are fewer than the identical-chunk count.

Compares, component by component, what this process would put into the cache fingerprint against what
the frozen S3 request payload recorded: chunk fields, node/relation memory, LaTeX candidates, the
system-prompt hash and the model configuration. The first component that differs explains the misses.
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'input_scope'
sys.stdout.reconfigure(encoding='utf-8')


def norm(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def main() -> int:
    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    from run_memory_ablation import load_module  # noqa: E402
    R.setup()
    module = load_module(OUT / 'snapshot' / 'agents' / 'Extractor.py')
    chunks = json.loads((OUT / 'plans' / 's1' / 'chunks.json').read_text(encoding='utf-8'))
    run_dir = MERGED / 'runs' / 'r1' / 'merged' / 'maf'
    extractor = module.Extractor(input_path=OUT / 'work' / 's1' / 'merged_input.md', verbose=False)
    import hashlib
    print('computed system prompt sha256:', hashlib.sha256(extractor.system_prompt.encode('utf-8')).hexdigest())

    for index in (0, 5, 6, 16):
        chunk = chunks[index]
        recorded = json.loads((run_dir / 'requests' / ('%s.json' % chunk['id'])).read_text(encoding='utf-8'))
        payload = recorded['payload']
        chunk_obj = module.DocumentChunk(**chunk)
        node_memory = [module.NodeMemoryItem.model_validate(i) for i in payload['node_memory']]
        relation_memory = [module.RelationMemoryItem.model_validate(i) for i in payload['relation_memory']]
        dumped_nodes = [i.model_dump(mode='json', exclude_none=True) for i in node_memory]
        dumped_relations = [i.model_dump(mode='json', exclude_none=True) for i in relation_memory]
        latex = extractor._source_latex_candidates_for_chunk(chunk_obj)
        print('--- %s (index %d) ---' % (chunk['id'], index))
        print('  recorded system_prompt_sha256 :', recorded['system_prompt_sha256'])
        print('  chunk fields equal            :', norm(chunk) == norm(payload['chunk']))
        if norm(chunk) != norm(payload['chunk']):
            for key in sorted(set(chunk) | set(payload['chunk'])):
                if norm(chunk.get(key)) != norm(payload['chunk'].get(key)):
                    print('      differs:', key)
        print('  node_memory count             : recorded=%d dumped=%d' % (len(payload['node_memory']), len(dumped_nodes)))
        print('  node_memory equal             :', norm(dumped_nodes) == norm(payload['node_memory']))
        if norm(dumped_nodes) != norm(payload['node_memory']):
            for a, b in zip(norm(dumped_nodes), norm(payload['node_memory'])):
                if a != b:
                    print('      first differing item:')
                    print('        dumped  :', json.dumps(a, ensure_ascii=False)[:300])
                    print('        recorded:', json.dumps(b, ensure_ascii=False)[:300])
                    break
        print('  relation_memory equal         :', norm(dumped_relations) == norm(payload['relation_memory']))
        print('  latex candidates equal        :', norm(latex) == norm(payload['source_latex_candidates']))
        if norm(latex) != norm(payload['source_latex_candidates']):
            print('        computed:', json.dumps(norm(latex), ensure_ascii=False)[:300])
            print('        recorded:', json.dumps(norm(payload['source_latex_candidates']), ensure_ascii=False)[:300])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
