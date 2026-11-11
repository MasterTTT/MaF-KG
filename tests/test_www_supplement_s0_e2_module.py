"""Offline tests for the E2 prerequisite-module runner (S0, no network, no model call).

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md sections 4.1-4.5, 7.3.
Everything here runs against the frozen inputs, the frozen scripts and the frozen
reference. `urllib.request.urlopen` is patched to raise in the rehearsal tests, so
a passing suite proves the offline paths cannot reach the network.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = ROOT / "evaluation" / "www_evidence_supplement_2026-10-11" / "runner"
sys.path.insert(0, str(RUNNER_DIR))

import run_supplement_e2 as e2  # noqa: E402

TARGET_IDS = ["ku_fb8130a0dde2", "ku_da56c3fdccf2", "ku_e9f8c9c2740e", "ku_99bdf15cb98e",
              "ku_d43d41d9a5e8", "ku_bc75c2202ddf", "ku_8dfb5403bb52", "ku_c9aad5c266bb",
              "ku_f10444319956"]
REFERENCE_COVERED_TARGETS = TARGET_IDS[:4]
UNCOVERED_TARGETS = TARGET_IDS[4:]


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    """One offline prepare over the real frozen inputs, shared by the module."""
    output = tmp_path_factory.mktemp("e2_prepared") / "run"
    manifest = e2.prepare(output)
    return {"output": output, "manifest": manifest}


class FakeTransport:
    """Records the payloads and answers deterministically; never touches a socket."""

    def __init__(self, mode="mixed"):
        self.mode = mode
        self.calls = []

    def call(self, system, user, model):
        assert model == e2.MODEL
        payload = json.loads(user)
        self.calls.append({"system": system, "payload": payload, "model": model})
        if isinstance(payload, list) and payload and "eligible_targets" in payload[0]:
            task = payload[0]
            member = task["source"]["members"][0]
            target = task["eligible_targets"][0]
            return {"proposals": [{"source_ku": task["source"]["id"], "target_ku": target["id"],
                                   "witness_node_id": member["id"],
                                   "witness_quote": member["description"][:10],
                                   "reason": "描述直接使用该成员"}]}, {"prompt_tokens": 10,
                                                                         "completion_tokens": 5}
        rows = []
        for entry in payload:
            if self.mode == "abstain":
                rows.append({"index": entry["index"], "role": "uncertain", "directness": None,
                             "reason": "信息不足"})
            elif entry["index"] % 3 == 0:
                rows.append({"index": entry["index"], "role": "required", "directness": "direct",
                             "reason": "直接前提"})
            elif entry["index"] % 3 == 1:
                rows.append({"index": entry["index"], "role": "required", "directness": "indirect",
                             "reason": "间接依赖"})
            else:
                rows.append({"index": entry["index"], "role": "related_or_none", "directness": None,
                             "reason": "非必需"})
        return {"verdicts": rows}, {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.fixture(scope="module")
def rehearsed(tmp_path_factory):
    """An offline rehearsal of arm P and arm P+S with an injected transport."""
    output = tmp_path_factory.mktemp("e2_rehearsal") / "run"
    manifest = e2.prepare(output)
    transports = {}
    for arm in ("P", "P+S"):
        transport = FakeTransport()
        transports[arm] = transport
        e2.run_module(output, arm, 1, transport=transport)
    return {"output": output, "manifest": manifest, "transports": transports}


@pytest.fixture(scope="module")
def reference():
    return e2.load_reference(e2.DEFAULT_REFERENCE)


@pytest.fixture(scope="module")
def catalogue():
    return e2.read_json(e2.FROZEN_CATALOG)


@pytest.fixture(scope="module")
def selection():
    return e2.read_json(e2.TARGET_SELECTION)


# ---------------------------------------------------------------------------
# 1. target selection
# ---------------------------------------------------------------------------

def test_target_selection_picks_the_nine_real_ku_ids(selection, catalogue):
    ids = e2.select_targets(selection, [u["id"] for u in catalogue["knowledge_units"]])
    assert ids == TARGET_IDS
    assert len(ids) == 9
    assert len(set(ids)) == 9
    names = {u["id"]: u["name"] for u in catalogue["knowledge_units"]}
    assert names["ku_da56c3fdccf2"] == "罗尔定理"


def test_target_selection_is_not_the_legacy_job_selector(selection, catalogue):
    ids = e2.select_targets(selection, [u["id"] for u in catalogue["knowledge_units"]])
    legacy = e2.legacy_job_selector_targets(catalogue)
    assert legacy["used"] is False
    assert legacy["resolved_unit_ids"] == ["ku_bc75c2202ddf", "ku_e9f8c9c2740e",
                                           "ku_7e5eb485bdb4", "ku_6593bc618737",
                                           "ku_7bead3dccdd8"]
    assert set(legacy["resolved_unit_ids"]) != set(ids)
    assert "ku_fb8130a0dde2" in ids and "ku_fb8130a0dde2" not in legacy["resolved_unit_ids"]


def test_target_selection_fails_loudly_on_unknown_id(selection, catalogue):
    catalogue_ids = [u["id"] for u in catalogue["knowledge_units"]]
    broken = json.loads(json.dumps(selection))
    broken["targets"][0]["unit_id"] = "ku_not_a_real_unit"
    with pytest.raises(ValueError, match="unknown target unit_id"):
        e2.select_targets(broken, catalogue_ids)


def test_target_selection_fails_loudly_on_wrong_count(selection, catalogue):
    catalogue_ids = [u["id"] for u in catalogue["knowledge_units"]]
    broken = json.loads(json.dumps(selection))
    broken["targets"] = broken["targets"][:8]
    with pytest.raises(ValueError, match="expects 9 targets"):
        e2.select_targets(broken, catalogue_ids)
    broken = json.loads(json.dumps(selection))
    broken["targets"][1]["unit_id"] = broken["targets"][0]["unit_id"]
    with pytest.raises(ValueError, match="duplicate target unit_id"):
        e2.select_targets(broken, catalogue_ids)


def test_duplicate_target_ids_are_rejected(selection, catalogue):
    catalogue_ids = [u["id"] for u in catalogue["knowledge_units"]]
    broken = json.loads(json.dumps(selection))
    broken["targets"].append(dict(broken["targets"][0]))
    with pytest.raises(ValueError):
        e2.select_targets(broken, catalogue_ids)


# ---------------------------------------------------------------------------
# 2. reference pair field conversion and direction
# ---------------------------------------------------------------------------

def test_pair_field_conversion_uses_the_reference_direction(reference):
    pair = next(p for p in reference["pairs"] if p["id"] == "N16")
    assert (pair["target_ku"], pair["prerequisite_ku"]) == ("ku_e9f8c9c2740e", "ku_fb8130a0dde2")
    edge = e2.edge_from_reference_pair(pair)
    assert edge == {"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_fb8130a0dde2"}
    assert e2.reference_pair_from_edge(edge) == {"target_ku": "ku_e9f8c9c2740e",
                                                 "prerequisite_ku": "ku_fb8130a0dde2"}
    assert e2.canonical_pair(edge) == ("ku_fb8130a0dde2", "ku_e9f8c9c2740e")


def test_deliberately_reversed_pair_is_caught(reference):
    pairs = reference["pairs"]
    e2.assert_pair_direction(pairs, TARGET_IDS)
    reversed_pairs = [{"id": p["id"], "target_ku": p["prerequisite_ku"],
                       "prerequisite_ku": p["target_ku"], "role": p["role"],
                       "directness": p["directness"]} for p in pairs]
    with pytest.raises(ValueError, match="direction looks reversed"):
        e2.assert_pair_direction(reversed_pairs, TARGET_IDS)
    partly = [dict(p) for p in pairs]
    partly[0] = {"id": "X", "target_ku": "ku_09206fe29015", "prerequisite_ku": "ku_fb8130a0dde2",
                 "role": "related_or_none", "directness": None}
    with pytest.raises(ValueError, match="not one of the nine selected targets"):
        e2.assert_pair_direction(partly, TARGET_IDS)


def test_reference_must_use_pairs_not_annotations():
    legacy = {"annotations": [{"prerequisite_ku": "a", "target_ku": "b", "role": "required",
                              "directness": "direct", "provenance": "draft"}]}
    with pytest.raises(ValueError, match="top-level 'pairs'"):
        e2.reference_pairs(legacy)
    with pytest.raises(ValueError, match="top-level 'pairs'"):
        e2.reference_pairs({"annotationz": 1})
    with pytest.raises(ValueError, match="invalid role"):
        e2.reference_pairs({"pairs": [{"id": "N01", "target_ku": "a", "prerequisite_ku": "b",
                                       "role": "accept"}]})
    with pytest.raises(ValueError, match="needs directness"):
        e2.reference_pairs({"pairs": [{"id": "N01", "target_ku": "a", "prerequisite_ku": "b",
                                       "role": "required", "directness": None}]})
    with pytest.raises(ValueError, match="cannot carry directness"):
        e2.reference_pairs({"pairs": [{"id": "N01", "target_ku": "a", "prerequisite_ku": "b",
                                       "role": "related_or_none", "directness": "direct"}]})


def test_unknown_endpoint_ku_id_is_rejected(reference, catalogue):
    catalogue_ids = [unit["id"] for unit in catalogue["knowledge_units"]]
    rows = [{"source_ku": "ku_fb8130a0dde2", "target_ku": "ku_ghost"}]
    with pytest.raises(ValueError, match="unknown endpoint KU id"):
        e2.assert_known_endpoints(rows, catalogue_ids, "candidates")
    with pytest.raises(ValueError, match="self loop"):
        e2.assert_known_endpoints([{"source_ku": "ku_fb8130a0dde2",
                                    "target_ku": "ku_fb8130a0dde2"}], catalogue_ids, "candidates")
    pairs = json.loads(json.dumps(reference["pairs"]))
    pairs[0]["prerequisite_ku"] = "ku_ghost"
    edges = [e2.edge_from_reference_pair(pair) for pair in pairs]
    with pytest.raises(ValueError, match="unknown endpoint KU id"):
        e2.assert_known_endpoints(edges, catalogue_ids, "reference")


# ---------------------------------------------------------------------------
# 3. semantic proposals: eligibility, cap, witness
# ---------------------------------------------------------------------------

def semantic_fixture(catalogue):
    units = {u["id"]: u for u in catalogue["knowledge_units"]}
    tasks = e2.build_semantic_tasks(catalogue, TARGET_IDS)
    return tasks, units


def genuine_witness(tasks, task_index=0):
    task = tasks[task_index]
    member = task["source"]["members"][0]
    quote = member["description"][:12]
    target = task["eligible_targets"][0]["id"]
    return {"source_ku": task["source"]["id"], "target_ku": target,
            "witness_node_id": member["id"], "witness_quote": quote,
            "reason": "目标描述直接使用该成员的内容"}, task


def test_semantic_tasks_follow_the_frozen_proposer_contract(catalogue):
    tasks, _ = semantic_fixture(catalogue)
    assert len(tasks) == 9
    assert [t["source"]["id"] for t in tasks] == TARGET_IDS
    for task in tasks:
        e2.assert_keys(task, e2.PROPOSER_TASK_KEYS, "task")
        assert task["eligible_targets"], "every target has at least one earlier KU"
    # the earlier-KU rule: no eligible target may sit at or after the goal
    catalogue_units = catalogue["knowledge_units"]
    chapters = list(dict.fromkeys(u["chapter"] for u in catalogue_units))
    by_id = {u["id"]: u for u in catalogue_units}
    for task in tasks:
        goal = e2.chapter_position(by_id[task["source"]["id"]], chapters)
        for target in task["eligible_targets"]:
            assert e2.chapter_position(by_id[target["id"]], chapters) < goal
    # the goal text itself is an input; the reference is not
    e2.assert_proposer_payload(tasks, where="test")


def test_genuine_witness_is_accepted(catalogue):
    tasks, units = semantic_fixture(catalogue)
    proposal, _ = genuine_witness(tasks)
    result = e2.validate_semantic_proposals({"proposals": [proposal]}, tasks, units)
    assert result["counts"] == {"proposals": 1, "accepted": 1, "rejected": 0}
    assert result["accepted"][0]["source_ku"] == proposal["source_ku"]
    assert result["witnesses_exact"] is True


def test_witness_that_is_not_a_verbatim_substring_is_rejected(catalogue):
    tasks, units = semantic_fixture(catalogue)
    proposal, task = genuine_witness(tasks)
    member = next(m for m in task["source"]["members"] if m["id"] == proposal["witness_node_id"])
    proposal["witness_quote"] = member["description"][:6] + "改写过的内容"
    result = e2.validate_semantic_proposals({"proposals": [proposal]}, tasks, units)
    assert result["counts"] == {"proposals": 1, "accepted": 0, "rejected": 1}
    assert "verbatim" in result["rejected"][0]["rejection_reason"]

    proposal, task = genuine_witness(tasks)
    proposal["witness_quote"] = "短"
    result = e2.validate_semantic_proposals({"proposals": [proposal]}, tasks, units)
    assert result["counts"]["rejected"] == 1
    assert "shorter than" in result["rejected"][0]["rejection_reason"]

    proposal, task = genuine_witness(tasks)
    proposal["witness_node_id"] = "node_not_in_source"
    result = e2.validate_semantic_proposals({"proposals": [proposal]}, tasks, units)
    assert result["counts"]["rejected"] == 1
    assert "not a source member" in result["rejected"][0]["rejection_reason"]


def test_witness_rejection_can_hard_stop(catalogue):
    tasks, units = semantic_fixture(catalogue)
    proposal, _ = genuine_witness(tasks)
    proposal["witness_quote"] = "not in any description"
    with pytest.raises(ValueError, match="proposal contract violation"):
        e2.validate_semantic_proposals({"proposals": [proposal]}, tasks, units, policy="abort")


def test_per_target_cap_of_three(catalogue):
    tasks, units = semantic_fixture(catalogue)
    task = tasks[0]
    member = task["source"]["members"][0]
    rows = []
    for target in task["eligible_targets"][:4]:
        rows.append({"source_ku": task["source"]["id"], "target_ku": target["id"],
                     "witness_node_id": member["id"], "witness_quote": member["description"][:10],
                     "reason": "理由"})
    result = e2.validate_semantic_proposals({"proposals": rows}, tasks, units)
    assert result["counts"] == {"proposals": 4, "accepted": 3, "rejected": 1}
    assert "cap of 3" in result["rejected"][0]["rejection_reason"]


def test_duplicate_proposal_and_out_of_scope_target_are_rejected(catalogue):
    tasks, units = semantic_fixture(catalogue)
    proposal, _ = genuine_witness(tasks)
    result = e2.validate_semantic_proposals({"proposals": [proposal, dict(proposal)]}, tasks, units)
    assert result["counts"] == {"proposals": 2, "accepted": 1, "rejected": 1}
    assert "duplicate" in result["rejected"][0]["rejection_reason"]

    later = TARGET_IDS[3]
    bad = dict(proposal, source_ku=TARGET_IDS[0], target_ku=later)
    with pytest.raises(ValueError, match="not an earlier-KU eligible target"):
        e2.validate_semantic_proposals({"proposals": [bad]}, tasks, units)
    with pytest.raises(ValueError, match="unknown source_ku"):
        e2.validate_semantic_proposals(
            {"proposals": [dict(proposal, source_ku="ku_ghost")]}, tasks, units)


# ---------------------------------------------------------------------------
# 4. merge and dedupe
# ---------------------------------------------------------------------------

def test_duplicate_candidates_are_deduplicated_and_origins_recorded(reference):
    projection = [{"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_fb8130a0dde2",
                   "source_name": "分部积分法及其应用", "target_name": "原函数与不定积分的概念",
                   "signals": [{"type": "defined_in_terms_of"}], "chain_via": []},
                  {"source_ku": "ku_99bdf15cb98e", "target_ku": "ku_fb8130a0dde2",
                   "source_name": "牛顿-莱布尼茨公式", "target_name": "原函数与不定积分的概念",
                   "signals": [], "chain_via": []}]
    semantic = [{"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_fb8130a0dde2",
                 "source_name": "分部积分法及其应用", "target_name": "原函数与不定积分的概念",
                 "signals": [], "chain_via": [], "origin": "semantic", "sources": ["semantic"],
                 "proposal_reason": "重复", "proposal_witness": {"node_id": "n", "quote": "q"}},
                {"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_29e511730e46",
                 "source_name": "分部积分法及其应用", "target_name": "基本积分表",
                 "signals": [], "chain_via": [], "origin": "semantic", "sources": ["semantic"],
                 "proposal_reason": "新增", "proposal_witness": {"node_id": "n", "quote": "q"}}]
    merged, log = e2.merge_candidates(projection, semantic)
    assert len(merged) == 3
    by_key = {e2.canonical_pair(row): row for row in merged}
    duplicate = by_key[("ku_fb8130a0dde2", "ku_e9f8c9c2740e")]
    assert duplicate["sources"] == ["projection", "semantic"]
    assert duplicate["also_proposed_semantically"] is True
    assert by_key[("ku_29e511730e46", "ku_e9f8c9c2740e")]["sources"] == ["semantic"]
    assert by_key[("ku_fb8130a0dde2", "ku_99bdf15cb98e")]["sources"] == ["projection"]
    duplicate_log = [row for row in log if row["duplicate_status"] == "duplicate_of_projection"]
    assert len(duplicate_log) == 1
    assert duplicate_log[0]["duplicate_of"] == "ku_fb8130a0dde2->ku_e9f8c9c2740e"
    e2.assert_no_injected_pairs(merged, projection, semantic)
    with pytest.raises(ValueError, match="no projection or semantic origin"):
        e2.assert_no_injected_pairs(merged + [{"source_ku": "ku_fb8130a0dde2",
                                               "target_ku": "ku_8dfb5403bb52",
                                               "sources": ["manual"]}], projection, semantic)


def test_merge_does_not_read_reference_pairs(reference):
    """A reference pair absent from both stages must not appear in the pool."""
    gold = e2.canonical_pair(e2.edge_from_reference_pair(
        next(p for p in reference["pairs"] if p["id"] == "N18")))
    merged, _ = e2.merge_candidates([], [])
    assert gold not in {e2.canonical_pair(row) for row in merged}


# ---------------------------------------------------------------------------
# 5. reference-payload guard
# ---------------------------------------------------------------------------

def test_reference_free_guard_rejects_role_label(reference):
    clean = {"proposals": [{"source_ku": "a", "target_ku": "b", "reason": "普通理由"}]}
    e2.assert_reference_free(clean, reference=reference, where="clean")
    with pytest.raises(ValueError, match="forbidden reference/adjudication key 'role'"):
        e2.assert_reference_free({"items": [{"index": 0, "role": "required"}]},
                                 reference=reference, where="role")
    with pytest.raises(ValueError, match="reference role or adjudication value"):
        e2.assert_reference_free({"note": "principle_support"}, reference=reference, where="value")
    with pytest.raises(ValueError, match="forbidden reference/adjudication key 'directness'"):
        e2.assert_reference_free({"x": [{"directness": "direct"}]}, reference=reference, where="nested")


def test_reference_free_guard_rejects_adjudication_fields_and_pair_ids(reference):
    for key in ("provenance", "adjudication_reason", "author_confirmation", "lead_override",
                "missed_pairs", "unlisted_pairs", "proposal_witness", "v2_role", "required_scope"):
        with pytest.raises(ValueError, match="forbidden reference/adjudication key"):
            e2.assert_reference_free({"outer": {key: 1}}, reference=reference, where=key)
    with pytest.raises(ValueError, match="reference pair id"):
        e2.assert_reference_free({"edge": "N16 is a direct prerequisite"},
                                 reference=reference, where="pair_id")
    with pytest.raises(ValueError, match="adjudication phrase"):
        e2.assert_reference_free({"hint": "this is a missing prerequisite"},
                                 reference=reference, where="phrase")
    with pytest.raises(ValueError, match="adjudication phrase"):
        e2.assert_reference_free({"hint": "这是缺失先修"}, reference=reference, where="zh_phrase")


def test_review_payload_shape_rejects_proposer_fields(reference):
    endpoint = {"id": "s", "name": "n", "goal": "g", "centers": [], "members": []}
    batch = [{"index": 0, "edge": "A depends_on B", "source": endpoint, "target": endpoint,
              "signals": [], "chain_via": []}]
    e2.assert_review_payload(batch, reference=reference, where="clean")
    with pytest.raises(ValueError, match="outside the frozen payload shape"):
        e2.assert_review_payload([dict(batch[0], n_edges=3)], reference=reference, where="extra_key")
    with pytest.raises(ValueError, match="forbidden reference/adjudication key 'proposal_reason'"):
        e2.assert_review_payload([dict(batch[0], proposal_reason="因为")], reference=reference,
                                 where="with_reason")
    with pytest.raises(ValueError, match="forbidden reference/adjudication key 'proposal_witness'"):
        e2.assert_review_payload([dict(batch[0], proposal_witness={"quote": "q"})],
                                 reference=reference, where="with_witness")


# ---------------------------------------------------------------------------
# 6. review validation and accepted edges
# ---------------------------------------------------------------------------

def review_batch_fixture():
    units = {"ku_a": {"id": "ku_a", "name": "A", "description": "目标", "centers_on": [],
                      "full_member_nodes": []},
             "ku_b": {"id": "ku_b", "name": "B", "description": "先修", "centers_on": [],
                      "full_member_nodes": []}}
    merged = [{"source_ku": "ku_a", "target_ku": "ku_b", "source_name": "A", "target_name": "B",
               "signals": [], "chain_via": []}]
    return e2.build_review_batches(merged, units)[0]


def test_review_validation_checks_index_role_and_directness():
    batch = review_batch_fixture()
    rows = e2.validate_review_answer({"verdicts": [{"index": 0, "role": "required",
                                                    "directness": "direct", "reason": "r"}]}, batch)
    assert rows == [{"index": 0, "source_ku": "ku_a", "target_ku": "ku_b",
                     "label": "required", "reason": "r", "directness": "direct"}]
    with pytest.raises(ValueError, match="cover every index"):
        e2.validate_review_answer({"verdicts": []}, batch)
    with pytest.raises(ValueError, match="cover every index"):
        e2.validate_review_answer({"verdicts": [{"index": 3, "role": "uncertain",
                                                 "directness": None, "reason": "r"}]}, batch)
    with pytest.raises(ValueError, match="invalid review role"):
        e2.validate_review_answer({"verdicts": [{"index": 0, "role": "accept",
                                                 "directness": None, "reason": "r"}]}, batch)
    with pytest.raises(ValueError, match="needs directness"):
        e2.validate_review_answer({"verdicts": [{"index": 0, "role": "required",
                                                 "directness": None, "reason": "r"}]}, batch)
    with pytest.raises(ValueError, match="cannot carry directness"):
        e2.validate_review_answer({"verdicts": [{"index": 0, "role": "principle_support",
                                                 "directness": "direct", "reason": "r"}]}, batch)
    with pytest.raises(ValueError, match="non-empty reason"):
        e2.validate_review_answer({"verdicts": [{"index": 0, "role": "uncertain",
                                                 "directness": None, "reason": " "}]}, batch)


def test_review_batch_cap_is_eight_and_indices_are_global():
    units = {f"ku_{i}": {"id": f"ku_{i}", "name": str(i), "description": "", "centers_on": [],
                         "full_member_nodes": []} for i in range(20)}
    merged = [{"source_ku": f"ku_{i}", "target_ku": "ku_0", "source_name": str(i),
               "target_name": "0", "signals": [], "chain_via": []} for i in range(1, 20)]
    batches = e2.build_review_batches(merged, units)
    assert [len(batch) for batch in batches] == [8, 8, 3]
    assert [entry["index"] for batch in batches for entry in batch] == list(range(19))


def test_only_required_direct_becomes_a_direct_edge():
    predictions = [{"source_ku": "a", "target_ku": "b", "label": "required", "directness": "direct"},
                   {"source_ku": "c", "target_ku": "d", "label": "required", "directness": "indirect"},
                   {"source_ku": "e", "target_ku": "f", "label": "principle_support"},
                   {"source_ku": "g", "target_ku": "h", "label": "proof_support"},
                   {"source_ku": "i", "target_ku": "j", "label": "related_or_none"},
                   {"source_ku": "k", "target_ku": "l", "label": "uncertain"}]
    edges = e2.accepted_direct_edges(predictions)
    assert edges == [{"source_ku": "a", "target_ku": "b"}]
    classes = e2.classify_verdicts(predictions)
    assert set(classes) == {"required_direct", "required_indirect", "principle_support",
                            "proof_support", "related_or_none", "uncertain"}


# ---------------------------------------------------------------------------
# 7. gate tests: deadline, budget, caps, call settings
# ---------------------------------------------------------------------------

def test_deadline_gate_fires(tmp_path, monkeypatch):
    native, phase, _ = e2.load_frozen_clients()
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key-not-used")
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    manifest = {"deadline": expired.isoformat(), "usd_limit": 1.0, "token_limit": 1_000_000,
                "max_tokens": e2.REVIEW_MAX_TOKENS, "timeout_seconds": e2.REVIEW_TIMEOUT_SECONDS,
                "session": "test"}
    client = phase.Native(tmp_path, manifest)
    with pytest.raises(RuntimeError, match="deadline"):
        client.call("s", "u", e2.MODEL)
    assert client.budget.data["requests"] == []


def test_deadline_must_carry_a_timezone(tmp_path):
    _, phase, _ = e2.load_frozen_clients()
    with pytest.raises(ValueError, match="timezone"):
        phase.Native(tmp_path, {"deadline": "2026-10-06T03:32:00", "usd_limit": 1.0,
                                "token_limit": 1000, "max_tokens": 100})


def test_budget_gate_fires_before_any_request(tmp_path, monkeypatch):
    native, phase, _ = e2.load_frozen_clients()
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key-not-used")
    manifest = {"deadline": datetime.max.replace(tzinfo=timezone.utc).isoformat(),
                "usd_limit": 0.0, "token_limit": 1_000_000, "max_tokens": e2.REVIEW_MAX_TOKENS,
                "timeout_seconds": e2.REVIEW_TIMEOUT_SECONDS, "session": "test"}
    client = phase.Native(tmp_path, manifest)
    with pytest.raises(ValueError, match="USD budget gate"):
        client.call("s", "u", e2.MODEL)
    small = dict(manifest, usd_limit=1.0, token_limit=10)
    client = phase.Native(tmp_path / "second", small)
    with pytest.raises(ValueError, match="token budget gate"):
        client.call("s", "u", e2.MODEL)


def test_caps_must_cover_the_measured_bound():
    bound = {"usd_bound": 0.5, "token_bound": 1000}
    e2.assert_caps_sufficient({"usd": 0.6, "tokens": 1200}, bound, "ok")
    with pytest.raises(ValueError, match="USD cap"):
        e2.assert_caps_sufficient({"usd": 0.4, "tokens": 1200}, bound, "usd")
    with pytest.raises(ValueError, match="token cap"):
        e2.assert_caps_sufficient({"usd": 0.6, "tokens": 900}, bound, "tokens")


def test_call_settings_match_the_hard_rules():
    native, phase, _ = e2.load_frozen_clients()
    manifest = {"model": e2.MODEL, "max_tokens": 12000, "timeout_seconds": 600, "attempts": 1,
                "thinking": "enabled", "reasoning_effort": "low", "temperature": None}
    e2.assert_call_settings(manifest, native)
    assert e2.MODEL == "deepseek-v4-flash"
    assert e2.REVIEW_BATCH_CAP == 8
    route, body = native.payload(e2.MODEL, "system", "user", e2.REVIEW_MAX_TOKENS)
    assert route == "chat/completions"
    assert body["max_tokens"] == 12000
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert "temperature" not in body
    assert body["stream"] is False
    for field, wrong in (("max_tokens", 8000), ("attempts", 2), ("timeout_seconds", 300),
                         ("reasoning_effort", "high"), ("temperature", 0)):
        with pytest.raises(ValueError, match="call settings drifted"):
            e2.assert_call_settings(dict(manifest, **{field: wrong}), native)


# ---------------------------------------------------------------------------
# 8. prepare-level offline invariants
# ---------------------------------------------------------------------------

def test_prepare_freezes_inputs_targets_and_projection(prepared, catalogue):
    manifest = prepared["manifest"]
    assert manifest["status"] == "prepared"
    assert manifest["no_model_calls"] is True
    counts = manifest["frozen_inputs"]["counts"]
    assert counts == {"catalogue_knowledge_units": 28, "ku_knowledge_units": 28,
                      "graph_nodes": 189, "graph_relations": 226}
    assert all(row["declared_match"] for row in manifest["frozen_inputs"]["files"])
    assert manifest["target_selection"]["unit_ids"] == TARGET_IDS
    assert manifest["projection"]["total_candidates"] == 37
    assert manifest["projection"]["in_scope_candidates"] == 22
    assert manifest["projection"]["reproduces_pre_registered_file"] is True
    assert manifest["projection"]["per_target_counts"] == {
        "ku_fb8130a0dde2": 0, "ku_da56c3fdccf2": 2, "ku_e9f8c9c2740e": 1, "ku_99bdf15cb98e": 2,
        "ku_d43d41d9a5e8": 6, "ku_bc75c2202ddf": 4, "ku_8dfb5403bb52": 3, "ku_c9aad5c266bb": 2,
        "ku_f10444319956": 2}
    assert manifest["legacy_job_selector"]["used"] is False
    guard = manifest["foundation_guard"]
    assert guard["present_in_catalogue"] == []
    assert guard["fires_on_this_catalogue"] is False
    assert guard["whitelist_present"] is False
    assert guard["guard_source_lines"], "the guard source lines must be recorded, not assumed"
    assert e2.foundation_guard_trace(catalogue)["fires_on_this_catalogue"] is False


def test_prepare_registers_an_expiring_deadline(prepared):
    manifest = prepared["manifest"]
    deadline = datetime.fromisoformat(manifest["deadline"]["iso"])
    created = datetime.fromisoformat(manifest["created_at"])
    assert deadline.tzinfo is not None
    assert deadline > created
    assert deadline < created + timedelta(hours=48)
    assert "2026-10-06" not in manifest["deadline"]["iso"]
    assert manifest["deadline"]["hours"] == e2.DEFAULT_DEADLINE_HOURS


def test_prepared_payloads_are_reference_free_and_frozen(prepared, reference):
    output = prepared["output"]
    batches = sorted((output / "plan").rglob("review_batch_*.json"))
    assert len(batches) == 12  # two arms x two repeats x three frozen projection batches
    for path in batches:
        e2.assert_review_payload(e2.read_json(path), reference=reference, where=str(path))
    for path in (output / "plan").rglob("proposal_request.json"):
        request = e2.read_json(path)
        e2.assert_proposer_payload(request["payload"], reference=reference, where=str(path))
    for arm in ("P", "P_plus_S"):
        first = e2.read_json(output / "plan" / arm / "r1" / "review_batch_001.json")
        second = e2.read_json(output / "plan" / arm / "r2" / "review_batch_001.json")
        assert first == second, "the two repeats must share a byte-identical projection payload"
    systems = {path.read_text(encoding="utf-8")
               for path in (output / "plan").rglob("review_system.txt")}
    assert len(systems) == 1, "both arms must use the same frozen review system prompt"
    system_text = systems.pop()
    assert "不要求事先掌握该内容的完整证明" in system_text
    for role in e2.REFERENCE_ROLES:
        assert role in system_text
    e2.assert_reference_free({"prompt": system_text}, reference=reference, where="review_system")


def test_prepare_accepts_a_larger_extension_reference(tmp_path, reference):
    """The real run passes an extension file; it must load, guard and score."""
    extension = {"experiment": "test-extension", "pairs": [dict(p) for p in reference["pairs"]]}
    expansion = UNCOVERED_TARGETS
    extra = []
    for index, target in enumerate(expansion):
        prerequisite = TARGET_IDS[index % 4]
        extra.append({"id": f"X{index + 1:02d}", "target_ku": target, "prerequisite_ku": prerequisite,
                      "role": "required", "directness": "direct",
                      "provenance": "user_adjudicated_new"})
    extension["pairs"].extend(extra)
    path = tmp_path / "reference_extension.json"
    path.write_text(json.dumps(extension, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    output = tmp_path / "extension_run"
    manifest = e2.prepare(output, reference_path=path)
    assert manifest["reference"]["pair_count"] == len(reference["pairs"]) + len(expansion)
    assert manifest["reference"]["path"] == str(path.resolve())
    report = e2.audit(output, reference_path=path)
    assert report["reference"]["pair_count_verified"] == len(reference["pairs"]) + len(expansion)
    assert report["scores"] == []
    loaded = e2.load_reference(path)
    covered = {p["target_ku"] for p in loaded["pairs"]}
    assert covered == set(TARGET_IDS)


def test_reference_extension_with_a_mis_anchored_pair_is_rejected(tmp_path, reference):
    extension = {"pairs": [dict(p) for p in reference["pairs"]]}
    extension["pairs"].append({"id": "R1", "target_ku": "ku_09206fe29015",
                               "prerequisite_ku": "ku_fb8130a0dde2", "role": "required",
                               "directness": "direct"})
    path = tmp_path / "mis_anchored_extension.json"
    path.write_text(json.dumps(extension, ensure_ascii=False), encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="not one of the nine selected targets"):
        e2.prepare(tmp_path / "mis_anchored_run", reference_path=path)


def test_prepare_and_audit_never_open_a_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline actions must not open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    output = tmp_path / "offline_only"
    e2.prepare(output)
    report = e2.audit(output)
    assert report["no_model_calls"] is True
    assert report["scores"] == []


def test_execute_refuses_after_the_deadline(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("execute must not reach the network once the deadline passed")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    output = tmp_path / "expired"
    e2.prepare(output, deadline_hours=-1.0)
    with pytest.raises(RuntimeError, match="deadline"):
        e2.execute(output)


def test_prepare_records_a_measured_budget_bound(prepared):
    manifest = prepared["manifest"]
    budget = manifest["budget"]
    assert set(budget["per_run"]) == {"P/r1", "P/r2", "P_plus_S/r1", "P_plus_S/r2"}
    for key, caps in budget["per_run"].items():
        run = next(row for row in manifest["runs"] if e2.arm_slug(row["arm"]) + f"/r{row['repeat']}" == key)
        assert caps["usd"] >= run["estimated_bound"]["usd"]
        assert caps["tokens"] >= run["estimated_bound"]["tokens"]
        assert run["estimated_bound"]["usd"] > 0
    assert budget["measured_bound_total"]["usd"] > 0
    assert budget["measured_bound_total"]["tokens"] > 0
    # the P+S arm carries the proposer call and up to three new candidates per target
    assert budget["per_run"]["P_plus_S/r1"]["usd"] > budget["per_run"]["P/r1"]["usd"]
    line = budget["e2_line_from_budget_plan"]
    assert isinstance(line["est_usd"], (int, float)) and isinstance(line["est_tokens"], int)
    comparison = budget["measured_bound_vs_plan_line"]
    assert comparison["usd_ratio"] > 0 and comparison["token_ratio"] > 0
    assert isinstance(comparison["measured_bound_exceeds_plan_line"], bool)
    assert budget["stage_caps"]["source"] in ("budgets/budget_plan.json caps",
                                              "runner fallback constants")
    assert budget["measured_bound_total"]["usd"] <= budget["stage_caps"]["usd"]


def test_plan_bindings_are_stable(prepared):
    manifest = prepared["manifest"]
    for run in manifest["runs"]:
        for binding in run["plan_bindings"]:
            assert e2.sha256(binding["path"]) == binding["sha256"]


# ---------------------------------------------------------------------------
# 9. offline rehearsal of one module run with an injected transport
# ---------------------------------------------------------------------------

def test_offline_rehearsal_of_both_arms_never_opens_a_socket(rehearsed, reference, monkeypatch):
    output = rehearsed["output"]
    transports = rehearsed["transports"]

    def forbidden(*args, **kwargs):
        raise AssertionError("the offline rehearsal must not open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    for arm in ("P", "P+S"):
        run_dir = Path(e2.run_entry(e2.read_json(output / "manifest.json"), arm, 1)["directory"])
        run_manifest = e2.read_json(run_dir / "run_manifest.json")
        assert run_manifest["status"] == "completed"
        assert run_manifest["reference_loaded_in_worker"] is False
        assert run_manifest["transport"] == "injected"
        predictions = e2.read_json(run_dir / "predictions.json")["verdicts"]
        candidates = e2.read_json(run_dir / "merged_candidates.json")["candidates"]
        assert len(predictions) == len(candidates)
        assert all(row["label"] in e2.REFERENCE_ROLES for row in predictions)
        assert all(("directness" in row) == (row["label"] == "required") for row in predictions)
        edges = e2.read_json(run_dir / "accepted_direct_edges.json")["edges"]
        accepted = {(row["source_ku"], row["target_ku"]) for row in edges}
        for row in predictions:
            is_direct = row["label"] == "required" and row["directness"] == "direct"
            assert ((row["source_ku"], row["target_ku"]) in accepted) == is_direct
        classes = e2.read_json(run_dir / "accepted_direct_edges.json")["classes"]
        assert "required_indirect" in classes or "principle_support" in classes or len(classes) >= 1
        diagnostics = e2.read_json(run_dir / "diagnostics_after_review.json")
        assert diagnostics["rewrites_verdicts"] is False
        for call in transports[arm].calls:
            body = call["payload"]
            if isinstance(body, list) and body and "edge" in body[0]:
                e2.assert_review_payload(body, reference=reference, where="rehearsal")
            else:
                e2.assert_proposer_payload(body, reference=reference, where="rehearsal")
    plus_dir = Path(e2.run_entry(e2.read_json(output / "manifest.json"), "P+S", 1)["directory"])
    assert (plus_dir / "proposal_validation.json").exists()
    plain_dir = Path(e2.run_entry(e2.read_json(output / "manifest.json"), "P", 1)["directory"])
    assert not (plain_dir / "proposal_validation.json").exists()
    assert "proposal_witness" not in json.dumps(
        e2.read_json(plus_dir / "merged_candidates.json"), ensure_ascii=False)
    assert "proposal_witness" in json.dumps(
        e2.read_json(plus_dir / "proposal_validation.json"), ensure_ascii=False)


def test_frozen_projection_batch_is_reused_byte_identically(rehearsed):
    output = rehearsed["output"]
    for arm in ("P", "P_plus_S"):
        run_dir = Path(e2.run_entry(e2.read_json(output / "manifest.json"),
                                    "P" if arm == "P" else "P+S", 1)["directory"])
        plan_batch = e2.read_json(output / "plan" / arm / "r1" / "review_batch_001.json")
        run_batch = e2.read_json(run_dir / "review_batch_001.json")
        assert plan_batch == run_batch
    plus_dir = Path(e2.run_entry(e2.read_json(output / "manifest.json"), "P+S", 1)["directory"])
    run_manifest = e2.read_json(plus_dir / "run_manifest.json")
    first = run_manifest["review_batches"][0]
    assert first["byte_identical_to_plan"] is True
    assert first["frozen_batch_is_exact_prefix"] is True
    # 22 projection candidates + 1 semantic proposal = 23 candidates in 3 batches of 8/8/7
    assert [batch["candidates"] for batch in run_manifest["review_batches"]] == [8, 8, 7]
    assert [batch["frozen_batch_is_exact_prefix"] for batch in run_manifest["review_batches"]] == [True] * 3
    assert [batch["byte_identical_to_plan"] for batch in run_manifest["review_batches"]] == [True, True, False]

    for arm in ("P", "P_plus_S"):
        run_dir = Path(e2.run_entry(e2.read_json(output / "manifest.json"),
                                    "P" if arm == "P" else "P+S", 1)["directory"])
        plan_batch = e2.read_json(output / "plan" / arm / "r1" / "review_batch_001.json")
        run_batch = e2.read_json(run_dir / "review_batch_001.json")
        assert plan_batch == run_batch


# ---------------------------------------------------------------------------
# 10. layered scoring: the section 4.5 denominators
# ---------------------------------------------------------------------------

def score_with(reference, candidates, verdicts, catalogue, entered_review=None,
               semantic_stage_present=True):
    _, _, scorer = e2.load_frozen_clients()
    return e2.score_layered(reference=reference, candidates=candidates, verdicts=verdicts,
                            catalogue=catalogue, target_ids=TARGET_IDS,
                            entered_review=entered_review,
                            semantic_stage_present=semantic_stage_present,
                            scorer_module=scorer)


def test_reference_denominators_are_the_section_4_5_numbers(reference):
    counts = {role: sum(1 for pair in reference["pairs"] if pair["role"] == role)
              for role in e2.REFERENCE_ROLES}
    assert counts == {"required": 10, "principle_support": 1, "proof_support": 1,
                      "related_or_none": 15, "uncertain": 1}
    direct = sum(1 for pair in reference["pairs"]
                 if pair["role"] == "required" and pair["directness"] == "direct")
    indirect = sum(1 for pair in reference["pairs"]
                   if pair["role"] == "required" and pair["directness"] == "indirect")
    assert (direct, indirect) == (7, 3)
    assert len(reference["pairs"]) - counts["uncertain"] == 27


def test_empty_pool_denominators_and_misses(reference, catalogue):
    scores = score_with(reference, [], [], catalogue, semantic_stage_present=False)
    assert scores["reference"]["direct_positives"] == 7
    assert scores["reference"]["required_positives"] == 10
    assert scores["reference"]["determinate"] == 27
    assert scores["reference"]["uncertain"] == 1
    assert scores["candidate_recall"]["merged"]["direct"] == {
        "hits": 0, "denominator": 7, "recall": 0.0, "scope": "frozen main reference pairs"}
    assert scores["candidate_recall"]["merged"]["required_total"]["denominator"] == 10
    assert scores["candidate_recall"]["merged"]["required_indirect"]["denominator"] == 3
    assert scores["review_performance"]["entered_review_and_determinate"] == 0
    assert scores["review_performance"]["requiredness"]["precision"] is None
    assert scores["end_to_end_direct"]["tp"] == 0
    assert scores["end_to_end_direct"]["fn"] == 7
    assert scores["end_to_end_direct"]["recall_denominator"] == 7
    assert scores["end_to_end_required"]["recall_denominator"] == 10
    assert scores["verdict_coverage"]["determinate_reference_pairs"] == 27
    assert scores["verdict_coverage"]["judged_and_determinate"] == 0
    assert scores["verdict_coverage"]["coverage"] == 0.0
    assert scores["direct_false_negative_breakdown"]["not_generated"] == sorted(
        f"{key[0]}->{key[1]}" for key in
        {(p["prerequisite_ku"], p["target_ku"]) for p in reference["pairs"]
         if p["role"] == "required" and p["directness"] == "direct"})


def test_candidate_recall_is_measured_per_stage(reference, catalogue):
    """N16 and N24 are the two direct positives carried by projection + semantic."""
    projection = [{"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_fb8130a0dde2",
                   "source_name": "分部积分法及其应用", "target_name": "原函数与不定积分的概念",
                   "signals": [], "chain_via": [], "origin": "projection",
                   "sources": ["projection"]},
                  {"source_ku": "ku_99bdf15cb98e", "target_ku": "ku_fb8130a0dde2",
                   "source_name": "牛顿-莱布尼茨公式", "target_name": "原函数与不定积分的概念",
                   "signals": [], "chain_via": [], "origin": "projection",
                   "sources": ["projection"]}]
    semantic = [{"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_29e511730e46",
                 "source_name": "分部积分法及其应用", "target_name": "基本积分表",
                 "signals": [], "chain_via": [], "origin": "semantic", "sources": ["semantic"]},
                {"source_ku": "ku_e9f8c9c2740e", "target_ku": "ku_fb8130a0dde2",
                 "source_name": "分部积分法及其应用", "target_name": "原函数与不定积分的概念",
                 "signals": [], "chain_via": [], "origin": "semantic", "sources": ["semantic"]}]
    merged, _ = e2.merge_candidates(projection, semantic)
    by_key = {e2.canonical_pair(row): row for row in merged}
    assert by_key[("ku_fb8130a0dde2", "ku_e9f8c9c2740e")]["sources"] == ["projection", "semantic"]
    scores = score_with(reference, merged, [], catalogue, entered_review=set())
    assert scores["candidate_recall"]["projection"]["direct"]["hits"] == 2
    assert scores["candidate_recall"]["merged"]["direct"]["hits"] == 3
    assert scores["candidate_recall"]["semantic_added"]["direct"]["hits"] == 1
    assert scores["candidate_recall"]["semantic_added"]["direct"]["denominator"] == 7
    assert scores["candidate_recall"]["projection"]["direct"]["denominator"] == 7
    assert scores["candidate_recall"]["semantic_proposed"]["direct"]["hits"] == 2
    assert scores["review_performance"]["entered_review"] == 0
    assert scores["end_to_end_direct"]["fn"] == 7


def test_ungenerated_unreviewed_and_abstained_positives_are_misses(reference, catalogue):
    direct = [p for p in reference["pairs"]
              if p["role"] == "required" and p["directness"] == "direct"]
    assert len(direct) == 7
    in_pool = direct[:3]
    candidates = [{"source_ku": p["target_ku"], "target_ku": p["prerequisite_ku"],
                   "source_name": p["target_name"], "target_name": p["prerequisite_name"],
                   "signals": [], "chain_via": [], "sources": ["projection"]} for p in in_pool]
    entered = {e2.canonical_pair(row) for row in candidates[:2]}
    verdicts = [{"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                 "label": "uncertain"} for row in candidates[:1]]
    verdicts.append({"source_ku": candidates[1]["source_ku"], "target_ku": candidates[1]["target_ku"],
                     "label": "related_or_none"})
    scores = score_with(reference, candidates, verdicts, catalogue, entered_review=entered)
    breakdown = scores["direct_false_negative_breakdown"]
    assert breakdown["not_generated"] == sorted(
        f"{p['prerequisite_ku']}->{p['target_ku']}" for p in direct[3:])
    assert breakdown["reviewed_and_abstained"] == [
        f"{in_pool[0]['prerequisite_ku']}->{in_pool[0]['target_ku']}"]
    assert breakdown["reviewed_and_not_direct"] == [
        f"{in_pool[1]['prerequisite_ku']}->{in_pool[1]['target_ku']}"]
    assert breakdown["in_candidate_pool_not_reviewed"] == [
        f"{in_pool[2]['prerequisite_ku']}->{in_pool[2]['target_ku']}"]
    assert breakdown["total_missed"] == 7
    assert scores["end_to_end_direct"]["fn"] == 7
    assert scores["end_to_end_direct"]["tp"] == 0
    assert scores["review_performance"]["entered_review_and_determinate"] == 2
    assert scores["review_performance"]["abstained_determinate"] == 1
    assert scores["review_performance"]["judged_determinate"] == 1
    assert scores["verdict_coverage"]["judged_and_determinate"] == 1
    assert scores["verdict_coverage"]["coverage"] == pytest.approx(1 / 27)


def test_end_to_end_direct_precision_and_recall(reference, catalogue):
    direct = [p for p in reference["pairs"]
              if p["role"] == "required" and p["directness"] == "direct"]
    indirect = [p for p in reference["pairs"]
                if p["role"] == "required" and p["directness"] == "indirect"]
    support = [p for p in reference["pairs"] if p["role"] in e2.SUPPORT_ROLES]
    rows = direct[:2] + indirect[:1] + support[:1]
    candidates = [{"source_ku": p["target_ku"], "target_ku": p["prerequisite_ku"],
                   "source_name": p["target_name"], "target_name": p["prerequisite_name"],
                   "signals": [], "chain_via": [], "sources": ["projection"]} for p in rows]
    verdicts = [{"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                 "label": "required", "directness": "direct"} for row in candidates]
    scores = score_with(reference, candidates, verdicts, catalogue)
    e2e = scores["end_to_end_direct"]
    assert (e2e["tp"], e2e["fp"], e2e["fn"]) == (2, 2, 5)
    assert e2e["precision"] == pytest.approx(0.5)
    assert e2e["recall"] == pytest.approx(2 / 7)
    assert e2e["f1"] == pytest.approx(2 * 2 / (2 * 2 + 2 + 5))
    assert scores["support_false_accepts"]["as_direct"]["count"] == 1
    assert scores["support_false_accepts"]["as_direct"]["denominator"] == 2
    assert scores["support_false_accepts"]["indirect_upgraded_to_direct"]["count"] == 1
    assert scores["support_false_accepts"]["indirect_upgraded_to_direct"]["denominator"] == 3
    assert scores["end_to_end_required"]["tp"] == 3
    assert scores["end_to_end_required"]["fp"] == 1
    assert scores["end_to_end_required"]["recall"] == pytest.approx(3 / 10)


def test_out_of_reference_accepts_do_not_claim_whole_catalogue_precision(reference, catalogue):
    direct = next(p for p in reference["pairs"]
                  if p["role"] == "required" and p["directness"] == "direct")
    rows = [{"source_ku": direct["target_ku"], "target_ku": direct["prerequisite_ku"],
             "source_name": direct["target_name"], "target_name": direct["prerequisite_name"],
             "signals": [], "chain_via": [], "sources": ["projection"]},
            {"source_ku": "ku_99bdf15cb98e", "target_ku": "ku_02887cd3accc",
             "source_name": "牛顿-莱布尼茨公式", "target_name": "可分离变量微分方程及其求解",
             "signals": [], "chain_via": [], "sources": ["semantic"]}]
    verdicts = [{"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                 "label": "required", "directness": "direct"} for row in rows]
    scores = score_with(reference, rows, verdicts, catalogue)
    assert scores["end_to_end_direct"]["precision"] == 1.0
    assert scores["end_to_end_direct"]["precision_denominator"] == 1
    out = scores["out_of_reference_predictions"]
    assert out["count_direct"] == 1
    assert out["accepted_direct"] == ["ku_02887cd3accc->ku_99bdf15cb98e"]
    assert out["sensitivity_precision_if_counted_as_false_positives"] == pytest.approx(0.5)
    assert "not a whole-catalogue precision" in out["policy"]


def test_verdict_coverage_is_not_one_minus_abstention(reference, catalogue):
    determinate = [p for p in reference["pairs"] if p["role"] != "uncertain"]
    rows = determinate[:12]
    candidates = [{"source_ku": p["target_ku"], "target_ku": p["prerequisite_ku"],
                   "source_name": p["target_name"], "target_name": p["prerequisite_name"],
                   "signals": [], "chain_via": [], "sources": ["projection"]} for p in rows]
    verdicts = []
    for index, row in enumerate(candidates):
        if index < 2:
            verdicts.append({"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                             "label": "uncertain"})
        else:
            verdicts.append({"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                             "label": "related_or_none"})
    scores = score_with(reference, candidates, verdicts, catalogue)
    coverage = scores["verdict_coverage"]
    assert coverage["determinate_reference_pairs"] == 27
    assert coverage["judged_and_determinate"] == 10
    assert coverage["coverage"] == pytest.approx(10 / 27)
    assert coverage["abstention_rate_over_entered_review"] == pytest.approx(2 / 12)
    assert coverage["one_minus_abstention_rate"] == pytest.approx(1 - 2 / 12)
    assert coverage["coverage"] != pytest.approx(coverage["one_minus_abstention_rate"])
    assert "never computed as one minus the abstention rate" in coverage["warning"]


def test_review_performance_is_restricted_to_reviewed_determinate(reference, catalogue):
    direct = next(p for p in reference["pairs"]
                  if p["role"] == "required" and p["directness"] == "direct")
    other = next(p for p in reference["pairs"] if p["role"] == "related_or_none")
    rows = [direct, other]
    candidates = [{"source_ku": p["target_ku"], "target_ku": p["prerequisite_ku"],
                   "source_name": p["target_name"], "target_name": p["prerequisite_name"],
                   "signals": [], "chain_via": [], "sources": ["projection"]} for p in rows]
    entered = {e2.canonical_pair(candidates[0])}
    verdicts = [{"source_ku": candidates[0]["source_ku"], "target_ku": candidates[0]["target_ku"],
                 "label": "required", "directness": "direct"}]
    scores = score_with(reference, candidates, verdicts, catalogue, entered_review=entered)
    review = scores["review_performance"]
    assert review["entered_review"] == 1
    assert review["entered_review_and_determinate"] == 1
    assert review["requiredness"]["tp"] == 1
    assert review["requiredness"]["fp"] == 0
    assert review["directness"]["tp"] == 1
    assert "only candidates that actually entered review" in review["requiredness"]["scope"]
    assert scores["end_to_end_direct"]["fp"] == 0
    assert scores["end_to_end_direct"]["tp"] == 1
    with pytest.raises(ValueError, match="no verdict"):
        score_with(reference, candidates, [], catalogue, entered_review=entered)
    with pytest.raises(ValueError, match="never entered review"):
        score_with(reference, candidates,
                   verdicts + [{"source_ku": candidates[1]["source_ku"],
                                "target_ku": candidates[1]["target_ku"], "label": "uncertain"}],
                   catalogue, entered_review=entered)


def test_uncovered_expansion_targets_have_undefined_recall(reference, catalogue):
    scores = score_with(reference, [], [], catalogue, semantic_stage_present=False)
    for target in REFERENCE_COVERED_TARGETS:
        block = scores["candidate_recall"]["merged"]["by_target"][target]
        assert block["reference_covered"] is True
        assert block["coverage_note"] is None
    for target in UNCOVERED_TARGETS:
        block = scores["candidate_recall"]["merged"]["by_target"][target]
        assert block["reference_covered"] is False
        assert block["direct"]["denominator"] == 0
        assert block["direct"]["recall"] is None
        assert "undefined, not zero" in block["coverage_note"]


def test_scorer_matches_the_frozen_scorer_formulas(reference, catalogue):
    """The layered scorer's direct row must agree with the frozen scorer."""
    _, _, scorer = e2.load_frozen_clients()
    direct = next(p for p in reference["pairs"]
                  if p["role"] == "required" and p["directness"] == "direct")
    indirect = next(p for p in reference["pairs"]
                    if p["role"] == "required" and p["directness"] == "indirect")
    candidates = [{"source_ku": direct["target_ku"], "target_ku": direct["prerequisite_ku"],
                   "source_name": direct["target_name"], "target_name": direct["prerequisite_name"],
                   "signals": [], "chain_via": [], "sources": ["projection"]},
                  {"source_ku": indirect["target_ku"], "target_ku": indirect["prerequisite_ku"],
                   "source_name": indirect["target_name"],
                   "target_name": indirect["prerequisite_name"],
                   "signals": [], "chain_via": [], "sources": ["projection"]}]
    verdicts = [{"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                 "label": "required", "directness": "direct"} for row in candidates]
    layered = score_with(reference, candidates, verdicts, catalogue)
    frozen_input = {
        "endpoints": [{"id": unit["id"]} for unit in catalogue["knowledge_units"]],
        "candidates": [{"source_ku": row["source_ku"], "target_ku": row["target_ku"]}
                       for row in candidates],
        "predictions": {"verdicts": [{"source_ku": row["source_ku"], "target_ku": row["target_ku"],
                                      "verdict": row["label"],
                                      "directness": row.get("directness")} for row in verdicts]},
        "reference": [{"prerequisite_ku": p["prerequisite_ku"], "target_ku": p["target_ku"],
                       "role": p["role"], "directness": p["directness"],
                       "provenance": p["provenance"] or "draft"} for p in reference["pairs"]],
    }
    frozen = scorer.score_evaluation(frozen_input)
    assert frozen["end_to_end_direct"]["tp"] == layered["end_to_end_direct"]["tp"]
    assert frozen["end_to_end_direct"]["fp"] == layered["end_to_end_direct"]["fp"]
    assert frozen["end_to_end_direct"]["fn"] == layered["end_to_end_direct"]["fn"]
    assert frozen["end_to_end_direct"]["precision"] == layered["end_to_end_direct"]["precision"]
    assert frozen["end_to_end_direct"]["recall"] == layered["end_to_end_direct"]["recall"]
    assert frozen["end_to_end_direct"]["f1"] == layered["end_to_end_direct"]["f1"]
    assert frozen["candidate_direct_recall"]["denominator"] == 7
    assert frozen["candidate_direct_recall"]["hits"] == 1
    assert frozen["candidate_pool_review"]["directness"]["tp"] == layered["review_performance"]["directness"]["tp"]
    assert frozen["scope"]["determinate_reference_pair_count"] == 27


def test_structural_diagnostics_never_upgrade_an_indirect_verdict(reference, catalogue):
    _, _, scorer = e2.load_frozen_clients()
    indirect = next(p for p in reference["pairs"]
                    if p["role"] == "required" and p["directness"] == "indirect")
    prediction = {"source_ku": indirect["target_ku"], "target_ku": indirect["prerequisite_ku"],
                  "label": "required", "directness": "indirect"}
    edges = e2.accepted_direct_edges([prediction])
    assert edges == []
    diagnostics = scorer.graph_diagnostics([unit["id"] for unit in catalogue["knowledge_units"]],
                                          [(row["target_ku"], row["source_ku"]) for row in edges])
    assert diagnostics["edge_count"] == 0
    assert prediction["directness"] == "indirect"
    assert e2.accepted_direct_edges([prediction]) == []
    classes = e2.classify_verdicts([prediction])
    assert list(classes) == ["required_indirect"]


def test_audit_scores_a_rehearsal_and_marks_it_as_not_evidence(rehearsed):
    report = e2.audit(rehearsed["output"], allow_injected_transport=True)
    assert report["evidence"] is False
    assert report["evidence_runs"] == []
    assert sorted(report["injected_transport_runs"]) == ["P+S/r1", "P/r1"]
    assert report["payload_guard"]["reference_free"] is True
    assert report["plan_bindings_verified"] > 0
    assert {row["arm"] for row in report["scores"]} == {"P", "P+S"}
    assert report["by_arm"]["P"]["runs"] == 1
    assert report["by_arm"]["P+S"]["runs"] == 1
    plus = next(row["scores"] for row in report["scores"] if row["arm"] == "P+S")
    plain = next(row["scores"] for row in report["scores"] if row["arm"] == "P")
    assert plus["candidate_pool"]["semantic_added"] == 1
    assert plus["candidate_pool"]["merged"] == plain["candidate_pool"]["merged"] + 1
    assert plus["candidate_recall"]["merged"]["direct"]["hits"] >= plain["candidate_recall"]["merged"]["direct"]["hits"]
    assert (rehearsed["output"] / "offline" / "e2_audit_report.json").exists()


def test_audit_refuses_an_injected_transport_by_default(rehearsed):
    with pytest.raises(ValueError, match="not evidence"):
        e2.audit(rehearsed["output"])


def test_audit_reports_pending_runs_without_scoring(tmp_path):
    output = tmp_path / "fresh"
    e2.prepare(output)
    report = e2.audit(output)
    assert report["scores"] == []
    assert report["evidence"] is False
    assert all(row["run_status"] == "not_started" for row in report["runs"])
    assert report["by_arm"]["P"] == {"runs": 0}
