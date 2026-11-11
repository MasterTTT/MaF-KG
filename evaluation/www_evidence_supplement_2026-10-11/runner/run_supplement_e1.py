"""E1 driver for the WWW evidence supplement.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md sections 3.1-3.3.

This is a NEW, independently named runner. It does not modify the frozen
pipeline: it imports the frozen snapshot modules by path and changes ONLY

  * which reference supplies the section list,
  * which output directory and budget ledger root are used,
  * the per-method safety caps.

Everything that produces graph content -- chunking, prompts, the extractor, the
EDC/iText2KG adapters, the transport -- is the frozen code, unmodified. The
deltas are written to `runner_deltas.json` with the frozen file hashes so a
reviewer can see exactly what was adapted.

Adapted from the frozen `run_external_baselines.prepare`, whose own
`BASE = ROOT/"evaluation/external_baselines_2026-10-08"` resolves against the
repository root rather than the snapshot, and whose budget root is hardcoded to
the earlier experiment's ledgers. Reusing those ledgers would mix this round's
spend with the previous round's, so a fresh root is mandatory.

Usage:
  python run_supplement_e1.py prepare  <output-dir> --reference <ref.json> \
      --snapshot-source <frozen snapshot> --embedding-lock <lock.json> \
      --budget-root <dir> --caps <caps.json>
  python run_supplement_e1.py execute  <output-dir> [--methods ...] [--repeat N] [--max-jobs N]
  python run_supplement_e1.py audit    <output-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
SUPPLEMENT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
RUNNER_DIR = SUPPLEMENT / "runner"
# The composite snapshot is the only artifact that can drive all four reported
# arm configurations. See runner/build_composite_snapshot.py for the provenance
# of every file in it.
COMPOSITE = RUNNER_DIR / "composite_snapshot"
DEFAULT_SNAPSHOT = COMPOSITE if COMPOSITE.exists() else (
    WORKSPACE / "evaluation" / "external_baselines_merged_2026-10-09" / "snapshot")
DEFAULT_EMBEDDING_LOCK = DEFAULT_SNAPSHOT / "embedding_lock.json"
DEFAULT_VENV = WORKSPACE / "evaluation" / "external_baselines_2026-10-08" / "runtime"

# Manifest flags the adapters read. Verified against the manifests that produced
# the reported rows: edc_v3 set edc_parse_compat, itext_v4 set the three itext
# flags. Omitting any of them silently changes the arm's configuration.
REQUIRED_ARM_FLAGS = {
    "edc_parse_compat": True,
    "itext_no_progress_limit": 3,
    "itext_preserve_symbols": True,
    "itext_preserve_names": True,
}

# Visible-memory budgets per job. "e1" is the frozen primary configuration; the
# E3 arm keeps chunking, the persistent program graph, merging and the prompt
# identical and only empties what the model can see. 2 bytes is the value the
# earlier memory ablation used for its no-visible-memory arm.
MEMORY_MODES = {
    "e1": {"node_bytes": 12000, "relation_bytes": 6000, "methods": [], "label": "frozen primary"},
    "no_visible_memory": {"node_bytes": 2, "relation_bytes": 2, "methods": ["maf"],
                          "label": "E3: model-visible memory emptied, everything else unchanged"},
}

# The frozen worker reads these manifest keys directly. A missing key aborts the
# job inside the subprocess, after the output directory exists but before any
# request is sent. That is exactly what produced attempt A1, so `audit` now
# asserts every key is present before a run is allowed to start.
WORKER_REQUIRED_KEYS = (
    "snapshot", "budget_root", "session", "task_max_calls", "task_max_seconds",
    "per_method_budget_usd", "per_method_token_limit", "shared_domain_instruction",
    "embedding_lock",
)
WORKER_OPTIONAL_KEYS = (
    "edc_replay_dirs", "edc_parse_compat", "itext_no_progress_limit",
    "itext_preserve_symbols", "itext_preserve_names",
)

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_frozen(snapshot: Path):
    """Import the frozen snapshot modules by path; nothing is edited."""
    scripts = snapshot / "scripts"
    for name in ("run_direct_extraction_baseline", "run_external_baselines",
                 "run_memory_ablation", "external_baseline_runtime",
                 "prospective_native_client", "probe_go_streaming"):
        if not (scripts / f"{name}.py").exists():
            raise FileNotFoundError(scripts / f"{name}.py")
    sys.path.insert(0, str(scripts))
    import run_direct_extraction_baseline as baseline
    import run_external_baselines as reb
    import run_memory_ablation as rma

    if Path(baseline.__file__).resolve() != (scripts / "run_direct_extraction_baseline.py").resolve():
        raise RuntimeError(f"wrong module resolved: {baseline.__file__}")
    if Path(reb.__file__).resolve() != (scripts / "run_external_baselines.py").resolve():
        raise RuntimeError(f"wrong module resolved: {reb.__file__}")
    return baseline, reb, rma


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------

def prepare(output: Path, *, reference_path: Path, snapshot_source: Path,
            embedding_lock: Path, budget_root: Path, caps: dict,
            task_max_calls: int, task_max_seconds: int,
            scope: str, memory_mode: str = "e1") -> dict:
    baseline, reb, rma = load_frozen(snapshot_source)
    reb.setup()  # sets baseline.COMMON = reb.CONFIG; required by audit_prepared

    reference = read(reference_path)
    if "sections" not in reference:
        raise ValueError("reference must carry a 'sections' list for the runner")

    # The caps file wraps the per-method map under a "caps" key alongside its own
    # documentation; accept either shape.
    caps = caps.get("caps", caps) if isinstance(caps, dict) else caps
    for method in ("maf", "direct", "edc", "itext2kg"):
        if method not in caps or "usd" not in caps[method] or "tokens" not in caps[method]:
            raise ValueError(f"caps file lacks a usable entry for {method}")

    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"

    # --- frozen code, copied verbatim ------------------------------------
    copied: dict[str, str] = {}
    for name in ("agents", "prompts", "scripts"):
        shutil.copytree(snapshot_source / name, snapshot / name, ignore=IGNORE)
    for path in sorted((snapshot / "agents").rglob("*.py")) + sorted((snapshot / "prompts").glob("*.txt")) \
            + sorted((snapshot / "scripts").glob("*.py")):
        copied[str(path.resolve())] = sha256(path)

    # vendor tree is small enough to copy; the venv is recorded, not copied.
    shutil.copytree(snapshot_source / "vendor", snapshot / "vendor", ignore=IGNORE)
    for path in sorted((snapshot / "vendor").rglob("*")):
        if path.is_file():
            copied[str(path.resolve())] = sha256(path)

    for name in ("embedding_lock.json", "requirements.lock.txt"):
        src = snapshot_source / name
        if src.exists():
            shutil.copy2(src, snapshot / name)
            copied[str((snapshot / name).resolve())] = sha256(snapshot / name)

    lock = read(embedding_lock)
    for item in lock["files"]:
        if sha256(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"embedding asset changed: {item['path']}")

    # Guard against the trap that produced the earlier all-null EDC graph: the
    # base and itext_v4 snapshots have no parse_compat support, so an EDC run
    # started from them silently drops every full-width-colon definition.
    guards = {
        "scripts/external_edc.py": ("parse_compat", "U+FF1A parse compatibility"),
        "scripts/external_itext2kg.py": ("preserve_symbols", "display-symbol preservation"),
        "scripts/run_external_baselines.py": ("parse_compat", "parse_compat forwarding"),
    }
    for rel, (needle, what) in guards.items():
        text = (snapshot / rel).read_text(encoding="utf-8")
        if needle not in text:
            raise ValueError(
                f"{rel} lacks {what} ({needle!r}); this snapshot would run the arm in the "
                f"wrong configuration. Rebuild the composite snapshot.")

    provenance = snapshot_source / "composite_provenance.json"
    if provenance.exists():
        shutil.copy2(provenance, snapshot / "composite_provenance.json")
        copied[str((snapshot / "composite_provenance.json").resolve())] = sha256(
            snapshot / "composite_provenance.json")

    write_json(snapshot / "domain.json",
               {"instruction": reb.DOMAIN, "edc_target_schema": reb.TARGET_SCHEMA})
    copied[str((snapshot / "domain.json").resolve())] = sha256(snapshot / "domain.json")

    semantic = {
        "status": "This round's source-reviewed reference; assistant source review, "
                  "author approval pending",
        "nodes": reference["nodes"],
        "relations": reference.get("relations_provisional",
                                   [r for r in reference.get("relations", [])
                                    if r.get("type") != "involves"]),
        "alignment": "Actual native output must self-sufficiently express meaning; "
                     "no source-as-output supplementation.",
        "no_total_f1": "Required recall and all-prediction source support are not one "
                       "closed evaluation universe.",
    }
    write_json(snapshot / "semantic_reference.json", semantic)
    copied[str((snapshot / "semantic_reference.json").resolve())] = sha256(
        snapshot / "semantic_reference.json")

    # --- per-section planning, exactly as the frozen prepare does --------
    module = rma.load_module(snapshot / "agents" / "Extractor.py")
    sections = []
    for section in reference["sections"]:
        source = Path(section["source"])
        if sha256(source).lower() != section["source_sha256"].lower():
            raise ValueError(f"reference source version changed: {source}")
        original = snapshot / "source" / source.name
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, original)
        full_lines = original.read_text(encoding="utf-8").splitlines(keepends=True)
        target = snapshot / "input" / f"{section['id']}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(full_lines[section["start_line"] - 1:section["end_line"]]),
            encoding="utf-8", newline="\n")

        extractor = module.Extractor(input_path=target, verbose=False)
        state = extractor._chunk_document(extractor._load_document(
            {**reb.CONFIG, "input_path": str(target), "include_exercises": False,
             "diagnostics": {}}))
        chunks = [asdict(chunk) for chunk in state["chunks"]]
        once = baseline.direct_chunk(module, chunks, state["raw_lines"])
        selected = baseline.shared_evidence(chunks, state["raw_lines"])

        plan_dir = output / "plans" / section["id"]
        plan_dir.mkdir(parents=True, exist_ok=True)
        templates = {
            "sequential": [json.loads(extractor._chunk_extraction_prompt(
                chunk, str(target), [], [])) for chunk in state["chunks"]],
            "direct": [json.loads(extractor._chunk_extraction_prompt(
                once, str(target), [], []))],
        }
        write_json(plan_dir / "chunks.json", chunks)
        write_json(plan_dir / "direct_chunk.json", asdict(once))
        write_json(plan_dir / "templates.json", templates)
        write_json(plan_dir / "reachability.json",
                   baseline.reference_reachability(reference, section, selected,
                                                   state["raw_lines"]))
        write_json(plan_dir / "evidence_map.json", {
            "source_line_offset": section["start_line"] - 1,
            "selected_local_lines": list(selected),
            "excluded_nonempty_local_lines": [
                n for n, body in enumerate(state["raw_lines"], 1)
                if body.strip() and n not in selected],
            "source_chars": sum(len(body) for body in selected.values()),
            "note": "Both arms use this exact core evidence union, including any example "
                    "blocks retained by the frozen parser; the common prompt determines "
                    "node admission. Sequential neighboring navigation is recorded separately.",
        })
        for path in (original, target, *sorted(plan_dir.glob("*.json"))):
            copied[str(path.resolve())] = sha256(path)

        sections.append({**{k: v for k, v in section.items() if k != "sections"},
                         "input": str(target), "plan_dir": str(plan_dir),
                         "chunk_count": len(chunks), "core_line_count": len(selected)})
        print(f"prepared {section['id']}: {len(chunks)} sequential chunks / 1 direct request; "
              f"core lines {len(selected)}", flush=True)

    # --- jobs: interleave methods so no method always runs last ----------
    methods = list(reb.METHODS)
    memory = MEMORY_MODES[memory_mode]
    jobs = []
    for repeat in (1, 2):
        for index, section in enumerate(sections):
            order = methods[((repeat + index) % len(methods)):]
            order = order + methods[:((repeat + index) % len(methods))]
            for method in order:
                job = {
                    "method": method, "section": section["id"], "repeat": repeat,
                    "directory": str(output / "runs" / f"r{repeat}" / section["id"] / method),
                    "status": "pending",
                }
                if memory_mode != "e1" and method in memory.get("methods", ["maf"]):
                    job["memory_node_bytes"] = memory["node_bytes"]
                    job["memory_relation_bytes"] = memory["relation_bytes"]
                jobs.append(job)

    # Guard: an E1 job must resolve to exactly the frozen budgets. Hunk 2 of the
    # composite runner reads these keys with those defaults, so this asserts the
    # E1 configuration is unchanged by the E3 support.
    for job in jobs:
        resolved = (int(job.get("memory_node_bytes", 12000)),
                    int(job.get("memory_relation_bytes", 6000)))
        expected = MEMORY_MODES["e1"]["node_bytes"], MEMORY_MODES["e1"]["relation_bytes"]
        if memory_mode == "e1" and resolved != expected:
            raise ValueError(f"E1 memory budget drifted: {resolved} != {expected}")

    manifest = {
        "experiment": "www-supplement-e1-content-comparison",
        "status": "prepared",
        "snapshot": str(snapshot),
        "snapshot_source": str(snapshot_source.resolve()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(uuid.uuid4()),
        "common": reb.CONFIG,
        "reference": str(reference_path.resolve()),
        "reference_sha256": sha256(reference_path),
        "budget_root": str(budget_root.resolve()),
        "embedding_lock": str(snapshot / "embedding_lock.json"),
        "methods": methods,
        # adapter behaviour flags; the adapters read these from the manifest
        **REQUIRED_ARM_FLAGS,
        "sections": sections,
        "jobs": jobs,
        "artifacts": [{"path": path, "sha256": digest}
                      for path, digest in sorted(copied.items())],
        # The frozen worker reads these two as scalars, one ledger per method.
        # Per-method caps cannot be expressed through it, so the scalar is the
        # loosest per-method cap and the per-method detail is kept alongside for
        # documentation. Worst case across four methods therefore stays inside
        # the stage cap recorded in budgets/budget_plan.json.
        "per_method_budget_usd": max(v["usd"] for v in caps.values()),
        "per_method_token_limit": max(v["tokens"] for v in caps.values()),
        "per_method_caps": caps,
        "caps_effective_note": "one scalar gate per method ledger; the per-method caps below are "
                               "documentation, not separately enforced by the frozen worker",
        "memory_mode": memory_mode,
        "memory_mode_definition": MEMORY_MODES[memory_mode],
        "task_max_calls": task_max_calls,
        "task_max_seconds": task_max_seconds,
        "shared_domain_instruction": reb.DOMAIN,
        "streaming": True,
        "input_serialization": {
            "maf": "structured teaching-unit chunks, sequential with persistent memory",
            "direct": "one core-evidence union, single request",
            "itext2kg": "ordered original lines packed to 2400 chars, incremental fusion",
            "edc": "one native text unit containing the core-evidence union",
        },
        "scope": scope,
        "disabled": ["resume", "verify", "semantic_dedupe", "embedding_memory",
                     "coverage_review", "logical_form_review", "seed_graph",
                     "generated_summary"],
    }
    write_json(output / "manifest.json", manifest)
    baseline.audit_prepared(manifest)
    print(f"prepared {len(sections)} sections, {len(jobs)} jobs -> {output}", flush=True)
    return manifest


def write_deltas(output: Path, snapshot_source: Path, reference_path: Path,
                 budget_root: Path) -> dict:
    baseline, reb, rma = load_frozen(snapshot_source)
    deltas = {
        "schema": "maf_kg.www_supplement.runner_deltas.v1",
        "runner": "evaluation/www_evidence_supplement_2026-10-11/runner/run_supplement_e1.py",
        "frozen_source": str(snapshot_source.resolve()),
        "frozen_hashes": {
            "run_external_baselines.py": sha256(snapshot_source / "scripts/run_external_baselines.py"),
            "run_direct_extraction_baseline.py": sha256(
                snapshot_source / "scripts/run_direct_extraction_baseline.py"),
            "run_memory_ablation.py": sha256(snapshot_source / "scripts/run_memory_ablation.py"),
            "external_edc.py": sha256(snapshot_source / "scripts/external_edc.py"),
            "external_itext2kg.py": sha256(snapshot_source / "scripts/external_itext2kg.py"),
            "Extractor.py": sha256(snapshot_source / "agents/Extractor.py"),
        },
        "content_producing_code_modified": False,
        "deltas": [
            {"what": "section list", "frozen": "reference_merged_v1.json sections",
             "here": str(reference_path.resolve()),
             "why": "this round uses two new inputs instead of the earlier merged document"},
            {"what": "budget ledger root", "frozen": "BASE/budgets under the earlier experiment",
             "here": str(budget_root.resolve()),
             "why": "reusing the earlier ledgers would mix rounds and can trip a stale cap; "
                    "the frozen prepare hardcodes that path"},
            {"what": "per-method caps", "frozen": "usd 1.50 / 2.5M tokens",
             "here": "see manifest per_method_caps",
             "why": "four runs per method on two inputs exceed the earlier single-document caps"},
            {"what": "job directory layout", "frozen": "output/r{repeat}/{section}/{method}",
             "here": "output/runs/r{repeat}/{section}/{method}",
             "why": "keeps the runner's own bookkeeping out of the run tree"},
            {"what": "runtime venv", "frozen": "snapshot/runtime (bundled copy)",
             "here": str(DEFAULT_VENV),
             "why": "the venv is recorded by hash in freeze_record.json rather than copied; "
                    "the driver must be launched with that interpreter"},
        ],
        "unchanged": [
            "chunking parameters (3200 chars, 6 before / 4 after context lines)",
            "memory budgets (12000 node bytes / 6000 relation bytes)",
            "prompts and system prompt",
            "EDC frozen seven-predicate schema and adapter",
            "iText2KG packing (2400 chars), matcher threshold and progress guard",
            "transport: single attempt, no retry, no JSON repair, temperature omitted",
            "shared domain instruction",
        ],
    }
    write_json(output / "runner_deltas.json", deltas)
    return deltas


# --------------------------------------------------------------------------
# execute / audit
# --------------------------------------------------------------------------

def execute(output: Path, **kwargs):
    manifest = read(output / "manifest.json")
    baseline, reb, rma = load_frozen(Path(manifest["snapshot"]))
    reb.setup()
    reb.execute(output, **kwargs)


def audit(output: Path) -> int:
    manifest = read(output / "manifest.json")
    baseline, reb, rma = load_frozen(Path(manifest["snapshot"]))
    reb.setup()
    baseline.audit_prepared(manifest)
    print("audit_prepared: PASSED")

    # Preflight: every key the frozen worker reads must exist, otherwise the job
    # dies inside the subprocess before sending anything.
    missing = [k for k in WORKER_REQUIRED_KEYS if k not in manifest]
    if missing:
        raise SystemExit(f"manifest is missing keys the frozen worker reads: {missing}")
    print(f"worker key preflight: PASSED ({len(WORKER_REQUIRED_KEYS)} required keys present)")
    for k in WORKER_OPTIONAL_KEYS:
        if k in manifest:
            print(f"  optional key set: {k} = {manifest[k]}")
    rows = []
    for job in manifest["jobs"]:
        rows.append({"repeat": job["repeat"], "section": job["section"],
                     "method": job["method"], "status": job["status"],
                     "exit_code": job.get("exit_code"),
                     "elapsed_seconds": job.get("elapsed_seconds")})
    ledger = Path(manifest["budget_root"])
    spend = {}
    for path in sorted(ledger.glob("*.json")):
        data = read(path)
        spend[path.stem] = {"requests": len(data.get("requests", [])),
                            "accounting_tokens": data.get("accounting_tokens"),
                            "accounting_usd": data.get("accounting_usd"),
                            "limit_raise_history": data.get("limit_raise_history", [])}
    print(json.dumps({"jobs": rows, "spend": spend}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute", "audit"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--snapshot-source", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--embedding-lock", type=Path, default=DEFAULT_EMBEDDING_LOCK)
    parser.add_argument("--budget-root", type=Path)
    parser.add_argument("--caps", type=Path)
    parser.add_argument("--task-max-calls", type=int, default=200)
    parser.add_argument("--task-max-seconds", type=int, default=7200)
    parser.add_argument("--scope", default="Two new exposed development sections from the "
                                          "same textbook, two repeats; not independent held-out "
                                          "textbooks and not an equal-compute comparison.")
    parser.add_argument("--memory-mode", choices=tuple(MEMORY_MODES), default="e1")
    parser.add_argument("--methods", nargs="+")
    parser.add_argument("--repeat", type=int, choices=(1, 2))
    parser.add_argument("--max-jobs", type=int, default=32)
    args = parser.parse_args()

    output = args.output.resolve()
    if args.action == "prepare":
        for required in ("reference", "budget_root", "caps"):
            if getattr(args, required) is None:
                parser.error(f"prepare requires --{required.replace('_', '-')}")
        caps = read(args.caps)
        prepare(output, reference_path=args.reference.resolve(),
                snapshot_source=args.snapshot_source.resolve(),
                embedding_lock=args.embedding_lock.resolve(),
                budget_root=args.budget_root.resolve(), caps=caps,
                task_max_calls=args.task_max_calls,
                task_max_seconds=args.task_max_seconds, scope=args.scope,
                memory_mode=args.memory_mode)
        write_deltas(output, args.snapshot_source.resolve(),
                     args.reference.resolve(), args.budget_root.resolve())
    elif args.action == "execute":
        execute(output, methods=args.methods, repeat=args.repeat,
                max_jobs=args.max_jobs)
    else:
        return audit(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
