#!/usr/bin/env python3
"""Freeze and run a paired model-visible memory ablation without editing Extractor."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARMS = {
    "visible_memory": (12000, 6000),
    "no_visible_memory": (2, 2),
}
COMMON = {
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",
    "thinking": "enabled",
    "reasoning_effort": "low",
    "chunk_strategy": "teaching-units",
    "max_chunk_chars": 3200,
    "chunk_context_before_lines": 6,
    "chunk_context_after_lines": 4,
    "temperature": 0.1,
    "max_tokens": 24000,
    "max_retries": 4,
    "request_timeout": 180,
    "request_delay": 8,
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_module(path: Path):
    name = "memory_ablation_frozen_extractor"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def freeze(root: Path, source: Path, output: Path) -> dict:
    # Exclusive creation prevents accidental overwriting or reuse of an old run.
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    files = [root / "agents/Extractor.py", *sorted((root / "prompts").glob("*.txt"))]
    files += [root / "evaluation/gold/semantic_first_three_sections_v5.json",
              root / "evaluation/gold/relations_section2_v1.json",
              root / "scripts/run_memory_ablation.py"]
    artifacts = []
    for path in files:
        relative = path.relative_to(root)
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        artifacts.append({"source": str(path), "snapshot": str(target), "sha256": digest(target)})
    frozen_input = snapshot / "input" / source.name
    frozen_input.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, frozen_input)
    artifacts.append({"source": str(source), "snapshot": str(frozen_input), "sha256": digest(frozen_input)})
    module = load_module(snapshot / "agents/Extractor.py")
    extractor = module.Extractor(input_path=frozen_input, chunk_strategy=COMMON["chunk_strategy"],
                                 max_chunk_chars=COMMON["max_chunk_chars"], verbose=False)
    state = {**COMMON, "input_path": str(frozen_input), "include_exercises": False, "diagnostics": {}}
    state = extractor._chunk_document(extractor._load_document(state))
    chunks = [asdict(chunk) for chunk in state["chunks"]]
    if len(chunks) < 2:
        raise ValueError("Memory ablation needs at least two sequential chunks")
    write_json(output / "chunks.json", chunks)
    artifacts.append({"source": "offline chunk plan", "snapshot": str(output / "chunks.json"),
                      "sha256": digest(output / "chunks.json")})
    manifest = {
        "experiment": "model_visible_memory_pilot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], cwd=root, text=True, encoding="utf-8"),
        "python": sys.executable,
        "common": COMMON,
        "artifacts": artifacts,
        "input": str(frozen_input),
        "chunk_count": len(chunks),
        "arms": [{"name": name, "memory_node_bytes": budgets[0], "memory_relation_bytes": budgets[1],
                  "status": "pending"} for name, budgets in ARMS.items()],
        "scope": "Only model-visible memory is removed. Global storage and deterministic name merging remain in both arms.",
        "disabled": ["resume", "verify", "semantic_dedupe", "embedding_memory", "coverage_review", "logical_form_review", "seed_graph"],
        "scoring": "Existing Gold is frozen; name matching is diagnostic only. Semantic alignment and duplicate/false-merge review must be explicitly recorded.",
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def command(manifest: dict, output: Path, arm: dict) -> list[str]:
    args = [sys.executable, "-X", "utf8", str(output / arm["name"] / "runtime/agents/Extractor.py"),
            "--input", manifest["input"], "--output", str(output / arm["name"] / "graph.json"),
            "--no-resume", "--no-proxy"]
    for key, value in manifest["common"].items():
        args += ["--" + key.replace("_", "-"), str(value)]
    args += ["--memory-node-bytes", str(arm["memory_node_bytes"]),
             "--memory-relation-bytes", str(arm["memory_relation_bytes"])]
    return args


def prepare_repetition(baseline: Path, output: Path, reverse_order: bool) -> dict:
    original = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    if any(arm["status"] != "completed" for arm in original["arms"]):
        raise ValueError("Repeat baseline must be a fully completed pair")
    for artifact in original["artifacts"]:
        if digest(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError("Baseline frozen artifact changed")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(baseline / "snapshot", output / "snapshot")
    shutil.copy2(baseline / "chunks.json", output / "chunks.json")
    artifacts = []
    for artifact in original["artifacts"]:
        old = Path(artifact["snapshot"])
        target = output / "chunks.json" if old == baseline / "chunks.json" else output / "snapshot" / old.relative_to(baseline / "snapshot")
        artifacts.append({"source": str(old), "snapshot": str(target), "sha256": artifact["sha256"]})
    # Keep the exact source_path in model input unchanged across repetitions.
    shared_input = Path(original["input"])
    artifacts.append({"source": "shared frozen model input", "snapshot": str(shared_input), "sha256": digest(shared_input)})
    arms = [{"name": arm["name"], "memory_node_bytes": arm["memory_node_bytes"],
             "memory_relation_bytes": arm["memory_relation_bytes"], "status": "pending"}
            for arm in original["arms"]]
    if reverse_order:
        arms.reverse()
    manifest = {**original, "created_at": datetime.now(timezone.utc).isoformat(),
                "experiment": "model_visible_memory_repetition", "artifacts": artifacts,
                "arms": arms, "repeat_from": str(baseline),
                "baseline_manifest_sha256": digest(baseline / "manifest.json"),
                "driver_sha256": digest(Path(__file__)), "execution_order": [a["name"] for a in arms]}
    write_json(output / "manifest.json", manifest)
    return manifest


def validate_graph(graph: dict, manifest: dict, arm: dict, chunks: list[dict]) -> None:
    diagnostics = graph["diagnostics"]
    config = diagnostics["run_config"]
    expected = {"provider": manifest["common"]["provider"], "model_requested": manifest["common"]["model"], "thinking": "enabled",
                "reasoning_effort_sent": "low", "chunk_strategy": "teaching-units",
                "memory_node_bytes": arm["memory_node_bytes"],
                "memory_relation_bytes": arm["memory_relation_bytes"], "resume_enabled": False}
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(f"Configuration mismatch: {key}: {config.get(key)!r} != {value!r}")
    if graph["model"] != manifest["common"]["model"]:
        raise ValueError("Graph model differs from frozen requested model")
    observed = diagnostics["chunk_summaries"]
    if [row["chunk_id"] for row in observed] != [row["id"] for row in chunks]:
        raise ValueError("Actual chunk sequence differs from frozen sequence")
    if arm["name"] == "no_visible_memory":
        if any(row["memory_node_count"] or row["memory_relation_count"] for row in observed):
            raise ValueError("Ablated arm unexpectedly exposed memory")
    elif not any(row["memory_node_count"] for row in observed[1:]):
        raise ValueError("Memory arm did not expose any historical nodes; ablation is uninformative")


def execute(output: Path, manifest: dict) -> int:
    for artifact in manifest["artifacts"]:
        if digest(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError(f"Frozen artifact changed: {artifact['snapshot']}")
    chunks = json.loads((output / "chunks.json").read_text(encoding="utf-8"))
    failed = False
    for arm in manifest["arms"]:
        directory = output / arm["name"]
        directory.mkdir(exist_ok=False)
        for name in ("agents", "prompts"):
            shutil.copytree(output / "snapshot" / name, directory / "runtime" / name)
        args = command(manifest, output, arm)
        write_json(directory / "command.json", args)
        arm["status"] = "running"
        arm["started_at"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "manifest.json", manifest)
        start = time.monotonic()
        print(f"[MemoryAblation] starting {arm['name']}", flush=True)
        try:
            with (directory / "stdout.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, cwd=output / "snapshot", stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8")
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith("[Extractor]") or "failed:" in line:
                        print(line.rstrip(), flush=True)
                code = process.wait()
            arm["exit_code"] = code
            if code:
                raise RuntimeError(f"Extractor exited {code}; see {directory / 'stdout.log'}")
            graph_path = directory / "graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            validate_graph(graph, manifest, arm, chunks)
            if digest(directory / "runtime/agents/Extractor.py") != digest(output / "snapshot/agents/Extractor.py"):
                raise ValueError("Arm runtime code differs from frozen Extractor")
            arm["status"] = "completed"
            arm["graph_sha256"] = digest(graph_path)
            diagnostics = graph["diagnostics"]
            arm["diagnostics"] = {key: diagnostics.get(key) for key in
                                  ("node_count", "relation_count", "api_summary", "audit")}
        except Exception as exc:
            arm["status"] = "failed"
            arm["error"] = str(exc)
            failed = True
            print(f"[MemoryAblation] {arm['name']} failed: {exc}", flush=True)
        finally:
            arm["elapsed_seconds"] = round(time.monotonic() - start, 3)
            write_json(output / "manifest.json", manifest)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--repeat-from", type=Path)
    parser.add_argument("--reverse-order", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if bool(args.input) == bool(args.repeat_from):
        parser.error("Provide exactly one of --input and --repeat-from")
    if args.reverse_order and not args.repeat_from:
        parser.error("--reverse-order requires --repeat-from")
    manifest = (prepare_repetition(args.repeat_from.resolve(), args.output_dir.resolve(), args.reverse_order)
                if args.repeat_from else freeze(ROOT, args.input.resolve(), args.output_dir.resolve()))
    print(f"Frozen {manifest['chunk_count']} chunks in {args.output_dir}", flush=True)
    if args.prepare_only:
        return 0
    return execute(args.output_dir.resolve(), manifest)


if __name__ == "__main__":
    raise SystemExit(main())
