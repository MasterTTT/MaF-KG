"""Three-arm reading-summary diagnostic; no verifier or production edits."""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import time
from pathlib import Path

from run_memory_ablation import ROOT, digest, load_module, write_json
from extraction_context_state import data_hash

ARMS = ("original", "source_context", "source_and_summary")
SUMMARY_PROMPT = """Produce a concise source-grounded reading state for a sequential
mathematics textbook extractor. You are not extracting a graph or verifying an
existing response. You see the current source passage, selected prior source
context, parser navigation, and an incomplete committed graph view.
Return JSON with exactly these keys:
current_focus: {text, source_spans};
relevant_source_context: [{text, source_spans, relevance_to_current_focus}];
graph_context: {scope, visible_identity_ids, text};
uncertainties: [short factual limitations].
Each source span is {start_line, end_line}. State the current topic, relevant
earlier context, and source-explicit scope/qualifications without inventing
facts. Distinguish approximation from exact claims. Preserve qualifications
when compressing, but do not add implicit mathematical domain conditions.
Use only supplied source lines for mathematical statements. Graph summaries
are identity context, not mathematical evidence. Do not suggest nodes, edges,
expected answers, extraction scores, or repair operations. Avoid copying the
entire source. Keep Chinese prose to roughly 350 Chinese characters excluding
identities and citations. This is a navigational note, not a substitute source.
"""
EXTRACTION_CONTRACT = """Reading-state experiment input contract
All three arms use the same extraction rules. Two optional fields may be empty.
source_applicability_context contains exact line-cited textbook excerpts. They
may help qualify ideas taught in current_evidence, not create standalone nodes.
reading_state_summary is a model-generated, possibly incomplete or inaccurate
reading note. It is not evidence, not an extraction target list, and not a graph
mutation. Check its meaning against the supplied original text; prefer original
text on disagreement. It must not override node admission or reuse rules.
Retain source-explicit applicability conditions relevant to current statements.
Node evidence must remain within current_evidence. Do not invent unstated domain
conditions, quantitative thresholds or exact finite-error guarantees.

"""


def build_arms(payload: dict, summary: dict) -> dict:
    result = {}
    for arm in ARMS:
        row = copy.deepcopy(payload)
        if arm == "original":
            row["source_applicability_context"] = []
        row["reading_state_summary"] = copy.deepcopy(summary) if arm == "source_and_summary" else None
        result[arm] = row
    a, b = (copy.deepcopy(result[arm]) for arm in ARMS[1:])
    a.pop("reading_state_summary")
    b.pop("reading_state_summary")
    if a != b:
        raise ValueError("Summary pair changed non-summary input")
    return result


def summary_request(payload: dict) -> dict:
    return {"reading_context": payload["reading_context"],
            "current_evidence": payload["current_evidence"],
            "source_applicability_context": payload["source_applicability_context"],
            "graph_state": payload["graph_state"]}


def check_summary(note: dict, payload: dict) -> dict:
    if set(note) != {"current_focus", "relevant_source_context", "graph_context", "uncertainties"}:
        raise ValueError("Unexpected summary fields")
    allowed = set(range(payload["current_evidence"]["start_line"], payload["current_evidence"]["end_line"] + 1))
    for excerpt in payload["source_applicability_context"]:
        allowed.update(range(excerpt["start_line"], excerpt["end_line"] + 1))
    for key in ("context_before_text", "context_after_text"):
        text = payload.get("reading_context", {}).get(key, "")
        allowed.update(int(line) for line in re.findall(r"^(\d+):", text, re.MULTILINE))
    for item in [note["current_focus"], *note["relevant_source_context"]]:
        for span in item["source_spans"]:
            start, end = span["start_line"], span["end_line"]
            if start > end or not set(range(start, end + 1)) <= allowed:
                raise ValueError("Summary cites outside supplied source")
    identities = {row["id"] for row in payload["graph_state"]["nodes"]}
    if not set(note["graph_context"]["visible_identity_ids"]) <= identities:
        raise ValueError("Summary invents graph identity")
    return {"citation_range_check": "passed", "identity_check": "passed",
            "semantic_fidelity": "requires separate source-grounded inspection; not automatically certified"}


def prepare(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    previous = ROOT / "evaluation/context_state_2026-10-03/run06"
    old = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
    snapshot = output / "snapshot"
    snapshot.mkdir()
    for name in ("run_reading_summary_test.py", "run_memory_ablation.py", "extraction_context_state.py",
                 "audit_applicability_context_test.py"):
        shutil.copy2(ROOT / "scripts" / name, snapshot / name)
    shutil.copy2(previous / "snapshot/Extractor.py", snapshot / "Extractor.py")
    (snapshot / "summary_system_prompt.txt").write_text(SUMMARY_PROMPT, encoding="utf-8", newline="\n")
    cases = []
    for case in old["cases"]:
        name = case["name"]
        folder = output / name
        folder.mkdir()
        old_folder = previous / name
        source = next(old_folder.glob("*.md"))
        shutil.copy2(source, folder / source.name)
        request = json.loads((old_folder / "source_context_request.json").read_text(encoding="utf-8"))
        write_json(folder / "base_request.json", request)
        write_json(folder / "summary_request.json", summary_request(request))
        topic = "differential" if name == "error_propagation" else "fundamental"
        original = ROOT / "evaluation/context_state_2026-10-03/run04" / topic / "structured_system_prompt.txt"
        prompt = EXTRACTION_CONTRACT + original.read_text(encoding="utf-8").strip()
        (folder / "system_prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
        cases.append({**case, "system_prompt_sha256": digest(folder / "system_prompt.txt"),
                      "base_request_sha256": digest(folder / "base_request.json")})
    manifest = {"experiment": "model_reading_state_summary", "status": "prepared", "common": old["common"],
                "arms": list(ARMS), "repeats": 2, "cases": cases,
                "summary_policy": "One generated summary per case, frozen across extraction repeats",
                "history": "Same frozen source chunks and Top-K graph memory in every arm",
                "primary_contrast": "source_and_summary versus source_context; only generated summary differs",
                "secondary_contrast": "source_and_summary versus original; bundled context intervention",
                "limits": "Two selected chunks, manual source-context selection, raw responses only; no end-to-end graph run",
                "summary_system_prompt_sha256": digest(snapshot / "summary_system_prompt.txt"),
                "extractor_sha256": digest(snapshot / "Extractor.py"),
                "criteria": old["criteria"]}
    write_json(output / "manifest.json", manifest)
    return manifest


def execute(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    module = load_module(output / "snapshot/Extractor.py")
    c = manifest["common"]
    client = module.ProviderChatClient(provider=c["provider"], model=c["model"], base_url=c["base_url"],
        api_key_env=c["api_key_env"], temperature=c["temperature"], timeout=c["request_timeout"],
        max_retries=c["max_retries"], max_tokens=c["max_tokens"], trust_env=False,
        thinking=c["thinking"], reasoning_effort=c["reasoning_effort"])
    manifest["status"] = "running"
    write_json(output / "manifest.json", manifest)

    def call(path: Path, prompt: str, payload: dict, label: str) -> dict:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        print(f"START {label}", flush=True)
        start = len(client.request_history)
        try:
            response = client.complete_json(prompt, json.dumps(payload, ensure_ascii=False), label)
            write_json(path, {"response": response, "requests": client.request_history[start:],
                             "system_prompt_hash": data_hash(prompt), "payload_hash": data_hash(payload)})
        except Exception as exc:
            write_json(path.with_suffix(".error.json"), {"error": str(exc), "requests": client.request_history[start:]})
            raise
        print(f"DONE {label}", flush=True)
        time.sleep(c["request_delay"])
        return response

    summary_prompt = (output / "snapshot/summary_system_prompt.txt").read_text(encoding="utf-8")
    if digest(output / "snapshot/summary_system_prompt.txt") != manifest["summary_system_prompt_sha256"]:
        raise ValueError("Frozen summary prompt changed")
    for case in manifest["cases"]:
        folder = output / case["name"]
        payload = json.loads((folder / "base_request.json").read_text(encoding="utf-8"))
        if digest(folder / "base_request.json") != case["base_request_sha256"]:
            raise ValueError("Frozen baseline changed")
        request = json.loads((folder / "summary_request.json").read_text(encoding="utf-8"))
        note = call(folder / "generated_summary.json", summary_prompt, request, f"summary_{case['name']}")
        write_json(folder / "summary_reference_audit.json", check_summary(note, payload))
        for arm, row in build_arms(payload, note).items():
            write_json(folder / f"{arm}_request.json", row)
    for repeat in (1, 2):
        for index, case in enumerate(manifest["cases"]):
            folder = output / case["name"]
            order = list(manifest["arms"])
            if not order or len(set(order)) != len(order) or not set(order) <= set(ARMS):
                raise ValueError("Invalid explicitly configured experiment arms")
            shift = (repeat - 1 + index) % len(order)
            order = order[shift:] + order[:shift]
            for arm in order:
                prompt = (folder / "system_prompt.txt").read_text(encoding="utf-8")
                if digest(folder / "system_prompt.txt") != case["system_prompt_sha256"]:
                    raise ValueError("Frozen extraction prompt changed")
                payload = json.loads((folder / f"{arm}_request.json").read_text(encoding="utf-8"))
                call(folder / f"repeat_{repeat}_{arm}.json", prompt, payload, f"{case['name']}_{repeat}_{arm}")
    manifest["status"] = "completed"
    write_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        prepare(args.output)
    if not args.prepare_only:
        execute(args.output)
