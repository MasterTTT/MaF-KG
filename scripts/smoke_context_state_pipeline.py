"""Offline pipeline validation using archived responses; never calls a model API."""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import run_context_state_experiment as runner
from run_memory_ablation import command, load_module, write_json


def smoke(output: Path, archived: Path, context_mode: str = "package") -> None:
    pairs = runner.prepare(output, context_mode)
    pair = next(path for path in pairs if path.name == "differential")
    manifest = json.loads((pair / "manifest.json").read_text(encoding="utf-8"))
    chunks = json.loads((pair / "chunks.json").read_text(encoding="utf-8"))
    archived_chunks = json.loads((archived.parent / "chunks.json").read_text(encoding="utf-8"))
    if chunks != archived_chunks:
        raise ValueError("Offline probe requires identical archived chunk boundaries")
    responses = {}
    cache = archived / "runtime/node_cache/semantic_layer_v1_7_0"
    for chunk in chunks:
        paths = list(cache.glob("*__" + chunk["id"] + "__*.json"))
        if len(paths) != 1:
            raise ValueError("Offline probe requires one archived response per chunk")
        responses[chunk["id"]] = json.loads(paths[0].read_text(encoding="utf-8"))

    class OfflineClient:
        def __init__(self, **kwargs):
            self.request_history = []

        def complete_json(self, system_prompt, user_prompt, label):
            self.request_history.append({"label": label, "status": "success", "usage": {},
                                         "latency_seconds": 0, "provenance": "offline_archived_response"})
            return copy.deepcopy(responses[label])

    for arm in manifest["arms"]:
        target = pair / arm["name"]
        target.mkdir()
        for folder in ("agents", "prompts"):
            shutil.copytree(pair / "snapshot" / folder, target / "runtime" / folder)
        shutil.copy2(arm["system_prompt"], target / "runtime/prompts/extraction_system.txt")
        (target / "requests").mkdir()
        (target / "commits").mkdir()
        module = load_module(target / "runtime/agents/Extractor.py")
        runner.install_hooks(module, chunks, arm["name"],
                             lambda row: write_json(target / "requests" / (row["chunk_id"] + ".json"), row),
                             lambda row: write_json(target / "commits" / (row["chunk_id"] + ".json"), row),
                             context_mode=manifest["context_mode"])
        module.ProviderChatClient = OfflineClient
        args = command(manifest, pair, arm)[3:]
        with (target / "offline_stdout.log").open("w", encoding="utf-8", newline="\n") as log:
            with patch.object(sys, "argv", args), patch.object(module.time, "sleep"), contextlib.redirect_stdout(log):
                module.main()
        graph = json.loads((target / "graph.json").read_text(encoding="utf-8"))
        runner.validate_run(pair, manifest, arm, graph)
        arm.update(status="offline_smoke_passed_not_model_execution")
        print(f"Offline pipeline gate passed: {arm['name']}, {len(chunks)} chunks", flush=True)
    write_json(pair / "manifest.json", manifest)
    write_json(output / "study.json", {"status": "offline_smoke_passed_not_quality_experiment",
                                      "api_requests_executed": 0, "topic": pair.name,
                                      "arms": [arm["name"] for arm in manifest["arms"]],
                                      "archived_responses": str(archived)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archived-arm", type=Path, required=True)
    parser.add_argument("--context-mode", choices=tuple(runner.CONTEXT_MODES), default="package")
    args = parser.parse_args()
    smoke(args.output_dir.resolve(), args.archived_arm.resolve(), args.context_mode)


if __name__ == "__main__":
    main()
