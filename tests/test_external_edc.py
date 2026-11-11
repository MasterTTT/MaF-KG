import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from external_edc import (  # noqa: E402
    ChatCallLimitReached,
    EMBEDDING_DIMENSION,
    run,
)


VENDOR = ROOT / "evaluation/external_baselines_2026-10-08/vendor/edc"
TARGET_SCHEMA = {
    "special_case_of": "The subject is a mathematical subclass or special case of the object.",
    "defined_in_terms_of": "The subject's explicit definition uses the object as a defining ingredient.",
    "uses": "A mathematical method explicitly uses a stated result, concept, or method.",
    "derived_from": "The subject result is explicitly derived from the object result in the source.",
    "equivalent_to": "The source explicitly states mathematical equivalence, not mere similarity.",
    "implies": "The subject statement is sufficient for the object conclusion under preserved conditions.",
    "has_property": "The source explicitly assigns the stated mathematical property to its subject.",
}


def unit_vector(axis=0):
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[axis] = 1.0
    return vector


def cosine_fixture_embed(texts):
    vectors = []
    for text in texts:
        axis = 0 if "sufficient" in text.lower() else 1
        vectors.append(unit_vector(axis))
    return vectors


def read_audit(directory):
    return json.loads((directory / "edc_audit.json").read_text(encoding="utf-8"))


def test_offline_improper_integral_pilot_keeps_native_three_stage_output(tmp_path):
    calls = []
    raw_by_stage = {
        "extract": "[['反常积分收敛', 'open_relation', '尾积分趋于零']]",
        "define": (
            "open_relation: The statement is sufficient for the tail integral "
            "to approach zero under the stated convergence condition."
        ),
        "canonicalize": "A",
    }

    def client(messages, label, **kwargs):
        json.dumps(kwargs, allow_nan=False)
        calls.append({"label": label, "messages": messages, "kwargs": kwargs})
        if label.startswith("edc.extract."):
            return raw_by_stage["extract"]
        if label.startswith("edc.define."):
            return raw_by_stage["define"]
        return raw_by_stage["canonicalize"]

    output = tmp_path / "improper-integral-offline"
    result = run(
        VENDOR,
        ["反常积分收敛时，尾积分趋于零。"],
        client,
        cosine_fixture_embed,
        output,
        TARGET_SCHEMA,
        embedding_model_id="offline-fixture-384d-l2",
    )

    assert result == [[["反常积分收敛", "implies", "尾积分趋于零"]]]
    assert [call["label"].split(".")[1] for call in calls] == [
        "extract",
        "define",
        "canonicalize",
    ]
    assert all("model" not in call["kwargs"] for call in calls)
    assert [call["kwargs"]["max_tokens"] for call in calls] == [512, 512, 1]

    audit = read_audit(output)
    assert audit["status"] == "completed"
    assert audit["embedding_dimension"] == 384
    assert audit["embedding_similarity"] == "L2-normalized vectors; upstream dot product equals cosine"
    assert audit["embedding_model_id"] == "offline-fixture-384d-l2"
    assert audit["target_schema"] == TARGET_SCHEMA
    assert audit["result"]["target_schema_unchanged"] is True
    assert audit["chat_calls"][0]["raw_response"] == raw_by_stage["extract"]
    assert [event["parser"] for event in audit["parse_events"]] == [
        "parse_raw_triplets",
        "parse_relation_definition",
        "native_canonicalization_choice",
    ]
    assert all(row["l2_norm"] == pytest.approx(1.0) for row in audit["embedding_calls"])


def test_native_parser_drops_are_audited_without_repair(tmp_path):
    responses = iter(("No triples here\n[not a python literal]", "No relation definitions"))

    def client(messages, label, **kwargs):
        return next(responses)

    output = tmp_path / "parse-drops"
    result = run(
        VENDOR,
        ["一段没有结构化三元组的教材文字。"],
        client,
        cosine_fixture_embed,
        output,
        TARGET_SCHEMA,
    )

    assert result == [[]]
    audit = read_audit(output)
    triplet_event, definition_event = audit["parse_events"]
    assert triplet_event["raw_response"] == "No triples here\n[not a python literal]"
    assert triplet_event["parsed"] == []
    assert triplet_event["dropped"][0]["fragment"] == "[not a python literal]"
    assert definition_event["raw_response"] == "No relation definitions"
    assert definition_event["dropped"][0]["reason"] == "missing colon"
    assert audit["chat_calls"][0]["raw_response"] == triplet_event["raw_response"]


def test_chat_exception_is_audited_and_never_retried(tmp_path):
    attempts = []

    def client(messages, label, **kwargs):
        attempts.append(label)
        raise TimeoutError("fixture timeout")

    output = tmp_path / "single-attempt"
    with pytest.raises(TimeoutError, match="fixture timeout"):
        run(
            VENDOR,
            ["一段离线测试文字。"],
            client,
            cosine_fixture_embed,
            output,
            TARGET_SCHEMA,
        )

    audit = read_audit(output)
    assert attempts == ["edc.extract.section-0001"]
    assert audit["status"] == "failed"
    assert audit["failure"] == {"type": "TimeoutError"}
    assert len(audit["chat_calls"]) == 1
    assert audit["chat_calls"][0]["status"] == "failed"
    assert audit["chat_calls"][0]["raw_response"] is None


def test_adapter_enforces_40_call_ceiling(tmp_path):
    calls = []

    def client(messages, label, **kwargs):
        calls.append(label)
        if label.startswith("edc.extract."):
            return "[['subject', 'unknown', 'object']]"
        if label.startswith("edc.define."):
            return "unknown: a relation definition"
        return "A"

    client.max_calls = 40
    output = tmp_path / "call-limit"
    with pytest.raises(ChatCallLimitReached):
        run(
            VENDOR,
            ["same source"] * 14,
            client,
            cosine_fixture_embed,
            output,
            TARGET_SCHEMA,
        )

    audit = read_audit(output)
    assert len(calls) == 40
    assert audit["chat_policy"]["max_calls"] == 40
    assert audit["gate"]["status"] == "max_chat_calls_reached"


def test_adapter_rejects_non_unit_embeddings(tmp_path):
    def client(messages, label, **kwargs):
        if label.startswith("edc.extract."):
            return "[]"
        return "No relation definitions"

    def unnormalized_embed(texts):
        vector = unit_vector()
        vector[0] = 2.0
        return [vector for _ in texts]

    output = tmp_path / "bad-embedding"
    with pytest.raises(ValueError, match="L2-normalized"):
        run(
            VENDOR,
            ["一段离线测试文字。"],
            client,
            unnormalized_embed,
            output,
            TARGET_SCHEMA,
        )

    audit = read_audit(output)
    assert audit["status"] == "failed"
    assert audit["embedding_calls"][0]["status"] == "failed"
