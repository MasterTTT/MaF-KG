# -*- coding: utf-8 -*-
"""Prepare `itext_v4`: a symbol-native iText2KG variant that delivers the model's own names.

Why this variant exists
-----------------------
`itext_v3` bypassed the pinned ASCII sanitiser for *relation* names and endpoints only. The pinned
`add_embeddings_as_property_batch` (utils/data_handling.py L30-36) still replaces every delivered
item name with `lower().replace("_", " ").replace("-", " ")`, and `remove_duplicates` (L92-95) uses
`record["name"]` as the entity identity. On this corpus that is not cosmetic:

  * `str.lower()` maps the Greek capital Delta to delta, so the increment Δy and the error limit δy
    collapse onto one symbol in a section whose whole subject is the difference between them;
  * `-` becomes a space, so a minus sign disappears (`|A - a|` -> `|a   a|`);
  * `_` becomes a space, so subscripts disappear (`δ_A` -> `δ a`).

Measured on the symbol-compatible arm: 5/26 (r1) and 3/23 (r2) delivered entity names equal the
pinned normalisation rather than the model string
(`evaluation/external_baselines_merged_2026-10-09/itext_entity_name_audit.json`).

Design
------
Deliver the model's name verbatim, keep the pinned normalisation as `retrieval_key`, compute
embeddings from that key (pair-selection geometry unchanged) and let dedup identity prefer the key
(so the pinned case/underscore/hyphen-insensitive identity semantics survive). Relation predicates
and endpoints stay verbatim as in v3. The vendor tree is not touched; the patch is applied in memory.

Because the delivered names now differ, relation prompts differ, so this variant cannot reuse v3
responses: both repeats are fresh runs on the shared cumulative iText ledger (never zeroed).

Budget: the ledger and the manifest are set to the approved incremental ceiling
(cumulative USD 1.58 / 2 150 000 tokens = v3 spend plus the approved 0.40 USD / 0.6 M token gate).

Usage: python analysis/merged_prepare_v4.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
SRC = NEW / 'itext_v3'
OUT = NEW / 'itext_v4'
LEDGER_PATH = NEW / 'budgets' / 'itext2kg.json'
USD_LIMIT = 1.58
TOKEN_LIMIT = 2_150_000
sys.stdout.reconfigure(encoding='utf-8')


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
    print('patched ->', path.relative_to(NEW))


NAME_HANDLER = '''def _name_preserving_data_handler(original):
    """Deliver the model's own item names; keep the pinned normalisation as a retrieval key.

    Pinned add_embeddings_as_property_batch (utils/data_handling.py L30-36) stores
    lower().replace("_", " ").replace("-", " ") as the item name, and remove_duplicates (L92-95) uses
    record["name"] as the entity identity. Here the delivered name stays verbatim, the pinned
    normalisation is kept as "retrieval_key", embeddings are computed from that key so pair-selection
    geometry is unchanged, and dedup identity prefers the key so the pinned case/underscore/hyphen
    insensitive identity semantics are preserved.
    """

    def _retrieval_key(value):
        return str(value).lower().replace("_", " ").replace("-", " ")

    def add_embeddings_as_property_batch(self, embeddings_function, items, property_name="properties",
                                         embeddings_name="embeddings", item_name_key="name",
                                         embeddings=True):
        prepared = [{**item, item_name_key: item[item_name_key],
                     "retrieval_key": _retrieval_key(item[item_name_key]),
                     property_name: {}} for item in items]
        if embeddings:
            vectors = embeddings_function([item["retrieval_key"] for item in prepared])
            prepared = [{**item, property_name: {embeddings_name: vector}}
                        for item, vector in zip(prepared, vectors)]
        return prepared

    def remove_duplicates(self, records, data_type):
        seen, unique = set(), []
        for record in records:
            name = record.get("retrieval_key") or _retrieval_key(record.get("name", ""))
            if data_type == "relation":
                identifier = (record.get("startNode"), record.get("endNode"), name)
            else:
                identifier = name
            if identifier not in seen:
                seen.add(identifier)
                unique.append(record)
        return unique

    return type("NamePreservingDataHandler", (original,), {
        "add_embeddings_as_property_batch": add_embeddings_as_property_batch,
        "remove_duplicates": remove_duplicates,
    })


'''

LOAD_OLD = "def _load_pinned_iText2KG(source_root: Path, package_root: Path, preserve_symbols: bool = False):"
LOAD_NEW = NAME_HANDLER + \
    "def _load_pinned_iText2KG(source_root: Path, package_root: Path, preserve_symbols: bool = False,\n" \
    "                         preserve_names: bool = False):"

ASSIGN_OLD = """    if preserve_symbols:
        data_handling.DataHandler = _symbol_preserving_data_handler(data_handling.DataHandler)
    utils.DataHandler = data_handling.DataHandler"""
ASSIGN_NEW = """    if preserve_names:
        data_handling.DataHandler = _name_preserving_data_handler(data_handling.DataHandler)
    if preserve_symbols:
        data_handling.DataHandler = _symbol_preserving_data_handler(data_handling.DataHandler)
    utils.DataHandler = data_handling.DataHandler"""

SIG_OLD = """    no_progress_limit: int | None = None,
    preserve_symbols: bool = False,
) -> dict:"""
SIG_NEW = """    no_progress_limit: int | None = None,
    preserve_symbols: bool = False,
    preserve_names: bool = False,
) -> dict:"""

MANIFEST_OLD = """        "preserve_symbols": bool(preserve_symbols),
        "symbol_policy": ("relation names and endpoints kept verbatim; pinned ASCII sanitiser bypassed "
                          "in memory" if preserve_symbols else "pinned upstream ASCII sanitiser active"),"""
MANIFEST_NEW = """        "preserve_symbols": bool(preserve_symbols),
        "symbol_policy": ("relation names and endpoints kept verbatim; pinned ASCII sanitiser bypassed "
                          "in memory" if preserve_symbols else "pinned upstream ASCII sanitiser active"),
        "preserve_names": bool(preserve_names),
        "name_policy": ("delivered entity/relation names verbatim; pinned lower()/separator "
                        "normalisation kept as retrieval_key and used for embeddings and dedup "
                        "identity" if preserve_names else "pinned normalisation is the delivered name"),"""

CLASS_OLD = "        graph_class = _load_pinned_iText2KG(source_root, package_root, preserve_symbols=preserve_symbols)"
CLASS_NEW = ("        graph_class = _load_pinned_iText2KG(source_root, package_root, preserve_symbols=preserve_symbols,\n"
             "                                              preserve_names=preserve_names)")

RUNNER_OLD = """            result = run(vendor, texts, client, embed, directory,
                         no_progress_limit=manifest.get("itext_no_progress_limit"),
                         preserve_symbols=bool(manifest.get("itext_preserve_symbols")))"""
RUNNER_NEW = """            result = run(vendor, texts, client, embed, directory,
                         max_chat_calls=int(manifest.get("task_max_calls", 120)),
                         no_progress_limit=manifest.get("itext_no_progress_limit"),
                         preserve_symbols=bool(manifest.get("itext_preserve_symbols")),
                         preserve_names=bool(manifest.get("itext_preserve_names")))"""


def raise_ledger():
    ledger = json.loads(LEDGER_PATH.read_text(encoding='utf-8'))
    unsettled = [r for r in ledger['requests'] if r.get('status') == 'reserved']
    if unsettled:
        raise SystemExit('ledger has %d unsettled reservations; audit before changing limits' % len(unsettled))
    before = (ledger['usd_limit'], ledger['token_limit'], ledger['accounting_usd'], ledger['accounting_tokens'])
    ledger['usd_limit'], ledger['token_limit'] = USD_LIMIT, TOKEN_LIMIT
    ledger['limit_note'] = ('Raised on 2026-10-10 to the approved incremental gate for itext_v4: cumulative '
                            'ceiling = v3 spend + approved 0.40 USD / 0.6 M token run gate. Never zeroed.')
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ledger limits %s -> %s | spent %.4f usd / %d tokens | headroom %.4f usd / %d tokens'
          % (before[:2], (USD_LIMIT, TOKEN_LIMIT), ledger['accounting_usd'], ledger['accounting_tokens'],
             USD_LIMIT - ledger['accounting_usd'], TOKEN_LIMIT - ledger['accounting_tokens']))


def main() -> int:
    if OUT.exists():
        raise SystemExit('exists: %s' % OUT)
    clone_snapshot(OUT / 'snapshot', SRC / 'snapshot')
    patch(OUT / 'snapshot' / 'scripts' / 'external_itext2kg.py', [
        (LOAD_OLD, LOAD_NEW),
        (ASSIGN_OLD, ASSIGN_NEW),
        (SIG_OLD, SIG_NEW),
        (MANIFEST_OLD, MANIFEST_NEW),
        (CLASS_OLD, CLASS_NEW),
    ])
    patch(OUT / 'snapshot' / 'scripts' / 'run_external_baselines.py', [(RUNNER_OLD, RUNNER_NEW)])
    # The ledger is raised only after every patch applied, so a patch failure cannot leave the
    # ceiling raised without a variant to spend it on.
    raise_ledger()

    base = json.loads((SRC / 'manifest.json').read_text(encoding='utf-8'))
    manifest = dict(base)
    manifest.update({
        'experiment': 'external-l1-native-method-merged-three-sections--itext-symbol-native',
        'status': 'prepared',
        'snapshot': str(OUT / 'snapshot'),
        'jobs': [{'method': 'itext2kg', 'section': 'merged', 'repeat': repeat,
                  'directory': str(OUT / ('r%d' % repeat) / 'merged' / 'itext2kg'), 'status': 'pending'}
                 for repeat in (1, 2)],
        'per_method_budget_usd': USD_LIMIT,
        'per_method_token_limit': TOKEN_LIMIT,
        'task_max_calls': 120,
        'itext_no_progress_limit': 3,
        'itext_preserve_symbols': True,
        'itext_preserve_names': True,
        'scope': ('Merged three-section document, two repeats, iText2KG with progress bounding, verbatim '
                  'relation names/endpoints and verbatim delivered item names. Separate label; shared '
                  'cumulative iText ledger; v2 and v3 outputs preserved as evidence and never pooled.'),
        'name_fix_authorization': ('User approved on 2026-10-10: the pinned normalisation is visible in the '
                                   'delivered graph (5/26 r1, 3/23 r2 entity names) and destroys the '
                                   'increment/error-limit distinction through str.lower() on Greek Delta; '
                                   'names are delivered verbatim with the normalisation kept as a separate '
                                   'retrieval key, and the vendor tree stays byte-identical.'),
        'call_ceiling_note': ('The runner now forwards task_max_calls to the adapter ceiling; the value is '
                              'unchanged at 120, so the effective ceiling equals the v3 arm.'),
        'budget_alignment_note': ('Ledger and manifest limits are set together to the approved incremental '
                                  'gate; prospective_native_client.Budget validates the (model, usd, token) triple.'),
    })
    for key in ('guard_authorization', 'parse_fix_authorization', 'symbol_fix_authorization',
                'task_ceiling_note', 'budget_alignment_note_previous'):
        manifest.pop(key, None)

    sys.path.insert(0, str(OUT / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    files = sorted((OUT / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((OUT / 'snapshot' / 'prompts').glob('*.txt')) + \
            [OUT / 'snapshot' / 'domain.json', OUT / 'snapshot' / 'embedding_lock.json'] + \
            sorted((NEW / 'input').glob('*.md')) + \
            [NEW / 'input' / 'provenance.json', NEW / 'input' / 'sections.json', NEW / 'work' / 'merged_input.md'] + \
            sorted((NEW / 'plans' / 'merged').glob('*.json'))
    manifest['common'] = R.CONFIG
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    R.setup()
    baseline.audit_prepared(manifest)
    print('%-9s AUDIT OK | jobs %s | flags %s' % (
        OUT.name, [(j['repeat'], j['status']) for j in manifest['jobs']],
        {k: manifest[k] for k in ('itext_preserve_symbols', 'itext_preserve_names',
                                  'itext_no_progress_limit', 'task_max_calls',
                                  'per_method_budget_usd', 'per_method_token_limit')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
