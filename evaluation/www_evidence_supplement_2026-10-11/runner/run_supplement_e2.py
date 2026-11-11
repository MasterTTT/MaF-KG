"""E2 prerequisite-module driver for the WWW evidence supplement.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md, sections 4.1-4.5 and 7.3.
Pre-registration: evaluation/www_evidence_supplement_2026-10-11/e2_prerequisite/target_selection.json

This is a NEW, independently named runner in the style of `run_supplement_e1.py`.
It does not modify the frozen pipeline. It imports the frozen modules by path and
changes ONLY

  * which units are targets (the nine real KU ids from target_selection.json,
    never the legacy three job ids ch3_s3 / ch4_s3 / ch7_s5),
  * which reference file is scored against (a parameter; the default is the
    28-pair reference_v3.json used for smoke testing),
  * where the run tree and the budget ledger live (the legacy prepare writes a
    2026-10-06 deadline and shares one ledger across rounds),
  * the review payload's evidence limits are NOT changed: the 350-character
    member description, the 2 evidence items, the 200-character quote and the
    batch cap of 8 are the frozen values, re-asserted from the frozen source
    text at prepare time.

Two arms over the same nine targets:

  P    : L1 cross-KU projection -> independent review
  P+S  : L1 projection -> independent semantic completion -> dedupe/merge ->
         independent review

Each arm runs twice: four module runs. The projection is one deterministic
candidate file shared by every run. Every proposing and reviewing call is a NEW
call: no earlier round's verdict is imported, and the worker never opens the
reference file (scoring happens only in `audit`).

Actions
-------
prepare <out>   offline. Verifies frozen input hashes and sizes, selects the
                nine targets, runs the frozen deterministic projection,
                freezes every model payload (proposer request and review
                batches) to disk, proves each payload is reference-free,
                estimates the worst-case request bound, and registers an
                expiring deadline. No network.
execute <out>   paid. Runs the four module runs through `worker` subprocesses.
worker  <out>   paid. Runs exactly one module run in its own directory and
                ledger. Test-only `transport=` injection exists for offline
                plumbing tests; never used by `execute`.
audit   <out>   offline. Re-verifies every frozen payload, then scores whatever
                module runs completed, with the section 4.5 denominators, and
                writes the offline report. No network.

Interpreter
-----------
Run with an interpreter that provides `networkx` (the frozen scorer imports it),
for example the workspace Anaconda interpreter. The bundled E1 venv
(evaluation/external_baselines_2026-10-08/runtime) has no networkx and is NOT
required here: this runner never imports the frozen extractor and never re-runs
L1 or KU extraction.

Usage
-----
python run_supplement_e2.py prepare <out> [--reference R] [--deadline-hours H]
python run_supplement_e2.py execute <out> [--arm P|P+S] [--repeat 1|2]
python run_supplement_e2.py worker  <out> --arm P|P+S --repeat 1|2
python run_supplement_e2.py audit   <out> [--reference R] [--report P]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
SUPPLEMENT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
E2_DIR = SUPPLEMENT / "e2_prerequisite"
TARGET_SELECTION = E2_DIR / "target_selection.json"
FROZEN_PROJECTION_FILE = E2_DIR / "projection_candidates_frozen.json"

FROZEN_INPUT_DIR = (WORKSPACE / "evaluation" / "prerequisite_new_targets_2026-10-07"
                    / "run02" / "frozen_inputs01")
FROZEN_CATALOG = FROZEN_INPUT_DIR / "catalog.json"
FROZEN_KU = FROZEN_INPUT_DIR / "ku.json"
FROZEN_GRAPH = FROZEN_INPUT_DIR / "graph.json"
FREEZE_RECORD = SUPPLEMENT / "freeze" / "freeze_record.json"
BUDGET_PLAN = SUPPLEMENT / "budgets" / "budget_plan.json"

DEFAULT_REFERENCE = (WORKSPACE / "evaluation" / "paper_closeout_2026-10-10" / "final_closeout_v1"
                     / "prerequisite_adjudication" / "reference_v3.json")

SCRIPTS = WORKSPACE / "scripts"
PROJECTION_SCRIPT = SCRIPTS / "build_ku_depends_candidates.py"
SEMANTIC_SCRIPT = SCRIPTS / "run_semantic_ku_candidate_experiment.py"
REVIEW_SCRIPT = SCRIPTS / "run_scoped_ku_prerequisite_review.py"
PHASE_SCRIPT = SCRIPTS / "run_ku_prerequisite_phase.py"
SCORER_SCRIPT = SCRIPTS / "score_ku_prerequisite_evaluation.py"
NATIVE_SCRIPT = SCRIPTS / "prospective_native_client.py"
RETRIEVAL_SCRIPT = SCRIPTS / "run_ku_candidate_retrieval_experiment.py"

# Plan section 4.1 / verified sizes of this frozen catalogue.
FROZEN_KU_COUNT = 28
FROZEN_NODE_COUNT = 189
FROZEN_RELATION_COUNT = 226
SELECTED_TARGET_COUNT = 9
DECLARED_PROJECTION_TOTAL = 37

# Legacy selector, recorded but never used (plan section 4.4).
LEGACY_JOB_SELECTOR = ("ch3_s3", "ch4_s3", "ch7_s5")
# The legacy projection special-cases these names (build_ku_depends_candidates.py
# lines 40, 88-91 and 115). Plan section 4.4 asks whether the guard fires here.
FOUNDATION_GUARD_NAMES = ("映射", "函数的基本概念")

MODEL = "deepseek-v4-flash"
REVIEW_MAX_TOKENS = 12000
REVIEW_TIMEOUT_SECONDS = 600
REVIEW_ATTEMPTS = 1
REVIEW_BATCH_CAP = 8
REVIEW_THINKING = "enabled"
REVIEW_REASONING_EFFORT = "low"
REVIEW_TEMPERATURE = None
SEMANTIC_MAX_PROPOSALS_PER_TARGET = 3
WITNESS_MIN_CHARS = 4

MEMBER_DESCRIPTION_CHARS = 350
MEMBER_EVIDENCE_LIMIT = 2
EVIDENCE_TEXT_CHARS = 200

ARMS = ("P", "P+S")
REPEATS = (1, 2)
ARM_SLUGS = {"P": "P", "P+S": "P_plus_S"}

REFERENCE_ROLES = ("required", "principle_support", "proof_support",
                   "related_or_none", "uncertain")
SUPPORT_ROLES = ("principle_support", "proof_support")
DIRECTNESS = ("direct", "indirect")
UNCERTAIN_ROLE = "uncertain"

STAGE_USD_CAP = 9.24          # budgets/budget_plan.json caps.stage_usd_cap
STAGE_TOKEN_CAP = 19283222    # budgets/budget_plan.json caps.stage_token_cap
DEFAULT_HEADROOM = 1.25
DEFAULT_DEADLINE_HOURS = 24.0

DEFAULT_SCOPE = (
    "New-target prerequisite module on the frozen run02 catalogue (28 KU / 189 nodes / "
    "226 relations); two arms P and P+S, two repeats each; not new L1 or KU extraction, "
    "not whole-book prerequisite coverage, and the five author-approved expansion targets "
    "have no reference pairs in the main reference."
)

# The reviewer must not see the proposer's role judgement, its reasons, or any
# "this is a missing prerequisite" hint. Two hard structural guards, both used
# as assertions and not as comments (plan sections 4.1/4.3).
REVIEW_PAYLOAD_KEYS = frozenset({"index", "edge", "source", "target", "signals", "chain_via"})
REVIEW_ENDPOINT_KEYS = frozenset({"id", "name", "goal", "centers", "members"})
REVIEW_MEMBER_KEYS = frozenset({"id", "name", "type", "description", "evidence"})
REVIEW_EVIDENCE_KEYS = frozenset({"source", "start_line", "end_line", "quote"})
PROPOSER_TASK_KEYS = frozenset({"source", "eligible_targets"})
PROPOSER_SOURCE_KEYS = frozenset({"id", "name", "goal", "members"})
PROPOSER_TARGET_KEYS = frozenset({"id", "name", "goal", "centers"})
PROPOSER_MEMBER_KEYS = frozenset({"id", "name", "description"})
PROPOSER_CENTER_KEYS = frozenset({"name", "aliases", "description"})

FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "role", "directness", "provenance", "label", "gold", "reference", "pairs",
    "annotations", "adjudication_reason", "author_confirmation", "author_decision",
    "lead_override", "v2_role", "v2_directness", "v2_reason", "required_scope",
    "scope_basis", "changed_in_v3", "proposal_confidence", "proposal_file",
    "evidence_note", "missed", "missed_items", "missed_pairs", "unlisted_pairs",
    "outside_scope", "bridges", "role_counts", "proposal_reason", "proposal_witness",
    "witness", "witness_quote", "verdict", "verdicts", "accept", "strength",
})
FORBIDDEN_VALUE_TOKENS = frozenset({
    "required", "required_direct", "required_indirect", "principle_support",
    "proof_support", "related_or_none", "uncertain", "direct", "indirect",
    "user_adjudicated_new", "user_approved_existing", "assistant_source_adjudication",
    "human_user_adjudicated", "support",
})
ADJUDICATION_PHRASES = (
    "missing prerequisite", "author adjudication", "gold label", "role label",
    "reference pair", "缺失先修", "漏检清单", "漏检", "作者裁决", "参考角色",
)
# Frozen formatter literals re-asserted from the frozen source text.
FORMATTER_GUARDS = (
    ('n.get("description", "")[:350]', MEMBER_DESCRIPTION_CHARS, "member description truncation"),
    ('n.get("evidence", [])[:2]', MEMBER_EVIDENCE_LIMIT, "member evidence limit"),
    ('e.get("text", "")[:200]', EVIDENCE_TEXT_CHARS, "evidence quote truncation"),
)
ELIGIBILITY_RULE_NEEDLE = 'chapters.index(unit["chapter"]), min(c["start_line"] for c in unit["cards"])'


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def sha256(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def load_module(path, name):
    """Import a frozen module by path; nothing is edited."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != path:
        raise RuntimeError(f"wrong module resolved: {module.__file__}")
    return module


def frozen_string_constant(path, name) -> str:
    """Read a module-level string literal out of a frozen file without importing it.

    `run_semantic_ku_candidate_experiment.py` imports the BM25 retriever, whose
    `rank_bm25` dependency is absent from this environment, so the frozen prompt
    text is taken from the frozen source by AST instead of by import. The file
    hash is recorded in the manifest, so the text is still frozen by hash.
    """
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} in {path} is not a non-empty string literal")
            return value
    raise ValueError(f"{name} not found as a module-level literal in {path}")


def ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else None


def now_iso(now=None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def posix(path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(WORKSPACE).as_posix()
    except ValueError:
        return resolved.as_posix()


def arm_slug(arm: str) -> str:
    if arm not in ARM_SLUGS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    return ARM_SLUGS[arm]


# ---------------------------------------------------------------------------
# frozen-input audit and target selection (plan sections 4.1, 4.4)
# ---------------------------------------------------------------------------

def frozen_declared_hashes() -> dict:
    if not Path(FREEZE_RECORD).exists():
        return {}
    record = read_json(FREEZE_RECORD)
    declared = {}
    for relative, item in record.get("frozen_artifacts", {}).items():
        if isinstance(item, dict) and item.get("sha256"):
            declared[relative] = item["sha256"]
    return declared


def audit_frozen_inputs() -> dict:
    """Verify the frozen catalogue, KU file and L1 graph, and their sizes."""
    catalogue = read_json(FROZEN_CATALOG)
    ku = read_json(FROZEN_KU)
    graph = read_json(FROZEN_GRAPH)
    counts = {
        "catalogue_knowledge_units": len(catalogue["knowledge_units"]),
        "ku_knowledge_units": len(ku["knowledge_units"]),
        "graph_nodes": len(graph["nodes"]),
        "graph_relations": len(graph["relations"]),
    }
    expected = {
        "catalogue_knowledge_units": FROZEN_KU_COUNT,
        "ku_knowledge_units": FROZEN_KU_COUNT,
        "graph_nodes": FROZEN_NODE_COUNT,
        "graph_relations": FROZEN_RELATION_COUNT,
    }
    if counts != expected:
        raise ValueError(f"frozen input size changed: {counts} != {expected}")
    declared = frozen_declared_hashes()
    rows = []
    for path in (FROZEN_CATALOG, FROZEN_KU, FROZEN_GRAPH):
        digest = sha256(path)
        declared_digest = declared.get(posix(path))
        rows.append({"path": posix(path), "sha256": digest,
                     "declared_sha256": declared_digest,
                     "declared_match": declared_digest is None or declared_digest.lower() == digest.lower()})
        if declared_digest is not None and declared_digest.lower() != digest.lower():
            raise ValueError(f"frozen input hash changed: {posix(path)}")
    return {"files": rows, "counts": counts, "declared_expected": expected}


def select_targets(selection: dict, catalogue_ids) -> list:
    """Select the nine targets by real KU id; fail loudly on anything else."""
    catalogue_ids = list(catalogue_ids)
    unknown_catalogue = [unit_id for unit_id in catalogue_ids if not isinstance(unit_id, str)]
    if unknown_catalogue:
        raise ValueError(f"catalogue ids must be strings: {unknown_catalogue!r}")
    rows = selection.get("targets")
    if not isinstance(rows, list):
        raise ValueError("target_selection.json needs a targets list")
    if len(rows) != SELECTED_TARGET_COUNT:
        raise ValueError(f"target selection expects {SELECTED_TARGET_COUNT} targets, got {len(rows)}")
    selected, seen = [], set()
    known = set(catalogue_ids)
    for index, row in enumerate(rows):
        unit_id = row.get("unit_id") if isinstance(row, dict) else None
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError(f"targets[{index}] needs a non-empty string unit_id")
        if unit_id in seen:
            raise ValueError(f"duplicate target unit_id: {unit_id}")
        if unit_id not in known:
            raise ValueError(f"unknown target unit_id {unit_id!r} not present in the frozen catalogue")
        seen.add(unit_id)
        selected.append(unit_id)
    if len(selected) != SELECTED_TARGET_COUNT:
        raise ValueError(f"target selection produced {len(selected)} targets")
    return selected


def legacy_job_selector_targets(catalogue) -> dict:
    """Record what the legacy job-id selector resolves to; never used to select."""
    rows = [{"unit_id": u["id"], "name": u["name"], "job_id": u["job_id"]}
            for u in catalogue["knowledge_units"] if u.get("job_id") in LEGACY_JOB_SELECTOR]
    return {"job_ids": list(LEGACY_JOB_SELECTOR), "resolved": rows,
            "resolved_unit_ids": [r["unit_id"] for r in rows], "used": False,
            "why": "plan section 4.4: the legacy semantic runner selected three job ids; "
                   "this round selects the nine real KU ids from target_selection.json"}


def foundation_guard_trace(catalogue) -> dict:
    """Whether the legacy foundation special case fires on this catalogue.

    The legacy projection drops cross-KU edges into the generic foundation
    buckets and whitelists one foundation-to-foundation edge. Plan section 4.4
    requires the actual trace rather than an assertion that the projection rule
    was verified domain-independent.
    """
    names = {unit["name"] for unit in catalogue["knowledge_units"]}
    present = sorted(names & set(FOUNDATION_GUARD_NAMES))
    lines = [number for number, line in enumerate(PROJECTION_SCRIPT.read_text(encoding="utf-8").splitlines(), 1)
             if any(name in line for name in FOUNDATION_GUARD_NAMES)]
    whitelist = ("函数的基本概念", "映射")
    return {"guard_names": list(FOUNDATION_GUARD_NAMES),
            "present_in_catalogue": present,
            "fires_on_this_catalogue": bool(present),
            "whitelist_pair": list(whitelist),
            "whitelist_present": all(name in names for name in whitelist),
            "guard_source": posix(PROJECTION_SCRIPT),
            "guard_source_lines": lines,
            "note": "on this catalogue the guard and the foundation-to-foundation whitelist are "
                    "inert, so the projection arm here is the general algorithm and NOT a verified "
                    "domain-independent projection rule"}


# ---------------------------------------------------------------------------
# reference loading, direction and leak guards (plan sections 4.1, 4.5, 7.3)
# ---------------------------------------------------------------------------

def reference_pairs(raw) -> list:
    """Map the reference's `pairs` to explicit fields; never guess field names.

    `target_ku` is the goal, `prerequisite_ku` is the prior. The mapping is
    written out field by field so a reversed file cannot be read by accident.
    """
    if isinstance(raw, dict):
        if "pairs" not in raw:
            raise ValueError("reference must carry a top-level 'pairs' list; the legacy "
                             "'annotations' reader is not used in this round")
        raw = raw["pairs"]
    if not isinstance(raw, list):
        raise ValueError("reference pairs must be a list")
    pairs = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"reference pairs[{index}] must be an object")
        target = row.get("target_ku")
        prerequisite = row.get("prerequisite_ku")
        if not isinstance(target, str) or not target:
            raise ValueError(f"reference pairs[{index}] needs a non-empty target_ku (the goal)")
        if not isinstance(prerequisite, str) or not prerequisite:
            raise ValueError(f"reference pairs[{index}] needs a non-empty prerequisite_ku (the prior)")
        role = row.get("role")
        if role not in REFERENCE_ROLES:
            raise ValueError(f"reference pairs[{index}] has invalid role {role!r}")
        directness = row.get("directness")
        if role == "required":
            if directness not in DIRECTNESS:
                raise ValueError(f"reference pairs[{index}] required needs directness direct/indirect")
        elif directness is not None:
            raise ValueError(f"reference pairs[{index}] non-required role cannot carry directness")
        pairs.append({"id": row.get("id"), "target_ku": target, "prerequisite_ku": prerequisite,
                      "target_name": row.get("target_name"), "prerequisite_name": row.get("prerequisite_name"),
                      "role": role, "directness": directness, "provenance": row.get("provenance")})
    if not pairs:
        raise ValueError("reference contains no pairs")
    return pairs


def load_reference(path) -> dict:
    path = Path(path)
    raw = read_json(path)
    pairs = reference_pairs(raw)
    return {"path": str(path.resolve()), "sha256": sha256(path), "pairs": pairs,
            "top_level_keys": sorted(raw.keys()) if isinstance(raw, dict) else None}


def edge_from_reference_pair(pair: dict) -> dict:
    """reference (prerequisite -> target) to the depends_on shape used by candidates.

    Candidates and predictions store `dependent depends_on prerequisite`, so the
    goal becomes `source_ku` and the prior becomes `target_ku`.
    """
    return {"source_ku": pair["target_ku"], "target_ku": pair["prerequisite_ku"]}


def reference_pair_from_edge(edge: dict) -> dict:
    """Inverse of `edge_from_reference_pair`: reads the reference's own direction."""
    source = edge.get("source_ku")
    target = edge.get("target_ku")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        raise ValueError("depends_on edge needs non-empty string source_ku and target_ku")
    return {"target_ku": source, "prerequisite_ku": target}


def canonical_pair(row) -> tuple:
    """Canonical (prerequisite, target) key of a depends_on-shaped row."""
    return row["target_ku"], row["source_ku"]


def assert_pair_direction(pairs, target_ids) -> dict:
    """Catch a reversed reference/pair file.

    Every pair of this round is anchored on one of the nine selected targets and
    `target_ku` is the goal. A wholesale field swap puts prerequisites in
    `target_ku`, which are almost never selected targets, so it fails loudly.
    """
    targets = set(target_ids)
    anchored = [p for p in pairs if p.get("target_ku") in targets]
    if pairs and len(anchored) * 2 < len(pairs):
        raise ValueError("reference direction looks reversed: fewer than half of the pairs have a "
                         "selected target in target_ku; target_ku must be the goal")
    offenders = [p for p in pairs if p.get("target_ku") not in targets]
    if offenders:
        sample = [p.get("id") or p.get("target_ku") for p in offenders[:5]]
        raise ValueError(f"reference pair target_ku is not one of the nine selected targets: {sample}")
    return {"rule": "target_ku is the goal and must be one of the nine selected targets",
            "checked_pairs": len(pairs), "target_count": len(targets)}


def assert_known_endpoints(rows, catalogue_ids, where: str) -> None:
    known = set(catalogue_ids)
    for index, row in enumerate(rows):
        for field in ("source_ku", "target_ku"):
            value = row.get(field)
            if value not in known:
                raise ValueError(f"{where}[{index}] unknown endpoint KU id in {field}: {value!r}")
        if row.get("source_ku") == row.get("target_ku"):
            raise ValueError(f"{where}[{index}] self loop: {row.get('source_ku')!r}")


def _iter_nodes(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            yield here, key, value
            yield from _iter_nodes(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_nodes(value, f"{path}[{index}]")


def assert_reference_free(payload, *, reference=None, where="payload") -> None:
    """Hard guard: no reference role, label, missed-item list or adjudication.

    Raises ValueError on the first offending key or value. Applied to every
    payload written for a model, in `prepare` and again in `audit`.
    """
    pair_ids = []
    if reference:
        pair_ids = [p["id"] for p in reference.get("pairs", []) if isinstance(p.get("id"), str)
                    and len(p["id"]) >= 3]
    for here, key, value in _iter_nodes(payload):
        if isinstance(key, str) and key.strip().lower() in FORBIDDEN_PAYLOAD_KEYS:
            raise ValueError(f"{where}: forbidden reference/adjudication key {key!r} at {here}")
        if not isinstance(value, str):
            continue
        token = value.strip().lower()
        if token in FORBIDDEN_VALUE_TOKENS:
            raise ValueError(f"{where}: reference role or adjudication value {value!r} at {here}")
        lowered = value.lower()
        for phrase in ADJUDICATION_PHRASES:
            if phrase in lowered:
                raise ValueError(f"{where}: adjudication phrase {phrase!r} at {here}")
        for pair_id in pair_ids:
            if re.search(rf"(?<![0-9A-Za-z]){re.escape(pair_id)}(?![0-9A-Za-z])", value, re.IGNORECASE):
                raise ValueError(f"{where}: reference pair id {pair_id!r} at {here}")


def assert_keys(node, allowed, where: str) -> None:
    if not isinstance(node, dict):
        raise ValueError(f"{where} must be an object")
    extra = sorted(set(node) - set(allowed))
    if extra:
        raise ValueError(f"{where} carries keys outside the frozen payload shape: {extra}")


def assert_proposer_payload(tasks, *, reference=None, where="proposal_input") -> None:
    assert_reference_free(tasks, reference=reference, where=where)
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{where} must be a non-empty list")
    for index, task in enumerate(tasks):
        assert_keys(task, PROPOSER_TASK_KEYS, f"{where}[{index}]")
        assert_keys(task["source"], PROPOSER_SOURCE_KEYS, f"{where}[{index}].source")
        for member in task["source"]["members"]:
            assert_keys(member, PROPOSER_MEMBER_KEYS, f"{where}[{index}].source.members")
        for target in task["eligible_targets"]:
            assert_keys(target, PROPOSER_TARGET_KEYS, f"{where}[{index}].eligible_targets")
            for center in target["centers"]:
                assert_keys(center, PROPOSER_CENTER_KEYS,
                            f"{where}[{index}].eligible_targets.centers")


def assert_review_payload(batch, *, reference=None, where="review_batch") -> None:
    """The reviewer sees candidate evidence only, never the proposer's judgement."""
    assert_reference_free(batch, reference=reference, where=where)
    if not isinstance(batch, list) or not batch:
        raise ValueError(f"{where} must be a non-empty list")
    for entry in batch:
        assert_keys(entry, REVIEW_PAYLOAD_KEYS, where)
        for side in ("source", "target"):
            endpoint = entry[side]
            assert_keys(endpoint, REVIEW_ENDPOINT_KEYS, f"{where}.{side}")
            for member in endpoint["members"]:
                assert_keys(member, REVIEW_MEMBER_KEYS, f"{where}.{side}.members")
                for item in member["evidence"]:
                    assert_keys(item, REVIEW_EVIDENCE_KEYS, f"{where}.{side}.members.evidence")


def verify_frozen_formatter(*paths) -> dict:
    """Re-assert the frozen truncation literals before any paid call."""
    checked = {}
    for path in (paths or (REVIEW_SCRIPT, RETRIEVAL_SCRIPT)):
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        for needle, value, what in FORMATTER_GUARDS:
            if needle not in text:
                raise ValueError(f"frozen formatter changed: {what} ({needle!r}) missing from {path}")
        checked[path.name] = sha256(path)
    semantic_text = Path(SEMANTIC_SCRIPT).read_text(encoding="utf-8")
    if ELIGIBILITY_RULE_NEEDLE not in semantic_text:
        raise ValueError(f"frozen earlier-KU eligibility rule changed in {SEMANTIC_SCRIPT}")
    checked[Path(SEMANTIC_SCRIPT).name] = sha256(SEMANTIC_SCRIPT)
    return checked


_EARLIER_ROUND_CACHE = None


def earlier_round_artifacts(refresh=False) -> dict:
    """Hashes of the earlier round's judgements; no run may reuse them."""
    global _EARLIER_ROUND_CACHE
    if _EARLIER_ROUND_CACHE is not None and not refresh:
        return _EARLIER_ROUND_CACHE
    roots = (WORKSPACE / "evaluation" / "prerequisite_new_targets_2026-10-07",
             WORKSPACE / "evaluation" / "paper_closeout_2026-10-10")
    hashes = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix != ".json":
                continue
            if path.name in ("predictions.json", "reference.json", "reference_v2.json"):
                hashes[sha256(path)] = posix(path)
    _EARLIER_ROUND_CACHE = hashes
    return hashes


def earlier_round_hash_file(output) -> Path:
    return Path(output) / "offline" / "earlier_round_hashes.json"


def load_earlier_round_hashes(output) -> dict:
    """The prepare-time hash list; recomputed only when the file is absent."""
    path = earlier_round_hash_file(output)
    if path.exists():
        return read_json(path)["artifacts"]
    return earlier_round_artifacts()


def assert_no_earlier_round_reuse(run_dir, hashes=None) -> None:
    """A run directory must not contain any earlier round's judgement file."""
    hashes = earlier_round_artifacts() if hashes is None else hashes
    for path in Path(run_dir).rglob("*"):
        if path.is_file() and sha256(path) in hashes:
            raise ValueError(f"{posix(path)} is a copy of an earlier round's judgement "
                             f"({hashes[sha256(path)]}); this round must not reuse it")


# ---------------------------------------------------------------------------
# projection stage (deterministic, offline; plan section 4.3 arm P)
# ---------------------------------------------------------------------------

def run_projection(*, ku_path=FROZEN_KU, graph_path=FROZEN_GRAPH, out_path, python=None) -> dict:
    """Run the frozen deterministic projection as a subprocess; no model call."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [python or sys.executable, "-X", "utf8", str(PROJECTION_SCRIPT),
               str(ku_path), str(graph_path), "--out", str(out_path)]
    completed = subprocess.run(command, check=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    if not out_path.exists():
        raise RuntimeError(f"projection produced no output: {completed.stdout[-500:]}")
    return {"command": command, "script_sha256": sha256(PROJECTION_SCRIPT),
            "stdout_tail": completed.stdout.strip().splitlines()[-3:]}


def projection_pool(projection: dict, target_ids) -> tuple:
    """Split the deterministic projection into in-scope candidates and a full log."""
    targets = list(target_ids)
    target_set = set(targets)
    rows, log = [], []
    for candidate in projection.get("candidates", []):
        source, target = candidate.get("source_ku"), candidate.get("target_ku")
        legal = source in target_set
        reason = None if legal else "source_ku is not one of the nine selected targets"
        log.append({"origin": "projection", "source_ku": source, "target_ku": target,
                    "source_name": candidate.get("source_name"), "target_name": candidate.get("target_name"),
                    "legal": legal, "rejection_reason": reason,
                    "duplicate_status": "unique", "duplicate_of": None,
                    "signals": [s.get("type") for s in candidate.get("signals", [])],
                    "chain_total": candidate.get("chain_total", 0)})
        if legal:
            rows.append({"source_ku": source, "target_ku": target,
                         "source_name": candidate.get("source_name"), "target_name": candidate.get("target_name"),
                         "signals": candidate.get("signals", []), "chain_via": candidate.get("chain_via", []),
                         "chain_total": candidate.get("chain_total", 0), "n_edges": candidate.get("n_edges", 0),
                         "origin": "projection", "sources": ["projection"]})
    seen, deduped = set(), []
    for row in rows:
        key = canonical_pair(row)
        if key in seen:
            for entry in log:
                if entry["source_ku"] == row["source_ku"] and entry["target_ku"] == row["target_ku"]:
                    entry.update(duplicate_status="duplicate_in_projection", duplicate_of=f"{key[0]}->{key[1]}")
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, log


# ---------------------------------------------------------------------------
# semantic completion stage (plan section 4.3 arm P+S)
# ---------------------------------------------------------------------------

def chapter_position(unit: dict, chapters) -> tuple:
    """Same rule as the frozen run_semantic_ku_candidate_experiment.position."""
    return chapters.index(unit["chapter"]), min(card["start_line"] for card in unit["cards"])


def build_semantic_tasks(catalogue: dict, target_ids) -> list:
    """Frozen proposer contract: real target statements, earlier-KU eligibility only."""
    units = catalogue["knowledge_units"]
    chapters = list(dict.fromkeys(unit["chapter"] for unit in units))
    tasks = []
    for unit_id in target_ids:
        unit = next(u for u in units if u["id"] == unit_id)
        center_ids = {center["id"] for center in unit["centers_on"]}
        source = {"id": unit["id"], "name": unit["name"], "goal": unit["description"],
                  "members": [{"id": node["id"], "name": node["name"],
                               "description": node.get("description", "")}
                              for node in unit["full_member_nodes"]
                              if node["id"] in center_ids or node["type"] in {"Claim", "Method"}]}
        eligible = []
        for other in units:
            if chapter_position(other, chapters) < chapter_position(unit, chapters):
                centers = {center["id"] for center in other["centers_on"]}
                eligible.append({"id": other["id"], "name": other["name"], "goal": other["description"],
                                 "centers": [{"name": node["name"], "aliases": node.get("aliases", []),
                                              "description": node.get("description", "")[:MEMBER_DESCRIPTION_CHARS]}
                                             for node in other["full_member_nodes"]
                                             if node["id"] in centers]})
        tasks.append({"source": source, "eligible_targets": eligible})
    return tasks


def validate_semantic_proposals(answer, tasks, units, *, policy="reject") -> dict:
    """Validate a proposer answer against the frozen candidate rules.

    Unknown or out-of-scope endpoints are contract violations and always raise.
    Per-candidate problems (duplicate pair, short or inexact witness, missing
    reason, per-target cap) are rejected with a recorded reason when
    `policy="reject"`, or raise when `policy="abort"`.
    """
    if policy not in ("reject", "abort"):
        raise ValueError(f"unknown proposal violation policy {policy!r}")
    if not isinstance(answer, dict) or not isinstance(answer.get("proposals"), list):
        raise ValueError("proposer answer needs a proposals list")
    task_index = {task["source"]["id"]: task for task in tasks}
    accepted, rejected, seen, counts = [], [], set(), {}
    for index, row in enumerate(answer["proposals"]):
        if not isinstance(row, dict):
            raise ValueError(f"proposals[{index}] must be an object")
        source, target = row.get("source_ku"), row.get("target_ku")
        if source not in task_index:
            raise ValueError(f"proposals[{index}] cites an unknown source_ku {source!r}")
        eligible = {t["id"] for t in task_index[source]["eligible_targets"]}
        if target not in eligible:
            raise ValueError(f"proposals[{index}] target {target!r} is not an earlier-KU "
                             f"eligible target of {source!r}")
        counts[source] = counts.get(source, 0) + 1
        key = (source, target)
        reason = None
        if key in seen:
            reason = "duplicate proposal for the same pair"
        elif counts[source] > SEMANTIC_MAX_PROPOSALS_PER_TARGET:
            reason = f"per-target cap of {SEMANTIC_MAX_PROPOSALS_PER_TARGET} proposals exceeded"
        members = {node["id"]: node for node in task_index[source]["source"]["members"]}
        node = members.get(row.get("witness_node_id"))
        quote = row.get("witness_quote")
        if reason is None:
            if node is None:
                reason = "witness_node_id is not a source member of the goal unit"
            elif not isinstance(quote, str) or len(quote) < WITNESS_MIN_CHARS:
                reason = f"witness_quote missing or shorter than {WITNESS_MIN_CHARS} characters"
            elif quote not in node.get("description", ""):
                reason = "witness_quote is not a verbatim substring of the cited member description"
            elif not isinstance(row.get("reason"), str) or not row["reason"].strip():
                reason = "proposal is missing a reason"
        if reason is not None:
            rejected.append({"origin": "semantic", "source_ku": source, "target_ku": target,
                             "index": index, "rejection_reason": reason,
                             "duplicate_status": "duplicate" if "duplicate" in reason else "unique"})
            if policy == "abort":
                raise ValueError(f"proposal contract violation: {reason}")
            seen.add(key)
            continue
        seen.add(key)
        accepted.append({"source_ku": source, "target_ku": target,
                         "source_name": units[source]["name"], "target_name": units[target]["name"],
                         "signals": [], "chain_via": [], "chain_total": 0, "n_edges": 0,
                         "origin": "semantic", "sources": ["semantic"],
                         "proposal_witness": {"node_id": node["id"], "quote": quote},
                         "proposal_reason": row["reason"]})
    return {"accepted": accepted, "rejected": rejected,
            "counts": {"proposals": len(answer["proposals"]), "accepted": len(accepted),
                       "rejected": len(rejected)},
            "witnesses_exact": not any("verbatim" in r["rejection_reason"] for r in rejected)}


MERGED_ROW_KEYS = ("source_ku", "target_ku", "source_name", "target_name", "signals",
                   "chain_via", "chain_total", "n_edges")


def merge_candidates(projection_rows, semantic_rows) -> tuple:
    """Dedupe on (goal, prior) and record which sources each survivor came from.

    The merged pool carries only the fields the reviewer payload needs; the
    proposer's witness and reason stay in `proposal_validation.json` so they can
    never ride along with a review batch.
    """

    def pool_row(row):
        entry = {key: row[key] for key in MERGED_ROW_KEYS if key in row}
        entry.setdefault("signals", [])
        entry.setdefault("chain_via", [])
        return entry

    merged, by_key, log = [], {}, []
    for row in projection_rows:
        key = canonical_pair(row)
        entry = pool_row(row)
        entry["sources"] = ["projection"]
        by_key[key] = entry
        merged.append(entry)
        log.append({"origin": "projection", "source_ku": row["source_ku"], "target_ku": row["target_ku"],
                    "legal": True, "duplicate_status": "unique", "duplicate_of": None,
                    "rejection_reason": None})
    for row in semantic_rows:
        key = canonical_pair(row)
        if key in by_key:
            by_key[key]["sources"] = sorted(set(by_key[key]["sources"]) | {"semantic"})
            by_key[key]["also_proposed_semantically"] = True
            log.append({"origin": "semantic", "source_ku": row["source_ku"], "target_ku": row["target_ku"],
                        "legal": True, "duplicate_status": "duplicate_of_projection",
                        "duplicate_of": f"{key[0]}->{key[1]}", "rejection_reason": None,
                        "kept": "projection row kept; both origins recorded"})
            continue
        entry = pool_row(row)
        entry["sources"] = ["semantic"]
        by_key[key] = entry
        merged.append(entry)
        log.append({"origin": "semantic", "source_ku": row["source_ku"], "target_ku": row["target_ku"],
                    "legal": True, "duplicate_status": "unique", "duplicate_of": None,
                    "rejection_reason": None})
    return merged, log


def assert_no_injected_pairs(merged, *sources) -> None:
    """No manual reference pair and no gold completion can enter the pool."""
    allowed = {canonical_pair(row) for group in sources for row in group}
    for row in merged:
        if canonical_pair(row) not in allowed:
            raise ValueError(f"candidate {canonical_pair(row)} has no projection or semantic origin")
        if not row.get("sources"):
            raise ValueError(f"candidate {canonical_pair(row)} lost its origin record")


# ---------------------------------------------------------------------------
# review stage (frozen system, frozen endpoint format, cap 8)
# ---------------------------------------------------------------------------

def endpoint_payload(unit: dict) -> dict:
    """Byte-equivalent to the frozen review endpoint formatter.

    Mirrors scripts/run_scoped_ku_prerequisite_review.py lines 37-45 and
    scripts/run_ku_candidate_retrieval_experiment.py lines 62-70, whose
    truncation literals are re-asserted by `verify_frozen_formatter`.
    """
    return {"id": unit["id"], "name": unit["name"], "goal": unit["description"],
            "centers": [node["id"] for node in unit["centers_on"]],
            "members": [{"id": node["id"], "name": node["name"], "type": node["type"],
                         "description": node.get("description", "")[:MEMBER_DESCRIPTION_CHARS],
                         "evidence": [{"source": Path(item.get("source_path", "")).name,
                                       "start_line": item.get("start_line"),
                                       "end_line": item.get("end_line"),
                                       "quote": item.get("text", "")[:EVIDENCE_TEXT_CHARS]}
                                      for item in node.get("evidence", [])[:MEMBER_EVIDENCE_LIMIT]]}
                        for node in unit["full_member_nodes"]]}


def build_review_batches(merged, units) -> list:
    """Global indices, frozen endpoint payloads, batch cap 8. Proposer fields are dropped."""
    entries = []
    for index, candidate in enumerate(merged):
        if candidate["source_ku"] not in units or candidate["target_ku"] not in units:
            raise ValueError(f"candidate {index} has an unknown endpoint")
        entries.append({"index": index,
                        "edge": candidate["source_name"] + " depends_on " + candidate["target_name"],
                        "source": endpoint_payload(units[candidate["source_ku"]]),
                        "target": endpoint_payload(units[candidate["target_ku"]]),
                        "signals": candidate.get("signals", []),
                        "chain_via": candidate.get("chain_via", [])})
    return [entries[start:start + REVIEW_BATCH_CAP] for start in range(0, len(entries), REVIEW_BATCH_CAP)]


def validate_review_answer(answer, batch) -> list:
    """Roles, directness and index coverage are validated exactly."""
    if not isinstance(answer, dict):
        raise ValueError("review answer must be a JSON object")
    rows = answer.get("verdicts")
    if not isinstance(rows, list):
        raise ValueError("review answer needs a verdicts list")
    expected = {entry["index"] for entry in batch}
    indices = [row.get("index") for row in rows]
    if any(type(index) is not int for index in indices):
        raise ValueError("verdict index must be an integer")
    if len(set(indices)) != len(indices) or set(indices) != expected:
        raise ValueError("verdict indices must cover every index of the batch exactly once")
    by_index = {entry["index"]: entry for entry in batch}
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("every verdict must be an object")
        role = row.get("role")
        if role not in REFERENCE_ROLES:
            raise ValueError(f"invalid review role {role!r}")
        directness = row.get("directness")
        if role == "required":
            if directness not in DIRECTNESS:
                raise ValueError("required role needs directness direct or indirect")
        elif directness is not None:
            raise ValueError("non-required role cannot carry directness")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("verdict needs a non-empty reason")
        entry = by_index[row["index"]]
        verdict = {"index": row["index"], "source_ku": entry["source"]["id"],
                   "target_ku": entry["target"]["id"], "label": role, "reason": row["reason"]}
        if role == "required":
            verdict["directness"] = directness
        out.append(verdict)
    return out


def accepted_direct_edges(predictions) -> list:
    """Only required + direct becomes a direct edge (plan section 4.3)."""
    edges = [{"source_ku": row["source_ku"], "target_ku": row["target_ku"]}
             for row in predictions
             if row.get("label") == "required" and row.get("directness") == "direct"]
    return sorted(edges, key=lambda row: (row["source_ku"], row["target_ku"]))


def classify_verdicts(predictions) -> dict:
    """required/indirect, principle_support, proof_support, related_or_none, uncertain stay separate."""
    classes = {}
    for row in predictions:
        label = row["label"]
        if label == "required":
            label = f"required_{row.get('directness')}"
        classes.setdefault(label, []).append({"source_ku": row["source_ku"], "target_ku": row["target_ku"]})
    return {label: sorted(rows, key=lambda row: (row["source_ku"], row["target_ku"]))
            for label, rows in sorted(classes.items())}


# ---------------------------------------------------------------------------
# request planning, cost bound and caps (offline, before any paid call)
# ---------------------------------------------------------------------------

def load_frozen_clients():
    """Import the frozen transport and scorer by path."""
    sys.path.insert(0, str(SCRIPTS))
    native = load_module(NATIVE_SCRIPT, "prospective_native_client")
    phase = load_module(PHASE_SCRIPT, "e2_frozen_phase")
    scorer = load_module(SCORER_SCRIPT, "e2_frozen_scorer")
    if phase.MODEL != MODEL:
        raise ValueError(f"frozen model changed: {phase.MODEL} != {MODEL}")
    return native, phase, scorer


def request_bound(native, system: str, user: str, output_limit: int = REVIEW_MAX_TOKENS) -> dict:
    """Exact pre-request bound that prospective_native_client.Budget.reserve will charge."""
    route, body = native.payload(MODEL, system, user, output_limit)
    body_bytes = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    input_bound = body_bytes + 4096
    token_bound = input_bound + output_limit
    in_rate, out_rate = native.RATES[MODEL]
    usd_bound = (input_bound * in_rate + output_limit * out_rate) / 1_000_000
    return {"route": route, "body": body, "body_bytes": body_bytes,
            "input_token_bound": input_bound, "token_bound": token_bound, "usd_bound": usd_bound}


def assert_call_settings(manifest: dict, native) -> dict:
    """Assert the frozen review call settings before any paid call."""
    expected = {"model": MODEL, "max_tokens": REVIEW_MAX_TOKENS,
                "timeout_seconds": REVIEW_TIMEOUT_SECONDS, "attempts": REVIEW_ATTEMPTS,
                "thinking": REVIEW_THINKING, "reasoning_effort": REVIEW_REASONING_EFFORT,
                "temperature": REVIEW_TEMPERATURE}
    observed = {"model": manifest.get("model"), "max_tokens": manifest.get("max_tokens"),
                "timeout_seconds": manifest.get("timeout_seconds"), "attempts": manifest.get("attempts"),
                "thinking": manifest.get("thinking"), "reasoning_effort": manifest.get("reasoning_effort"),
                "temperature": manifest.get("temperature")}
    if observed != expected:
        raise ValueError(f"call settings drifted: {observed} != {expected}")
    route, body = native.payload(MODEL, "s", "u", REVIEW_MAX_TOKENS)
    if route != "chat/completions":
        raise ValueError(f"unexpected frozen route {route!r}")
    if body.get("model") != MODEL or body.get("max_tokens") != REVIEW_MAX_TOKENS:
        raise ValueError("frozen payload does not carry the frozen model and output cap")
    if "temperature" in body:
        raise ValueError("frozen payload must not carry a temperature")
    if body.get("thinking") != {"type": REVIEW_THINKING} or body.get("reasoning_effort") != REVIEW_REASONING_EFFORT:
        raise ValueError("frozen payload does not carry thinking enabled and reasoning effort low")
    if body.get("stream") is not False:
        raise ValueError("frozen payload must not stream")
    return {"expected": expected, "body_keys": sorted(body)}


def assert_caps_sufficient(caps: dict, bound: dict, where: str) -> None:
    if caps["usd"] + 1e-12 < bound["usd_bound"]:
        raise ValueError(f"{where}: USD cap {caps['usd']} is below the required pre-request "
                         f"bound {bound['usd_bound']:.4f}")
    if caps["tokens"] < bound["token_bound"]:
        raise ValueError(f"{where}: token cap {caps['tokens']} is below the required pre-request "
                         f"bound {bound['token_bound']}")


# ---------------------------------------------------------------------------
# prepared plan (frozen payloads) and prepare action
# ---------------------------------------------------------------------------

def plan_projection_batches(merged, units, native, review_system, plan_dir, *, reference) -> dict:
    """Freeze the projection review payloads byte-for-byte before any paid call."""
    batches = build_review_batches(merged, units)
    rows, total_usd, total_tokens, total_bytes = [], 0.0, 0, 0
    for number, batch in enumerate(batches, start=1):
        where = f"plan/{plan_dir.name}/review_batch_{number:03d}"
        assert_review_payload(batch, reference=reference, where=where)
        path = plan_dir / f"review_batch_{number:03d}.json"
        write_json(path, batch)
        bound = request_bound(native, review_system, json.dumps(batch, ensure_ascii=False))
        total_usd += bound["usd_bound"]
        total_tokens += bound["token_bound"]
        total_bytes += bound["body_bytes"]
        rows.append({"number": number, "path": posix(path), "sha256": sha256(path),
                     "candidate_indices": [entry["index"] for entry in batch],
                     "candidates": len(batch), "body_bytes": bound["body_bytes"],
                     "token_bound": bound["token_bound"], "usd_bound": bound["usd_bound"]})
    return {"batches": rows, "candidates": len(merged), "usd_bound": total_usd,
            "token_bound": total_tokens, "body_bytes": total_bytes}


def plan_semantic_worst_case(merged, units, tasks, native, review_system, *, reference) -> dict:
    """Worst-case bound for the P+S run: at most 3 new candidates per target.

    The estimate is built from the largest eligible real (goal, prior) pairs so
    the bound does not depend on what the proposer will actually say.
    """
    units_by_id = {unit["id"]: unit for unit in units}
    seen = {canonical_pair(row) for row in merged}
    synthetic = []
    for task in tasks:
        source = task["source"]["id"]
        eligible = [target for target in task["eligible_targets"] if (target["id"], source) not in seen]
        for target in eligible[:SEMANTIC_MAX_PROPOSALS_PER_TARGET]:
            synthetic.append({"source_ku": source, "target_ku": target["id"],
                              "source_name": task["source"]["name"], "target_name": target["name"],
                              "signals": [], "chain_via": [], "origin": "semantic",
                              "sources": ["semantic"]})
    # Batch boundaries depend on the merged order, so simulate them exactly.
    all_rows = list(merged) + synthetic
    batches = []
    for start in range(0, len(all_rows), REVIEW_BATCH_CAP):
        chunk = all_rows[start:start + REVIEW_BATCH_CAP]
        entries = [{"index": start + offset,
                    "edge": row["source_name"] + " depends_on " + row["target_name"],
                    "source": endpoint_payload(units_by_id[row["source_ku"]]),
                    "target": endpoint_payload(units_by_id[row["target_ku"]]),
                    "signals": row.get("signals", []), "chain_via": row.get("chain_via", [])}
                   for offset, row in enumerate(chunk)]
        assert_review_payload(entries, reference=reference, where="worst_case_batch")
        batches.append(entries)
    total_usd, total_tokens = 0.0, 0
    for batch in batches:
        bound = request_bound(native, review_system, json.dumps(batch, ensure_ascii=False))
        total_usd += bound["usd_bound"]
        total_tokens += bound["token_bound"]
    return {"assumed_new_semantic_candidates": len(synthetic), "batches": len(batches),
            "usd_bound": total_usd, "token_bound": total_tokens,
            "assumption": f"at most {SEMANTIC_MAX_PROPOSALS_PER_TARGET} semantic proposals per target; "
                          "the estimate is an upper bound on the pre-request gate"}


def plan_stage_caps(budget_plan: dict) -> dict:
    """Stage caps come from budgets/budget_plan.json, with the constants as fallback."""
    caps = budget_plan.get("caps", {}) if isinstance(budget_plan, dict) else {}
    return {"usd": caps.get("stage_usd_cap", STAGE_USD_CAP),
            "tokens": caps.get("stage_token_cap", STAGE_TOKEN_CAP),
            "source": "budgets/budget_plan.json caps"
                      if "stage_usd_cap" in caps else "runner fallback constants"}


def prepare(output, *, reference_path=DEFAULT_REFERENCE, python=None,
            deadline_hours=DEFAULT_DEADLINE_HOURS, headroom=DEFAULT_HEADROOM,
            stage_usd_cap=None, stage_token_cap=None, scope=DEFAULT_SCOPE,
            proposal_violation_policy="reject", now=None) -> dict:
    """Offline preparation: freeze inputs, payloads, budget bounds and a deadline."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("prepare needs a timezone-aware clock")
    if proposal_violation_policy not in ("reject", "abort"):
        raise ValueError(f"unknown proposal violation policy {proposal_violation_policy!r}")

    frozen = audit_frozen_inputs()
    catalogue = read_json(FROZEN_CATALOG)
    catalogue_ids = [unit["id"] for unit in catalogue["knowledge_units"]]
    units = {unit["id"]: unit for unit in catalogue["knowledge_units"]}
    selection = read_json(TARGET_SELECTION)
    target_ids = select_targets(selection, catalogue_ids)
    reference = load_reference(reference_path)
    direction = assert_pair_direction(reference["pairs"], target_ids)

    native, phase, scorer = load_frozen_clients()
    formatter_hashes = verify_frozen_formatter()
    proposer_prompt = frozen_string_constant(SEMANTIC_SCRIPT, "PROPOSER")
    review_system = frozen_string_constant(REVIEW_SCRIPT, "SYSTEM")
    if "不要求事先掌握该内容的完整证明" not in review_system:
        raise ValueError("frozen review system lost the frozen goal-scope instruction")
    for role in REFERENCE_ROLES:
        if role not in review_system:
            raise ValueError(f"frozen review system does not define role {role!r}")
    assert_reference_free({"prompt": review_system}, reference=reference, where="review_system")

    # --- projection: one deterministic file shared by every run -----------
    projection_dir = output / "projection"
    projection_dir.mkdir(parents=True)
    projection_path = projection_dir / "projection_candidates.json"
    projection_run = run_projection(out_path=projection_path, python=python)
    projection = read_json(projection_path)
    total = len(projection.get("candidates", []))
    if total != DECLARED_PROJECTION_TOTAL:
        raise ValueError(f"projection produced {total} candidates, expected {DECLARED_PROJECTION_TOTAL}")
    if FROZEN_PROJECTION_FILE.exists() and sha256(projection_path) != sha256(FROZEN_PROJECTION_FILE):
        raise ValueError("fresh projection does not reproduce the pre-registered frozen projection file")
    pool, projection_log = projection_pool(projection, target_ids)
    declared_counts = {row["unit_id"]: row.get("projection_candidates_out") for row in selection["targets"]}
    observed_counts = {unit_id: sum(1 for row in pool if row["source_ku"] == unit_id) for unit_id in target_ids}
    if observed_counts != declared_counts:
        raise ValueError(f"projection counts differ from target_selection.json: "
                         f"{observed_counts} != {declared_counts}")
    write_json(projection_dir / "projection_pool.json", {"candidates": pool})
    write_json(projection_dir / "candidate_log.json", {"log": projection_log})

    # --- frozen model payloads -------------------------------------------
    tasks = build_semantic_tasks(catalogue, target_ids)
    assert_proposer_payload(tasks, reference=reference, where="proposal_input")
    runs = []
    plan_summary = {}
    for arm in ARMS:
        for repeat in REPEATS:
            slug = arm_slug(arm)
            plan_dir = output / "plan" / slug / f"r{repeat}"
            plan_dir.mkdir(parents=True)
            entry = {"arm": arm, "repeat": repeat,
                     "directory": str(output / "runs" / slug / f"r{repeat}"),
                     "plan_directory": str(plan_dir), "status": "pending"}
            if arm == "P+S":
                request = {"system": proposer_prompt, "payload": tasks}
                assert_proposer_payload(request["payload"], reference=reference,
                                        where="proposal_input")
                write_json(plan_dir / "proposal_request.json",
                           {"system": proposer_prompt, "payload": tasks})
                (plan_dir / "proposal_system.txt").write_text(proposer_prompt, encoding="utf-8", newline="\n")
                proposer_bound = request_bound(native, proposer_prompt, json.dumps(tasks, ensure_ascii=False))
                entry["proposer_bound"] = {"usd_bound": proposer_bound["usd_bound"],
                                           "token_bound": proposer_bound["token_bound"],
                                           "body_bytes": proposer_bound["body_bytes"]}
            (plan_dir / "review_system.txt").write_text(review_system, encoding="utf-8", newline="\n")
            plan = plan_projection_batches(pool, units, native, review_system, plan_dir,
                                           reference=reference)
            usd_bound = plan["usd_bound"] + entry.get("proposer_bound", {}).get("usd_bound", 0.0)
            token_bound = plan["token_bound"] + entry.get("proposer_bound", {}).get("token_bound", 0)
            worst_case_batches = len(plan["batches"])
            if arm == "P+S":
                worst = plan_semantic_worst_case(pool, catalogue["knowledge_units"], tasks, native,
                                                 review_system, reference=reference)
                entry["semantic_worst_case"] = worst
                usd_bound += worst["usd_bound"]
                token_bound += worst["token_bound"]
                worst_case_batches = worst["batches"]
            entry["request_plan"] = {"projection_review_batches": len(plan["batches"]),
                                     "worst_case_review_batches": worst_case_batches,
                                     "projection_candidates": len(pool),
                                     "proposer_calls": 1 if arm == "P+S" else 0,
                                     "repeats_share_byte_identical_projection_payloads": True}
            entry["estimated_bound"] = {"usd": usd_bound, "tokens": token_bound}
            entry["caps"] = {"usd": round(usd_bound * headroom, 4),
                             "tokens": math.ceil(token_bound * headroom)}
            entry["plan_bindings"] = [{"path": posix(p), "sha256": sha256(p)}
                                      for p in sorted(plan_dir.glob("*")) if p.is_file()]
            plan_summary[f"{slug}/r{repeat}"] = {"review_batches": len(plan["batches"]),
                                                 "worst_case_review_batches": worst_case_batches,
                                                 "review_candidates": plan["candidates"],
                                                 "usd_bound": round(usd_bound, 6),
                                                 "token_bound": token_bound,
                                                 "caps": entry["caps"]}
            runs.append(entry)

    total_usd = sum(run["caps"]["usd"] for run in runs)
    total_tokens = sum(run["caps"]["tokens"] for run in runs)
    budget_plan = read_json(BUDGET_PLAN) if BUDGET_PLAN.exists() else {}
    e2_line = next((task for task in budget_plan.get("tasks", []) if task.get("id") == "E2"), None)
    stage_caps = plan_stage_caps(budget_plan)
    if stage_usd_cap is not None:
        stage_caps["usd"] = stage_usd_cap
    if stage_token_cap is not None:
        stage_caps["tokens"] = stage_token_cap
    deadline = now + timedelta(hours=deadline_hours)
    earlier = earlier_round_artifacts()

    manifest = {
        "schema": "maf_kg.www_supplement.e2_runner.v1",
        "experiment": "www-supplement-e2-prerequisite-module",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md sections 4.1-4.5, 7.3",
        "status": "prepared",
        "created_at": now_iso(now),
        "session": str(uuid.uuid4()),
        "no_model_calls": True,
        "scope": scope,
        "deadline": {"iso": deadline.isoformat(), "timezone": "UTC", "hours": deadline_hours,
                     "registered_at": now_iso(now),
                     "note": "new expiring deadline; the legacy prepare carried a 2026-10-06 deadline"},
        "model": MODEL, "provider": "opencode-go",
        "call_settings": {"max_tokens": REVIEW_MAX_TOKENS, "timeout_seconds": REVIEW_TIMEOUT_SECONDS,
                          "attempts": REVIEW_ATTEMPTS, "thinking": REVIEW_THINKING,
                          "reasoning_effort": REVIEW_REASONING_EFFORT, "temperature": REVIEW_TEMPERATURE,
                          "batch_cap": REVIEW_BATCH_CAP,
                          "member_description_chars": MEMBER_DESCRIPTION_CHARS,
                          "member_evidence_limit": MEMBER_EVIDENCE_LIMIT,
                          "evidence_text_chars": EVIDENCE_TEXT_CHARS},
        "candidate_rules": {"semantic_max_proposals_per_target": SEMANTIC_MAX_PROPOSALS_PER_TARGET,
                            "witness_min_chars": WITNESS_MIN_CHARS,
                            "witness_must_be_verbatim_member_description": True,
                            "earlier_ku_eligibility": True,
                            "proposal_violation_policy": proposal_violation_policy},
        "frozen_inputs": frozen,
        "target_selection": {"path": posix(TARGET_SELECTION), "sha256": sha256(TARGET_SELECTION),
                             "selection_rule": "by real KU id from target_selection.json",
                             "count": len(target_ids), "unit_ids": target_ids},
        "targets": [{"unit_id": unit_id, "name": units[unit_id]["name"],
                     "chapter": units[unit_id]["chapter"], "job_id": units[unit_id]["job_id"],
                     "reference_pairs_in_v3": next((row.get("reference_pairs_in_v3") for row in selection["targets"]
                                                    if row["unit_id"] == unit_id), None),
                     "source": next((row.get("source") for row in selection["targets"]
                                     if row["unit_id"] == unit_id), None)}
                    for unit_id in target_ids],
        "legacy_job_selector": legacy_job_selector_targets(catalogue),
        "foundation_guard": foundation_guard_trace(catalogue),
        "reference": {"path": reference["path"], "sha256": reference["sha256"],
                      "pair_count": len(reference["pairs"]), "direction_guard": direction,
                      "loaded_by_worker": False,
                      "note": "roles, labels, missed lists and adjudications never enter a model payload"},
        "projection": {"script": posix(PROJECTION_SCRIPT), "script_sha256": sha256(PROJECTION_SCRIPT),
                       "output": posix(projection_path), "output_sha256": sha256(projection_path),
                       "total_candidates": total, "in_scope_candidates": len(pool),
                       "pre_registered_total": DECLARED_PROJECTION_TOTAL,
                       "reproduces_pre_registered_file": FROZEN_PROJECTION_FILE.exists(),
                       "shared_by_all_runs": True, "command": projection_run["command"],
                       "per_target_counts": observed_counts},
        "prompts": {"proposer_source": posix(SEMANTIC_SCRIPT),
                    "proposer_sha256": hashlib.sha256(proposer_prompt.encode("utf-8")).hexdigest(),
                    "review_system_source": posix(REVIEW_SCRIPT),
                    "review_system_sha256": hashlib.sha256(review_system.encode("utf-8")).hexdigest(),
                    "same_review_system_for_both_arms": True},
        "frozen_module_hashes": {**formatter_hashes,
                                 "score_ku_prerequisite_evaluation.py": sha256(SCORER_SCRIPT),
                                 "run_ku_prerequisite_phase.py": sha256(PHASE_SCRIPT),
                                 "prospective_native_client.py": sha256(NATIVE_SCRIPT)},
        "call_settings_check": assert_call_settings({"model": MODEL, "max_tokens": REVIEW_MAX_TOKENS,
                                                     "timeout_seconds": REVIEW_TIMEOUT_SECONDS,
                                                     "attempts": REVIEW_ATTEMPTS,
                                                     "thinking": REVIEW_THINKING,
                                                     "reasoning_effort": REVIEW_REASONING_EFFORT,
                                                     "temperature": REVIEW_TEMPERATURE}, native),
        "budget": {"headroom": headroom, "per_run": {key: value["caps"] for key, value in plan_summary.items()},
                   "stage_caps": stage_caps,
                   "e2_line_from_budget_plan": None if e2_line is None else
                   {"est_tokens": e2_line.get("est_tokens"), "est_usd": e2_line.get("est_usd"),
                    "label": e2_line.get("label"),
                    "estimate_source": e2_line.get("estimate_source")},
                   "measured_bound_total": {"usd": round(total_usd, 4), "tokens": total_tokens},
                   "measured_bound_vs_plan_line": None if e2_line is None else {
                       "usd_ratio": (round(total_usd / e2_line["est_usd"], 3)
                                     if e2_line.get("est_usd") else None),
                       "token_ratio": (round(total_tokens / e2_line["est_tokens"], 3)
                                       if e2_line.get("est_tokens") else None),
                       "measured_bound_exceeds_plan_line": bool(
                           total_usd > (e2_line.get("est_usd") or 0)
                           or total_tokens > (e2_line.get("est_tokens") or 0)),
                       "note": "the measured pre-request bound is what prospective_native_client."
                               "Budget.reserve charges before each request; budgets/budget_plan.json is "
                               "revised independently of this runner, so compare the two numbers before "
                               "authorizing execute"},
                   "authorization_note": "execute must not start before the measured bound is authorized"},
        "runs": runs,
        "reference_loaded_during_prepare": True,
        "reference_in_worker": False,
        "earlier_round_guard": {
            "artifacts_hashed": len(earlier),
            "policy": "no run directory may contain a copy of an earlier round's predictions or "
                      "reference; every paid call of this round is a new call",
            "sources": sorted(earlier.values())},
        "code": {"runner": posix(Path(__file__)), "runner_sha256": sha256(Path(__file__))},
        "disabled": ["resume", "retry", "json_repair", "fallback_model", "temperature",
                     "gold_completion", "manual_reference_pairs", "raising_K"],
    }
    if total_usd > stage_caps["usd"] or total_tokens > stage_caps["tokens"]:
        raise ValueError(f"measured E2 bound exceeds the stage caps: usd {total_usd} / "
                         f"tokens {total_tokens} > {stage_caps}")
    write_json(output / "manifest.json", manifest)

    reference_facts = {
        "schema": "maf_kg.www_supplement.e2_reference_facts.v1",
        "reference": reference["path"], "sha256": reference["sha256"],
        "pair_count": len(reference["pairs"]),
        "role_counts": {role: sum(1 for pair in reference["pairs"] if pair["role"] == role)
                        for role in REFERENCE_ROLES},
        "directness_counts": {value: sum(1 for pair in reference["pairs"]
                                         if pair["role"] == "required" and pair["directness"] == value)
                              for value in DIRECTNESS},
        "pairs": [{"id": pair["id"], "target_ku": pair["target_ku"],
                   "prerequisite_ku": pair["prerequisite_ku"], "role": pair["role"],
                   "directness": pair["directness"]} for pair in reference["pairs"]],
        "target_coverage": {unit_id: sum(1 for pair in reference["pairs"] if pair["target_ku"] == unit_id)
                            for unit_id in target_ids},
        "note": "offline scoring input only; never copied into a run directory or a model payload",
    }
    write_json(output / "offline" / "reference_facts.json", reference_facts)
    write_json(earlier_round_hash_file(output),
               {"schema": "maf_kg.www_supplement.e2_earlier_round_hashes.v1",
                "artifacts": earlier,
                "note": "sha256 of every earlier-round prediction or reference file found in this "
                        "workspace; a run directory containing one of them is refused"})
    report = {
        "schema": "maf_kg.www_supplement.e2_prepare_report.v1",
        "targets": manifest["targets"],
        "target_selection_check": {"selected": target_ids, "declared_counts": declared_counts,
                                   "observed_counts": observed_counts, "match": True},
        "projection_log": projection_log,
        "projection": manifest["projection"],
        "plan_summary": plan_summary,
        "budget": manifest["budget"],
        "reference_facts_path": posix(output / "offline" / "reference_facts.json"),
        "goal_scope_log": [{"unit_id": unit_id, "name": units[unit_id]["name"],
                            "goal": units[unit_id]["description"],
                            "instruction_applied": "理解与应用生成KU描述中的数学内容，不要求事先掌握该内容的完整证明",
                            "goal_type_from_reference_used": None,
                            "conflict_logged_only": True,
                            "note": "plan section 4.2: a goal-scope conflict is logged, never repaired by "
                                    "quietly widening the goal; the reference target goal_type is not used"}
                           for unit_id in target_ids],
        "no_model_calls": True,
    }
    write_json(output / "offline" / "prepare_report.json", report)
    print(f"prepared {len(runs)} module runs over {len(target_ids)} targets -> {output}")
    print(f"projection: {total} candidates, {len(pool)} in scope for the nine targets")
    print(f"bound: usd {total_usd:.4f} / tokens {total_tokens} with headroom {headroom}")
    return manifest


# ---------------------------------------------------------------------------
# one module run (paid; transport injectable for offline plumbing tests)
# ---------------------------------------------------------------------------

def run_entry(manifest: dict, arm: str, repeat: int) -> dict:
    for entry in manifest["runs"]:
        if entry["arm"] == arm and entry["repeat"] == repeat:
            return entry
    raise ValueError(f"no run planned for arm {arm!r} repeat {repeat!r}")


def run_module(output, arm: str, repeat: int, *, transport=None) -> dict:
    """Run exactly one module run. New paid calls only; no earlier verdict is read.

    The worker never opens the reference file, so the reference-payload guard it
    applies here is the reference-independent half (keys, role tokens and
    adjudication phrases); `prepare` and `audit` apply the full guard including
    reference pair ids. `transport` exists for offline tests: it must expose
    `call(system, user, model)` and is recorded in the run manifest as injected.
    """
    output = Path(output).resolve()
    top = read_json(output / "manifest.json")
    entry = run_entry(top, arm, repeat)
    run_dir = Path(entry["directory"])
    plan_dir = Path(entry["plan_directory"])
    if run_dir.exists() and read_json(run_dir / "run_manifest.json").get("status") == "completed":
        raise ValueError(f"run {arm}/r{repeat} already completed; no automatic continuation")
    run_dir.mkdir(parents=True, exist_ok=True)
    catalogue = read_json(FROZEN_CATALOG)
    units = {unit["id"]: unit for unit in catalogue["knowledge_units"]}
    target_ids = top["target_selection"]["unit_ids"]
    review_system = (plan_dir / "review_system.txt").read_text(encoding="utf-8")
    if hashlib.sha256(review_system.encode("utf-8")).hexdigest() != top["prompts"]["review_system_sha256"]:
        raise ValueError("review system prompt changed since prepare")

    native, phase, scorer = load_frozen_clients()
    manifest = {
        "schema": "maf_kg.www_supplement.e2_run.v1", "arm": arm, "repeat": repeat,
        "status": "running", "created_at": now_iso(), "session": str(uuid.uuid4()),
        "model": MODEL, "provider": "opencode-go", "max_tokens": REVIEW_MAX_TOKENS,
        "output_token_deviation": globals().get("OUTPUT_TOKEN_DEVIATION"),
        "timeout_seconds": REVIEW_TIMEOUT_SECONDS, "attempts": REVIEW_ATTEMPTS,
        "thinking": REVIEW_THINKING, "reasoning_effort": REVIEW_REASONING_EFFORT,
        "temperature": REVIEW_TEMPERATURE, "fallback": False, "json_repair": False,
        "deadline": top["deadline"]["iso"], "usd_limit": entry["caps"]["usd"],
        "token_limit": entry["caps"]["tokens"],
        "reference_loaded_in_worker": False,
        "transport": "injected" if transport is not None else "frozen_phase.Native",
        "projection": {"path": top["projection"]["output"],
                       "sha256": top["projection"]["output_sha256"], "reused": True},
        "stages": {"semantic": "skipped" if arm == "P" else "pending", "review": "pending"},
        "proposal_violation_policy": top["candidate_rules"]["proposal_violation_policy"],
    }
    assert_call_settings(manifest, native)
    probe = request_bound(native, review_system, json.dumps([{"index": 0}], ensure_ascii=False))
    assert_caps_sufficient(entry["caps"], probe, f"{arm}/r{repeat} first review batch probe")
    write_json(run_dir / "run_manifest.json", manifest)
    client = transport if transport is not None else phase.Native(run_dir, manifest)
    semantic_rows = []
    try:
        semantic_log = []
        if arm == "P+S":
            request = read_json(plan_dir / "proposal_request.json")
            assert_proposer_payload(request["payload"], where="proposal_input")
            answer, usage = client.call(request["system"],
                                        json.dumps(request["payload"], ensure_ascii=False), MODEL)
            write_json(run_dir / "proposal_answer.json", answer)
            validation = validate_semantic_proposals(
                answer, request["payload"], units, policy=manifest["proposal_violation_policy"])
            write_json(run_dir / "proposal_validation.json", validation)
            semantic_rows = validation["accepted"]
            semantic_log = ([{**{k: row[k] for k in ("source_ku", "target_ku")}, "origin": "semantic",
                              "legal": True, "duplicate_status": "unique", "rejection_reason": None}
                             for row in validation["accepted"]]
                            + [{"origin": "semantic", "source_ku": row["source_ku"],
                                "target_ku": row["target_ku"], "legal": False,
                                "duplicate_status": row["duplicate_status"],
                                "rejection_reason": row["rejection_reason"]}
                               for row in validation["rejected"]])
            manifest["stages"]["semantic"] = "completed"
            manifest["proposal_counts"] = validation["counts"]
            write_json(run_dir / "run_manifest.json", manifest)

        projection_rows = read_json(output / "projection" / "projection_pool.json")["candidates"]
        merged, merge_log = merge_candidates(projection_rows, semantic_rows)
        assert_no_injected_pairs(merged, projection_rows, semantic_rows)
        assert_known_endpoints(merged, list(units), "merged_candidates")
        write_json(run_dir / "merged_candidates.json", {"candidates": merged})
        write_json(run_dir / "candidate_log.json",
                   {"scope": "every projection and semantic candidate with origin, legality, "
                             "duplicate status and rejection reason",
                    "projection_log": read_json(output / "projection" / "candidate_log.json")["log"],
                    "semantic_log": semantic_log,
                    "merge_log": merge_log})
        assert_no_earlier_round_reuse(run_dir, load_earlier_round_hashes(output))

        batches = build_review_batches(merged, units)
        frozen = [read_json(path) for path in sorted(plan_dir.glob("review_batch_*.json"))]
        for number, batch in enumerate(frozen):
            if batches[number][:len(batch)] != batch:
                raise ValueError(f"frozen review batch {number + 1} changed; the projection payload "
                                 "must be byte-identical to the prepared plan")
        manifest["review_batches"] = []
        predictions = []
        for number, batch in enumerate(batches, start=1):
            where = f"{arm}/r{repeat}/review_batch_{number:03d}"
            assert_review_payload(batch, where=where)
            path = run_dir / f"review_batch_{number:03d}.json"
            write_json(path, batch)
            record = {"number": number, "path": posix(path), "sha256": sha256(path),
                      "candidates": len(batch), "indices": [item["index"] for item in batch],
                      "status": "planned"}
            shared = number <= len(frozen)
            if shared:
                frozen_batch = frozen[number - 1]
                record["frozen_plan_sha256"] = sha256(plan_dir / f"review_batch_{number:03d}.json")
                record["byte_identical_to_plan"] = (record["sha256"] == record["frozen_plan_sha256"])
                record["frozen_batch_is_exact_prefix"] = (batch[:len(frozen_batch)] == frozen_batch)
                if len(frozen_batch) == REVIEW_BATCH_CAP and not record["byte_identical_to_plan"]:
                    raise ValueError(f"{where}: a full frozen projection batch was modified before the call")
                if not record["frozen_batch_is_exact_prefix"]:
                    raise ValueError(f"{where}: the frozen projection entries are not an exact prefix")
            manifest["review_batches"].append(record)
            write_json(run_dir / "run_manifest.json", manifest)
            answer, usage = client.call(review_system, json.dumps(batch, ensure_ascii=False), MODEL)
            rows = validate_review_answer(answer, batch)
            predictions.extend(rows)
            record["status"] = "completed"
            record["usage"] = usage
            write_json(run_dir / "predictions.json", {"verdicts": predictions})
            write_json(run_dir / "run_manifest.json", manifest)
            print(f"{arm}/r{repeat}: reviewed {len(predictions)}/{len(merged)} candidates", flush=True)

        manifest["stages"]["review"] = "completed"
        write_json(run_dir / "predictions.json", {"verdicts": predictions})
        edges = accepted_direct_edges(predictions)
        write_json(run_dir / "accepted_direct_edges.json",
                   {"edges": edges, "policy": "only role=required with directness=direct becomes a direct edge",
                    "classes": classify_verdicts(predictions)})
        before = sha256(run_dir / "predictions.json")
        diagnostics = scorer.graph_diagnostics(
            [unit["id"] for unit in catalogue["knowledge_units"]],
            [(edge["target_ku"], edge["source_ku"]) for edge in edges])
        diagnostics["stage"] = "after review"
        diagnostics["rewrites_verdicts"] = False
        diagnostics["closure_scope"] = ("closure is interpretable only for the four targets with "
                                        "reference pairs in the main reference")
        write_json(run_dir / "diagnostics_after_review.json", diagnostics)
        if sha256(run_dir / "predictions.json") != before:
            raise ValueError("structural diagnostics rewrote the verdicts")
        manifest.update(status="completed", verdict_count=len(predictions),
                        candidate_count=len(merged),
                        accepted_direct_count=len(edges),
                        semantic_candidates=len(semantic_rows))
        write_json(run_dir / "run_manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
        write_json(run_dir / "run_manifest.json", manifest)
        raise


def worker(output, arm: str, repeat: int) -> dict:
    return run_module(output, arm, repeat)


def execute(output, *, arms=ARMS, repeats=REPEATS, max_runs=None, python=None,
            output_tokens=None) -> dict:
    """Run the module runs through `worker` subprocesses, newest call only."""
    output = Path(output).resolve()
    top = read_json(output / "manifest.json")
    deadline = datetime.fromisoformat(top["deadline"]["iso"])
    if deadline.tzinfo is None:
        raise ValueError("manifest deadline must include a timezone")
    if datetime.now(timezone.utc) >= deadline:
        raise RuntimeError("module deadline reached; no run started")
    jobs = [entry for entry in top["runs"] if entry["arm"] in arms and entry["repeat"] in repeats]
    if max_runs is not None:
        jobs = jobs[:max_runs]
    results = []
    for entry in jobs:
        run_dir = Path(entry["directory"])
        if run_dir.exists() and (run_dir / "run_manifest.json").exists() and \
                read_json(run_dir / "run_manifest.json").get("status") == "completed":
            results.append({"arm": entry["arm"], "repeat": entry["repeat"], "status": "already_completed"})
            continue
        command = [python or sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "worker",
                   str(output), "--arm", entry["arm"], "--repeat", str(entry["repeat"])]
        # The worker is a separate process and re-reads the module default, so the
        # override MUST be forwarded or it silently reverts to the plan value.
        if output_tokens is not None:
            command += ["--output-tokens", str(output_tokens)]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")
        results.append({"arm": entry["arm"], "repeat": entry["repeat"],
                        "returncode": completed.returncode,
                        "status": "completed" if completed.returncode == 0 else "failed",
                        "stdout_tail": completed.stdout.strip().splitlines()[-5:],
                        "stderr_tail": completed.stderr.strip().splitlines()[-5:]})
        top = read_json(output / "manifest.json")
        for run in top["runs"]:
            if run["arm"] == entry["arm"] and run["repeat"] == entry["repeat"]:
                run["status"] = results[-1]["status"]
        top["status"] = "executed" if all(r["status"] in ("completed", "already_completed")
                                          for r in results) else "partial"
        write_json(output / "manifest.json", top)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return {"runs": results}


# ---------------------------------------------------------------------------
# layered scoring (plan section 4.5)
# ---------------------------------------------------------------------------

def metric_block(scorer_module, tp: int, fp: int, fn: int, scope: str) -> dict:
    """Frozen formula, reused so the reported denominators cannot drift."""
    return dict(scorer_module._metrics(tp, fp, fn, scope))


def score_layered(*, reference, candidates, verdicts, catalogue, target_ids,
                  entered_review=None, semantic_stage_present: bool, scorer_module) -> dict:
    """Score one module run with the section 4.5 denominators.

    Rows: candidate direct recall, all-required candidate recall, review
    performance on reviewed and determinate candidates, end-to-end direct and
    all-required P/R/F1 with ungenerated, unreviewed and abstained positives
    still counted as misses, verdict coverage as |judged and determinate| /
    |determinate|, support false accepts, out-of-reference accepts and the
    post-review structural diagnostics.
    """
    ref = {(pair["prerequisite_ku"], pair["target_ku"]): pair for pair in reference["pairs"]}
    if len(ref) != len(reference["pairs"]):
        raise ValueError("reference contains a duplicate directed pair")
    determinate = {key: row for key, row in ref.items() if row["role"] != UNCERTAIN_ROLE}
    direct_gold = {key for key, row in determinate.items()
                   if row["role"] == "required" and row["directness"] == "direct"}
    indirect_gold = {key for key, row in determinate.items()
                     if row["role"] == "required" and row["directness"] == "indirect"}
    required_gold = direct_gold | indirect_gold
    support_gold = {key for key, row in determinate.items() if row["role"] in SUPPORT_ROLES}
    catalogue_ids = [unit["id"] for unit in catalogue["knowledge_units"]]
    names = {unit["id"]: unit["name"] for unit in catalogue["knowledge_units"]}
    assert_known_endpoints(candidates, catalogue_ids, "candidates")

    cand = {canonical_pair(row): row for row in candidates}
    if len(cand) != len(candidates):
        raise ValueError("candidate pool contains a duplicate directed pair")
    views = {"projection": {key for key, row in cand.items() if "projection" in row.get("sources", [])},
             "semantic_added": {key for key, row in cand.items() if row.get("sources") == ["semantic"]},
             "semantic_proposed": {key for key, row in cand.items() if "semantic" in row.get("sources", [])},
             "merged": set(cand)}
    if entered_review is None:
        entered_review = set(cand)
    entered_review = set(entered_review)
    unknown = entered_review - set(cand)
    if unknown:
        raise ValueError(f"a reviewed pair is not in the candidate pool: {sorted(unknown)[:3]}")

    def recall_block(pool, gold) -> dict:
        hits = len(pool & gold)
        return {"hits": hits, "denominator": len(gold), "recall": ratio(hits, len(gold)),
                "scope": "frozen main reference pairs"}

    candidate_recall = {}
    for name, pool in views.items():
        block = {"direct": recall_block(pool, direct_gold),
                 "required_total": recall_block(pool, required_gold),
                 "required_direct": recall_block(pool, direct_gold),
                 "required_indirect": recall_block(pool, indirect_gold),
                 "support": recall_block(pool, support_gold),
                 "semantic_stage_present": semantic_stage_present,
                 "by_target": {}}
        for unit_id in target_ids:
            target_direct = {key for key in direct_gold if key[1] == unit_id}
            target_required = {key for key in required_gold if key[1] == unit_id}
            target_pool = {key for key in pool if key[1] == unit_id}
            block["by_target"][unit_id] = {
                "name": names[unit_id],
                "reference_covered": bool(target_direct or target_required),
                "direct": recall_block(target_pool, target_direct),
                "required_total": recall_block(target_pool, target_required),
                "coverage_note": None if (target_direct or target_required)
                else "no pair of this target is in the main reference; recall is undefined, not zero",
            }
        candidate_recall[name] = block

    reviewed = {}
    for row in verdicts:
        key = canonical_pair(row)
        if key in reviewed:
            raise ValueError(f"duplicate verdict for pair {key}")
        reviewed[key] = row
    stray = set(reviewed) - entered_review
    if stray:
        raise ValueError(f"a verdict exists for a pair that never entered review: {sorted(stray)[:3]}")
    unjudged = {key for key in entered_review & set(determinate) if key not in reviewed}
    if unjudged:
        raise ValueError(f"a candidate entered review but has no verdict: {sorted(unjudged)[:3]}")
    labelled = {key: row for key, row in reviewed.items() if key in determinate}
    judged = {key: row for key, row in labelled.items() if row["label"] != UNCERTAIN_ROLE}
    abstained = set(labelled) - set(judged)
    all_predicted_required = {key for key, row in reviewed.items() if row["label"] == "required"}
    all_predicted_direct = {key for key in all_predicted_required
                            if reviewed[key].get("directness") == "direct"}
    predicted_required = all_predicted_required & set(determinate)
    predicted_direct = all_predicted_direct & set(determinate)
    stale_directness = {key for key, row in reviewed.items()
                        if row["label"] != "required" and row.get("directness") is not None}
    if stale_directness:
        raise ValueError("a non-required verdict carries directness")

    review_scope = ("only candidates that actually entered review and whose reference pair is "
                    "determinate; positive means required (direct or indirect)")
    review_direct_scope = ("same conditional review subset; positive means required and direct")
    review_performance = {
        "entered_review": len(entered_review),
        "entered_review_and_determinate": len(labelled),
        "judged_determinate": len(judged),
        "abstained_determinate": len(abstained),
        "requiredness": metric_block(scorer_module,
                                     len(predicted_required & required_gold),
                                     len(predicted_required - required_gold),
                                     len((set(judged) & required_gold) - predicted_required),
                                     review_scope),
        "directness": metric_block(scorer_module,
                                   len(predicted_direct & direct_gold),
                                   len(predicted_direct - direct_gold),
                                   len((set(judged) & direct_gold) - predicted_direct),
                                   review_direct_scope),
        "role_confusion": {},
    }
    for key, row in sorted(labelled.items()):
        gold = determinate[key]
        gold_class = f"required_{gold['directness']}" if gold["role"] == "required" else gold["role"]
        predicted = row["label"] if row["label"] != "required" else f"required_{row.get('directness')}"
        review_performance["role_confusion"].setdefault(gold_class, {})
        review_performance["role_confusion"][gold_class][predicted] = (
            review_performance["role_confusion"][gold_class].get(predicted, 0) + 1)

    scored_direct = predicted_direct & set(determinate)
    end_to_end_direct = metric_block(
        scorer_module, len(scored_direct & direct_gold), len(scored_direct - direct_gold),
        len(direct_gold - predicted_direct),
        "final accepted direct edges against the frozen reference's direct positives; "
        "ungenerated, unreviewed and abstained positives remain false negatives; "
        "pairs outside the reference are excluded from this precision denominator")
    scored_required = predicted_required & set(determinate)
    end_to_end_required = metric_block(
        scorer_module, len(scored_required & required_gold), len(scored_required - required_gold),
        len(required_gold - predicted_required),
        "final accepted required edges (direct or indirect) against the frozen reference's "
        "required positives; this row never replaces the direct-edge row")

    pool = set(cand)
    not_generated = direct_gold - pool
    in_pool_not_reviewed = (direct_gold & pool) - entered_review
    abstained_gold = direct_gold & abstained
    reviewed_not_direct = (direct_gold & set(judged)) - predicted_direct
    accounted = not_generated | in_pool_not_reviewed | abstained_gold | reviewed_not_direct
    if accounted != direct_gold - predicted_direct:
        raise ValueError("false-negative breakdown does not account for every missed direct positive")
    fn_breakdown = {
        "not_generated": sorted(f"{key[0]}->{key[1]}" for key in not_generated),
        "in_candidate_pool_not_reviewed": sorted(f"{key[0]}->{key[1]}" for key in in_pool_not_reviewed),
        "reviewed_and_abstained": sorted(f"{key[0]}->{key[1]}" for key in abstained_gold),
        "reviewed_and_not_direct": sorted(f"{key[0]}->{key[1]}" for key in reviewed_not_direct),
        "total_missed": len(accounted),
    }

    coverage = {"judged_and_determinate": len(judged),
                "determinate_reference_pairs": len(determinate),
                "coverage": ratio(len(judged), len(determinate)),
                "abstained_determinate": len(abstained),
                "abstention_rate_over_entered_review": ratio(len(abstained), len(labelled)),
                "one_minus_abstention_rate": (None if not labelled else
                                              1 - len(abstained) / len(labelled)),
                "formula": "|determinate reference pairs with a non-abstaining verdict| / "
                           "|determinate reference pairs|",
                "warning": "never computed as one minus the abstention rate"}

    indirect_upgrades = sorted(indirect_gold & predicted_direct)
    support_direct = sorted(support_gold & predicted_direct)
    support_required_non_direct = sorted((support_gold & predicted_required) - predicted_direct)
    support_false_accepts = {
        "as_direct": {"count": len(support_direct), "denominator": len(support_gold),
                      "pairs": [f"{key[0]}->{key[1]}" for key in support_direct]},
        "as_required_not_direct": {"count": len(support_required_non_direct),
                                   "denominator": len(support_gold),
                                   "pairs": [f"{key[0]}->{key[1]}" for key in support_required_non_direct]},
        "indirect_upgraded_to_direct": {"count": len(indirect_upgrades),
                                        "denominator": len(indirect_gold),
                                        "pairs": [f"{key[0]}->{key[1]}" for key in indirect_upgrades]},
        "scope": "reference support and indirect positives emitted as required/direct",
    }

    out_of_reference_direct = sorted(all_predicted_direct - set(determinate))
    out_of_reference_required = sorted((all_predicted_required - set(determinate))
                                       - set(out_of_reference_direct))
    out_of_reference = {
        "accepted_direct": [f"{key[0]}->{key[1]}" for key in out_of_reference_direct],
        "accepted_required_not_direct": [f"{key[0]}->{key[1]}" for key in out_of_reference_required],
        "count_direct": len(out_of_reference_direct),
        "sensitivity_precision_if_counted_as_false_positives": ratio(
            end_to_end_direct["tp"],
            end_to_end_direct["tp"] + end_to_end_direct["fp"] + len(out_of_reference_direct)),
        "policy": "a prediction absent from the frozen reference is neither automatically correct nor "
                  "automatically wrong; it is frozen, listed here, and reviewed against source text in a "
                  "separate pass. Precision above is reference-scoped and is not a whole-catalogue precision.",
    }

    return {
        "schema": "maf_kg.www_supplement.e2_layered_scores.v1",
        "reference": {"path": reference["path"], "sha256": reference["sha256"],
                      "pair_count": len(ref), "determinate": len(determinate),
                      "uncertain": len(ref) - len(determinate),
                      "direct_positives": len(direct_gold), "indirect_positives": len(indirect_gold),
                      "required_positives": len(required_gold), "support_positives": len(support_gold)},
        "candidate_pool": {"merged": len(cand), "projection": len(views["projection"]),
                           "semantic_added": len(views["semantic_added"]),
                           "semantic_proposed": len(views["semantic_proposed"]),
                           "entered_review": len(entered_review),
                           "reviewed_and_determinate": len(labelled)},
        "candidate_recall": candidate_recall,
        "review_performance": review_performance,
        "end_to_end_direct": end_to_end_direct,
        "end_to_end_required": end_to_end_required,
        "direct_false_negative_breakdown": fn_breakdown,
        "verdict_coverage": coverage,
        "support_false_accepts": support_false_accepts,
        "out_of_reference_predictions": out_of_reference,
        "caveat": ("Descriptive scores on a finite frozen reference. The five author-approved expansion "
                   "targets have no reference pair in the main reference, so their recall is undefined, "
                   "not zero; uncertain pairs are excluded from both denominators."),
    }


def aggregate_runs(scores: list) -> dict:
    if not scores:
        return {"runs": 0}

    def spread(*path):
        values = []
        for entry in scores:
            node = entry["scores"]
            for step in path:
                if not isinstance(node, dict) or step not in node:
                    node = None
                    break
                node = node[step]
            if isinstance(node, (int, float)):
                values.append(node)
        if not values:
            return None
        return {"n": len(values), "min": min(values), "max": max(values),
                "mean": sum(values) / len(values)}

    return {
        "runs": len(scores),
        "arms": sorted({entry["arm"] for entry in scores}),
        "candidate_direct_recall": {
            "projection": spread("candidate_recall", "projection", "direct", "recall"),
            "semantic_added": spread("candidate_recall", "semantic_added", "direct", "recall"),
            "merged": spread("candidate_recall", "merged", "direct", "recall"),
        },
        "candidate_required_recall": {
            "projection": spread("candidate_recall", "projection", "required_total", "recall"),
            "merged": spread("candidate_recall", "merged", "required_total", "recall"),
        },
        "end_to_end_direct": {"precision": spread("end_to_end_direct", "precision"),
                              "recall": spread("end_to_end_direct", "recall"),
                              "f1": spread("end_to_end_direct", "f1")},
        "end_to_end_required": {"precision": spread("end_to_end_required", "precision"),
                                "recall": spread("end_to_end_required", "recall"),
                                "f1": spread("end_to_end_required", "f1")},
        "verdict_coverage": spread("verdict_coverage", "coverage"),
        "note": "two repeats per arm are technical replicates, not independent samples; "
                "min/max across repeats is technical variation, not an uncertainty interval",
    }


# ---------------------------------------------------------------------------
# audit (offline)
# ---------------------------------------------------------------------------

def audit(output, *, reference_path=None, report_path=None,
          allow_injected_transport=False) -> dict:
    """Re-verify the frozen payloads and score every completed module run."""
    output = Path(output).resolve()
    top = read_json(output / "manifest.json")
    reference = load_reference(reference_path or top["reference"]["path"])
    if reference["sha256"] != top["reference"]["sha256"]:
        raise ValueError("reference hash changed since prepare")
    catalogue = read_json(FROZEN_CATALOG)
    target_ids = top["target_selection"]["unit_ids"]
    assert_pair_direction(reference["pairs"], target_ids)
    native, phase, scorer = load_frozen_clients()

    binding_report = []
    earlier = load_earlier_round_hashes(output)
    for entry in top["runs"]:
        for binding in entry.get("plan_bindings", []):
            if sha256(binding["path"]) != binding["sha256"]:
                raise ValueError(f"frozen payload changed since prepare: {binding['path']}")
            binding_report.append({"path": binding["path"], "sha256": binding["sha256"]})
        run_dir = Path(entry["directory"])
        if run_dir.exists():
            assert_no_earlier_round_reuse(run_dir, earlier)
    for path in (top["projection"]["output"],):
        if sha256(path) != top["projection"]["output_sha256"]:
            raise ValueError(f"frozen projection file changed: {path}")

    payload_report = []
    checked = []
    for path in sorted((output / "plan").rglob("*.json")):
        checked.append(path)
    for path in sorted((output / "runs").rglob("review_batch_*.json")):
        checked.append(path)
    for path in sorted((output / "runs").rglob("merged_candidates.json")):
        checked.append(path)
    for path in checked:
        payload = read_json(path)
        if path.name.startswith("review_batch"):
            assert_review_payload(payload, reference=reference, where=posix(path))
        else:
            assert_reference_free(payload, reference=reference, where=posix(path))
        payload_report.append({"path": posix(path), "sha256": sha256(path), "reference_free": True})

    runs, scores, injected, evidence_runs = [], [], [], []
    for entry in top["runs"]:
        run_dir = Path(entry["directory"])
        run_manifest_path = run_dir / "run_manifest.json"
        if not run_manifest_path.exists():
            runs.append({**{k: entry[k] for k in ("arm", "repeat", "status")},
                         "run_status": "not_started"})
            continue
        run_manifest = read_json(run_manifest_path)
        assert_call_settings(run_manifest, native)
        if run_manifest.get("transport") != "frozen_phase.Native" and run_manifest.get("status") == "completed":
            if not allow_injected_transport:
                raise ValueError(f"{entry['arm']}/r{entry['repeat']} used an injected transport; "
                                 "its verdicts are not evidence")
            injected.append(f"{entry['arm']}/r{entry['repeat']}")
        elif run_manifest.get("status") == "completed":
            evidence_runs.append(f"{entry['arm']}/r{entry['repeat']}")
        if run_manifest.get("reference_loaded_in_worker") is not False:
            raise ValueError("worker must never load the reference")
        budget_path = run_dir / "budget.json"
        budget = read_json(budget_path) if budget_path.exists() else None
        if budget and (budget["accounting_usd"] > budget["usd_limit"] + 1e-9
                       or budget["accounting_tokens"] > budget["token_limit"]):
            raise ValueError(f"{entry['arm']}/r{entry['repeat']} exceeded its ledger cap")
        row = {**{k: entry[k] for k in ("arm", "repeat")}, "run_status": run_manifest.get("status"),
               "verdict_count": run_manifest.get("verdict_count"),
               "accepted_direct_count": run_manifest.get("accepted_direct_count"),
               "requests": 0 if not budget else len(budget["requests"]),
               "accounting_usd": None if not budget else budget["accounting_usd"],
               "accounting_tokens": None if not budget else budget["accounting_tokens"]}
        runs.append(row)
        if run_manifest.get("status") != "completed":
            continue
        candidates = read_json(run_dir / "merged_candidates.json")["candidates"]
        verdicts = read_json(run_dir / "predictions.json")["verdicts"]
        entered = set()
        for batch in run_manifest.get("review_batches", []):
            if batch["status"] != "completed":
                continue
            for index in batch["indices"]:
                entered.add(canonical_pair(candidates[index]))
        if {canonical_pair(row) for row in verdicts} != entered:
            raise ValueError(f"{entry['arm']}/r{entry['repeat']}: verdicts do not match the completed batches")
        stored_edges = [canonical_pair(row) for row in read_json(run_dir / "accepted_direct_edges.json")["edges"]]
        recomputed = [canonical_pair(row) for row in accepted_direct_edges(verdicts)]
        if sorted(stored_edges) != sorted(recomputed):
            raise ValueError(f"{entry['arm']}/r{entry['repeat']}: accepted direct edges were rewritten "
                             "after review")
        scores.append({"arm": entry["arm"], "repeat": entry["repeat"],
                       "scores": score_layered(reference=reference, candidates=candidates, verdicts=verdicts,
                                               catalogue=catalogue, target_ids=target_ids,
                                               entered_review=entered,
                                               semantic_stage_present=entry["arm"] == "P+S",
                                               scorer_module=scorer)})
        write_json(run_dir / "scores_layered.json", scores[-1]["scores"])

    by_arm = {}
    for arm in ARMS:
        by_arm[arm] = aggregate_runs([row for row in scores if row["arm"] == arm])
    report = {
        "schema": "maf_kg.www_supplement.e2_offline_report.v1",
        "experiment": top["experiment"], "output": posix(output),
        "created_at": now_iso(), "no_model_calls": True,
        "frozen_inputs": top["frozen_inputs"],
        "targets": top["targets"],
        "reference": {**top["reference"], "pair_count_verified": len(reference["pairs"])},
        "budget": {"per_run": top["budget"]["per_run"],
                   "measured_bound_total": top["budget"]["measured_bound_total"],
                   "e2_line_from_budget_plan": top["budget"]["e2_line_from_budget_plan"]},
        "payload_guard": {"checked": len(payload_report), "reference_free": True, "files": payload_report},
        "plan_bindings_verified": len(binding_report),
        "injected_transport_runs": injected,
        "evidence_runs": evidence_runs,
        "evidence": bool(evidence_runs) and not injected,
        "runs": runs, "scores": scores, "by_arm": by_arm,
        "notes": [
            "worker never loads the reference; scoring happens only in audit",
            "structural diagnostics run after review and never rewrite a verdict",
            "only required+direct becomes a direct edge; indirect, principle_support, "
            "proof_support, related_or_none and uncertain stay separate",
            "a run scored from an injected transport is an offline rehearsal, not evidence",
        ],
    }
    write_json(output / "offline" / "e2_audit_report.json", report)
    if report_path:
        write_json(report_path, report)
    print(json.dumps({"runs": runs, "by_arm": by_arm, "evidence": report["evidence"],
                      "payload_guard": {"checked": len(payload_report),
                                        "plan_bindings_verified": len(binding_report)}},
                     ensure_ascii=False, indent=2))
    return report


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("prepare", "execute", "worker", "audit"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--reference", type=Path, default=None,
                        help=f"reference file; prepare defaults to {DEFAULT_REFERENCE.name}")
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    parser.add_argument("--python", default=None, help="interpreter for the frozen projection")
    parser.add_argument("--deadline-hours", type=float, default=DEFAULT_DEADLINE_HOURS)
    parser.add_argument("--headroom", type=float, default=DEFAULT_HEADROOM)
    parser.add_argument("--stage-usd-cap", type=float, default=None,
                        help="override the stage cap; default is budgets/budget_plan.json")
    parser.add_argument("--stage-token-cap", type=int, default=None,
                        help="override the stage cap; default is budgets/budget_plan.json")
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--proposal-violation-policy", choices=("reject", "abort"), default="reject")
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--output-tokens", type=int, default=None,
                        help="override the per-call output ceiling (default: the plan's "
                             "REVIEW_MAX_TOKENS). Any value other than the default is a "
                             "PARAMETER DEVIATION and must be run as a new, separately "
                             "named version with the original failure kept.")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--allow-injected-transport", action="store_true",
                        help="audit an offline rehearsal built with an injected transport; "
                             "the report is marked as not evidence")
    args = parser.parse_args(argv)
    if args.output_tokens is not None and args.output_tokens != REVIEW_MAX_TOKENS:
        # A parameter deviation: the plan's ceiling was consumed entirely by
        # reasoning tokens on the proposer payload. Recorded, not hidden.
        globals()["REVIEW_MAX_TOKENS"] = int(args.output_tokens)
        globals()["OUTPUT_TOKEN_DEVIATION"] = {
            "plan_value": 12000,
            "used_value": int(args.output_tokens),
            "reason": "the plan's 12000-token ceiling was consumed entirely by reasoning tokens "
                      "(finish_reason=length, content empty) on the 86 KB proposer payload, so the "
                      "P+S arm could not execute; the earlier failures are preserved",
        }
        print(f"PARAMETER DEVIATION: output ceiling {args.output_tokens} (plan value 12000)")

    output = args.output.resolve()
    if args.action == "prepare":
        prepare(output, reference_path=args.reference or DEFAULT_REFERENCE, python=args.python,
                deadline_hours=args.deadline_hours, headroom=args.headroom,
                stage_usd_cap=args.stage_usd_cap, stage_token_cap=args.stage_token_cap,
                scope=args.scope, proposal_violation_policy=args.proposal_violation_policy)
    elif args.action == "worker":
        if args.arm is None or args.repeat is None:
            parser.error("worker requires --arm and --repeat")
        worker(output, args.arm, args.repeat)
    elif args.action == "execute":
        arms = (args.arm,) if args.arm else ARMS
        repeats = (args.repeat,) if args.repeat else REPEATS
        execute(output, arms=arms, repeats=repeats, max_runs=args.max_runs, python=args.python,
            output_tokens=args.output_tokens)
    else:
        audit(output, reference_path=args.reference, report_path=args.report,
              allow_injected_transport=args.allow_injected_transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
