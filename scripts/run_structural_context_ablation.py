"""Freeze and execute an explicit structural-metadata ablation."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from run_cross_topic_memory_ablation import prepare as prepare_topics
from run_memory_ablation import command, digest, load_module, validate_graph, write_json

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("visible_structure", "hidden_structure")
REMOVED_KEYS = ("heading_path", "teaching_block_ids", "teaching_blocks")


def transform(payload: dict, hidden: bool) -> dict:
    result = copy.deepcopy(payload)
    if hidden:
        for key in REMOVED_KEYS:
            del result["chunk"][key]
    return result


def prepare(output: Path) -> list[Path]:
    paths = prepare_topics(output)
    for index, directory in enumerate(paths):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifest["experiment"] = "explicit_structural_metadata_ablation"
        manifest["scope"] = "Only explicit chunk heading/block metadata is hidden; raw text, boundaries, hints and memory remain."
        manifest["removed_keys"] = list(REMOVED_KEYS)
        names = ARMS if not index % 2 else tuple(reversed(ARMS))
        manifest["arms"] = [{"name": name, "memory_node_bytes": 12000,
                             "memory_relation_bytes": 6000, "status": "pending"} for name in names]
        manifest["execution_order"] = list(names)
        manifest["scoring"] = "Frozen core/detail Gold; explicit review, no automatic semantic matching."
        driver = directory / "snapshot/protocol" / Path(__file__).name
        shutil.copy2(Path(__file__), driver)
        manifest["artifacts"].append({"source": str(Path(__file__)), "snapshot": str(driver), "sha256": digest(driver)})
        module = load_module(directory / "snapshot/agents/Extractor.py")
        extractor = module.Extractor(input_path=Path(manifest["input"]), verbose=False)
        state = {**manifest["common"], "input_path": manifest["input"], "include_exercises": False, "diagnostics": {}}
        state = extractor._chunk_document(extractor._load_document(state))
        planned = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        if [c.id for c in state["chunks"]] != [c["id"] for c in planned]:
            raise ValueError("Offline chunk sequence changed")
        checks = []
        for chunk in state["chunks"]:
            full = json.loads(extractor._chunk_extraction_prompt(chunk, manifest["input"], [], []))
            hidden = transform(full, True)
            reconstructed = copy.deepcopy(hidden)
            for key in REMOVED_KEYS:
                reconstructed["chunk"][key] = full["chunk"][key]
            if reconstructed != full:
                raise ValueError("Ablation changed nonstructural payload")
            # Candidate hints and memory contain no discourse-role metadata.
            for key in ("defined_term_hints", "source_latex_candidates", "node_memory", "relation_memory"):
                if any(token in json.dumps(full[key], ensure_ascii=False) for token in
                       ('"discourse_role"', '"teaching_block_id"', '"heading_path"')):
                    raise ValueError(f"Structural metadata leaked through {key}")
            checks.append({"chunk_id": chunk.id, "payload": full, "ablated_payload": hidden})
        check_path = directory / "offline_payload_gate.json"
        write_json(check_path, {"status": "passed", "checks": checks})
        manifest["artifacts"].append({"source": "offline input equality gate", "snapshot": str(check_path), "sha256": digest(check_path)})
        write_json(directory / "manifest.json", manifest)
    write_json(output / "study.json", {"status": "prepared", "experiment": "explicit_structural_metadata_ablation",
                                      "pairs": [str(path) for path in paths], "repetitions_per_topic": 1})
    return paths


def worker(directory: Path, name: str) -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    runtime = directory / name / "runtime"
    module = load_module(runtime / "agents/Extractor.py")
    original = module.Extractor._chunk_extraction_prompt
    request_dir = directory / name / "requests"
    request_dir.mkdir(exist_ok=False)

    def render(self, chunk, *args, **kwargs):
        full = json.loads(original(self, chunk, *args, **kwargs))
        sent = transform(full, name == "hidden_structure")
        write_json(request_dir / (chunk.id + ".json"), {"chunk_id": chunk.id,
                   "full_payload": full, "sent_payload": sent,
                   "system_prompt_sha256": digest(runtime / "prompts/extraction_system.txt")})
        return json.dumps(sent, ensure_ascii=False, separators=(",", ":"))

    module.Extractor._chunk_extraction_prompt = render
    arm = next(arm for arm in manifest["arms"] if arm["name"] == name)
    sys.argv = command(manifest, directory, arm)[3:]
    module.main()


def validate_requests(directory: Path, name: str, chunks: list[dict]) -> None:
    requests = directory / name / "requests"
    if {p.stem for p in requests.glob("*.json")} != {c["id"] for c in chunks}:
        raise ValueError("Not every frozen chunk has a recorded request")
    expected = json.loads((directory / "offline_payload_gate.json").read_text(encoding="utf-8"))
    reference = {row["chunk_id"]: row["payload"] for row in expected["checks"]}
    for chunk in chunks:
        row = json.loads((requests / (chunk["id"] + ".json")).read_text(encoding="utf-8"))
        if row["sent_payload"] != transform(row["full_payload"], name == "hidden_structure"):
            raise ValueError("Recorded request did not apply exact frozen ablation")
        for key in ("source_path", "chunk", "defined_term_hints", "source_latex_candidates"):
            if row["full_payload"][key] != reference[chunk["id"]][key]:
                raise ValueError(f"Actual static context changed: {key}")


def execute(directory: Path) -> int:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if any(arm["status"] != "pending" for arm in manifest["arms"]):
        raise ValueError("Cannot overwrite a started experiment")
    for artifact in manifest["artifacts"]:
        if digest(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError("Frozen artifact changed")
    chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    failed = False
    for arm in manifest["arms"]:
        target = directory / arm["name"]
        target.mkdir(exist_ok=False)
        for folder in ("agents", "prompts"):
            shutil.copytree(directory / "snapshot" / folder, target / "runtime" / folder)
        args = [sys.executable, "-X", "utf8", str(directory / "snapshot/protocol" / Path(__file__).name),
                "--worker-dir", str(directory), "--arm", arm["name"]]
        write_json(target / "command.json", {"worker": args, "extractor": command(manifest, directory, arm)})
        arm.update(status="running", started_at=datetime.now(timezone.utc).isoformat())
        write_json(directory / "manifest.json", manifest)
        start = time.monotonic()
        try:
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
                raise RuntimeError(f"Extractor exited {code}; see stdout.log")
            graph = json.loads((target / "graph.json").read_text(encoding="utf-8"))
            validate_graph(graph, manifest, arm, chunks)
            validate_requests(directory, arm["name"], chunks)
            if digest(target / "runtime/agents/Extractor.py") != digest(directory / "snapshot/agents/Extractor.py"):
                raise ValueError("Frozen Extractor changed")
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
    args = parser.parse_args()
    if args.worker_dir:
        if not args.arm:
            parser.error("Worker requires --arm")
        worker(args.worker_dir, args.arm)
        return 0
    if not args.output_dir or (args.prepare_only and args.execute_prepared):
        parser.error("Provide output directory and a single preparation/execution mode")
    output = args.output_dir.resolve()
    paths = ([Path(p) for p in json.loads((output / "study.json").read_text(encoding="utf-8"))["pairs"]]
             if args.execute_prepared else prepare(output))
    if args.prepare_only:
        return 0
    codes = [execute(path) for path in paths]
    study = json.loads((output / "study.json").read_text(encoding="utf-8"))
    study.update(status="completed" if not any(codes) else "completed_with_failures", pair_exit_codes=codes)
    write_json(output / "study.json", study)
    return int(any(codes))


if __name__ == "__main__":
    raise SystemExit(main())
