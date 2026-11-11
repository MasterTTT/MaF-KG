"""Freeze and run a section-once versus sequential extraction comparison."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from run_memory_ablation import COMMON, command, digest, load_module, write_json

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("sequential", "direct")
REFERENCE = ROOT / "evaluation/heldout_2026-09-25/reference_v1.json"
PROTOCOL = ROOT / "docs/www_direct_extraction_baseline_protocol_2026-10-03.md"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def numbered_lines(text: str) -> dict[int, str]:
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(\d+): (.*)", line)
        if not match:
            raise ValueError("Source text is not fully line-numbered")
        number, body = int(match[1]), match[2]
        if number in result:
            raise ValueError("Duplicate evidence line in one chunk")
        result[number] = body
    return result


def shared_evidence(chunks: list[dict], source_lines: list[str]) -> dict[int, str]:
    selected = {}
    for chunk in chunks:
        for number, body in numbered_lines(chunk["text"]).items():
            if not 1 <= number <= len(source_lines) or source_lines[number - 1] != body:
                raise ValueError("Chunk evidence differs from the original source")
            if number in selected and selected[number] != body:
                raise ValueError("Conflicting source content")
            selected[number] = body
    if not selected:
        raise ValueError("No shared evidence for the section")
    return dict(sorted(selected.items()))


def direct_chunk(module, chunks: list[dict], source_lines: list[str]):
    selected = shared_evidence(chunks, source_lines)
    return module.DocumentChunk(
        id=f"section_once_{min(selected)}_{max(selected)}",
        start_line=min(selected), end_line=max(selected), heading_path=[],
        text="\n".join(f"{number}: {body}" for number, body in selected.items()),
    )


def reference_reachability(reference: dict, section: dict, selected: dict, lines: list[str]) -> list[dict]:
    rows = []
    offset = section["start_line"] - 1
    for node in reference["nodes"]:
        if node["section"] != section["id"]:
            continue
        first, last = node["evidence"]
        meaningful = {n - offset for n in range(first, last + 1)
                      if lines[n - offset - 1].strip()}
        present = meaningful & set(selected)
        status = "full" if meaningful <= set(selected) else "partial" if present else "unreachable"
        rows.append({"gold_id": node["id"], "requirement": node["status"],
                     "source_range": node["evidence"], "input_status": status,
                     "missing_nonempty_original_lines": sorted(n + offset for n in meaningful - set(selected))})
    return rows


def bind(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": digest(path)}


def prepare(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    files = [ROOT / "agents/Extractor.py", *sorted((ROOT / "prompts").glob("*.txt"))]
    files += [REFERENCE, PROTOCOL, Path(__file__), ROOT / "scripts/run_memory_ablation.py"]
    artifacts = []
    for source in files:
        target = snapshot / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        artifacts.append(bind(target))
    reference = read(snapshot / REFERENCE.relative_to(ROOT))
    module = load_module(snapshot / "agents/Extractor.py")
    sections = []
    for section in reference["sections"]:
        source = Path(section["source"])
        if digest(source).lower() != section["source_sha256"].lower():
            raise ValueError("Reference source version changed")
        original = snapshot / "source" / source.name
        original.parent.mkdir(exist_ok=True)
        shutil.copy2(source, original)
        full_lines = original.read_text(encoding="utf-8").splitlines(keepends=True)
        target = snapshot / "input" / f"{section['id']}.md"
        target.parent.mkdir(exist_ok=True)
        target.write_text("".join(full_lines[section["start_line"] - 1:section["end_line"]]), encoding="utf-8")
        extractor = module.Extractor(input_path=target, verbose=False)
        state = extractor._chunk_document(extractor._load_document(
            {**COMMON, "input_path": str(target), "include_exercises": False, "diagnostics": {}}))
        chunks = [asdict(chunk) for chunk in state["chunks"]]
        once = direct_chunk(module, chunks, state["raw_lines"])
        plan_dir = output / "plans" / section["id"]
        plan_dir.mkdir(parents=True)
        selected = shared_evidence(chunks, state["raw_lines"])
        templates = {"sequential": [json.loads(extractor._chunk_extraction_prompt(
            chunk, str(target), [], [])) for chunk in state["chunks"]],
            "direct": [json.loads(extractor._chunk_extraction_prompt(once, str(target), [], []))]}
        write_json(plan_dir / "chunks.json", chunks)
        write_json(plan_dir / "direct_chunk.json", asdict(once))
        write_json(plan_dir / "templates.json", templates)
        reachability = reference_reachability(reference, section, selected, state["raw_lines"])
        write_json(plan_dir / "reachability.json", reachability)
        write_json(plan_dir / "evidence_map.json", {
            "source_line_offset": section["start_line"] - 1,
            "selected_local_lines": list(selected),
            "excluded_nonempty_local_lines": [n for n, body in enumerate(state["raw_lines"], 1)
                                               if body.strip() and n not in selected],
            "source_chars": sum(len(body) for body in selected.values()),
            "note": "Both arms use this exact core evidence union, including any example blocks retained by the frozen parser; the common prompt determines node admission. Sequential neighboring navigation is recorded separately.",
        })
        for path in [original, target, *sorted(plan_dir.glob("*.json"))]:
            artifacts.append(bind(path))
        sections.append({**section, "input": str(target), "plan_dir": str(plan_dir),
                         "chunk_count": len(chunks), "core_line_count": len(selected)})
        print(f"Prepared {section['id']}: {len(chunks)} sequential chunks / 1 direct request; "
              f"unreachable M={[r['gold_id'] for r in reachability if r['requirement']=='M' and r['input_status']=='unreachable']}", flush=True)
    jobs = []
    for repeat in (1, 2):
        for index, section in enumerate(sections):
            order = ARMS if (index + repeat) % 2 else tuple(reversed(ARMS))
            for arm in order:
                jobs.append({"section": section["id"], "repeat": repeat, "name": arm,
                             "memory_node_bytes": 12000, "memory_relation_bytes": 6000,
                             "directory": str(output / f"r{repeat}" / section["id"] / arm), "status": "pending"})
    manifest = {"experiment": "same_model_section_once_baseline", "status": "prepared", "snapshot": str(snapshot),
                "created_at": datetime.now(timezone.utc).isoformat(), "common": COMMON,
                "reference": str(snapshot / REFERENCE.relative_to(ROOT)), "sections": sections,
                "jobs": jobs, "artifacts": artifacts,
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "scope": "Exposed development sections, independent empty graphs, same core source union; per-request but not total output budget matched.",
                "disabled": ["resume", "verify", "semantic_dedupe", "embedding_memory", "coverage_review",
                             "logical_form_review", "seed_graph", "generated_summary"]}
    write_json(output / "manifest.json", manifest)
    audit_prepared(manifest)
    return manifest


def audit_prepared(manifest: dict) -> None:
    if manifest["common"] != COMMON:
        raise ValueError("Experimental configuration changed")
    for artifact in manifest["artifacts"]:
        if digest(Path(artifact["path"])) != artifact["sha256"]:
            raise ValueError(f"Frozen artifact changed: {artifact['path']}")
    for section in manifest["sections"]:
        plan = Path(section["plan_dir"])
        chunks, once = read(plan / "chunks.json"), read(plan / "direct_chunk.json")
        lines = Path(section["input"]).read_text(encoding="utf-8").splitlines()
        if shared_evidence(chunks, lines) != numbered_lines(once["text"]):
            raise ValueError("Baseline source differs from sequential evidence union")
        templates = read(plan / "templates.json")
        for name, payloads in templates.items():
            expected_chunks = chunks if name == "sequential" else [once]
            if len(payloads) != len(expected_chunks):
                raise ValueError("Unexpected offline request count")
            for payload, chunk in zip(payloads, expected_chunks):
                if payload["chunk"]["text"] != chunk["text"] or payload["node_memory"] or payload["relation_memory"]:
                    raise ValueError("Offline request text or empty memory changed")
                if name == "direct" and any(payload["chunk"][key] for key in
                    ("heading_path", "teaching_block_ids", "teaching_blocks", "context_before_text", "context_after_text")):
                    raise ValueError("Direct baseline received structured labels or neighbors")


def install_hooks(module, section: dict, job: dict, directory: Path) -> None:
    plan = Path(section["plan_dir"])
    expected = read(plan / "chunks.json")
    once = read(plan / "direct_chunk.json")
    original_chunk = module.Extractor._chunk_document
    original_prompt = module.Extractor._chunk_extraction_prompt
    original_complete = module.ProviderChatClient.complete_json
    original_request = module.ProviderChatClient._execute_request

    def chunk_document(self, state):
        result = original_chunk(self, state)
        if json.loads(json.dumps([asdict(c) for c in result["chunks"]])) != expected:
            raise ValueError("Runtime structure plan differs from the frozen source")
        if job["name"] == "direct":
            result["chunks"] = [module.DocumentChunk(**once)]
        return result

    def prompt(self, chunk, *args, **kwargs):
        value = original_prompt(self, chunk, *args, **kwargs)
        payload = json.loads(value)
        templates = read(plan / "templates.json")[job["name"]]
        frozen = next(t for t in templates if t["chunk"]["chunk_id"] == chunk.id)
        for key in ("source_path", "chunk", "defined_term_hints", "source_latex_candidates"):
            if payload[key] != frozen[key]:
                raise ValueError(f"Static request changed: {key}")
        if job["name"] == "direct" and (payload["node_memory"] or payload["relation_memory"]):
            raise ValueError("Section-once baseline exposed historical memory")
        return value

    def complete(self, system_prompt, user_prompt, label="request"):
        write_json(directory / "requests" / f"{label}.json", {
            "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "payload": json.loads(user_prompt)})
        try:
            result = original_complete(self, system_prompt, user_prompt, label)
            write_json(directory / "responses" / f"{label}.json", result)
            return result
        finally:
            write_json(directory / "api_requests.json", self.request_history)

    def execute_request(self, request, transport, attempt, label):
        def recorded_request():
            result = request()
            write_json(directory / "raw" / f"{label}_{attempt}_{transport}.json", result)
            return result
        return original_request(self, recorded_request, transport, attempt, label)

    module.Extractor._chunk_document = chunk_document
    module.Extractor._chunk_extraction_prompt = prompt
    module.Extractor._cache_dir = lambda self: directory / "cache"
    module.ProviderChatClient.complete_json = complete
    module.ProviderChatClient._execute_request = execute_request
    (directory / "cache").mkdir()


def worker(output: Path, job_index: int) -> None:
    manifest = read(output / "manifest.json")
    audit_prepared(manifest)
    job = manifest["jobs"][job_index]
    section = next(s for s in manifest["sections"] if s["id"] == job["section"])
    directory = Path(job["directory"])
    for name in ("requests", "responses", "raw"):
        (directory / name).mkdir()
    module = load_module(directory / "rt/agents/Extractor.py")
    install_hooks(module, section, job, directory)
    cli_manifest = {"input": section["input"], "common": manifest["common"]}
    args = command(cli_manifest, output, job)
    args[3] = str(directory / "rt/agents/Extractor.py")
    args[args.index("--output") + 1] = str(directory / "graph.json")
    sys.argv = args[3:]
    module.main()


def audit_job(manifest: dict, job: dict) -> dict:
    directory = Path(job["directory"])
    section = next(s for s in manifest["sections"] if s["id"] == job["section"])
    graph = read(directory / "graph.json")
    config = graph["diagnostics"]["run_config"]
    for key, expected in {"provider": "deepseek", "model_requested": "deepseek-v4-flash",
                          "thinking": "enabled", "reasoning_effort_sent": "low",
                          "chunk_strategy": "teaching-units", "resume_enabled": False}.items():
        if config[key] != expected:
            raise ValueError(f"Wrong runtime setting: {key}")
    templates = read(Path(section["plan_dir"]) / "templates.json")[job["name"]]
    expected_ids = [t["chunk"]["chunk_id"] for t in templates]
    observed = graph["diagnostics"]["chunk_summaries"]
    if [c["chunk_id"] for c in observed] != expected_ids:
        raise ValueError("Unexpected runtime chunk sequence")
    for template, summary in zip(templates, observed):
        request = read(directory / "requests" / f"{template['chunk']['chunk_id']}.json")
        payload = request["payload"]
        for key in ("source_path", "chunk", "defined_term_hints", "source_latex_candidates"):
            if payload[key] != template[key]:
                raise ValueError("Executed source/candidates differ from the frozen input")
        if request["system_prompt_sha256"] != config["system_prompt_sha256"]:
            raise ValueError("Runtime system prompt mismatch")
        if [len(payload["node_memory"]), len(payload["relation_memory"])] != [summary["memory_node_count"], summary["memory_relation_count"]]:
            raise ValueError("Memory telemetry disagrees with actual request")
        if job["name"] == "direct" and (payload["node_memory"] or payload["relation_memory"]):
            raise ValueError("Direct baseline is not empty-history")
    for relative in ("agents/Extractor.py", "prompts/extraction_system.txt"):
        if digest(directory / "rt" / relative) != digest(Path(manifest["snapshot"]) / relative):
            raise ValueError("Runtime differs from the frozen production file")
    return {"protocol_passed": True, "chunks": len(observed), "nodes": len(graph["nodes"]),
            "relations": len(graph["relations"]), "graph_sha256": digest(directory / "graph.json"),
            "api_summary": graph["diagnostics"]["api_summary"]}


def execute(output: Path) -> int:
    manifest = read(output / "manifest.json")
    audit_prepared(manifest)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("DEEPSEEK_API_KEY is not set; no requests started")
    failed = False
    for index, job in enumerate(manifest["jobs"]):
        if job["status"] in {"completed", "failed"}:
            continue
        if job["status"] != "pending":
            raise ValueError("Interrupted running job needs explicit inspection, not silent overwrite")
        directory = Path(job["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        for name in ("agents", "prompts"):
            shutil.copytree(output / "snapshot" / name, directory / "rt" / name)
        args = [sys.executable, "-X", "utf8", str(output / "snapshot/scripts" / Path(__file__).name),
                "--worker-dir", str(output), "--job-index", str(index)]
        write_json(directory / "command.json", args)
        job.update(status="running", started_at=datetime.now(timezone.utc).isoformat())
        manifest["status"] = "running"
        write_json(output / "manifest.json", manifest)
        print(f"Starting r{job['repeat']} {job['section']} {job['name']}", flush=True)
        started = time.monotonic()
        try:
            with (directory / "stdout.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, cwd=output / "snapshot", stdout=log, stderr=subprocess.STDOUT)
                code = process.wait()
            job["exit_code"] = code
            if code:
                raise RuntimeError(f"Worker exited {code}; see stdout.log")
            job.update(audit_job(manifest, job))
            job["status"] = "completed"
        except Exception as exc:
            failed = True
            job.update(status="failed", error=str(exc))
        finally:
            job["elapsed_seconds"] = round(time.monotonic() - started, 3)
            write_json(output / "manifest.json", manifest)
            print(f"Finished {job['section']} {job['name']}: {job['status']}", flush=True)
    failed = failed or any(j["status"] == "failed" for j in manifest["jobs"])
    manifest["status"] = "completed_with_failures" if failed else "completed"
    write_json(output / "manifest.json", manifest)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute-prepared", action="store_true")
    parser.add_argument("--worker-dir", type=Path)
    parser.add_argument("--job-index", type=int)
    args = parser.parse_args()
    if args.worker_dir:
        worker(args.worker_dir.resolve(), args.job_index)
        return 0
    if not args.output_dir or (args.prepare_only and args.execute_prepared):
        parser.error("Specify one preparation/execution mode and an output directory")
    output = args.output_dir.resolve()
    if not args.execute_prepared:
        prepare(output)
    return 0 if args.prepare_only else execute(output)


if __name__ == "__main__":
    raise SystemExit(main())
