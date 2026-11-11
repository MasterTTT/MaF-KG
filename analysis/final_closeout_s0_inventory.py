#!/usr/bin/env python
"""S0 of docs/DSH_FINAL_EXPERIMENT_CLOSEOUT_PLAN_2026-10-10.md: freeze the current
state and the original evidence as a hash-bound, auditable inventory.

Read-only with respect to every experiment artifact: this script only hashes files and
reads JSON metadata.  It writes exactly two files, both under

    evaluation/paper_closeout_2026-10-10/final_closeout_v1/inventory/

    input_bindings.json      -- what the evidence is, bound by sha256
    execution_inventory.json -- what actually ran, and how each run ended

No model API is called.  No existing file is modified.

Usage:
    python analysis/final_closeout_s0_inventory.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/inventory"

# Directories that must never be hashed: the experiment venv is a junction into a
# shared runtime and would dominate the inventory with thousands of unrelated files.
EXCLUDE_DIR_NAMES = {"__pycache__", "runtime", ".pytest_cache", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

# --------------------------------------------------------------------------------------
# Groups of material the plan requires to be bound (§3 "至少绑定下列材料")
# --------------------------------------------------------------------------------------

GROUPS = [
    dict(
        group_id="merged_input",
        role="frozen_experiment_input",
        provenance=(
            "Three consecutive sections (ch2_s5, ch3_s1, ch3_s2) concatenated into one "
            "1094-line / 32853-character CRLF document; the single input shared by all four methods."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/input/**"],
    ),
    dict(
        group_id="merged_reference",
        role="frozen_reference_layer1",
        provenance=(
            "reference_merged_v1.json plus reference_digest.json, frozen before any method output "
            "was inspected (42 items, 45 intra-section and 2 cross-section relations)."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/reference/**"],
    ),
    dict(
        group_id="merged_freeze_record",
        role="freeze_record",
        provenance="FREEZE_RECORD.md and the output-cap smoke record for the merged round.",
        patterns=["evaluation/external_baselines_merged_2026-10-09/freeze/**"],
    ),
    dict(
        group_id="merged_runs_r1",
        role="method_outputs_repeat1",
        provenance="Repeat 1 outputs for Direct, MaF, EDC (v2) and iText2KG (v3, sanitised names).",
        patterns=["evaluation/external_baselines_merged_2026-10-09/runs/r1/**"],
    ),
    dict(
        group_id="merged_runs_r2",
        role="method_outputs_repeat2",
        provenance="Repeat 2 outputs for Direct, MaF, EDC (v2) and iText2KG (v3, sanitised names).",
        patterns=["evaluation/external_baselines_merged_2026-10-09/runs/r2/**"],
    ),
    dict(
        group_id="merged_runs_v1_aborted",
        role="aborted_first_attempt",
        provenance="First merged attempt, aborted; kept as provenance, never scored.",
        patterns=["evaluation/external_baselines_merged_2026-10-09/runs_v1_aborted/**"],
    ),
    dict(
        group_id="edc_parse_compatible",
        role="external_edc_parse_compatible_rerun",
        provenance=(
            "EDC rerun with the separator normalised for parsing only; replaces the empty-graph v2 "
            "runs in the comparison, which stay listed separately."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/edc_v3/**"],
    ),
    dict(
        group_id="itext_v3_sanitised",
        role="external_itext2kg_sanitised_names",
        provenance=(
            "iText2KG run whose entity and relation names were rewritten by the pinned upstream "
            "ASCII sanitiser; retained as the corrupted-name comparison point."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/itext_v3/**"],
    ),
    dict(
        group_id="itext_v4_symbol_native",
        role="external_itext2kg_symbol_native_rerun",
        provenance=(
            "iText2KG rerun keeping model-returned names and endpoints verbatim, with a separate "
            "retrieval key for embedding identity; this is the version that enters the comparison."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/itext_v4/**"],
    ),
    dict(
        group_id="itext_guarded_retry",
        role="external_itext2kg_no_progress_guard_rerun",
        provenance=(
            "Separately authorised no-progress-guard variant that completed the tasks the pinned "
            "loop could not finish; reported separately and never pooled."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/guarded/**"],
    ),
    dict(
        group_id="merged_reviews",
        role="ai_assisted_source_reviews",
        provenance=(
            "Review packets and review outputs for the merged round (legacy, v3 and v4 rounds), "
            "including status overrides and the private label mapping."
        ),
        patterns=["evaluation/external_baselines_merged_2026-10-09/review*/**"],
    ),
    dict(
        group_id="merged_derived_summaries",
        role="derived_audits_and_tables",
        provenance=(
            "Machine-generated summaries for the merged round: config parity wire checks, adapter "
            "deviation, symbol audits, four-method table and the versioned semantic score files."
        ),
        patterns=[
            "evaluation/external_baselines_merged_2026-10-09/*.json",
            "evaluation/external_baselines_merged_2026-10-09/*.md",
        ],
    ),
    dict(
        group_id="merged_plans_and_ctl",
        role="plans_and_control_files",
        provenance="Run plans and control files for the merged round.",
        patterns=[
            "evaluation/external_baselines_merged_2026-10-09/plans/**",
            "evaluation/external_baselines_merged_2026-10-09/ctl/**",
        ],
    ),
    dict(
        group_id="merged_reference_candidates",
        role="reference_candidate_pool",
        provenance="Candidate pool from which the frozen merged reference was selected.",
        patterns=["evaluation/external_baselines_merged_2026-10-09/reference_candidates/**"],
    ),
    dict(
        group_id="merged_work_and_snapshot",
        role="materialised_work_and_code_snapshot",
        provenance=(
            "Materialised input work products and the code/prompt snapshot the merged round ran "
            "against (venv junction excluded by policy)."
        ),
        patterns=[
            "evaluation/external_baselines_merged_2026-10-09/work/**",
            "evaluation/external_baselines_merged_2026-10-09/snapshot/**",
        ],
    ),
    dict(
        group_id="input_scope_runs",
        role="prefix_scope_outputs",
        provenance=(
            "Prefix runs S1 (ch2_s5 only) and S2 (ch2_s5+ch3_s1) for Direct and MaF, two repeats; "
            "S3 trajectories are the merged round itself."
        ),
        patterns=["evaluation/paper_closeout_2026-10-10/input_scope/runs/**"],
    ),
    dict(
        group_id="input_scope_reviews",
        role="prefix_scope_reviews",
        provenance=(
            "R01-R07 review packets and review outputs, the duplicate adjudication, the private "
            "label mapping and the review policy."
        ),
        patterns=["evaluation/paper_closeout_2026-10-10/input_scope/review_scope/**"],
    ),
    dict(
        group_id="input_scope_reference",
        role="prefix_scope_references",
        provenance=(
            "Per-scope references and the pre-frozen ch2_s5 common subset used for the paired "
            "cross-scope comparison."
        ),
        patterns=["evaluation/paper_closeout_2026-10-10/input_scope/reference/**"],
    ),
    dict(
        group_id="input_scope_control",
        role="prefix_scope_control",
        provenance=(
            "Prefix-identity evidence, common-subset retention, semantic scores, budgets, plans, "
            "manifest and the code snapshot for the prefix runs."
        ),
        patterns=[
            "evaluation/paper_closeout_2026-10-10/input_scope/*.json",
            "evaluation/paper_closeout_2026-10-10/input_scope/*.md",
            "evaluation/paper_closeout_2026-10-10/input_scope/budgets/**",
            "evaluation/paper_closeout_2026-10-10/input_scope/plans/**",
            "evaluation/paper_closeout_2026-10-10/input_scope/work/**",
            "evaluation/paper_closeout_2026-10-10/input_scope/snapshot/**",
        ],
    ),
    dict(
        group_id="prerequisite_context",
        role="prerequisite_context_conditions",
        provenance=(
            "Condition A (pooled candidate pool), condition B (real source text) and the unexecuted "
            "condition C payloads, with their budgets, comparison and scores."
        ),
        patterns=["evaluation/paper_closeout_2026-10-10/prerequisite_context/**"],
    ),
    dict(
        group_id="prerequisite_confirmation",
        role="prerequisite_new_target_confirmation",
        provenance=(
            "New four-target confirmation: reference_v2, selection, both review payloads and "
            "responses, the single-stage proposal runs including the superseded 4000-token outputs, "
            "and the scores under both conditions."
        ),
        patterns=["evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/**"],
    ),
    dict(
        group_id="prerequisite_topology",
        role="prerequisite_topology_diagnostic",
        provenance="Offline partial-order and transitive-closure diagnostic over the frozen references.",
        patterns=["evaluation/paper_closeout_2026-10-10/prerequisite_topology/**"],
    ),
    dict(
        group_id="kst_probe",
        role="kst_probe_attempt",
        provenance=(
            "KST-style probe attempt: prepared probes, requests, native responses and the full, "
            "sharp and default PSR reports. Retained as an unvalidated attempt."
        ),
        patterns=["evaluation/paper_closeout_2026-10-10/kst_probe/**"],
    ),
    dict(
        group_id="legacy_prerequisite_27",
        role="legacy_prerequisite_experiment",
        provenance=(
            "Original 27-pair prerequisite experiment: real-KU pipeline, candidate retrieval, "
            "assisted review and the audit that produced the original reference."
        ),
        patterns=[
            "evaluation/prerequisite_new_targets_2026-10-07/**",
            "evaluation/prerequisite_real_ku_2026-10-05/**",
            "evaluation/prerequisite_candidate_retrieval_2026-10-07/**",
            "evaluation/prerequisite_assisted_review_2026-10-07/**",
            "evaluation/prerequisite_audit_2026-10-03/**",
            "evaluation/prerequisite_preflight_2026-10-05/**",
        ],
    ),
    dict(
        group_id="scorers_and_preparation",
        role="scoring_and_preparation_code",
        provenance=(
            "Current scoring, validation, preparation and audit scripts in analysis/. Production "
            "extraction code is out of scope for this round and is bound separately."
        ),
        patterns=["analysis/*.py", "analysis/**/*.md"],
    ),
    dict(
        group_id="production_prompts",
        role="production_prompts",
        provenance="Production prompt templates as they stand in the worktree (read-only this round).",
        patterns=["prompts/**"],
    ),
    dict(
        group_id="validators_and_tests",
        role="validators_and_tests",
        provenance="Repository validators and tests used to check review packets and artifacts.",
        patterns=["tests/**", "scripts/validate*.py", "scripts/*validate*.py"],
    ),
]

# --------------------------------------------------------------------------------------
# Run discovery: find every unit that actually produced artifacts and classify its end state
# --------------------------------------------------------------------------------------

SCAN_ROOTS = [
    ("merged_four_method", "evaluation/external_baselines_merged_2026-10-09/runs"),
    ("edc_parse_compatible", "evaluation/external_baselines_merged_2026-10-09/edc_v3"),
    ("itext_v3_sanitised", "evaluation/external_baselines_merged_2026-10-09/itext_v3"),
    ("itext_v4_symbol_native", "evaluation/external_baselines_merged_2026-10-09/itext_v4"),
    ("itext_guarded_retry", "evaluation/external_baselines_merged_2026-10-09/guarded"),
    ("prefix_scope", "evaluation/paper_closeout_2026-10-10/input_scope/runs"),
    ("prerequisite_context", "evaluation/paper_closeout_2026-10-10/prerequisite_context"),
    ("prerequisite_confirmation", "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation"),
    ("kst_probe", "evaluation/paper_closeout_2026-10-10/kst_probe"),
]

LEDGER_PATTERNS = [
    "evaluation/paper_closeout_2026-10-10/**/budgets/*.json",
    "evaluation/external_baselines_merged_2026-10-09/budgets/*.json",
]

SCOPE_NOTES = {
    "merged_four_method": "S3: the full concatenated three-section input (1094 lines).",
    "edc_parse_compatible": "S3: full concatenated input, EDC with parsing normalised.",
    "itext_v3_sanitised": "S3: full concatenated input, iText2KG with upstream-sanitised names.",
    "itext_v4_symbol_native": "S3: full concatenated input, iText2KG with verbatim names.",
    "itext_guarded_retry": "S3: full concatenated input, iText2KG with the no-progress guard.",
    "prefix_scope": "S1 (ch2_s5) and S2 (ch2_s5 + ch3_s1) prefixes; S3 is the merged round.",
    "prerequisite_context": "Legacy 27-pair prerequisite diagnostic.",
    "prerequisite_confirmation": "New four-target prerequisite confirmation.",
    "kst_probe": "KST-style probe attempt over the frozen reference.",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def excluded(path: Path) -> bool:
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return any(part in EXCLUDE_DIR_NAMES for part in path.parts)


def expand(patterns) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        # pathlib's recursive wildcard matches directories, not the files inside them, so a
        # pattern ending in '**' needs a trailing '*' to enumerate the files it reaches.
        candidates = [pattern]
        if pattern.endswith("/**"):
            candidates.append(pattern + "/*")
        for candidate in candidates:
            for path in REPO.glob(candidate):
                if path.is_file() and not excluded(path):
                    found[str(path.relative_to(REPO)).replace("\\", "/")] = path
    return [found[key] for key in sorted(found)]


def git_state() -> dict:
    def run(*args):
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
        ).stdout.strip()

    porcelain = run("status", "--porcelain")
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    return {
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "head": run("rev-parse", "HEAD"),
        "porcelain_entries": len(lines),
        "tracked_modified": [ln[3:] for ln in lines if not ln.startswith("??")],
        "untracked_entries": sum(1 for ln in lines if ln.startswith("??")),
        "note": (
            "Recorded at S0. The worktree is intentionally dirty; this round does not reset, "
            "checkout, clean or bulk-stage anything."
        ),
    }


def build_input_bindings() -> dict:
    groups = []
    total_files = 0
    total_bytes = 0
    for spec in GROUPS:
        files = expand(spec["patterns"])
        records = []
        for path in files:
            rel = str(path.relative_to(REPO)).replace("\\", "/")
            records.append({"path": rel, "sha256": sha256_file(path), "bytes": path.stat().st_size})
        digest = hashlib.sha256()
        for rec in records:
            digest.update(f"{rec['path']}:{rec['sha256']}\n".encode("utf-8"))
        group = {
            "group_id": spec["group_id"],
            "role": spec["role"],
            "provenance": spec["provenance"],
            "patterns": spec["patterns"],
            "file_count": len(records),
            "total_bytes": sum(r["bytes"] for r in records),
            "group_sha256": digest.hexdigest(),
            "files": records,
        }
        if not records:
            group["warning"] = "no file matched this group's patterns"
        groups.append(group)
        total_files += len(records)
        total_bytes += group["total_bytes"]

    return {
        "schema": "dsh.final_closeout.input_bindings.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "analysis/final_closeout_s0_inventory.py",
        "repo_root": str(REPO),
        "git": git_state(),
        "hashing_policy": {
            "algorithm": "sha256",
            "excluded_dir_names": sorted(EXCLUDE_DIR_NAMES),
            "excluded_suffixes": sorted(EXCLUDE_SUFFIXES),
            "rationale": (
                "snapshot/runtime is a junction into the shared experiment virtualenv; hashing it "
                "would bind thousands of unrelated third-party files. Vendor code inside each "
                "snapshot is hashed, the venv is not."
            ),
            "group_sha256": "sha256 over sorted 'path:sha256' lines of the group's files",
        },
        "groups": groups,
        "totals": {"groups": len(groups), "files": total_files, "bytes": total_bytes},
    }


def graph_shape(path: Path) -> dict:
    """Return a small structural summary of a graph file without judging its quality.

    Three native schemas appear in this project and all three are counted here:
      * our own graph: {"nodes": [...], "relations": [...]}
      * iText2KG native: {"entities": [...], "relationships": [...]}
      * EDC native: [[triple-or-null, ...]] -- one document, one slot per alignment attempt
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        return {"parseable": False, "error": f"{type(exc).__name__}: {exc}"}

    if isinstance(data, dict):
        if "nodes" in data or "relations" in data:
            nodes = data.get("nodes")
            rels = data.get("relations")
            n = len(nodes) if isinstance(nodes, list) else None
            r = len(rels) if isinstance(rels, list) else None
            return {
                "parseable": True,
                "container": "node_relation_dict",
                "nodes": n,
                "relations": r,
                "empty": (n in (0, None)) and (r in (0, None)),
            }
        if "entities" in data or "relationships" in data:
            ents = data.get("entities")
            rels = data.get("relationships")
            e = len(ents) if isinstance(ents, list) else None
            r = len(rels) if isinstance(rels, list) else None
            shape = {
                "parseable": True,
                "container": "itext2kg_dict",
                "entities": e,
                "relationships": r,
                "empty": (e in (0, None)) and (r in (0, None)),
                "top_level_keys": sorted(data.keys()),
            }
            if isinstance(data.get("method"), str):
                shape["method_field"] = data["method"]
            return shape
        return {
            "parseable": True,
            "container": "dict",
            "top_level_keys": sorted(data.keys())[:20],
            "empty": None,
        }

    if isinstance(data, list):
        if data and isinstance(data[0], list):
            slots = data[0]
            triples = sum(1 for slot in slots if isinstance(slot, list) and len(slot) >= 3)
            null_slots = sum(1 for slot in slots if slot is None)
            return {
                "parseable": True,
                "container": "edc_triple_list",
                "documents": len(data),
                "slots": len(slots),
                "triples": triples,
                "null_slots": null_slots,
                "empty": triples == 0,
            }
        return {
            "parseable": True,
            "container": "list",
            "items": len(data),
            "empty": len(data) == 0,
        }
    return {"parseable": True, "container": type(data).__name__, "empty": None}


def classify_unit(unit_dir: Path, experiment: str) -> dict:
    completion_path = unit_dir / "completion.json"
    manifest_path = unit_dir / "manifest.json"
    graph_path = unit_dir / "graph.json"

    completion = None
    if completion_path.is_file():
        try:
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            completion = {"unreadable": f"{type(exc).__name__}: {exc}"}

    manifest = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            manifest = {"unreadable": f"{type(exc).__name__}: {exc}"}

    graph = graph_shape(graph_path) if graph_path.is_file() else None

    responses = sorted(p for p in unit_dir.glob("responses/**/*") if p.is_file())
    native_responses = sorted(p for p in unit_dir.glob("native/responses/**/*") if p.is_file())
    requests = sorted(p for p in unit_dir.glob("requests/**/*") if p.is_file())
    native_requests = sorted(p for p in unit_dir.glob("native/requests/**/*") if p.is_file())
    logs = sorted(p for p in unit_dir.glob("logs/**/*") if p.is_file())

    status = "not_run"
    detail = ""
    manifest_status = str((manifest or {}).get("status", "")).lower() if isinstance(manifest, dict) else ""
    if manifest_status == "failed":
        status = "failed"
        detail = str(manifest.get("error_type") or manifest.get("error") or "manifest reports failure")
    elif manifest_status in {"running", "prepared"}:
        status = "incomplete_aborted"
        detail = (
            f"manifest status is {manifest_status!r} and no final graph was produced; the run was "
            "superseded and is kept as provenance only"
        )
    elif graph is not None and graph.get("parseable") and graph.get("empty") is False:
        status = "completed_with_graph"
        if completion is not None and str(completion.get("status", "")).lower() != "completed":
            detail = f"completion.json says {completion.get('status')!r} but a non-empty graph exists"
    elif graph is not None and graph.get("parseable") and graph.get("empty") is True:
        status = "completed_empty_graph"
        detail = "run finished but the final graph carries no triples, nodes or relations"
    elif completion is not None and str(completion.get("status", "")).lower() == "completed":
        status = "completed_without_graph_file"
    elif native_responses or responses:
        status = "failed_no_graph"
        detail = "responses exist but no final graph was produced"

    method = None
    for source in (completion, manifest):
        if isinstance(source, dict) and source.get("method"):
            method = source["method"]
            break
    if method is None:
        method = unit_dir.name

    calls = None
    for source in (completion, manifest):
        if isinstance(source, dict):
            for key in ("calls", "call_count", "chat_calls"):
                if source.get(key) is not None:
                    calls = source[key]
                    break
        if calls is not None:
            break

    unit = {
        "unit_id": f"{experiment}::{unit_dir.relative_to(REPO).as_posix()}",
        "experiment": experiment,
        "experiment_label": (manifest or {}).get("experiment") if isinstance(manifest, dict) else None,
        "scope_note": SCOPE_NOTES.get(experiment, ""),
        "run_dir": unit_dir.relative_to(REPO).as_posix(),
        "method": method,
        "status": status,
        "status_detail": detail,
        "calls": calls,
        "graph": graph,
        "artifacts": {
            "completion_json": completion_path.relative_to(REPO).as_posix()
            if completion_path.is_file()
            else None,
            "manifest_json": manifest_path.relative_to(REPO).as_posix()
            if manifest_path.is_file()
            else None,
            "graph_json": graph_path.relative_to(REPO).as_posix() if graph_path.is_file() else None,
            "request_count": len(requests) + len(native_requests),
            "response_count": len(responses) + len(native_responses),
            "log_count": len(logs),
        },
    }
    if status == "failed_no_graph":
        unit["failure_evidence"] = [p.relative_to(REPO).as_posix() for p in (logs + responses)[:6]]

    stdout_log = unit_dir / "stdout.log"
    if stdout_log.is_file() and status in {"failed", "failed_no_graph", "incomplete_aborted"}:
        tail = stdout_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        error_lines = [ln.strip() for ln in tail if "Error" in ln or "error:" in ln]
        unit["failure_evidence"] = unit.get("failure_evidence", [])
        unit["failure_evidence"].append(stdout_log.relative_to(REPO).as_posix())
        if error_lines:
            unit["failure_last_error_line"] = error_lines[-1][:400]
    return unit


def discover_units() -> list[dict]:
    units: list[dict] = []
    for experiment, rel_root in SCAN_ROOTS:
        root = REPO / rel_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_dir() or excluded(path):
                continue
            if not (
                (path / "completion.json").is_file()
                or (path / "manifest.json").is_file()
                or (path / "graph.json").is_file()
                or (path / "stdout.log").is_file()
            ):
                continue
            units.append(classify_unit(path, experiment))
    return units


def request_field_audit() -> dict:
    """Record the fields that were actually sent, not the fields a manifest claims."""
    samples = {}
    probes = [
        ("merged.r1.direct", "evaluation/external_baselines_merged_2026-10-09/runs/r1/merged/direct/native/requests"),
        ("merged.r1.maf", "evaluation/external_baselines_merged_2026-10-09/runs/r1/merged/maf/native/requests"),
        ("merged.r1.edc", "evaluation/external_baselines_merged_2026-10-09/runs/r1/merged/edc/requests"),
        ("prefix.r1.s1.maf", "evaluation/paper_closeout_2026-10-10/input_scope/runs/r1/s1/maf/native/requests"),
        ("prefix.r1.s1.direct", "evaluation/paper_closeout_2026-10-10/input_scope/runs/r1/s1/direct/native/requests"),
        ("prereq.ku.batch1", "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/arm_ku/requests"),
        ("prereq.ctx.batch1", "evaluation/paper_closeout_2026-10-10/prerequisite_confirmation/arm_ctx/requests"),
        ("prereq.context.armB", "evaluation/paper_closeout_2026-10-10/prerequisite_context/armB/requests"),
        ("kst.r1", "evaluation/paper_closeout_2026-10-10/kst_probe/requests"),
        ("itext.v4.r1", "evaluation/external_baselines_merged_2026-10-09/itext_v4/r1/merged/itext2kg/requests"),
    ]
    for label, rel in probes:
        d = REPO / rel
        if not d.is_dir():
            samples[label] = {"available": False, "dir": rel}
            continue
        files = sorted(p for p in d.glob("*.json") if p.is_file())
        if not files:
            samples[label] = {"available": False, "dir": rel, "note": "directory present, no request files"}
            continue
        first = files[0]
        try:
            data = json.loads(first.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            samples[label] = {"available": True, "file": first.name, "unreadable": str(exc)}
            continue
        entry: dict = {"available": True, "file": first.relative_to(REPO).as_posix(),
                       "request_count": len(files)}
        if isinstance(data, dict) and isinstance(data.get("body"), dict):
            body = data["body"]
            entry["transport"] = "native wrapper"
            entry["endpoint"] = data.get("endpoint")
            entry["body_keys"] = sorted(body.keys())
            entry["wrapper_option_keys"] = sorted(data.get("wrapper_options", {}).keys())
            entry["schema_transport"] = data.get("schema_transport")
            entry["temperature_in_body"] = "temperature" in body
            entry["temperature_value"] = body.get("temperature", "absent")
            entry["max_tokens_in_body"] = body.get("max_tokens", "absent")
            entry["stream_in_body"] = body.get("stream", "absent")
            entry["thinking_related_keys"] = sorted(
                k for k in body if k in {"reasoning_effort", "thinking", "reasoning", "include_reasoning"}
            )
            entry["response_format_in_body"] = "response_format" in body
            entry["message_roles"] = [m.get("role") for m in body.get("messages", [])]
        elif isinstance(data, dict):
            entry["transport"] = "plain request record"
            entry["top_level_keys"] = sorted(data.keys())
            payload = data.get("payload")
            if isinstance(payload, dict):
                entry["payload_keys"] = sorted(payload.keys())
                entry["temperature_in_payload"] = "temperature" in payload
        samples[label] = entry
    return {
        "note": (
            "Read from actual request artifacts. Fields that were never recorded are reported as "
            "absent, not guessed. Timeouts and attempt counts are client-side and are not part of "
            "the request body."
        ),
        "client_side_fields_not_recorded": {
            "request_timeout": "not recorded inside the request artifact; only the manifest states 600 s",
            "attempts": "not recorded inside the request artifact; the manifests state max_retries 0",
            "reasoning_effort_value": (
                "the key is present in the body; the per-sample value is recorded in the audit entry "
                "where available and is not re-derived here"
            ),
        },
        "samples": samples,
    }


def ledger_summary() -> list[dict]:
    ledgers = []
    for pattern in LEDGER_PATTERNS:
        for path in sorted(REPO.glob(pattern)):
            if not path.is_file() or excluded(path):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                ledgers.append({"path": path.relative_to(REPO).as_posix(), "unreadable": str(exc)})
                continue
            if not isinstance(data, dict) or "accounting_tokens" not in data:
                continue
            requests = data.get("requests")
            ledgers.append(
                {
                    "path": path.relative_to(REPO).as_posix(),
                    "sha256": sha256_file(path),
                    "model": data.get("model"),
                    "usd_limit": data.get("usd_limit"),
                    "token_limit": data.get("token_limit"),
                    "accounting_usd": data.get("accounting_usd"),
                    "accounting_tokens": data.get("accounting_tokens"),
                    "request_entries": len(requests) if isinstance(requests, list) else None,
                    "within_gate": (
                        data.get("accounting_usd", 0) <= data.get("usd_limit", 0)
                        and data.get("accounting_tokens", 0) <= data.get("token_limit", 0)
                    ),
                }
            )
    return ledgers


def expected_units() -> dict:
    """The unit grid each experiment was designed to produce, so that gaps are visible."""
    grid: dict[str, list[str]] = {}
    merged = "evaluation/external_baselines_merged_2026-10-09"
    grid["merged_four_method"] = [
        f"{merged}/runs/{repeat}/merged/{method}"
        for repeat in ("r1", "r2")
        for method in ("direct", "maf", "edc", "itext2kg")
    ]
    grid["edc_parse_compatible"] = [
        f"{merged}/edc_v3/{repeat}/merged/edc" for repeat in ("r1", "r2")
    ]
    grid["itext_v3_sanitised"] = [
        f"{merged}/itext_v3/{repeat}/merged/itext2kg" for repeat in ("r1", "r2")
    ]
    grid["itext_v4_symbol_native"] = [
        f"{merged}/itext_v4/{repeat}/merged/itext2kg" for repeat in ("r1", "r2")
    ]
    grid["itext_guarded_retry"] = [
        f"{merged}/guarded/{repeat}/merged/itext2kg" for repeat in ("r1", "r2")
    ]
    prefix = "evaluation/paper_closeout_2026-10-10/input_scope/runs"
    grid["prefix_scope"] = [
        f"{prefix}/{repeat}/{scope}/{method}"
        for repeat in ("r1", "r2")
        for scope in ("s1", "s2")
        for method in ("direct", "maf")
    ]
    return grid


def coverage(units: list[dict]) -> dict:
    found = {unit["run_dir"] for unit in units}
    report = {}
    for experiment, expected in expected_units().items():
        missing = [path for path in expected if path not in found]
        report[experiment] = {
            "expected": len(expected),
            "found": sum(1 for path in expected if path in found),
            "missing": missing,
        }
    extra = sorted(found - {p for paths in expected_units().values() for p in paths})
    report["_units_outside_the_expected_grid"] = extra
    return report


def build_execution_inventory() -> dict:
    units = discover_units()
    by_status: dict[str, int] = {}
    for unit in units:
        by_status[unit["status"]] = by_status.get(unit["status"], 0) + 1
    return {
        "schema": "dsh.final_closeout.execution_inventory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "analysis/final_closeout_s0_inventory.py",
        "status_vocabulary": {
            "completed_with_graph": "finished and produced a non-empty final graph",
            "completed_empty_graph": "finished but the final graph carries no triples, nodes or relations",
            "completed_without_graph_file": "completion record claims success, no graph artifact found",
            "failed": "manifest reports failure (for example a call ceiling)",
            "failed_no_graph": "responses exist but no final graph; includes contract violations",
            "incomplete_aborted": "manifest still says running/prepared and no graph was produced",
            "not_run": "no artifacts found",
        },
        "counting_rule": (
            "A successful HTTP response is not a successful graph. Status is derived from the final "
            "graph artifact first, then the completion record, then the manifest."
        ),
        "units": units,
        "status_counts": by_status,
        "coverage_against_design": coverage(units),
        "ledgers": ledger_summary(),
        "request_field_audit": request_field_audit(),
        "known_open_items": [
            "Direct repeat 1 on the S1 prefix has no graph: two independent contract violations, "
            "kept as evidence and not resampled in this round.",
            "Condition C of the legacy 27-pair prerequisite diagnostic was never executed.",
            "The KST probe is retained as an unvalidated attempt and is not accepted as system-level "
            "prerequisite validity evidence.",
        ],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bindings = build_input_bindings()
    execution = build_execution_inventory()

    bindings_path = OUT_DIR / "input_bindings.json"
    execution_path = OUT_DIR / "execution_inventory.json"
    bindings_path.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {bindings_path.relative_to(REPO).as_posix()}")
    print(f"  groups={bindings['totals']['groups']} files={bindings['totals']['files']} "
          f"bytes={bindings['totals']['bytes']}")
    print(f"wrote {execution_path.relative_to(REPO).as_posix()}")
    print(f"  units={len(execution['units'])} status_counts={execution['status_counts']}")
    print(f"  ledgers={len(execution['ledgers'])}")
    empty = [g["group_id"] for g in bindings["groups"] if g.get("warning")]
    if empty:
        print(f"  WARNING empty groups: {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
