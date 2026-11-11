"""Compare frozen teaching boundaries with fixed raw-character windows."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from run_cross_topic_memory_ablation import prepare as prepare_topics
from run_memory_ablation import command, digest, load_module, validate_graph, write_json

ARMS = ("teaching_boundaries", "character_boundaries")
STRUCTURAL_KEYS = ("heading_path", "teaching_block_ids", "teaching_blocks")


def make_stream(lines: list[str], eligible: list[int]) -> tuple[str, list[dict]]:
    parts, positions = [], []
    offset = 0
    for number in eligible:
        part = lines[number - 1] + "\n"
        positions.append({"line": number, "start": offset, "end": offset + len(part)})
        parts.append(part)
        offset += len(part)
    return "".join(parts), positions


def fragments(stream: str, positions: list[dict], start: int, end: int) -> list[dict]:
    result = []
    for row in positions:
        left, right = max(start, row["start"]), min(end, row["end"])
        if left < right:
            result.append({"line": row["line"], "source_start": left - row["start"],
                           "source_end": right - row["start"], "text": stream[left:right]})
    return result


def numbered(parts: list[dict]) -> str:
    return "\n".join(f"{row['line']}: {row['text'].removesuffix(chr(10))}" for row in parts)


def plan(stream: str, positions: list[dict], intervals: list[tuple[int, int]], lines: list[str],
         eligible: set[int], before: int, after: int, strategy: str) -> list[dict]:
    result = []
    for index, (start, end) in enumerate(intervals, 1):
        parts = fragments(stream, positions, start, end)
        first, last = parts[0]["line"], parts[-1]["line"]
        chunk = {
            "id": f"chunk_{index:03d}_{first}_{last}", "start_line": first, "end_line": last,
            "heading_path": [], "text": numbered(parts),
            "context_before_text": "\n".join(f"{n}: {lines[n - 1]}" for n in range(max(1, first - before), first) if n in eligible),
            "context_after_text": "\n".join(f"{n}: {lines[n - 1]}" for n in range(last + 1, min(len(lines), last + after) + 1) if n in eligible),
            "break_reason": strategy,
        }
        result.append({"chunk": chunk, "stream_start": start, "stream_end": end,
                       "raw_body": stream[start:end], "fragments": parts})
    return result


def raw_text(chunk: dict) -> str:
    return "\n".join(re.sub(r"^\d+: ", "", line) for line in chunk["text"].splitlines())


def model_payload(payload: dict) -> dict:
    result = copy.deepcopy(payload)
    for key in STRUCTURAL_KEYS:
        del result["chunk"][key]
    # A line-level candidate must not reconstruct content beyond a character cut.
    visible = re.sub(r"\s+", "", raw_text(result["chunk"]))
    result["source_latex_candidates"] = [row for row in result["source_latex_candidates"]
                                         if re.sub(r"\s+", "", row["latex"]) in visible]
    return result


def character_intervals(length: int, budget: int) -> list[tuple[int, int]]:
    if budget <= 0:
        raise ValueError("Character budget must be positive")
    return [(start, min(length, start + budget)) for start in range(0, length, budget)]


def prepare(output: Path) -> list[Path]:
    directories = prepare_topics(output)
    for topic_index, directory in enumerate(directories):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        original = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        lines = Path(manifest["input"]).read_text(encoding="utf-8").splitlines()
        eligible = sorted({n for c in original for n in range(c["start_line"], c["end_line"] + 1)})
        stream, positions = make_stream(lines, eligible)
        by_line = {row["line"]: row for row in positions}
        structured = [(by_line[c["start_line"]]["start"], by_line[c["end_line"]]["end"]) for c in original]
        intervals = {ARMS[0]: structured, ARMS[1]: character_intervals(len(stream), manifest["common"]["max_chunk_chars"])}
        module = load_module(directory / "snapshot/agents/Extractor.py")
        extractor = module.Extractor(input_path=Path(manifest["input"]), verbose=False)
        state = {**manifest["common"], "input_path": manifest["input"], "include_exercises": False, "diagnostics": {}}
        extractor._chunk_document(extractor._load_document(state))
        order = ARMS if not topic_index % 2 else tuple(reversed(ARMS))
        arms = []
        for name in order:
            rows = plan(stream, positions, intervals[name], lines, set(eligible),
                        manifest["common"]["chunk_context_before_lines"], manifest["common"]["chunk_context_after_lines"], name)
            if "".join(row["raw_body"] for row in rows) != stream:
                raise ValueError("Chunk core universe differs between arms")
            for previous, current in zip(rows, rows[1:]):
                if previous["stream_end"] != current["stream_start"]:
                    raise ValueError("Chunk core is omitted or duplicated")
            if rows[0]["stream_start"] != 0 or rows[-1]["stream_end"] != len(stream):
                raise ValueError("Chunk core coverage incomplete")
            frozen_plan = directory / (name + "_plan.json")
            write_json(frozen_plan, rows)
            manifest["artifacts"].append({"source": "frozen boundary plan", "snapshot": str(frozen_plan), "sha256": digest(frozen_plan)})
            templates = []
            for row in rows:
                chunk = module.DocumentChunk(**row["chunk"])
                full = json.loads(extractor._chunk_extraction_prompt(chunk, manifest["input"], [], []))
                templates.append({"chunk_id": chunk.id, "payload": model_payload(full)})
            template_path = directory / (name + "_payloads.json")
            write_json(template_path, templates)
            manifest["artifacts"].append({"source": "offline empty-memory request", "snapshot": str(template_path), "sha256": digest(template_path)})
            arms.append({"name": name, "memory_node_bytes": 12000, "memory_relation_bytes": 6000,
                         "plan": str(frozen_plan), "payloads": str(template_path), "chunk_count": len(rows),
                         "body_chars": [len(row["raw_body"]) for row in rows],
                         "within_line_cuts": sum(row["fragments"][0]["source_start"] > 0 for row in rows), "status": "pending"})
        universe_path = directory / "eligible_source.json"
        write_json(universe_path, {"line_numbers": eligible, "raw_stream": stream, "positions": positions,
                                  "core_universe_sha256": hashlib.sha256(stream.encode("utf-8")).hexdigest(),
                                  "excluded_nonblank_lines": [n for n, line in enumerate(lines, 1) if n not in set(eligible) and line.strip()]})
        manifest["artifacts"].append({"source": "common content universe", "snapshot": str(universe_path), "sha256": digest(universe_path)})
        for path in (Path(__file__), Path(__file__).with_name("score_chunk_boundary_ablation.py")):
            target = directory / "snapshot/protocol" / path.name
            shutil.copy2(path, target)
            manifest["artifacts"].append({"source": str(path), "snapshot": str(target), "sha256": digest(target)})
        manifest.update(experiment="chunk_boundary_ablation", arms=arms, execution_order=list(order),
                        scope="Same eligible source character stream; frozen teaching boundaries versus fixed raw-character windows; metadata hidden in both.",
                        boundary_budget_definition="Character arm: 3200 Python Unicode code points of raw body including line feeds, excluding line-number prefixes. Structured arm: existing serialized block budget, exact observed sizes recorded.",
                        core_universe_sha256=hashlib.sha256(stream.encode("utf-8")).hexdigest(),
                        scoring="Explicit review of final descriptions and visible evidence; do not equate raw source retention with Claim coverage.")
        write_json(directory / "manifest.json", manifest)
        print(f"Boundary plans {manifest['topic']}: " + ", ".join(f"{a['name']}={a['chunk_count']}" for a in arms), flush=True)
    write_json(output / "study.json", {"experiment": "chunk_boundary_ablation", "status": "prepared",
                                      "pairs": [str(p) for p in directories], "repetitions_per_topic": 1})
    return directories


def worker(directory: Path, name: str) -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    arm = next(a for a in manifest["arms"] if a["name"] == name)
    rows = json.loads(Path(arm["plan"]).read_text(encoding="utf-8"))
    runtime = directory / name / "runtime"
    module = load_module(runtime / "agents/Extractor.py")
    original_chunks = module.Extractor._chunk_document
    original_prompt = module.Extractor._chunk_extraction_prompt
    original_evidence = module.Extractor._sanitize_evidence
    requests = directory / name / "requests"
    requests.mkdir(exist_ok=False)

    def chunks(self, state):
        native = original_chunks(self, state)
        planned = [module.DocumentChunk(**row["chunk"]) for row in rows]
        diagnostics = dict(native["diagnostics"])
        diagnostics.update(chunk_count=len(planned), chunk_spans=[asdict(c) for c in planned],
                           experimental_boundary_strategy=name,
                           boundary_driver_sha256=digest(Path(__file__)))
        self._log(f"Frozen boundary override {name}: {len(planned)} chunks")
        return {**native, "chunks": planned, "diagnostics": diagnostics}

    def prompt(self, chunk, *args, **kwargs):
        sent = model_payload(json.loads(original_prompt(self, chunk, *args, **kwargs)))
        write_json(requests / (chunk.id + ".json"), {"chunk_id": chunk.id, "sent_payload": sent,
                   "system_prompt_sha256": digest(runtime / "prompts/extraction_system.txt")})
        return json.dumps(sent, ensure_ascii=False, separators=(",", ":"))

    def evidence(self, ref, chunk, source_path, raw_lines):
        result = original_evidence(self, ref, chunk, source_path, raw_lines)
        visible = {}
        for text in (chunk.context_before_text, chunk.text, chunk.context_after_text):
            for line in text.splitlines():
                match = re.match(r"^(\d+): (.*)$", line)
                if match:
                    number = int(match.group(1))
                    if number in visible:
                        raise ValueError("Visible line fragments overlap unexpectedly")
                    visible[number] = match.group(2)
        result.text = "\n".join(visible[n] for n in range(ref.start_line, ref.end_line + 1) if n in visible)
        if not result.text.strip():
            raise ValueError("Evidence contains no actually visible source characters")
        return result

    module.Extractor._chunk_document = chunks
    module.Extractor._chunk_extraction_prompt = prompt
    module.Extractor._sanitize_evidence = evidence
    sys.argv = command(manifest, directory, arm)[3:]
    module.main()


def validate_run(directory: Path, manifest: dict, arm: dict, graph: dict) -> None:
    rows = json.loads(Path(arm["plan"]).read_text(encoding="utf-8"))
    chunks = [row["chunk"] for row in rows]
    validate_graph(graph, manifest, arm, chunks)
    if graph["diagnostics"].get("experimental_boundary_strategy") != arm["name"]:
        raise ValueError("Actual boundary strategy mismatch")
    templates = {r["chunk_id"]: r["payload"] for r in json.loads(Path(arm["payloads"]).read_text(encoding="utf-8"))}
    requests = directory / arm["name"] / "requests"
    if {p.stem for p in requests.glob("*.json")} != set(templates):
        raise ValueError("Request coverage differs from frozen plan")
    prompt_hash = digest(directory / "snapshot/prompts/extraction_system.txt")
    summaries = {r["chunk_id"]: r for r in graph["diagnostics"]["chunk_summaries"]}
    for chunk in chunks:
        record = json.loads((requests / (chunk["id"] + ".json")).read_text(encoding="utf-8"))
        sent = record["sent_payload"]
        if record["system_prompt_sha256"] != prompt_hash:
            raise ValueError("System prompt changed")
        for key in ("source_path", "chunk", "defined_term_hints", "source_latex_candidates"):
            if sent[key] != templates[chunk["id"]][key]:
                raise ValueError("Actual static payload differs from frozen plan")
        summary = summaries[chunk["id"]]
        if len(sent["node_memory"]) != summary["memory_node_count"] or len(sent["relation_memory"]) != summary["memory_relation_count"]:
            raise ValueError("Memory request counts differ from diagnostics")
    for node in graph["nodes"]:
        for ev in node["evidence"]:
            record = json.loads((requests / (ev["chunk_id"] + ".json")).read_text(encoding="utf-8"))
            chunk = record["sent_payload"]["chunk"]
            texts = (chunk["text"], chunk["context_before_text"], chunk["context_after_text"])
            visible = {int(m.group(1)): m.group(2) for text in texts for line in text.splitlines()
                       if (m := re.match(r"^(\d+): (.*)$", line))}
            expected = "\n".join(visible[n] for n in range(ev["start_line"], ev["end_line"] + 1) if n in visible)
            if ev["text"] != expected:
                raise ValueError("Evidence was expanded beyond visible source fragments")


def execute(directory: Path) -> int:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if any(a["status"] != "pending" for a in manifest["arms"]):
        raise ValueError("Cannot overwrite started experiment")
    for item in manifest["artifacts"]:
        if digest(Path(item["snapshot"])) != item["sha256"]:
            raise ValueError("Frozen artifact changed")
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
            graph_path = target / "graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            validate_run(directory, manifest, arm, graph)
            arm.update(status="completed", graph_sha256=digest(graph_path), api_summary=graph["diagnostics"]["api_summary"])
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
            parser.error("Worker needs arm")
        worker(args.worker_dir, args.arm)
        return 0
    if not args.output_dir or (args.prepare_only and args.execute_prepared):
        parser.error("Provide output directory and only one mode")
    output = args.output_dir.resolve()
    paths = ([Path(p) for p in json.loads((output / "study.json").read_text(encoding="utf-8"))["pairs"]]
             if args.execute_prepared else prepare(output))
    if args.prepare_only:
        return 0
    codes = [execute(p) for p in paths]
    study = json.loads((output / "study.json").read_text(encoding="utf-8"))
    study.update(status="completed" if not any(codes) else "completed_with_failures", pair_exit_codes=codes)
    write_json(output / "study.json", study)
    return int(any(codes))


if __name__ == "__main__":
    raise SystemExit(main())
