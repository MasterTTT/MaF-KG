# -*- coding: utf-8 -*-
"""Focused tests for the S2 relation-evaluation aggregation (closeout plan §5.3).

These test the scoring and binding code, not the mathematics.  Each test corresponds to one of the
checks the plan requires: inverse-predicate equivalence, a genuinely reversed edge, free-phrase
endpoint alignment, topic-only association, a missing condition, an illegal item id, duplicates and
missing rows, uncertain versus not-comparable, repeats that are not independent samples, and the
refusal to mix reference sets.

Run: python -m pytest tests/test_final_closeout_s2_metrics.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "analysis"))

import final_closeout_s2_metrics as metrics  # noqa: E402


def make_worksheet(label: str, count: int = 47) -> dict:
    return {
        "label": label,
        "rows": [{"reference_id": f"R{i:02d}"} for i in range(count)],
    }


def make_judgement(reference_id: str, semantic: str, strict: str, items=None) -> dict:
    return {
        "reference_id": reference_id,
        "native_item_ids": items or [],
        "verbatim_fragment": "native fragment",
        "semantic": semantic,
        "strict": strict,
        "reason": "reason",
        "source_line": 1,
    }


def write_case(tmp_path: Path, label: str, rows: list[dict], worksheet_count: int = 47,
               counts: dict | None = None) -> None:
    (tmp_path / f"worksheet_{label}.json").write_text(
        json.dumps(make_worksheet(label, worksheet_count), ensure_ascii=False), encoding="utf-8"
    )
    payload = {
        "label": label,
        "judgements": rows,
        "counts": counts
        if counts is not None
        else {
            "semantic": metrics.count_values(rows, "semantic", metrics.SEMANTIC_VALUES),
            "strict": metrics.count_values(rows, "strict", metrics.STRICT_VALUES),
        },
    }
    (tmp_path / f"judgements_{label}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def full_rows(**overrides) -> list[dict]:
    rows = [make_judgement(f"R{i:02d}", "full", "conforms") for i in range(47)]
    for index, replacement in overrides.items():
        rows[int(index)] = replacement
    return rows


def test_inverse_predicate_equivalence_is_accepted():
    """A reversed-but-equivalent predicate may be semantic full; the strict column still records it."""
    row = make_judgement("R00", "full", "direction_mismatch")
    row["reason"] = "教材以相反语序表述同一事实，构成逆谓词等价"
    assert metrics.normalise_strict(row) == "direction_mismatch"
    assert row["semantic"] == "full"


def test_true_reversed_edge_is_not_treated_as_absent():
    row = make_judgement("R01", "partial", "direction_mismatch")
    assert metrics.normalise_strict(row) == "direction_mismatch"


def test_free_phrase_endpoint_alignment_counts_as_covered():
    rows = full_rows(**{"0": make_judgement("R00", "full", "not_comparable", ["T001_001"])})
    summary = metrics.summarise(
        [{**row, "strict_normalised": metrics.normalise_strict(row)} for row in rows]
    )
    assert summary["semantic_counts"]["full"] == 47
    assert summary["strict_counts_normalised"]["not_comparable"] == 1
    assert summary["comparable_denominator"] == 46


def test_topic_only_association_is_not_counted_as_covered():
    rows = full_rows(**{"0": make_judgement("R00", "mentioned_only", "endpoint_identity_mismatch")})
    summary = metrics.summarise(
        [{**row, "strict_normalised": metrics.normalise_strict(row)} for row in rows]
    )
    assert summary["semantic_counts"]["mentioned_only"] == 1
    assert summary["semantic_covered_full_or_partial"] == 46


def test_missing_condition_stays_partial_not_full():
    row = make_judgement("R00", "partial", "type_mismatch")
    row["conditions_complete"] = False
    rows = full_rows(**{"0": row})
    summary = metrics.summarise(
        [{**r, "strict_normalised": metrics.normalise_strict(r)} for r in rows]
    )
    assert summary["semantic_counts"]["partial"] == 1
    assert summary["semantic_counts"]["full"] == 46


def test_illegal_item_id_is_rejected(tmp_path, monkeypatch):
    rows = full_rows(**{"0": make_judgement("R00", "full", "conforms", ["NOT_AN_ITEM"])})
    write_case(tmp_path, "P08", rows)
    monkeypatch.setattr(metrics, "GRAPHS", [("P08", "direct", 1)])
    monkeypatch.setattr(metrics, "PACKET_DIRS", {"P08": "review_merged"})
    result = metrics.validate_judgements(tmp_path)
    assert not result["ok"]
    assert any("illegal native item_id" in problem for problem in result["problems"])


def test_duplicate_and_missing_rows_are_rejected(tmp_path, monkeypatch):
    rows = full_rows()
    rows[1] = make_judgement("R00", "full", "conforms")  # duplicate reference id
    write_case(tmp_path, "P08", rows)
    monkeypatch.setattr(metrics, "GRAPHS", [("P08", "direct", 1)])
    monkeypatch.setattr(metrics, "PACKET_DIRS", {"P08": "review_merged"})
    result = metrics.validate_judgements(tmp_path)
    assert not result["ok"]
    assert any("does not match" in problem or "duplicate" in problem for problem in result["problems"])

    rows = full_rows()[:46]  # missing row
    write_case(tmp_path, "P08", rows, counts={"semantic": {}, "strict": {}})
    result = metrics.validate_judgements(tmp_path)
    assert not result["ok"]
    assert any("judgements for" in problem for problem in result["problems"])


def test_uncertain_and_not_comparable_leave_the_comparable_denominator():
    rows = full_rows(
        **{
            "0": make_judgement("R00", "uncertain", "uncertain"),
            "1": make_judgement("R01", "partial", "not_comparable"),
            "2": make_judgement("R02", "not_covered", "not_comparable"),
        }
    )
    summary = metrics.summarise(
        [{**row, "strict_normalised": metrics.normalise_strict(row)} for row in rows]
    )
    assert summary["strict_counts_normalised"]["uncertain"] == 1
    # Rows whose representation has no comparable structure stay not_comparable, even when nothing
    # was covered: structural non-comparability is not a missing edge.
    assert summary["strict_counts_normalised"]["not_comparable"] == 2
    assert summary["strict_counts_normalised"].get("absent", 0) == 0
    assert summary["comparable_denominator"] == 45


def test_not_covered_becomes_absent_without_editing_the_raw_verdict():
    row = make_judgement("R00", "not_covered", "type_mismatch")
    assert metrics.normalise_strict(row) == "absent"
    assert row["strict"] == "type_mismatch"


def test_not_comparable_survives_the_absent_normalisation():
    """A representation with no node layer stays not_comparable even when nothing was covered."""
    row = make_judgement("R00", "not_covered", "not_comparable")
    assert metrics.normalise_strict(row) == "not_comparable"


def test_repeats_are_not_treated_as_independent_samples():
    """Two repeats are pooled by method for description only; no inferential statistics appear."""
    payload = json.loads(
        (metrics.OUT_DIR / "metrics_semantic_vs_strict.json").read_text(encoding="utf-8")
    )
    forbidden = ("p_value", "pvalue", "confidence_interval", "ci95", "significance", "std_error")
    text = json.dumps(payload, ensure_ascii=False).lower()
    for token in forbidden:
        assert token not in text, f"metrics payload must not contain {token}"
    assert payload["no_new_model_calls"] is True


def test_mixing_reference_sets_is_rejected(tmp_path, monkeypatch):
    rows = full_rows(**{"0": make_judgement("X-R1", "full", "conforms")})
    write_case(tmp_path, "P08", rows)
    monkeypatch.setattr(metrics, "GRAPHS", [("P08", "direct", 1)])
    monkeypatch.setattr(metrics, "PACKET_DIRS", {"P08": "review_merged"})
    result = metrics.validate_judgements(tmp_path)
    assert not result["ok"]
    assert any("not in this graph's reference set" in problem for problem in result["problems"])


def test_invalid_enumeration_values_are_rejected(tmp_path, monkeypatch):
    rows = full_rows(**{"0": make_judgement("R00", "mostly_there", "conforms")})
    write_case(tmp_path, "P08", rows)
    monkeypatch.setattr(metrics, "GRAPHS", [("P08", "direct", 1)])
    monkeypatch.setattr(metrics, "PACKET_DIRS", {"P08": "review_merged"})
    result = metrics.validate_judgements(tmp_path)
    assert not result["ok"]
    assert any("invalid semantic value" in problem for problem in result["problems"])


def test_protocol_states_the_two_columns_and_the_hard_constraints():
    protocol = (
        REPO
        / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/relation_semantic_review/protocol.md"
    ).read_text(encoding="utf-8")
    assert "共同语义覆盖" in protocol and "严格结构符合" in protocol
    assert "两列不得互相推导" in protocol
    assert "不得用源文补出输出没写的数学事实" in protocol
    assert "not_comparable" in protocol


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
