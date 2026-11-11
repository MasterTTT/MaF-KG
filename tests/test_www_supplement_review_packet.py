"""Offline tests for the author review packet sampler.

The sampling rule is a frozen protocol element: if it is wrong, the author's
confirmation no longer speaks to the sample the paper describes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "review_packet", WORKSPACE / "analysis" / "www_supplement_review_packet.py")
assert SPEC and SPEC.loader
packet_mod = importlib.util.module_from_spec(SPEC)
sys.modules["review_packet"] = packet_mod
SPEC.loader.exec_module(packet_mod)


def fake_outputs(n_outputs=16, content=8, relations=5, generated=4):
    outs = []
    for i in range(n_outputs):
        oid = f"out{i:02d}"
        outs.append({
            "output_id": oid,
            "run": f"r{i % 2 + 1}",
            "method": ["maf", "direct", "edc", "itext2kg"][i % 4],
            "content_rows": [
                {"item_id": f"{oid}-C{j}", "name": f"item{j}", "type": "Claim" if j % 2 == 0 else "Concept",
                 "status": "M", "coverage": "full", "conditions_complete": j % 3 == 0}
                for j in range(content)
            ],
            "relation_rows": [
                {"relation_id": f"{oid}-R{j}", "semantic": "full", "strict": "conforms"}
                for j in range(relations)
            ],
            "correctness_rows": [
                {"generated_id": f"{oid}-G{j}", "name": f"gen{j}", "source_support": "yes",
                 "math_error": "none", "verdict": "correct"}
                for j in range(generated)
            ],
        })
    return outs


# ------------------------------------------------------------------ shape

def test_packet_has_four_items_per_output():
    packet, _ = packet_mod.build_packet(fake_outputs(16))
    assert packet["item_count"] == 64
    assert packet["expected_item_count"] == 64
    assert packet["complete"] is True


def test_stratum_counts_match_the_rule():
    packet, _ = packet_mod.build_packet(fake_outputs(16))
    assert packet["stratum_counts"]["content_claim_with_conditions"] == 16
    assert packet["stratum_counts"]["content_other"] == 16
    assert packet["stratum_counts"]["relation_semantic"] == 16
    assert packet["stratum_counts"]["generated_correctness"] == 16


def test_at_least_one_content_item_per_output_is_a_claim():
    packet, _ = packet_mod.build_packet(fake_outputs(16))
    by_output = {}
    for it in packet["items"]:
        if it["stratum"].startswith("content_"):
            by_output.setdefault(it["output_id"], []).append(it)
    for oid, rows in by_output.items():
        assert any(r["type"] == "Claim" for r in rows), oid


def test_blind_ids_are_sequential_and_unique():
    packet, _ = packet_mod.build_packet(fake_outputs(16))
    ids = [it["blind_id"] for it in packet["items"]]
    assert len(set(ids)) == 64
    assert ids[0] == "AR-01" and ids[-1] == "AR-64"


def test_method_is_hidden_from_items_but_kept_in_the_mapping():
    packet, mapping = packet_mod.build_packet(fake_outputs(16))
    blob = str(packet["items"])
    for m in ("maf", "direct", "edc", "itext2kg"):
        assert f"'{m}'" not in blob
    assert {v["method"] for v in mapping.values()} == {"maf", "direct", "edc", "itext2kg"}


# ---------------------------------------------------------- determinism

def test_same_seed_same_packet():
    a, _ = packet_mod.build_packet(fake_outputs(16))
    b, _ = packet_mod.build_packet(fake_outputs(16))
    assert [i["blind_id"] + i.get("item_id", i.get("relation_id", i.get("generated_id", "")))
            for i in a["items"]] == \
           [i["blind_id"] + i.get("item_id", i.get("relation_id", i.get("generated_id", "")))
            for i in b["items"]]


def test_different_seed_changes_the_packet():
    a, _ = packet_mod.build_packet(fake_outputs(16), seed=20261011)
    b, _ = packet_mod.build_packet(fake_outputs(16), seed=1)
    key = lambda p: [i.get("item_id", i.get("relation_id", i.get("generated_id", "")))
                     for i in p["items"]]
    assert key(a) != key(b), "the seed must actually drive the sample"


def test_order_of_outputs_does_not_change_an_outputs_sample():
    outs = fake_outputs(16)
    a, _ = packet_mod.build_packet(outs)
    b, _ = packet_mod.build_packet(list(reversed(outs)))
    def per_output(p):
        return {it["output_id"]: it.get("item_id", it.get("relation_id", it.get("generated_id", "")))
                for it in p["items"] if it["stratum"] == "relation_semantic"}
    assert per_output(a) == per_output(b), "per-output streams must be order independent"


# ------------------------------------------------------------- shortfall

def test_shortfall_is_recorded_not_substituted():
    outs = fake_outputs(16)
    outs[0]["relation_rows"] = []
    packet, _ = packet_mod.build_packet(outs)
    assert any(s["output_id"] == "out00" and s["stratum"] == "relation_semantic"
               for s in packet["shortfalls"])
    assert packet["item_count"] == 63
    assert packet["complete"] is False


def test_shortfall_when_no_claim_exists_is_recorded():
    outs = fake_outputs(2)
    for o in outs:
        for r in o["content_rows"]:
            r["type"] = "Concept"
    packet, _ = packet_mod.build_packet(outs)
    assert any(s["stratum"] == "content_claim_with_conditions" for s in packet["shortfalls"])


def test_full_coverage_has_no_shortfalls():
    packet, _ = packet_mod.build_packet(fake_outputs(16))
    assert packet["shortfalls"] == []


# --------------------------------------------------------------- content

def test_packet_records_the_sampling_rule_and_the_blinding_limits():
    packet, _ = packet_mod.build_packet(fake_outputs(16))
    rule = packet["sampling_rule"]
    assert rule["per_output"] == 4
    assert "disagreement" in rule["not_selected_for"]
    assert packet["blinding"]["mapping_kept_separately"] is True
    assert "may still reveal the method" in packet["blinding"]["residual_risk"]


def test_seed_is_the_pre_frozen_value():
    packet, _ = packet_mod.build_packet(fake_outputs(4))
    assert packet["seed"] == 20261011
    assert packet_mod.SEED == 20261011


def test_highly_disputed_and_successful_items_are_not_privileged():
    """The sampler must not favour either extreme."""
    outs = fake_outputs(16)
    for o in outs:
        for j, r in enumerate(o["content_rows"]):
            r["coverage"] = "full" if j % 2 == 0 else "missing"
    packet, _ = packet_mod.build_packet(outs)
    picks = [i for i in packet["items"] if i["stratum"].startswith("content_")]
    kinds = {p.get("coverage") for p in picks}
    assert kinds == {"full", "missing"}, "both extremes must be reachable"
