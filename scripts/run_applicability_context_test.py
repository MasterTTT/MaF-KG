"""Paired source-context diagnostic with identical frozen graph memory."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import time
from pathlib import Path

from extraction_context_state import data_hash
from run_memory_ablation import COMMON, ROOT, digest, load_module, write_json

CASES = {
    "error_propagation": ("differential", "chunk_017_549_573", [(371, 375)], "earlier_source_context"),
    "integral_derivative": ("fundamental", "chunk_003_41_123", [(108, 108)], "current_source_salience"),
}
CONTRACT = """Supplementary source-context experiment contract
Both arms retain the original extraction schema and current-evidence scope.
source_applicability_context contains exact, line-cited textbook excerpts, not
generated notes or expected answers. It may be empty. Use an excerpt only to
qualify an idea actually taught in current_evidence when its applicability is
semantically relevant. Do not create standalone nodes from these excerpts or
attach their conditions to unrelated ideas. Node evidence must still come from
current_evidence; supplementary provenance is preserved in the request log.
Do not invent unstated domain conditions, quantitative thresholds or exact
finite-error guarantees. Approximation remains approximation. An empty
context field does not imply that no applicability conditions exist.

"""


def cited_excerpt(source: Path, start: int, end: int) -> dict:
    lines = source.read_text(encoding="utf-8").splitlines()
    if not 1 <= start <= end <= len(lines):
        raise ValueError("Invalid source range")
    text = "\n".join(f"L{i}: {lines[i - 1]}" for i in range(start, end + 1))
    return {"source_path": str(source), "source_sha256": digest(source),
            "start_line": start, "end_line": end, "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def paired_payloads(payload: dict, excerpts: list[dict]) -> dict:
    result = {}
    for arm in ("control", "source_context"):
        row = copy.deepcopy(payload)
        row["source_applicability_context"] = copy.deepcopy(excerpts) if arm == "source_context" else []
        result[arm] = row
    left, right = (copy.deepcopy(result[arm]) for arm in result)
    left.pop("source_applicability_context")
    right.pop("source_applicability_context")
    if left != right:
        raise ValueError("Paired request differs outside supplementary context")
    return result


def response_audit(response: dict, payload: dict) -> dict:
    allowed = {row["id"] for row in payload["graph_state"]["nodes"]}
    reused = []
    problems = []
    for row in response.get("node_actions", []):
        if row.get("action") == "reuse":
            node_id = row.get("existing_id")
            reused.append(node_id)
            if node_id not in allowed:
                problems.append(f"Reuse target outside frozen memory: {node_id}")
    return {"scope": "raw response identity audit, not production merge validation",
            "reused_ids": reused, "problems": problems}


def prepare(output: Path, repeats: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    baseline = ROOT / "evaluation/context_state_2026-10-03/run04"
    snapshot = output / "snapshot"
    snapshot.mkdir()
    shutil.copy2(Path(__file__), snapshot / Path(__file__).name)
    shutil.copy2(ROOT / "scripts/run_memory_ablation.py", snapshot / "run_memory_ablation.py")
    shutil.copy2(ROOT / "scripts/extraction_context_state.py", snapshot / "extraction_context_state.py")
    shutil.copy2(baseline / "differential/snapshot/agents/Extractor.py", snapshot / "Extractor.py")
    cases = []
    for name, (topic, chunk_id, ranges, kind) in CASES.items():
        folder = output / name
        folder.mkdir()
        source = baseline / topic / "snapshot/input" / f"{topic}.md"
        source_copy = folder / source.name
        shutil.copy2(source, source_copy)
        request_file = baseline / topic / "structured_state/requests" / f"{chunk_id}.json"
        saved = json.loads(request_file.read_text(encoding="utf-8"))
        payload = saved["sent_payload"]
        original_prompt = baseline / topic / "structured_system_prompt.txt"
        original_text = original_prompt.read_text(encoding="utf-8").strip()
        if data_hash(original_text) != saved["system_prompt_sha256"]:
            raise ValueError("Saved system prompt hash mismatch")
        system = CONTRACT + original_text
        (folder / "system_prompt.txt").write_text(system, encoding="utf-8", newline="\n")
        excerpts = [cited_excerpt(source, start, end) for start, end in ranges]
        payloads = paired_payloads(payload, excerpts)
        for arm, row in payloads.items():
            write_json(folder / f"{arm}_request.json", row)
        cases.append({"name": name, "chunk_id": chunk_id, "intervention_kind": kind,
                      "baseline_request_sha256": digest(request_file),
                      "source_sha256": digest(source_copy), "ranges": ranges,
                      "system_prompt_sha256": digest(folder / "system_prompt.txt")})
    manifest = {"experiment": "source_cited_applicability_context", "status": "prepared",
                "common": COMMON, "repeats": repeats, "cases": cases,
                "selection": "Manual diagnostic source selection; not automatic retrieval evaluation",
                "history": "Identical frozen run04 layout requests and selected memories in each pair",
                "scope": "Raw response evaluation only; no global graph mutation or finalizer",
                "criteria": {"error_propagation": ["small increment/local approximation scope",
                    "nonzero derivative", "approximate, not exact finite error bound",
                    "no unrelated imported conditions"],
                    "integral_derivative": ["continuous integrand on interval",
                    "one-sided endpoint derivatives", "no unrelated imported conditions"]},
                "domain_constraints": "y!=0 and a!=0 are not separately stated in selected source; report separately",
                "extractor_sha256": digest(snapshot / "Extractor.py")}
    write_json(output / "manifest.json", manifest)
    return manifest


def execute(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    module = load_module(output / "snapshot/Extractor.py")
    config = manifest["common"]
    client = module.ProviderChatClient(provider=config["provider"], model=config["model"],
        base_url=config["base_url"], api_key_env=config["api_key_env"],
        temperature=config["temperature"], timeout=config["request_timeout"],
        max_retries=config["max_retries"], max_tokens=config["max_tokens"],
        trust_env=False, thinking=config["thinking"], reasoning_effort=config["reasoning_effort"])
    manifest["status"] = "running"
    write_json(output / "manifest.json", manifest)
    for repeat in range(1, manifest["repeats"] + 1):
        for index, case in enumerate(manifest["cases"]):
            folder = output / case["name"]
            arms = ("control", "source_context") if (repeat + index) % 2 else ("source_context", "control")
            for arm in arms:
                target = folder / f"repeat_{repeat}_{arm}.json"
                if target.exists():
                    continue
                payload = json.loads((folder / f"{arm}_request.json").read_text(encoding="utf-8"))
                prompt = (folder / "system_prompt.txt").read_text(encoding="utf-8")
                if digest(folder / "system_prompt.txt") != case["system_prompt_sha256"]:
                    raise ValueError("Experiment prompt changed after preparation")
                label = f"{case['name']}_{repeat}_{arm}"
                offset = len(client.request_history)
                print(f"START {label}", flush=True)
                try:
                    response = client.complete_json(prompt, json.dumps(payload, ensure_ascii=False), label)
                    write_json(target, {"response": response,
                        "audit": response_audit(response, payload),
                        "requests": client.request_history[offset:]})
                except Exception as exc:
                    write_json(folder / f"{label}_error.json", {"error": str(exc),
                               "requests": client.request_history[offset:]})
                    raise
                print(f"DONE {label}", flush=True)
                time.sleep(config["request_delay"])
    manifest["status"] = "completed"
    write_json(output / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        if args.repeats < 1:
            raise ValueError("Positive repeat count required")
        prepare(args.output, args.repeats)
    if not args.prepare_only:
        execute(args.output)


if __name__ == "__main__":
    main()
