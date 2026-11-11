"""Freeze a flat-context versus structured-state pair without editing production."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from extraction_context_state import CONTEXT_MODES, ContextState, NODE_FIELDS, SCHEMA, data_hash, graph_snapshot
from run_cross_topic_memory_ablation import prepare as prepare_topics
from run_memory_ablation import COMMON, command, digest, load_module, validate_graph, write_json


ARMS = ("flat_context", "structured_state")
CACHE_FINGERPRINT_LENGTH = 20
PROTOCOL_FILES = ("run_context_state_experiment.py", "extraction_context_state.py",
                  "score_context_state_experiment.py", "run_cross_topic_memory_ablation.py",
                  "run_memory_ablation.py", "score_cross_topic_memory_ablation.py", "score_memory_ablation.py")


def prepare(output: Path, context_mode: str = "package") -> list[Path]:
    state_class, schema, prompt_transform = CONTEXT_MODES[context_mode]
    directories = prepare_topics(output)
    for index, directory in enumerate(directories):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        original_prompt = directory / "snapshot/prompts/extraction_system.txt"
        structured_prompt = directory / "structured_system_prompt.txt"
        structured_prompt.write_text(prompt_transform(original_prompt.read_text(encoding="utf-8")), encoding="utf-8")
        manifest["artifacts"].append({"source": "derived input contract; ontology unchanged",
                                      "snapshot": str(structured_prompt), "sha256": digest(structured_prompt)})
        for name in PROTOCOL_FILES:
            source = Path(__file__).with_name(name)
            target = directory / "snapshot/protocol" / name
            shutil.copy2(source, target)
            # prepare_topics has already frozen two of these dependencies.
            manifest["artifacts"] = [row for row in manifest["artifacts"] if Path(row["snapshot"]) != target]
            manifest["artifacts"].append({"source": str(source), "snapshot": str(target), "sha256": digest(target)})
        module = load_module(directory / "snapshot/agents/Extractor.py")
        extractor = module.Extractor(input_path=Path(manifest["input"]), verbose=False)
        state = {**manifest["common"], "input_path": manifest["input"], "include_exercises": False, "diagnostics": {}}
        actual = extractor._chunk_document(extractor._load_document(state))
        if json.loads(json.dumps([asdict(c) for c in actual["chunks"]])) != chunks:
            raise ValueError("Prepared context experiment changed chunk boundaries")
        tracker = state_class(chunks)
        templates = []
        for chunk in actual["chunks"]:
            legacy = json.loads(extractor._chunk_extraction_prompt(chunk, manifest["input"], [], []))
            templates.append({"chunk_id": chunk.id, "flat_context": legacy,
                              "structured_state": tracker.render(legacy, preview=True)})
        template_path = directory / "offline_templates.json"
        write_json(template_path, {"status": "offline_empty_graph_preview_not_execution", "requests": templates})
        manifest["artifacts"].append({"source": "offline request templates", "snapshot": str(template_path),
                                      "sha256": digest(template_path)})
        order = ARMS if not index % 2 else tuple(reversed(ARMS))
        arms = [{"name": name, "memory_node_bytes": 12000, "memory_relation_bytes": 6000,
                 "system_prompt": str(structured_prompt if name == "structured_state" else original_prompt),
                 "system_prompt_sha256": digest(structured_prompt if name == "structured_state" else original_prompt),
                 "status": "pending"} for name in order]
        manifest.update(experiment="structured_extraction_context_state", arms=arms,
                        execution_order=list(order), context_schema=schema, context_mode=context_mode,
                        scope=("Original selected memory retained; only input role separation and its contract change."
                               if context_mode == "layout_only" else
                               "Same boundaries and retrieval ranking; full descriptions, committed update facts, explicit evidence separation and input contract are jointly changed."),
                        reading_note_enabled=False,
                        interpretation=("No memory content intervention; independently evolving graphs can select different identities."
                                        if context_mode == "layout_only" else
                                        "Context-package experiment, not pure JSON-layout ablation; equal byte budgets can select different identities."))
        write_json(directory / "manifest.json", manifest)
        print(f"Prepared {manifest['topic']}: {len(chunks)} unchanged chunks per arm", flush=True)
    write_json(output / "study.json", {"experiment": "structured_extraction_context_state", "status": "prepared",
                                      "pairs": [str(p) for p in directories], "repetitions_per_topic": 1,
                                      "api_requests_executed": 0})
    return directories


def install_hooks(module, chunks: list[dict], arm: str, on_request, on_commit,
                  context_mode: str = "package") -> None:
    if arm not in ARMS:
        raise ValueError("Unknown context arm")
    state_class, schema, _ = CONTEXT_MODES[context_mode]
    original_chunks = module.Extractor._chunk_document
    original_memory = module.Extractor._build_memory_for_chunk
    original_item = module.Extractor._node_memory_item
    original_prompt = module.Extractor._chunk_extraction_prompt
    original_merge = module.Extractor._merge_chunk_response
    original_cache = module.Extractor._cache_fingerprint

    def chunk_document(self, state):
        result = original_chunks(self, state)
        if json.loads(json.dumps([asdict(c) for c in result["chunks"]])) != chunks:
            raise ValueError("Runtime chunk plan differs from frozen context experiment")
        self._experimental_context = state_class(chunks)
        diagnostics = dict(result["diagnostics"])
        diagnostics["context_state_experiment"] = {"arm": arm, "schema": schema,
                                                    "reading_note_enabled": False}
        return {**result, "diagnostics": diagnostics}

    def memory_item(self, node):
        item = original_item(self, node)
        if arm == "structured_state" and context_mode == "package":
            item = item.model_copy(update={"short_description": node.description})
        return item

    def build_memory(self, chunk, node_store, relation_store, *args, **kwargs):
        self._experimental_context.observe(graph_snapshot(node_store, relation_store))
        return original_memory(self, chunk, node_store, relation_store, *args, **kwargs)

    def rendered(self, legacy):
        return self._experimental_context.render(legacy) if arm == "structured_state" else copy.deepcopy(legacy)

    def prompt(self, chunk, *args, **kwargs):
        legacy = json.loads(original_prompt(self, chunk, *args, **kwargs))
        sent = rendered(self, legacy)
        on_request({"chunk_id": chunk.id, "legacy_payload": legacy, "sent_payload": sent,
                    "system_prompt_sha256": data_hash(self.system_prompt),
                    "committed_chunk_ids": list(self._experimental_context.completed)})
        return json.dumps(sent, ensure_ascii=False, separators=(",", ":"))

    def merge(self, response, chunk, source_path, raw_lines, node_store, relation_store,
              expression_store, allowed_existing_ids):
        before = graph_snapshot(node_store, relation_store)
        self._experimental_context.observe(before)
        summary = original_merge(self, response, chunk, source_path, raw_lines, node_store,
                                 relation_store, expression_store, allowed_existing_ids)
        record = self._experimental_context.commit(chunk.id, before, graph_snapshot(node_store, relation_store))
        on_commit(record)
        return summary

    def cache(self, chunk, state, node_memory, relation_memory):
        legacy = json.loads(original_prompt(self, chunk, state["input_path"], node_memory, relation_memory))
        return data_hash({"base_fingerprint": original_cache(self, chunk, state, node_memory, relation_memory),
                          "context_schema": schema, "arm": arm,
                          "system_prompt": self.system_prompt, "payload": rendered(self, legacy)})[:CACHE_FINGERPRINT_LENGTH]

    module.Extractor._chunk_document = chunk_document
    module.Extractor._node_memory_item = memory_item
    module.Extractor._build_memory_for_chunk = build_memory
    module.Extractor._chunk_extraction_prompt = prompt
    module.Extractor._merge_chunk_response = merge
    module.Extractor._cache_fingerprint = cache


def validate_cache_paths(extractor, chunks: list[dict], state: dict) -> None:
    for chunk in chunks:
        path = extractor._cache_path(chunk["id"], "0" * CACHE_FINGERPRINT_LENGTH, state)
        if os.name == "nt" and len(str(path.resolve())) >= 260:
            raise ValueError(f"Cache path exceeds Windows path limit before API execution: {path}")


def worker(directory: Path, name: str) -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    arm = next(a for a in manifest["arms"] if a["name"] == name)
    target = directory / name
    requests, commits = target / "requests", target / "commits"
    requests.mkdir(exist_ok=False)
    commits.mkdir(exist_ok=False)
    module = load_module(target / "runtime/agents/Extractor.py")
    chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    install_hooks(module, chunks, name,
                  lambda row: write_json(requests / (row["chunk_id"] + ".json"), row),
                  lambda row: write_json(commits / (row["chunk_id"] + ".json"), row),
                  context_mode=manifest["context_mode"])
    sys.argv = command(manifest, directory, arm)[3:]
    module.main()


def validate_protocol(directory: Path, manifest: dict) -> None:
    if manifest["context_mode"] not in CONTEXT_MODES:
        raise ValueError("Unknown context mode")
    schema = CONTEXT_MODES[manifest["context_mode"]][1]
    if manifest["experiment"] != "structured_extraction_context_state" or manifest["context_schema"] != schema:
        raise ValueError("Wrong context-state protocol")
    if {a["name"] for a in manifest["arms"]} != set(ARMS) or len(manifest["arms"]) != 2:
        raise ValueError("Context experiment requires exactly two arms")
    if manifest["common"] != COMMON:
        raise ValueError("Context experiment changed frozen extraction configuration")
    artifacts = {Path(row["snapshot"]).resolve(): row["sha256"] for row in manifest["artifacts"]}
    if len(artifacts) != len(manifest["artifacts"]):
        raise ValueError("Duplicate artifact binding")
    for path, expected in artifacts.items():
        if digest(path) != expected:
            raise ValueError(f"Frozen artifact changed: {path}")
    for name in PROTOCOL_FILES:
        if digest(Path(__file__).with_name(name)) != digest(directory / "snapshot/protocol" / name):
            raise ValueError(f"Live protocol dependency differs from snapshot: {name}")
    for path in (Path(manifest["input"]), Path(manifest["gold_snapshot"]),
                 directory / "chunks.json", directory / "offline_templates.json"):
        if path.resolve() not in artifacts:
            raise ValueError("Actual input, Gold or chunk/template plan not bound to snapshot")
    if artifacts[Path(manifest["gold_snapshot"]).resolve()] != manifest["gold_sha256"]:
        raise ValueError("Gold identity differs from frozen protocol")
    for arm in manifest["arms"]:
        path = Path(arm["system_prompt"]).resolve()
        expected_path = (directory / "structured_system_prompt.txt" if arm["name"] == "structured_state"
                         else directory / "snapshot/prompts/extraction_system.txt")
        if path != expected_path.resolve():
            raise ValueError("System prompt path does not match intended context arm")
        if path not in artifacts or artifacts[path] != arm["system_prompt_sha256"]:
            raise ValueError("Actual system prompt not bound to frozen artifact")
        if (arm["memory_node_bytes"], arm["memory_relation_bytes"]) != (12000, 6000):
            raise ValueError("Memory budget differs from context protocol")
    if manifest["reading_note_enabled"] is not False:
        raise ValueError("Generated reading notes must remain disabled")


def validate_run(directory: Path, manifest: dict, arm: dict, graph: dict) -> None:
    validate_protocol(directory, manifest)
    chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    validate_graph(graph, manifest, arm, chunks)
    state_class, schema, _ = CONTEXT_MODES[manifest["context_mode"]]
    if graph["diagnostics"]["context_state_experiment"] != {"arm": arm["name"], "schema": schema,
                                                          "reading_note_enabled": False}:
        raise ValueError("Context mode diagnostics mismatch")
    target = directory / arm["name"]
    for folder in ("agents", "prompts"):
        for path in (directory / "snapshot" / folder).glob("*"):
            if path.is_file():
                expected = arm["system_prompt_sha256"] if path.name == "extraction_system.txt" else digest(path)
                if digest(target / "runtime" / folder / path.name) != expected:
                    raise ValueError("Runtime code or prompt differs from intended snapshot")
    template = json.loads((directory / "offline_templates.json").read_text(encoding="utf-8"))
    reference = {row["chunk_id"]: row["flat_context"] for row in template["requests"]}
    tracker = state_class(chunks)
    ids = {chunk["id"] for chunk in chunks}
    if {p.stem for p in (target / "requests").glob("*.json")} != ids or {p.stem for p in (target / "commits").glob("*.json")} != ids:
        raise ValueError("Every frozen chunk must have exactly one request and successful commit")
    system_prompt = Path(arm["system_prompt"]).read_text(encoding="utf-8").strip()
    summaries = graph["diagnostics"]["chunk_summaries"]
    for chunk, summary in zip(chunks, summaries):
        row = json.loads((target / "requests" / (chunk["id"] + ".json")).read_text(encoding="utf-8"))
        legacy = row["legacy_payload"]
        if manifest["context_mode"] == "layout_only":
            runtime_module = load_module(target / "runtime/agents/Extractor.py")
            for item in legacy["node_memory"]:
                stored = tracker.graph["nodes"].get(item["id"])
                if stored is None or item["short_description"] != runtime_module.Extractor._short_description(stored["description"]):
                    raise ValueError("Layout arm changed original memory summary")
        if row["system_prompt_sha256"] != data_hash(system_prompt) or row["committed_chunk_ids"] != tracker.completed:
            raise ValueError("Request prompt or committed history mismatch")
        for key in ("source_path", "chunk", "defined_term_hints", "source_latex_candidates"):
            if legacy[key] != reference[chunk["id"]][key]:
                raise ValueError(f"Static input changed: {key}")
        expected = tracker.render(legacy) if arm["name"] == "structured_state" else legacy
        if row["sent_payload"] != expected:
            raise ValueError("Actual context does not match committed state")
        if (len(legacy["node_memory"]), len(legacy["relation_memory"])) != (summary["memory_node_count"], summary["memory_relation_count"]):
            raise ValueError("Request memory differs from actual merge candidates")
        commit = json.loads((target / "commits" / (chunk["id"] + ".json")).read_text(encoding="utf-8"))
        if tracker.commit(chunk["id"], commit["before"], commit["after"]) != commit:
            raise ValueError("Recorded update facts do not match graph mutations")
    node_map = {node["id"]: node for node in graph["nodes"]}
    if set(node_map) != set(tracker.graph["nodes"]):
        raise ValueError("Final graph node identities differ from committed state")
    for node_id, row in tracker.graph["nodes"].items():
        if any(node_map[node_id].get(key) != row.get(key) for key in NODE_FIELDS):
            raise ValueError("Final node semantics differ from committed state")
    final_edges = sorted([r["source_id"], r["type"], r["target_id"]] for r in graph["relations"])
    if final_edges != tracker.graph["relations"]:
        raise ValueError("Final graph edges differ from committed state")


def execute(directory: Path) -> int:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    validate_protocol(directory, manifest)
    if any(arm["status"] != "pending" for arm in manifest["arms"]):
        raise ValueError("Cannot overwrite a started context experiment")
    failed = False
    for arm in manifest["arms"]:
        target = directory / arm["name"]
        target.mkdir(exist_ok=False)
        for folder in ("agents", "prompts"):
            shutil.copytree(directory / "snapshot" / folder, target / "runtime" / folder)
        shutil.copy2(arm["system_prompt"], target / "runtime/prompts/extraction_system.txt")
        args = [sys.executable, "-X", "utf8", str(directory / "snapshot/protocol" / Path(__file__).name),
                "--worker-dir", str(directory), "--arm", arm["name"]]
        write_json(target / "command.json", {"worker": args, "extractor": command(manifest, directory, arm)})
        arm.update(status="running", started_at=datetime.now(timezone.utc).isoformat())
        write_json(directory / "manifest.json", manifest)
        start = time.monotonic()
        try:
            cache_module = load_module(target / "runtime/agents/Extractor.py")
            cache_extractor = cache_module.Extractor(input_path=Path(manifest["input"]), verbose=False)
            validate_cache_paths(cache_extractor,
                                 json.loads((directory / "chunks.json").read_text(encoding="utf-8")),
                                 {**manifest["common"], "input_path": manifest["input"]})
            with (target / "stdout.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, cwd=directory / "snapshot", stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8")
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith("[Extractor]") or "failed:" in line:
                        print(f"[{manifest['topic']}/{arm['name']}] {line.rstrip()}", flush=True)
                code = process.wait()
            arm["exit_code"] = code
            if code:
                raise RuntimeError(f"Worker exited {code}; see stdout.log")
            graph = json.loads((target / "graph.json").read_text(encoding="utf-8"))
            validate_run(directory, manifest, arm, graph)
            arm.update(status="completed", graph_sha256=digest(target / "graph.json"),
                       api_summary=graph["diagnostics"]["api_summary"])
        except Exception as exc:
            failed = True
            arm.update(status="failed", error=str(exc))
            print(f"[{manifest['topic']}/{arm['name']}] failed: {exc}", flush=True)
        finally:
            arm["elapsed_seconds"] = round(time.monotonic() - start, 3)
            write_json(directory / "manifest.json", manifest)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute-prepared", action="store_true")
    parser.add_argument("--worker-dir", type=Path)
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--context-mode", choices=tuple(CONTEXT_MODES), default="package")
    args = parser.parse_args()
    if args.worker_dir:
        if not args.arm:
            parser.error("Worker requires an arm")
        worker(args.worker_dir.resolve(), args.arm)
        return 0
    if not args.output_dir or (args.prepare_only and args.execute_prepared):
        parser.error("Provide output directory and one preparation/execution mode")
    output = args.output_dir.resolve()
    paths = ([Path(path) for path in json.loads((output / "study.json").read_text(encoding="utf-8"))["pairs"]]
             if args.execute_prepared else prepare(output, args.context_mode))
    for path in paths:
        validate_protocol(path, json.loads((path / "manifest.json").read_text(encoding="utf-8")))
    if args.prepare_only:
        return 0
    codes = [execute(path) for path in paths]
    study = json.loads((output / "study.json").read_text(encoding="utf-8"))
    study.update(status="completed" if not any(codes) else "completed_with_failures", pair_exit_codes=codes)
    study.pop("api_requests_executed", None)
    write_json(output / "study.json", study)
    return int(any(codes))


if __name__ == "__main__":
    raise SystemExit(main())
