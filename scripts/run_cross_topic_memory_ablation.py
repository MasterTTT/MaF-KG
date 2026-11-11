"""Freeze source-only adjudicated Gold and run two cross-topic memory pairs."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from run_memory_ablation import ARMS, COMMON, digest, execute, load_module, write_json

ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation/memory_ablation_2026-10-02"


def prepare(output: Path) -> list[Path]:
    baseline = EVALUATION / "run_official_pair02"
    original = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    if COMMON != original["common"]:
        raise ValueError("Cross-topic configuration differs from baseline")
    for artifact in original["artifacts"]:
        if digest(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError("Baseline artifact changed")
    gold_path = EVALUATION / "cross_topic_gold_v1.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if gold["status"] != "user_adjudicated_before_prediction":
        raise ValueError("Gold must be adjudicated before extraction")
    output.mkdir(parents=True, exist_ok=False)
    paths = []
    for index, topic in enumerate(gold["topics"]):
        directory = output / topic["id"]
        snapshot = directory / "snapshot"
        artifacts = []
        for folder in ("agents", "prompts"):
            shutil.copytree(baseline / "snapshot" / folder, snapshot / folder)
            for target in sorted((snapshot / folder).glob("*")):
                if target.is_file():
                    artifacts.append({"source": str(baseline / "snapshot" / folder / target.name),
                                      "snapshot": str(target), "sha256": digest(target)})
        for path in (gold_path, Path(__file__), ROOT / "scripts/run_memory_ablation.py"):
            target = snapshot / "protocol" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            artifacts.append({"source": str(path), "snapshot": str(target), "sha256": digest(target)})
        source = Path(topic["source"])
        original_source = snapshot / "source" / source.name
        original_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, original_source)
        artifacts.append({"source": str(source), "snapshot": str(original_source), "sha256": digest(original_source)})
        lines = original_source.read_text(encoding="utf-8").splitlines(keepends=True)
        if not 1 <= topic["start_line"] <= topic["end_line"] <= len(lines):
            raise ValueError("Invalid source range")
        frozen_input = snapshot / "input" / (topic["id"] + ".md")
        frozen_input.parent.mkdir(parents=True, exist_ok=True)
        frozen_input.write_text("".join(lines[topic["start_line"] - 1:topic["end_line"]]), encoding="utf-8")
        artifacts.append({"source": str(source), "snapshot": str(frozen_input), "sha256": digest(frozen_input)})
        module = load_module(snapshot / "agents/Extractor.py")
        extractor = module.Extractor(input_path=frozen_input, chunk_strategy=COMMON["chunk_strategy"],
                                     max_chunk_chars=COMMON["max_chunk_chars"], verbose=False)
        state = {**COMMON, "input_path": str(frozen_input), "include_exercises": False, "diagnostics": {}}
        state = extractor._chunk_document(extractor._load_document(state))
        chunks = [asdict(chunk) for chunk in state["chunks"]]
        if len(chunks) < 2:
            raise ValueError("Cross-topic memory pair needs sequential chunks")
        write_json(directory / "chunks.json", chunks)
        artifacts.append({"source": "offline chunk plan", "snapshot": str(directory / "chunks.json"),
                          "sha256": digest(directory / "chunks.json")})
        arms = [{"name": name, "memory_node_bytes": budget[0], "memory_relation_bytes": budget[1],
                 "status": "pending"} for name, budget in ARMS.items()]
        if index % 2:
            arms.reverse()
        manifest = {
            "experiment": "cross_topic_model_visible_memory", "topic": topic["id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "python": sys.executable, "common": original["common"], "artifacts": artifacts,
            "input": str(frozen_input), "chunk_count": len(chunks), "arms": arms,
            "gold_sha256": digest(gold_path), "gold_snapshot": str(snapshot / "protocol" / gold_path.name),
            "original_source": str(original_source), "original_source_range": [topic["start_line"], topic["end_line"]],
            "source_line_offset": topic["start_line"] - 1,
            "execution_order": [arm["name"] for arm in arms],
            "scope": original["scope"], "disabled": original["disabled"],
            "scoring": "Explicit review against pre-adjudicated cross-topic Gold; report core/detail separately; B1 optional.",
        }
        write_json(directory / "manifest.json", manifest)
        print(f"Frozen {topic['id']}: {len(chunks)} chunks, order={manifest['execution_order']}", flush=True)
        paths.append(directory)
    write_json(output / "study.json", {"status": "prepared", "gold_sha256": digest(gold_path),
                                      "pairs": [str(path) for path in paths], "repetitions_per_topic": 1})
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute-prepared", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.prepare_only and args.execute_prepared:
        parser.error("Choose preparation or execution")
    if args.execute_prepared:
        study = json.loads((output / "study.json").read_text(encoding="utf-8"))
        paths = [Path(path) for path in study["pairs"]]
    else:
        paths = prepare(output)
    if args.prepare_only:
        return 0
    codes = []
    for directory in paths:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if any(arm["status"] != "pending" for arm in manifest["arms"]):
            raise ValueError("Prepared execution must not overwrite a started pair")
        codes.append(execute(directory, manifest))
    study = json.loads((output / "study.json").read_text(encoding="utf-8"))
    study["status"] = "completed" if not any(codes) else "completed_with_failures"
    study["pair_exit_codes"] = codes
    write_json(output / "study.json", study)
    return int(any(codes))


if __name__ == "__main__":
    raise SystemExit(main())
