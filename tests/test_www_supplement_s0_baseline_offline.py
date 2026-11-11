"""S0.2 baseline-compatibility offline tests (no network, no model calls).

These tests pin the CURRENT behaviour of the frozen arms, including the behaviour that
is known to be lossy. They are regression guards for the S0.2 audit, not a statement
that the behaviour is desirable.

Covered:
  * the pinned iText2KG identity key (lower / "_"->" " / "-"->" ") and its dedup use
  * short Chinese text, plus/minus signs, letter case, sub/superscripts, formula tokens
  * the EDC canonicaliser: seven-predicate dictionary, verbatim endpoints, rejection
    retention (enrich=False is what stops a rejected relation being added back)
  * the EDC transport deviation (native max_tokens/temperature are never forwarded)
  * itext_progress_guard: bounded loops cannot invent edges and cannot terminate by
    deleting hard entities

Run:  & "D:\\ANACONDA\\python.exe" -m pytest tests/test_www_supplement_s0_baseline_offline.py -q
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
EXP = REPO / "evaluation/external_baselines_merged_2026-10-09"
SNAP_V4 = EXP / "itext_v4/snapshot"
SNAP_EDC = EXP / "edc_v3/snapshot"
ITEXT_PKG = (SNAP_V4 / "vendor/itext2kg/snapshot/"
             "itext2kg-43bc581c99a3a35ace99dc4699acdabb0f1a98f7/itext2kg")
EDC_PKG_ROOT = SNAP_V4 / "vendor/edc"
AUDIT_JSON = (REPO / "evaluation/www_evidence_supplement_2026-10-11/audit/"
              "s0_identity_collisions.json")
EDC_RUN_R1 = EXP / "edc_v3/r1/merged/edc"

ITEXT_COMMIT = "43bc581c99a3a35ace99dc4699acdabb0f1a98f7"
SEVEN_PREDICATES = {
    "special_case_of",
    "defined_in_terms_of",
    "uses",
    "derived_from",
    "equivalent_to",
    "implies",
    "has_property",
}


# ---------------------------------------------------------------------------------------
# module loading helpers
# ---------------------------------------------------------------------------------------
def _load_module_from_path(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def itext_adapter():
    """The frozen iText2KG adapter module (scripts/external_itext2kg.py)."""
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        yield _load_module_from_path("s0_external_itext2kg", SNAP_V4 / "scripts/external_itext2kg.py")
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))


@pytest.fixture(scope="session")
def pinned_data_handler():
    """The pinned upstream DataHandler, imported standalone (it only needs typing/re)."""
    return _load_module_from_path(
        "s0_pinned_data_handling",
        ITEXT_PKG / "utils/data_handling.py",
    ).DataHandler


@pytest.fixture(scope="session")
def itext_class():
    """The vendored iText2KG class with the v4 adapter overrides applied."""
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        adapter = _load_module_from_path("s0_itext_loader", SNAP_V4 / "scripts/external_itext2kg.py")
        source_root, package_root = adapter._resolve_source_package(SNAP_V4 / "vendor/itext2kg")
        assert adapter._source_tree_sha256(package_root) == adapter.PINNED_SOURCE_SHA256
        return adapter._load_pinned_iText2KG(source_root, package_root,
                                            preserve_symbols=True, preserve_names=True)
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))


@pytest.fixture(scope="session")
def edc_modules():
    """(canonicalization module, llm_utils module) from the vendored EDC package."""
    sys.path.insert(0, str(EDC_PKG_ROOT))
    try:
        llm_utils = importlib.import_module("edc.utils.llm_utils")
        canonicalization = importlib.import_module("edc.schema_canonicalization")
        yield canonicalization, llm_utils
    finally:
        sys.path.remove(str(EDC_PKG_ROOT))


@pytest.fixture(scope="session")
def edc_adapter():
    """The parse-compatible EDC adapter that produced the reported EDC row."""
    sys.path.insert(0, str(SNAP_EDC / "scripts"))
    try:
        yield _load_module_from_path("s0_external_edc_v3", SNAP_EDC / "scripts/external_edc.py")
    finally:
        sys.path.remove(str(SNAP_EDC / "scripts"))


class _StubEmbedder:
    """Deterministic 384-dim L2 unit vectors; never calls out."""

    prompts: dict[str, str] = {}

    def __init__(self):
        self.calls: list[list[str]] = []
        self.dimension = 384

    def encode(self, text, prompt_name=None):
        self.calls.append([text])
        value = 1.0 / (384 ** 0.5)
        return [value] * 384

    def __call__(self, texts):
        self.calls.append(list(texts))
        value = 1.0 / (384 ** 0.5)
        return [[value] * 384 for _ in texts]


class _StubChat:
    """Records labels and returns canned responses; never calls out."""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.labels: list[str] = []
        self.kwargs_seen: list[dict[str, Any]] = []

    def __call__(self, messages, label="request", **kwargs):
        self.labels.append(label)
        self.kwargs_seen.append(dict(kwargs))
        assert label in self.responses, f"unexpected chat label {label!r}"
        return self.responses[label]


# =======================================================================================
# 1. iText2KG identity key
# =======================================================================================
def key(value) -> str:
    """Independent re-implementation of the pinned key, used to check the real code."""
    return str(value).lower().replace("_", " ").replace("-", " ")


def test_identity_key_source_line_is_the_pinned_expression():
    text = (SNAP_V4 / "scripts/external_itext2kg.py").read_text(encoding="utf-8")
    assert 'return str(value).lower().replace("_", " ").replace("-", " ")' in text
    upstream = (ITEXT_PKG / "utils/data_handling.py").read_text(encoding="utf-8")
    assert 'item[item_name_key].lower().replace("_", " ").replace("-", " ")' in upstream
    assert 're.sub(r\'[^a-zA-Z0-9]\', \'_\', data["name\"]).replace("&", "and")' in upstream


def test_adapter_retrieval_key_matches_pinned_upstream_normalisation(pinned_data_handler, itext_adapter):
    """The adapter's retrieval_key must be exactly what the pinned upstream name becomes."""
    handler_class = itext_adapter._name_preserving_data_handler(pinned_data_handler)
    adapter_handler = handler_class()
    pinned_handler = pinned_data_handler()

    samples = [
        "不定积分",                                   # short Chinese text
        "原函数F(x)", "被积函数f(x)",                  # letter case
        "增量\u0394y的线性主部", "误差\u03b4y",         # the old Greek case failure
        "\u03a6(x)是f(x)的另一个原函数", "\u03c6(x)可微",  # Phi / varphi
        "x \u00b1 y", "a \u00b1 b", "a+b", "a-b",     # plus/minus
        "x_0", "x 0", "y^{(n)}", "E_{\\mathrm{m}}",   # sub/superscripts
        "\\int f(x)\\mathrm{d}x", "d x", "dx",        # formula-like tokens
        "\u221e", "\u2192", "a\u2212b", "\uff21",     # glyphs and full width
    ]
    pinned = pinned_handler.add_embeddings_as_property_batch(
        embeddings_function=lambda texts: [], items=[{"name": s} for s in samples],
        item_name_key="name", embeddings=False)
    adapted = adapter_handler.add_embeddings_as_property_batch(
        embeddings_function=lambda texts: [], items=[{"name": s} for s in samples],
        item_name_key="name", embeddings=False)

    for original, pinned_row, adapted_row in zip(samples, pinned, adapted):
        assert adapted_row["name"] == original, "the v4 adapter must deliver the name verbatim"
        assert adapted_row["retrieval_key"] == pinned_row["name"], original
        assert adapted_row["retrieval_key"] == key(original), original


def test_identity_key_collision_letter_case(pinned_data_handler, itext_adapter):
    """Names that differ only in letter case collapse onto one identity key."""
    handler_class = itext_adapter._name_preserving_data_handler(pinned_data_handler)
    handler = handler_class()
    records = [
        {"name": "F(x)\u662f\u539f\u51fd\u6570"},   # F: the antiderivative
        {"name": "f(x)\u662f\u539f\u51fd\u6570"},   # f: the integrand - a different object
        {"name": "\u4efb\u610f\u5e38\u6570C"},
        {"name": "\u4efb\u610f\u5e38\u6570c"},
        {"name": "\u03a6(x)\u7684\u5b9a\u4e49"},
        {"name": "\u03c6(x)\u7684\u5b9a\u4e49"},
    ]
    unique = handler.remove_duplicates(records, "entity")
    assert [row["name"] for row in unique] == [
        "F(x)\u662f\u539f\u51fd\u6570", "\u4efb\u610f\u5e38\u6570C", "\u03a6(x)\u7684\u5b9a\u4e49"]
    for pair in (("F(x)\u662f\u539f\u51fd\u6570", "f(x)\u662f\u539f\u51fd\u6570"),
                 ("\u4efb\u610f\u5e38\u6570C", "\u4efb\u610f\u5e38\u6570c"),
                 ("\u03a6(x)\u7684\u5b9a\u4e49", "\u03c6(x)\u7684\u5b9a\u4e49")):
        assert key(pair[0]) == key(pair[1]), pair

    # The collision only bites when everything except the case differs; two longer names
    # that merely contain the different letters stay distinct (real-world mitigation).
    assert key("\u0394y\u662f\u589e\u91cf") != key("\u03b4y\u662f\u8bef\u5dee")


def test_identity_key_collision_phi_and_varphi_are_the_same_key():
    assert key("\u03a6") == key("\u03c6") == "\u03c6"
    assert "\u03a6".lower() == "\u03c6"


def test_identity_key_rewrites_minus_and_underscore_into_a_space():
    assert key("a-b") == "a b"
    assert key("x_0") == "x 0"
    assert key("E_{\\mathrm{m}}") == "e {\\mathrm{m}}"
    assert key("d(arccos x) = -1/\u221a(1-x^2) dx") == "d(arccos x) =  1/\u221a(1 x^2) dx"


def test_identity_key_is_not_normalisation_of_display_variants():
    """The key does NOT unify display variants of the same object (under-merge)."""
    assert len({key("dx"), key("d x"), key("\\mathrm{d}x")}) == 3
    assert len({key("x_2"), key("x\u2082"), key("x2")}) == 3
    assert len({key("a-b"), key("a\u2212b"), key("a\uff0db")}) == 3
    assert len({key("\uff21"), key("A"), key("a")}) == 2      # full width folds only its own case
    assert key("\uff21") == "\uff41"


def test_identity_key_specifically_requested_pairs():
    assert key("ln") == "ln" and key("In") == "in"            # ln / In do NOT collide
    assert key("sin") == key("Sin") == "sin"                  # sin / Sin DO collide
    assert key("e^x") == "e^x" and key("ex") == "ex"          # e^x / ex do NOT collide
    assert key("x \u00b1 y") == "x \u00b1 y"                  # plus/minus untouched
    assert key("\u221e") == "\u221e" and key("\u2192") == "\u2192"
    assert key("\u4e0d\u5b9a\u79ef\u5206") == "\u4e0d\u5b9a\u79ef\u5206"   # CJK is identity


def test_identity_key_merges_relations_with_identical_endpoints():
    """Relation identity is (startNode, endNode, key(name)) in the v4 handler."""
    pinned = _load_module_from_path("s0_dh_for_relations", ITEXT_PKG / "utils/data_handling.py")
    assert pinned is not None
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        adapter = _load_module_from_path("s0_itext_for_relations",
                                         SNAP_V4 / "scripts/external_itext2kg.py")
        handler_class = adapter._name_preserving_data_handler(pinned.DataHandler)
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))
    handler = handler_class()
    records = [
        {"startNode": "F(x)", "endNode": "f(x)", "name": "derived_from", "retrieval_key": "derived from"},
        {"startNode": "F(x)", "endNode": "f(x)", "name": "derived-from", "retrieval_key": "derived from"},
        {"startNode": "F(x)", "endNode": "f(x)", "name": "derived_from", "retrieval_key": "derived from"},
    ]
    unique = handler.remove_duplicates(records, "relation")
    assert len(unique) == 1


def test_identity_key_collision_groups_recorded_by_the_audit_are_reproducible():
    """Re-derive the audit's collision groups from the raw key; no live model needed."""
    audit = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    groups = audit["symbol_collisions"]["single_character"]
    assert len(groups) == 13
    keys = {row["key"] for row in groups}
    assert {"f", "c", "t", "x", "e", "l", "a", "m", "p", "g", "n", "o", "\u03c6"} == keys
    for row in groups:
        surfaces = list(row["surfaces"])
        assert len({key(s) for s in surfaces}) == 1, row["key"]
    phi = next(row for row in groups if row["key"] == "\u03c6")
    assert set(phi["surfaces"]) == {"\u03a6", "\u03c6"}
    assert phi["surfaces"]["\u03a6"]["occurrences"] == {"I": 10}
    assert phi["surfaces"]["\u03c6"]["occurrences"] == {"I": 27}


def test_symbol_preserving_handler_bypasses_the_ascii_sanitiser(pinned_data_handler, itext_adapter):
    """v4 keeps relation predicates verbatim; the pinned handler destroys CJK."""
    symbol_class = itext_adapter._symbol_preserving_data_handler(pinned_data_handler)
    row = {"startNode": "\u539f\u51fd\u6570", "endNode": "\u88ab\u79ef\u51fd\u6570", "name": "\u5b9a\u4e49\u4e8e"}
    assert symbol_class().process(dict(row), "relation") == row
    assert pinned_data_handler().process(dict(row), "relation") == {
        "startNode": "\u539f\u51fd\u6570", "endNode": "\u88ab\u79ef\u51fd\u6570", "name": "___"}
    assert key("\u5b9a\u4e49\u4e8e") == "\u5b9a\u4e49\u4e8e"   # the key itself leaves CJK alone
    # entity labels still go through the pinned ASCII sanitiser in v4
    assert pinned_data_handler().process({"name": "n", "label": "\u5b9a\u4e49"}, "entity") == {
        "name": "n", "label": "__"}


# =======================================================================================
# 2. EDC predicate dictionary
# =======================================================================================
def test_edc_dictionary_has_exactly_seven_predicates_and_matches_domain_json():
    domain = json.loads((SNAP_V4 / "domain.json").read_text(encoding="utf-8"))
    schema = domain["edc_target_schema"]
    assert len(schema) == 7
    assert set(schema) == SEVEN_PREDICATES
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        runner = _load_module_from_path("s0_run_external_baselines",
                                        SNAP_V4 / "scripts/run_external_baselines.py")
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))
    assert len(runner.TARGET_SCHEMA) == 7
    assert runner.TARGET_SCHEMA == schema
    for relation, definition in schema.items():
        assert definition and definition.strip() == definition


def test_edc_target_schema_is_passed_by_the_adapter_and_never_enriched(edc_adapter):
    source = (SNAP_EDC / "scripts/external_edc.py").read_text(encoding="utf-8")
    assert "enrich=False," in source
    assert "target_schema_after_run" in source
    assert "target_schema_unchanged" in source
    # The parse-compatible EDC arm raised its own chat ceiling to 200 and its task ceiling to
    # 14400 s (manifest edc_v3); every other arm stayed at 120 / 7200.
    assert edc_adapter.MAX_CHAT_CALLS == 200
    assert edc_adapter.MAX_SECONDS == 14400
    assert edc_adapter.EMBEDDING_DIMENSION == 384
    v4_source = (SNAP_V4 / "scripts/external_edc.py").read_text(encoding="utf-8")
    assert "parse_compat" not in v4_source, "the itext_v4 snapshot copy is NOT the parse-compatible one"
    assert "parse_compat" in source


def test_edc_canonicaliser_rejection_is_returned_as_none_and_not_added_back(edc_modules):
    canonicalization, llm_utils = edc_modules
    schema = {
        "uses": "A mathematical method explicitly uses a stated result, concept, or method.",
        "implies": "The subject statement is sufficient for the object conclusion under preserved conditions.",
        "has_property": "The source explicitly assigns the stated mathematical property to its subject.",
    }
    embedder = _StubEmbedder()
    canonicalizer = canonicalization.SchemaCanonicalizer(schema, embedder,
                                                         verify_openai_model="edc-injected-call")
    before = dict(canonicalizer.schema_dict)
    rejection = _StubChat({"edc.canonicalize.section-0001.triplet-0001": "Z"})
    llm_utils.set_chat_call(rejection)
    try:
        triplet = ["\u5b9a\u79ef\u5206\u57fa\u672c\u516c\u5f0f", "\u5b9a\u4e49\u4e8e", "x \u00b1 y"]
        with llm_utils.chat_label("edc.canonicalize.section-0001.triplet-0001"):
            result, candidates = canonicalizer.canonicalize(
                "\u77ed\u4e2d\u6587\u6bb5\u843d", triplet, {"\u5b9a\u4e49\u4e8e": "\u4e00\u4e2a\u5b9a\u4e49"},
                "input:{input_text} triplet:{query_triplet} choices:{choices}", enrich=False)
    finally:
        llm_utils.set_chat_call(None)
    assert result is None, "a rejected triplet must not silently become canonical output"
    assert candidates, "the candidate list must survive as the loss diagnostic"
    assert canonicalizer.schema_dict == before, "enrich=False must keep the target schema fixed"
    assert rejection.labels == ["edc.canonicalize.section-0001.triplet-0001"]
    assert rejection.kwargs_seen[0]["max_tokens"] == 1


def test_edc_canonicaliser_enrich_true_is_what_would_add_the_relation_back(edc_modules):
    canonicalization, llm_utils = edc_modules
    schema = {"uses": "A mathematical method explicitly uses a stated result, concept, or method."}
    canonicalizer = canonicalization.SchemaCanonicalizer(schema, _StubEmbedder(),
                                                         verify_openai_model="edc-injected-call")
    llm_utils.set_chat_call(_StubChat({"edc.canonicalize.section-0001.triplet-0001": "Z"}))
    try:
        triplet = ["A", "\u65b0\u8c13\u8bcd", "B"]
        with llm_utils.chat_label("edc.canonicalize.section-0001.triplet-0001"):
            result, _ = canonicalizer.canonicalize(
                "text", triplet, {"\u65b0\u8c13\u8bcd": "\u63cf\u8ff0"},
                "input:{input_text} choices:{choices}", enrich=True)
    finally:
        llm_utils.set_chat_call(None)
    assert result == triplet
    assert "\u65b0\u8c13\u8bcd" in canonicalizer.schema_dict


def test_edc_canonicaliser_keeps_endpoints_verbatim_when_it_accepts(edc_modules):
    canonicalization, llm_utils = edc_modules
    schema = {"uses": "uses def", "implies": "implies def", "has_property": "has_property def"}
    canonicalizer = canonicalization.SchemaCanonicalizer(schema, _StubEmbedder(),
                                                         verify_openai_model="edc-injected-call")
    accepted = _StubChat({"edc.canonicalize.section-0001.triplet-0001": "A"})
    llm_utils.set_chat_call(accepted)
    try:
        triplet = ["\u5f53 |\u0394x| \u5f88\u5c0f\u65f6\uff0c\u0394y \u2248 dy",
                   "\u4f7f\u7528", "f(x_0) \u00b1 \u03b5"]
        with llm_utils.chat_label("edc.canonicalize.section-0001.triplet-0001"):
            result, _ = canonicalizer.canonicalize(
                "text", triplet, {"\u4f7f\u7528": "\u4e00\u4e2a\u65b9\u6cd5\u4f7f\u7528\u67d0\u4e2a\u7ed3\u679c"},
                "input:{input_text} choices:{choices}", enrich=False)
    finally:
        llm_utils.set_chat_call(None)
    assert result is not None
    assert result[0] == triplet[0] and result[2] == triplet[2]
    assert result[1] in schema


def test_edc_canonicaliser_returns_canonical_predicate_triplet_untouched(edc_modules):
    canonicalization, llm_utils = edc_modules
    schema = {"uses": "uses def", "implies": "implies def"}
    canonicalizer = canonicalization.SchemaCanonicalizer(schema, _StubEmbedder(),
                                                         verify_openai_model="edc-injected-call")
    chat = _StubChat({})
    llm_utils.set_chat_call(chat)
    try:
        triplet = ["\u5206\u90e8\u79ef\u5206\u6cd5", "uses", "\u4e58\u79ef\u6c42\u5bfc\u6cd5\u5219"]
        with llm_utils.chat_label("edc.canonicalize.section-0001.triplet-0001"):
            result, candidates = canonicalizer.canonicalize("text", triplet, {}, "input:{input_text}",
                                                            enrich=False)
    finally:
        llm_utils.set_chat_call(None)
    assert result == triplet
    assert candidates == {}
    assert chat.labels == [], "an already canonical predicate must not cost a request"


def test_edc_adapter_persists_raw_canonical_and_rejection_reason(edc_adapter, tmp_path):
    extract_raw = "Triplets: [['F(x)', '\u5b9a\u4e49\u4e8e', 'f(x)']]"
    define_raw = "Answer\uff1a\u5b8c\u6210\n\u5b9a\u4e49\u4e8e\uff1a\u4e00\u4e2a\u51fd\u6570\u662f\u53e6\u4e00\u4e2a\u51fd\u6570\u7684\u539f\u51fd\u6570\n"
    verify_raw = "Z"
    chat = _StubChat({
        "edc.extract.section-0001": extract_raw,
        "edc.define.section-0001": define_raw,
        "edc.canonicalize.section-0001.triplet-0001": verify_raw,
    })
    embedder = _StubEmbedder()
    out = tmp_path / "edc_out"
    result = edc_adapter.run(EDC_PKG_ROOT, ["\u77ed\u4e2d\u6587\u6bb5\u843d\u3002"], chat, embedder,
                             out, {"uses": "uses def", "implies": "implies def",
                                   "has_property": "has_property def"},
                             parse_compat=True)

    audit = json.loads((out / "edc_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "completed"
    assert audit["parse_compat"] is True
    assert audit["result"]["open_triplets"] == [[["F(x)", "\u5b9a\u4e49\u4e8e", "f(x)"]]]
    assert audit["result"]["canonical_triplets"] == [[None]]
    assert result == [[None]]
    assert audit["result"]["target_schema_unchanged"] is True
    assert set(audit["result"]["target_schema_after_run"]) == {"uses", "implies", "has_property"}
    assert audit["chat_calls"][0]["raw_response"] == extract_raw

    rejections = [event for event in audit["parse_events"]
                  if event["parser"] == "native_canonicalization_choice" and event["dropped"]]
    assert len(rejections) == 1
    assert rejections[0]["raw_response"] == verify_raw
    assert rejections[0]["first_character"] == "Z"
    assert rejections[0]["dropped"][0]["reason"] == (
        "native verifier response did not begin with a candidate letter")

    definition_events = [e for e in audit["parse_events"] if e["parser"] == "parse_relation_definition"]
    assert definition_events[0]["response_normalization"]["replacements"] == 2
    assert definition_events[0]["raw_response_before_normalization"] == define_raw
    assert definition_events[0]["raw_response"].count("\uff1a") == 0


def test_edc_adapter_without_parse_compat_drops_every_definition_without_a_reason_row(
        edc_adapter, tmp_path):
    """Current behaviour: U+FF1A separators are not accepted and the rejections are silent."""
    define_raw = "Answer\uff1a\u5b8c\u6210\n\u5b9a\u4e49\u4e8e\uff1a\u4e00\u4e2a\u51fd\u6570\u7684\u539f\u51fd\u6570\n"
    chat = _StubChat({
        "edc.extract.section-0001": "Triplets: [['F(x)', '\u5b9a\u4e49\u4e8e', 'f(x)']]",
        "edc.define.section-0001": define_raw,
    })
    out = tmp_path / "edc_out_no_parse_compat"
    result = edc_adapter.run(EDC_PKG_ROOT, ["\u77ed\u4e2d\u6587\u6bb5\u843d\u3002"], chat, _StubEmbedder(),
                             out, {"uses": "uses def"}, parse_compat=False)
    audit = json.loads((out / "edc_audit.json").read_text(encoding="utf-8"))
    assert audit["parse_compat"] is False
    assert audit["result"]["schema_definitions"] == [{}]
    assert result == [[None]]
    assert [event["parser"] for event in audit["parse_events"]] == [
        "parse_raw_triplets", "parse_relation_definition"]
    assert audit["parse_events"][1]["dropped"] == [
        {"line_number": 1, "line": "Answer\uff1a\u5b8c\u6210", "reason": "missing colon"},
        {"line_number": 2, "line": "\u5b9a\u4e49\u4e8e\uff1a\u4e00\u4e2a\u51fd\u6570\u7684\u539f\u51fd\u6570",
         "reason": "missing colon"},
    ]
    # the raw triplet is still retained, so the loss is reconstructible from the audit
    assert audit["result"]["open_triplets"] == [[["F(x)", "\u5b9a\u4e49\u4e8e", "f(x)"]]]
    assert chat.labels == ["edc.extract.section-0001", "edc.define.section-0001"]


def test_edc_adapter_refuses_to_overwrite_an_existing_audit(edc_adapter, tmp_path):
    chat = _StubChat({
        "edc.extract.section-0001": "Triplets: [['A', 'uses', 'B']]",
        "edc.define.section-0001": "Answer: done\nuses: d\n",
        "edc.canonicalize.section-0001.triplet-0001": "Z",
    })
    out = tmp_path / "edc_twice"
    edc_adapter.run(EDC_PKG_ROOT, ["t"], chat, _StubEmbedder(), out, {"uses": "uses def"},
                    parse_compat=True)
    with pytest.raises(FileExistsError):
        edc_adapter.run(EDC_PKG_ROOT, ["t"], chat, _StubEmbedder(), out, {"uses": "uses def"},
                        parse_compat=True)


# =======================================================================================
# 3. EDC transport deviation
# =======================================================================================
def test_edc_native_output_limit_is_512_and_temperature_zero(edc_modules):
    _, llm_utils = edc_modules
    import inspect
    signature = inspect.signature(llm_utils.openai_chat_completion)
    assert signature.parameters["max_tokens"].default == 512
    assert signature.parameters["temperature"].default == 0


def test_shared_transport_ignores_native_kwargs_and_sends_the_common_cap():
    source = (SNAP_V4 / "scripts/external_baseline_runtime.py").read_text(encoding="utf-8")
    assert '_, body = payload(MODEL, "", "", self.max_tokens)' in source
    assert '"wrapper_options": kwargs,' in source
    # the kwargs never reach the body builder
    body_section = source.split("def _complete(", 1)[1].split("request = urllib.request.Request", 1)[0]
    assert "kwargs" in body_section
    assert "body[\"max_tokens\"] = " not in body_section
    assert '"temperature"' not in body_section


def test_reported_edc_run_actually_sent_64000_and_no_temperature():
    """Read the frozen wire bodies of the row that produced the reported numbers."""
    requests = sorted((EDC_RUN_R1 / "requests").glob("*.json"))
    assert len(requests) == 94
    kinds: dict[tuple, int] = {}
    for path in requests:
        payload_row = json.loads(path.read_text(encoding="utf-8"))
        body = payload_row["body"]
        wrapper = payload_row.get("wrapper_options") or {}
        key = (body.get("max_tokens"), "temperature" in body, wrapper.get("max_tokens"),
               "temperature" in wrapper)
        kinds[key] = kinds.get(key, 0) + 1
    assert kinds == {(64000, False, 1, True): 92, (64000, False, 512, True): 2}
    assert set(json.loads(requests[0].read_text(encoding="utf-8"))["body"]) == {
        "model", "messages", "max_tokens", "stream", "stream_options",
        "thinking", "reasoning_effort"}


# =======================================================================================
# 4. iText progress guard
# =======================================================================================
def test_progress_guard_applies_to_the_real_vendored_build_graph(itext_class):
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        guard = importlib.import_module("itext_progress_guard")
        bounded = guard.bounded_graph_class(itext_class, 3)
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))
    assert bounded.__name__ == "ProgressBoundedIText2KG"
    assert issubclass(bounded, itext_class)
    events = json.loads((EXP / "itext_v4/r1/merged/itext2kg/call_trace.json").read_text(encoding="utf-8"))
    assert "no_progress_exits" in events


def test_progress_guard_rejects_a_class_without_the_two_pinned_loops():
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        guard = importlib.import_module("itext_progress_guard")
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))

    class OneLoop:
        def build_graph(self):
            isolated_entities = [1]
            while isolated_entities:
                isolated_entities = []
            return [], []

    with pytest.raises(ValueError, match="Expected exactly the two pinned isolated-entity loops"):
        guard.bounded_graph_class(OneLoop, 3)


def test_progress_guard_terminates_without_deleting_entities_or_inventing_edges():
    """Rebuild a build_graph with the two pinned loops, then bound it and run it."""
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        guard = importlib.import_module("itext_progress_guard")
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))

    class FakeGraph:
        """Minimal stand-in: the relations never cover the isolated entities."""

        def __init__(self):
            self._no_progress_events = []
            self.relation_calls = 0

        def build_graph(self):
            sections = ["s0", "s1"]
            global_entities = [{"name": "hard-entity"}]
            global_relationships = []
            isolated_entities = list(global_entities)
            while isolated_entities:
                self.relation_calls += 1
                if self.relation_calls > 100:
                    raise AssertionError("guard did not bound the loop")
                isolated_entities = list(global_entities)
            for i in range(1, len(sections)):
                isolated_entities = list(global_entities)
                while isolated_entities:
                    self.relation_calls += 1
                    if self.relation_calls > 100:
                        raise AssertionError("guard did not bound the second loop")
                    isolated_entities = list(global_entities)
            return global_entities, global_relationships

    module = sys.modules["test_www_supplement_s0_baseline_offline"]
    FakeGraph.__module__ = module.__name__
    bounded = guard.bounded_graph_class(FakeGraph, 3)
    graph = bounded()
    entities, relationships = graph.build_graph()
    assert entities == [{"name": "hard-entity"}], "the guard must not delete hard entities"
    assert relationships == [], "the guard must not invent edges"
    assert len(graph._no_progress_events) == 2
    for event in graph._no_progress_events:
        assert event["consecutive_no_progress_passes"] == 3
        assert event["retained_isolated_names"] == ["hard-entity"]
        assert event["action"] == "retain_entities_and_relations_then_continue_next_section"
    assert [event["section_index"] for event in graph._no_progress_events] == [0, 1]


def test_progress_guard_semantics_are_readable_from_the_frozen_source():
    source = (SNAP_V4 / "scripts/itext_progress_guard.py").read_text(encoding="utf-8")
    assert "if isolated_entities and _stall_passes >= {limit}" in source
    assert "'action': 'retain_entities_and_relations_then_continue_next_section'" in source
    assert "break" in source
    assert "remove" not in source and "pop(" not in source and "del " not in source


# =======================================================================================
# 5. arm readiness: can the frozen code still be loaded, and is the manifest the same
#    configuration the frozen runner carries?
# =======================================================================================
VENV_PYTHON = SNAP_V4 / "runtime/Scripts/python.exe"


def test_maf_and_direct_extractor_module_loads_offline():
    """run_memory_ablation.load_module(<snapshot>/agents/Extractor.py) is the arm's code path."""
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        loader = _load_module_from_path("s0_run_memory_ablation",
                                        SNAP_V4 / "scripts/run_memory_ablation.py")
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))
    assert loader.COMMON["model"] == "deepseek-v4-flash"
    assert loader.COMMON["max_chunk_chars"] == 3200
    assert loader.COMMON["chunk_context_before_lines"] == 6
    assert loader.COMMON["chunk_context_after_lines"] == 4
    assert loader.COMMON["max_tokens"] == 64000
    assert loader.ARMS == {"visible_memory": (12000, 6000), "no_visible_memory": (2, 2)}

    # The module itself only imports under the arm's bundled interpreter, so run the real
    # load there. The system interpreter is checked separately below.
    assert VENV_PYTHON.is_file()
    probe = (
        "import json, pathlib, sys;"
        f"sys.path.insert(0, r'{SNAP_V4 / 'scripts'}');"
        "import run_memory_ablation as loader;"
        f"m = loader.load_module(pathlib.Path(r'{SNAP_V4 / 'agents/Extractor.py'}'));"
        "print(json.dumps({'name': m.__name__,"
        " 'has_extractor': hasattr(m, 'Extractor'),"
        " 'has_chunk_document': hasattr(m.Extractor, '_chunk_document')}))"
    )
    import subprocess
    completed = subprocess.run([str(VENV_PYTHON), "-X", "utf8", "-c", probe],
                               capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert completed.returncode == 0, completed.stderr[-2000:]
    loaded = json.loads(completed.stdout.strip().splitlines()[-1])
    assert loaded == {"name": "memory_ablation_frozen_extractor",
                      "has_extractor": True, "has_chunk_document": True}


def test_system_interpreter_cannot_run_the_arms_so_the_bundled_venv_is_required():
    """Documents why the experiment venv is not optional."""
    import importlib.util
    assert (SNAP_V4 / "runtime/Scripts/python.exe").is_file()
    assert (SNAP_V4 / "runtime/pyvenv.cfg").read_text(encoding="utf-8").count(
        "include-system-site-packages = false") == 1
    if pathlib.Path(sys.prefix).resolve() == (SNAP_V4 / "runtime").resolve():
        pytest.skip("this run already uses the bundled venv")
    assert importlib.util.find_spec("fastembed") is None
    assert importlib.util.find_spec("langgraph.graph") is None


def test_frozen_runner_config_matches_the_reported_manifest():
    """A new run reuses run_external_baselines.CONFIG; it must equal the reported manifest."""
    manifest = json.loads((EXP / "itext_v4/manifest.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(SNAP_V4 / "scripts"))
    try:
        runner = _load_module_from_path("s0_runner_config", SNAP_V4 / "scripts/run_external_baselines.py")
    finally:
        sys.path.remove(str(SNAP_V4 / "scripts"))
    shared = ("provider", "model", "base_url", "api_key_env", "thinking", "reasoning_effort",
              "chunk_strategy", "max_chunk_chars", "chunk_context_before_lines",
              "chunk_context_after_lines", "temperature", "max_tokens", "max_retries",
              "request_timeout", "request_delay", "use_response_format")
    for field in shared:
        assert runner.CONFIG[field] == manifest["common"][field], field
    assert manifest["itext_no_progress_limit"] == 3
    assert manifest["itext_preserve_symbols"] is True
    assert manifest["itext_preserve_names"] is True
    assert set(runner.TARGET_SCHEMA) == SEVEN_PREDICATES


def test_reported_edc_arm_used_the_parse_compatible_snapshot_not_the_v4_one():
    """The freeze record names itext_v4/snapshot; the EDC row came from edc_v3/snapshot."""
    v4 = (SNAP_V4 / "scripts/external_edc.py").read_bytes()
    v3 = (SNAP_EDC / "scripts/external_edc.py").read_bytes()
    assert v4 != v3
    assert b"parse_compat" not in v4
    assert b"parse_compat" in v3
    audit = json.loads((EDC_RUN_R1 / "edc_audit.json").read_text(encoding="utf-8"))
    assert audit["parse_compat"] is True
    assert audit["method"] == "edc-target-alignment-02aad17e-adapted-embedding"
    assert all(row["status"] == "completed" for row in audit["chat_calls"])
    # the replay hook was configured but never matched: every call was live
    assert audit["replay_dir"] is not None
    assert not any(row.get("status") == "replayed" for row in audit["chat_calls"])
