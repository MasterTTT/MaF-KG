# -*- coding: utf-8 -*-
"""Prepare the two fixed variants required by the 2026-10-09 user review.

edc_v3   parse-compatibility fix (fullwidth ':' accepted for the Define stage) plus replay of the
         frozen Extract/Define responses, so the never-executed alignment stage is the only live
         work and the triplet set stays identical to the failed run.
itext_v3 symbol-compatibility fix: relation names and endpoints are kept exactly as the model
         returned them (the pinned ASCII sanitiser is bypassed in memory); progress bounding stays.

Both variants are separately labelled, share their method's cumulative ledger (never zeroed), and
never patch the vendor tree. Frozen v2 outputs stay on disk as evidence.
"""
import json, shutil, subprocess, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
NEW = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OLD_SNAP = NEW / 'snapshot'


def clone_snapshot(target: Path, source: Path = None):
    source = source or OLD_SNAP
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
            raise SystemExit('anchor not unique in %s: %r (%d)' % (path.name, anchor[:60], text.count(anchor)))
        text = text.replace(anchor, replacement)
    path.write_text(text, encoding='utf-8')
    print('patched ->', path.relative_to(NEW))


# ---------------------------------------------------------------- iText2KG symbol compatibility
ITEXT_RUN_SIG_OLD = """    *,
    max_chat_calls: int = DEFAULT_MAX_CHAT_CALLS,
    embedding_model_id: str | None = None,
    no_progress_limit: int | None = None,
) -> dict:"""
ITEXT_RUN_SIG_NEW = """    *,
    max_chat_calls: int = DEFAULT_MAX_CHAT_CALLS,
    embedding_model_id: str | None = None,
    no_progress_limit: int | None = None,
    preserve_symbols: bool = False,
) -> dict:"""

ITEXT_LOAD_OLD = """def _load_pinned_iText2KG(source_root: Path, package_root: Path):"""
ITEXT_LOAD_NEW = '''def _symbol_preserving_data_handler(original):
    """Keep model-returned relation names and endpoints verbatim.

    Pinned upstream DataHandler.process lower-cases relation endpoints and rewrites the relation
    name with re.sub(r'[^a-zA-Z0-9]', '_', name) (utils/data_handling.py L67-70). On Chinese input
    that destroys every Chinese character and every '-', leaving predicates as underscore runs.
    This evaluation never imports into a graph database, so the identifier sanitisation has no
    purpose here. Only the relation branch is overridden; entity handling is the original code.
    """

    def process(self, data, data_type):
        if data_type == "relation":
            return data.copy()
        return original.process(self, data, data_type)

    return type("SymbolPreservingDataHandler", (original,), {"process": process})


def _load_pinned_iText2KG(source_root: Path, package_root: Path, preserve_symbols: bool = False):'''

ITEXT_ASSIGN_OLD = """    utils.DataHandler = data_handling.DataHandler"""
ITEXT_ASSIGN_NEW = """    if preserve_symbols:
        data_handling.DataHandler = _symbol_preserving_data_handler(data_handling.DataHandler)
    utils.DataHandler = data_handling.DataHandler"""

ITEXT_CLASS_OLD = """        graph_class = _load_pinned_iText2KG(source_root, package_root)"""
ITEXT_CLASS_NEW = """        graph_class = _load_pinned_iText2KG(source_root, package_root, preserve_symbols=preserve_symbols)"""

ITEXT_MANIFEST_OLD = """        "no_progress_limit": no_progress_limit,"""
ITEXT_MANIFEST_NEW = """        "no_progress_limit": no_progress_limit,
        "preserve_symbols": bool(preserve_symbols),
        "symbol_policy": ("relation names and endpoints kept verbatim; pinned ASCII sanitiser bypassed "
                          "in memory" if preserve_symbols else "pinned upstream ASCII sanitiser active"),"""

# ---------------------------------------------------------------- EDC parse compatibility + replay
EDC_RUN_SIG_OLD = """    *,
    model_name: str | None = None,
    embedding_model_id: str | None = None,
) -> list[list[list[str]]]:"""
EDC_RUN_SIG_NEW = """    *,
    model_name: str | None = None,
    embedding_model_id: str | None = None,
    replay_dir: str | Path | None = None,
    parse_compat: bool = False,
) -> list[list[list[str]]]:"""

EDC_PARSER_OLD = """            def audited_definition_parser(raw: str):
                parsed = native_definition_parser(raw)
                event = _definition_parse_audit(raw, parsed)
                event["request_label"] = llm_utils.current_chat_label()
                audit["parse_events"].append(event)
                return parsed"""
EDC_PARSER_NEW = '''            def audited_definition_parser(raw: str):
                effective, normalization = raw, None
                if parse_compat:
                    # Pinned upstream parse_relation_definition (edc/utils/llm_utils.py L143-145) only
                    # accepts ASCII ':' while the model answers with the fullwidth U+FF1A, so every
                    # definition line is dropped as "missing colon". Normalise for parsing only; the
                    # raw response recorded by the client stays untouched.
                    effective = raw.replace("\\uff1a", ":")
                    normalization = {"rule": "U+FF1A -> U+003A, parsing only",
                                     "replacements": raw.count("\\uff1a")}
                parsed = native_definition_parser(effective)
                if parse_compat:
                    # a line whose definition is empty (e.g. a preamble ending in ':') is not a relation
                    parsed = {name: text for name, text in parsed.items() if text.strip()}
                event = _definition_parse_audit(effective, parsed)
                event["response_normalization"] = normalization
                event["raw_response_before_normalization"] = raw if normalization else None
                event["request_label"] = llm_utils.current_chat_label()
                audit["parse_events"].append(event)
                return parsed'''

EDC_CLIENT_OLD = """            def injected_client(messages: list[dict], label: str, **kwargs) -> str:
                if len(calls) >= call_limit:"""
EDC_CLIENT_NEW = '''            replay_root = Path(replay_dir).resolve() if replay_dir else None

            def replay_lookup(label: str):
                """Frozen raw response for this label, if a replay directory was supplied.

                The frozen runs keep responses under <run>/responses/, so both the run root and that
                subdirectory are searched. (The first executed variant searched only the run root, found
                nothing, and therefore re-sampled Extract/Define live; that deviation is recorded.)
                """
                if replay_root is None or not replay_root.is_dir():
                    return None, None
                matches = []
                for base in (replay_root, replay_root / "responses"):
                    matches = sorted(base.glob("*" + label + ".json"))
                    if matches:
                        break
                if not matches:
                    return None, None
                try:
                    payload = json.loads(matches[0].read_text(encoding="utf-8"))
                    return payload["choices"][0]["message"]["content"], matches[0]
                except Exception:  # noqa: BLE001
                    return None, None

            def injected_client(messages: list[dict], label: str, **kwargs) -> str:
                replayed, replay_path = replay_lookup(label)
                if replayed is not None:
                    # Extract/Define replayed from the frozen run: no API call, no ledger entry, so
                    # the never-executed alignment stage is the only live work in this variant.
                    calls.append({
                        "label": label,
                        "messages": copy.deepcopy(messages),
                        "status": "replayed",
                        "replayed_from": str(replay_path),
                        "raw_response": replayed,
                    })
                    return replayed
                if len(calls) >= call_limit:'''

EDC_AUDIT_OLD = """        "parse_events": [],"""
EDC_AUDIT_NEW = """        "parse_events": [],
        "parse_compat": bool(parse_compat),
        "parse_compat_rule": ("accept U+FF1A as a definition separator; drop empty definitions"
                              if parse_compat else "pinned upstream parser only (ASCII ':' required)"),
        "replay_dir": str(replay_dir) if replay_dir else None,"""

# The alignment stage makes one call per extracted triplet (131 for r1), which exceeds the adapted
# task ceiling of 120 calls; the ceiling is a task guard, not the budget gate (the ledger remains
# the budget gate). Time ceiling likewise raised for ~131 sequential calls.
EDC_CAPS_OLD = """MAX_CHAT_CALLS = 120
MAX_SECONDS = 5400"""
EDC_CAPS_NEW = """MAX_CHAT_CALLS = 200
MAX_SECONDS = 14400"""


def prepare_itext():
    out = NEW / 'itext_v3'
    if out.exists():
        raise SystemExit('exists: %s' % out)
    clone_snapshot(out / 'snapshot', NEW / 'guarded' / 'snapshot')
    patch(out / 'snapshot' / 'scripts' / 'external_itext2kg.py', [
        (ITEXT_LOAD_OLD, ITEXT_LOAD_NEW),
        (ITEXT_ASSIGN_OLD, ITEXT_ASSIGN_NEW),
        (ITEXT_RUN_SIG_OLD, ITEXT_RUN_SIG_NEW),
        (ITEXT_CLASS_OLD, ITEXT_CLASS_NEW),
        (ITEXT_MANIFEST_OLD, ITEXT_MANIFEST_NEW),
    ])
    patch(out / 'snapshot' / 'scripts' / 'run_external_baselines.py', [
        ("            result = run(vendor, texts, client, embed, directory,\n"
         "                         no_progress_limit=manifest.get(\"itext_no_progress_limit\"))",
         "            result = run(vendor, texts, client, embed, directory,\n"
         "                         no_progress_limit=manifest.get(\"itext_no_progress_limit\"),\n"
         "                         preserve_symbols=bool(manifest.get(\"itext_preserve_symbols\")))"),
    ])
    base = json.loads((NEW / 'guarded' / 'manifest.json').read_text(encoding='utf-8'))
    manifest = dict(base)
    manifest.update({
        'experiment': 'external-l1-native-method-merged-three-sections--itext-symbol-compatible',
        'status': 'prepared',
        'snapshot': str(out / 'snapshot'),
        'jobs': [{'method': 'itext2kg', 'section': 'merged', 'repeat': repeat,
                  'directory': str(out / ('r%d' % repeat) / 'merged' / 'itext2kg'), 'status': 'pending'}
                 for repeat in (1, 2)],
        'itext_no_progress_limit': 3,
        'itext_preserve_symbols': True,
        'scope': ('Merged three-section document, two repeats, iText2KG progress-bounded AND '
                  'symbol-compatible variant. Separate label; shared cumulative iText ledger; v2 '
                  'damaged output preserved as evidence and never used for the semantic comparison.'),
        'symbol_fix_authorization': ('User approved on 2026-10-09: the pinned ASCII sanitiser destroys Chinese '
                                     'relation predicates, so relation names and endpoints are kept verbatim '
                                     'in memory; the vendor tree stays byte-identical.'),
    })
    manifest.pop('guard_authorization', None)
    write_manifest(out, manifest)
    return out


def prepare_edc():
    out = NEW / 'edc_v3'
    if out.exists():
        raise SystemExit('exists: %s' % out)
    clone_snapshot(out / 'snapshot', NEW / 'guarded' / 'snapshot')
    patch(out / 'snapshot' / 'scripts' / 'external_edc.py', [
        (EDC_CAPS_OLD, EDC_CAPS_NEW),
        (EDC_RUN_SIG_OLD, EDC_RUN_SIG_NEW),
        (EDC_AUDIT_OLD, EDC_AUDIT_NEW),
        (EDC_PARSER_OLD, EDC_PARSER_NEW),
        (EDC_CLIENT_OLD, EDC_CLIENT_NEW),
    ])
    patch(out / 'snapshot' / 'scripts' / 'run_external_baselines.py', [
        ("            result = run(vendor, texts, client, embed, directory, TARGET_SCHEMA)",
         "            result = run(vendor, texts, client, embed, directory, TARGET_SCHEMA,\n"
         "                         replay_dir=(manifest.get(\"edc_replay_dirs\") or {}).get(str(job[\"repeat\"])),\n"
         "                         parse_compat=bool(manifest.get(\"edc_parse_compat\")))"),
    ])
    base = json.loads((NEW / 'ctl' / 'edc' / 'manifest.json').read_text(encoding='utf-8'))
    manifest = dict(base)
    manifest.update({
        'experiment': 'external-l1-native-method-merged-three-sections--edc-parse-compatible',
        'status': 'prepared',
        'snapshot': str(out / 'snapshot'),
        'jobs': [{'method': 'edc', 'section': 'merged', 'repeat': repeat,
                  'directory': str(out / ('r%d' % repeat) / 'merged' / 'edc'), 'status': 'pending'}
                 for repeat in (1, 2)],
        'edc_parse_compat': True,
        'task_max_calls': 200,
        'task_max_seconds': 14400,
        'task_ceiling_note': ('Alignment runs one call per extracted triplet (131 for r1), which exceeds the '
                              'previous 120-call task ceiling; the ceiling is a task guard, the ledger stays the '
                              'budget gate.'),
        'edc_replay_dirs': {str(repeat): str(NEW / 'runs' / ('r%d' % repeat) / 'merged' / 'edc')
                            for repeat in (1, 2)},
        'scope': ('Merged three-section document, two repeats, EDC with the Define separator made '
                  'Unicode-compatible. Extract/Define are replayed from the frozen raw responses; only '
                  'the alignment stage that never executed runs live.'),
        'parse_fix_authorization': ('User approved on 2026-10-09: the pinned parser requires ASCII ":" and dropped '
                                    'all 52/27 definitions as "missing colon"; the separator is normalised for '
                                    'parsing only and the vendor tree stays byte-identical.'),
    })
    write_manifest(out, manifest)
    return out


def write_manifest(out: Path, manifest: dict):
    sys.path.insert(0, str(out / 'snapshot' / 'scripts'))
    import run_external_baselines as R  # noqa: E402
    import run_direct_extraction_baseline as baseline  # noqa: E402
    files = sorted((out / 'snapshot' / 'scripts').glob('*.py')) + \
            sorted((out / 'snapshot' / 'agents').glob('*.py')) + \
            sorted((out / 'snapshot' / 'prompts').glob('*.txt')) + \
            [out / 'snapshot' / 'domain.json', out / 'snapshot' / 'embedding_lock.json'] + \
            sorted((NEW / 'input').glob('*.md')) + \
            [NEW / 'input' / 'provenance.json', NEW / 'input' / 'sections.json', NEW / 'work' / 'merged_input.md'] + \
            sorted((NEW / 'plans' / 'merged').glob('*.json'))
    manifest['common'] = R.CONFIG
    manifest['artifacts'] = [baseline.bind(p) for p in files if p.exists()]
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    R.setup()
    baseline.audit_prepared(manifest)
    print('%-9s AUDIT OK | jobs %s | flags %s' % (
        out.name, [(j['repeat'], j['status']) for j in manifest['jobs']],
        {k: v for k, v in manifest.items() if k in ('itext_preserve_symbols', 'itext_no_progress_limit',
                                                    'edc_parse_compat', 'edc_replay_dirs')}))


def main() -> int:
    prepare_itext()
    prepare_edc()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
