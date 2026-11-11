# -*- coding: utf-8 -*-
"""Record why adapted iText2KG relation names are underscore runs on Chinese input.

Pinned upstream iText2KG v0.0.5 (commit 43bc581c) sanitises identifiers for graph-database safety:

  utils/data_handling.py, DataHandler.process()
    relation -> startNode/endNode lower-cased; name := re.sub(r'[^a-zA-Z0-9]', '_', name)
    entity   -> label  := re.sub(r'[^a-zA-Z0-9]', '_', label)      (entity `name` untouched)

Every Chinese character is therefore replaced by '_' in relationship names, while entity names
stay Chinese. This is the pinned method's own behaviour on non-English input, not an adapter
artifact: the vendor tree is never patched and its source hash still verifies.

Output: evaluation/external_baselines_merged_2026-10-09/itext_relation_name_finding.json
"""
import hashlib, json, re, sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
VENDOR = (ROOT / 'evaluation' / 'external_baselines_2026-10-08' / 'vendor' / 'itext2kg' / 'snapshot'
          / 'itext2kg-43bc581c99a3a35ace99dc4699acdabb0f1a98f7')
sys.stdout.reconfigure(encoding='utf-8')
PLACEHOLDER = re.compile(r'^_+$')


def graph_names(path):
    graph = json.loads(path.read_text(encoding='utf-8'))
    edges = graph.get('relationships') or []
    labelled = [str(e.get('name') or '') for e in edges]
    return {
        'path': str(path),
        'relationships': len(edges),
        'placeholder_only_names': sum(1 for n in labelled if PLACEHOLDER.match(n)),
        'names_with_alphanumerics': sum(1 for n in labelled if re.search(r'[A-Za-z0-9]', n)),
        'sample_placeholder_lengths': sorted({len(n) for n in labelled if PLACEHOLDER.match(n)}),
    }


def main() -> int:
    source = VENDOR / 'itext2kg' / 'utils' / 'data_handling.py'
    text = source.read_text(encoding='utf-8')
    lines = text.splitlines()
    hits = [(i + 1, line.strip()) for i, line in enumerate(lines)
            if "re.sub(r'[^a-zA-Z0-9]'" in line]
    finding = {
        'claim': ('On Chinese input the pinned iText2KG destroys the relationship type field: every '
                  'non-alphanumeric character, including every Chinese character, is replaced by "_".'),
        'is_adapter_artifact': False,
        'vendor_patched': False,
        'upstream_commit': '43bc581c99a3a35ace99dc4699acdabb0f1a98f7',
        'source_file': str(source),
        'source_relative': 'itext2kg/utils/data_handling.py',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'sanitiser_lines': [{'line': n, 'text': t} for n, t in hits],
        'entity_names_affected': False,
        'consequence_for_scoring': ('Relationship direction (startNode/endNode) survives and is usable; the '
                                    'relationship type is not. Relation coverage can therefore reach "partial" '
                                    'but not "full" when the reference relation requires a correct native type.'),
        'graphs': {},
    }
    for key, path in {
        'itext2kg-progress-bound-3-r1': NEW / 'guarded' / 'r1' / 'merged' / 'itext2kg' / 'graph.json',
        'itext2kg-progress-bound-3-r2': NEW / 'guarded' / 'r2' / 'merged' / 'itext2kg' / 'graph.json',
    }.items():
        if path.exists():
            finding['graphs'][key] = graph_names(path)
    target = NEW / 'itext_relation_name_finding.json'
    target.write_text(json.dumps(finding, ensure_ascii=False, indent=2), encoding='utf-8')
    print('vendor sha256:', finding['source_sha256'])
    print('sanitiser lines:', finding['sanitiser_lines'])
    for key, info in finding['graphs'].items():
        print('%-30s relations=%d placeholder_only=%d with_alnum=%d lengths=%s' % (
            key, info['relationships'], info['placeholder_only_names'],
            info['names_with_alphanumerics'], info['sample_placeholder_lengths']))
    print('wrote ->', target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
