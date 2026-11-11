"""Run a frozen paired incremental graph-maintenance experiment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = {
    "provider": "opencode-go", "model": "deepseek-v4-flash",
    "base_url": "https://opencode.ai/zen/go/v1", "api_key_env": "OPENCODE_API_KEY",
    "thinking": "enabled", "reasoning_effort": "low", "temperature": 0.1,
    "chunk_strategy": "teaching-units", "max_chunk_chars": 3200,
    "chunk_context_before_lines": 6, "chunk_context_after_lines": 4,
    "max_tokens": 24000, "max_retries": 4, "request_timeout": 180, "request_delay": 8,
}
SECTIONS = (("ch5_s2", 565, 886, "# 第二节 微积分基本公式"),
            ("ch5_s4", 1290, 1552, "# 第四节 反常积分"))
ALLOWED_SEED_FILES = {
    "第一章前五节_合并_无习题.md", "第一章后五节_合并_无习题.md",
    "第二章_全章_无习题.md", "第三章_全章_无习题.md", "第四章_全章_无习题.md",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def audit_seed(graph: dict) -> dict:
    ids = [node["id"] for node in graph["nodes"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Seed has duplicate IDs")
    sources = set()
    counts = {}
    for field in ("nodes", "relations", "math_expressions"):
        rows = graph.get(field, [])
        counts[field] = len(rows)
        for row in rows:
            for evidence in row.get("evidence", []):
                name = evidence["source_path"].replace("\\", "/").rsplit("/", 1)[-1]
                if name not in ALLOWED_SEED_FILES:
                    raise ValueError(f"Seed has non-prefix evidence: {name}")
                sources.add(name)
    for edge in graph["relations"]:
        if edge["source_id"] not in ids or edge["target_id"] not in ids:
            raise ValueError("Seed relation endpoint absent")
    for row in graph.get("math_expressions", []):
        if row["owner_id"] not in ids:
            raise ValueError("Seed expression owner absent")
    if any(node["name"] in {"定积分", "反常积分", "牛顿-莱布尼茨公式", "牛顿–莱布尼茨公式"}
           for node in graph["nodes"]):
        raise ValueError("Seed already contains a target identity")
    return {**counts, "evidence_sources": sorted(sources),
            "limitation": "Evidence-prefix audit; not a replay audit of historical seed generation."}


def prepare(output: Path, source: Path, seed: Path) -> dict:
    seed_audit = audit_seed(read(seed))
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    artifacts = []

    def copy(path: Path, relative: Path) -> Path:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        artifacts.append({"source": str(path), "snapshot": str(target), "sha256": digest(target)})
        return target

    files = [ROOT / "agents/Extractor.py", Path(__file__),
             ROOT / "docs/seeded_memory_protocol_2026-10-04.md"]
    files += sorted((ROOT / "prompts").glob("*.txt"))
    for path in files:
        copy(path, path.relative_to(ROOT))
    frozen_source = copy(source, Path("source") / source.name)
    frozen_seed = copy(seed, Path("seed/graph.json"))
    extractor_module = load(snapshot / "agents/Extractor.py", "seeded_prepare_extractor")
    lines = frozen_source.read_text(encoding="utf-8").splitlines()
    sections = []
    for section_id, start, end, heading in SECTIONS:
        if len(lines) < end or lines[start - 1] != heading:
            raise ValueError("Source headings/line numbers differ from preregistration")
        path = snapshot / "input" / f"{section_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines[start - 1:end]) + "\n", encoding="utf-8")
        artifacts.append({"source": str(frozen_source), "snapshot": str(path), "sha256": digest(path)})
        extractor = extractor_module.Extractor(input_path=path, verbose=False,
                                               chunk_strategy=COMMON["chunk_strategy"],
                                               max_chunk_chars=COMMON["max_chunk_chars"])
        state = extractor._chunk_document(extractor._load_document({
            **COMMON, "input_path": str(path), "include_exercises": False, "diagnostics": {}}))
        chunks_path = output / "chunks" / f"{section_id}.json"
        write(chunks_path, [asdict(chunk) for chunk in state["chunks"]])
        artifacts.append({"source": "offline chunk plan", "snapshot": str(chunks_path),
                          "sha256": digest(chunks_path)})
        sections.append({"id": section_id, "input": str(path), "source_line_offset": start - 1,
                         "original_span": [start, end], "chunk_count": len(state["chunks"]),
                         "chunks": str(chunks_path)})
    arms = []
    for name, budgets in (("no_visible_memory", (2, 2)), ("visible_memory", (12000, 6000))):
        arms.append({"name": name, "memory_node_bytes": budgets[0],
                     "memory_relation_bytes": budgets[1], "status": "pending"})
    manifest = {
        "schema": "maf-kg.seeded-memory-experiment.v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": sys.executable, "common": COMMON, "artifacts": artifacts,
        "snapshot": str(snapshot), "seed": str(frozen_seed), "seed_audit": seed_audit,
        "sections": sections, "arms": arms,
        "initial_request_count": 2 * sum(row["chunk_count"] for row in sections),
        "scope": "Shared historical seed, per-arm sequential target updates; deterministic merging remains in both arms.",
    }
    write(output / "manifest.json", manifest)
    return manifest


def audit_artifacts(manifest: dict) -> None:
    for artifact in manifest["artifacts"]:
        if digest(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError(f"Frozen artifact changed: {artifact['snapshot']}")


def validate_graph(graph: dict, manifest: dict, arm: dict, section: dict) -> None:
    config = graph["diagnostics"]["run_config"]
    expected = {"provider": "opencode-go", "model_requested": "deepseek-v4-flash",
                "reasoning_effort_sent": "low", "thinking": "enabled",
                "chunk_strategy": "teaching-units", "resume_enabled": False,
                "coverage_review_enabled": False, "logical_form_review_enabled": False,
                "memory_node_bytes": arm["memory_node_bytes"],
                "memory_relation_bytes": arm["memory_relation_bytes"]}
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(f"Unexpected run config: {key}")
    chunks = read(Path(section["chunks"]))
    observed = graph["diagnostics"]["chunk_summaries"]
    if [row["id"] for row in chunks] != [row["chunk_id"] for row in observed]:
        raise ValueError("Actual chunk sequence changed")
    if arm["name"] == "no_visible_memory":
        if any(row["memory_node_count"] or row["memory_relation_count"] for row in observed):
            raise ValueError("Ablated arm exposed memory")
    elif not any(row["memory_node_count"] for row in observed):
        raise ValueError("Memory arm did not expose seed nodes")
    if graph["diagnostics"].get("cache_hits"):
        raise ValueError("Unexpected cache reads")


def worker(output: Path, arm_name: str) -> int:
    manifest = read(output / "manifest.json")
    audit_artifacts(manifest)
    arm = next(row for row in manifest["arms"] if row["name"] == arm_name)
    directory = output / arm_name
    directory.mkdir(exist_ok=False)
    for name in ("agents", "prompts"):
        shutil.copytree(Path(manifest["snapshot"]) / name, directory / "runtime" / name)
    module = load(directory / "runtime/agents/Extractor.py", "seeded_worker_extractor")
    seed = Path(manifest["seed"])
    original_complete = module.ProviderChatClient.complete_json
    original_execute = module.ProviderChatClient._execute_request
    for section in manifest["sections"]:
        section_dir = directory / section["id"]
        section_dir.mkdir()
        write(section_dir / "seed_used.json", {"path": str(seed), "sha256": digest(seed)})

        def complete(client, system, user, label="request"):
            payload = client.adapter.build_payload(
                model=client.model, system_prompt=system, user_prompt=user,
                temperature=client.temperature, max_tokens=client.max_tokens,
                use_response_format=client.use_response_format, thinking=client.thinking,
                reasoning_effort=client.reasoning_effort)
            write(section_dir / "requests" / f"{label}.json", {
                "endpoint": client.endpoint_url, "provider": client.provider,
                "session_id": client.session_id, "body": payload})
            try:
                parsed = original_complete(client, system, user, label)
                write(section_dir / "responses" / f"{label}.json", parsed)
                return parsed
            finally:
                write(section_dir / "api_requests.json", client.request_history)

        def execute_request(client, request, transport, attempt, label):
            def captured():
                raw = request()
                write(section_dir / "raw" / f"{label}_{attempt}_{transport}.json", raw)
                return raw
            return original_execute(client, captured, transport, attempt, label)

        module.ProviderChatClient.complete_json = complete
        module.ProviderChatClient._execute_request = execute_request
        extractor = module.Extractor(
            **manifest["common"], input_path=section["input"], output_path=section_dir / "graph.json",
            memory_node_bytes=arm["memory_node_bytes"], memory_relation_bytes=arm["memory_relation_bytes"],
            no_proxy=True, resume=False, include_exercises=False, verify=False,
            semantic_dedupe=False, embedding_memory=False, coverage_review=False,
            logical_form_review=False, seed_graph_path=seed)
        started = time.monotonic()
        extractor.run()
        graph = read(section_dir / "graph.json")
        validate_graph(graph, manifest, arm, section)
        write(section_dir / "completion.json", {"status": "completed", "graph_sha256": digest(section_dir / "graph.json"),
                                                "elapsed_seconds": round(time.monotonic() - started, 3)})
        seed = section_dir / "graph.json"
    if digest(directory / "runtime/agents/Extractor.py") != digest(Path(manifest["snapshot"]) / "agents/Extractor.py"):
        raise ValueError("Runtime Extractor changed")
    return 0


def execute(output: Path) -> int:
    manifest = read(output / "manifest.json")
    audit_artifacts(manifest)
    if not os.environ.get(manifest["common"]["api_key_env"]):
        raise ValueError("API key environment variable is absent")
    if any(arm["status"] != "pending" for arm in manifest["arms"]):
        raise ValueError("An attempted pair cannot be executed again")
    failed = False
    for arm in manifest["arms"]:
        arm["status"] = "running"
        arm["started_at"] = datetime.now(timezone.utc).isoformat()
        write(output / "manifest.json", manifest)
        args = [sys.executable, "-X", "utf8", str(Path(manifest["snapshot"]) / "scripts" / Path(__file__).name),
                "--worker", "--output-dir", str(output), "--arm", arm["name"]]
        write(output / f"{arm['name']}_command.json", args)
        started = time.monotonic()
        print(f"[SeededMemory] starting {arm['name']}", flush=True)
        with (output / f"{arm['name']}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(args, cwd=output, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8")
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line.rstrip(), flush=True)
            code = process.wait()
        arm["exit_code"] = code
        arm["status"] = "failed" if code else "completed"
        arm["elapsed_seconds"] = round(time.monotonic() - started, 3)
        failed = failed or bool(code)
        write(output / "manifest.json", manifest)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("E:/MaF-KG/data/raw/第五章_全章_无习题.md"))
    parser.add_argument("--seed", type=Path, default=Path("E:/MaF-KG/KG/第四章_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute-prepared", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--arm", choices=("visible_memory", "no_visible_memory"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.worker:
        if not args.arm:
            parser.error("Worker requires --arm")
        return worker(output, args.arm)
    if args.prepare_only and args.execute_prepared:
        parser.error("Preparation and execution modes are exclusive")
    if not args.execute_prepared:
        manifest = prepare(output, args.source.resolve(), args.seed.resolve())
        print(f"Frozen {manifest['initial_request_count']} initial requests", flush=True)
    return 0 if args.prepare_only else execute(output)


if __name__ == "__main__":
    raise SystemExit(main())
