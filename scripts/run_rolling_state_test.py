"""Sequential frozen-history replay with incrementally generated state notes."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import time
from pathlib import Path

from extraction_context_state import data_hash
from run_memory_ablation import COMMON, ROOT, digest, load_module, write_json

ARMS = ("no_state", "rolling_state")
STATE_PROMPT = """Update a compact persistent reading-and-graph state after one
successfully committed textbook chunk. This is an incremental state update, not
graph extraction, a verifier, or a plan of expected nodes. You receive the previous
state, the just-processed original chunk, and authoritative graph mutation records
from a frozen committed history. You cannot see future passages.
Return JSON with exactly these keys:
processed_chunk_ids: exact ordered list supplied in history_scope;
reading_progress: [{topic, status, source_spans}];
graph_notes: [{text, node_ids, relation_identities}];
scope_conditions: [{text, applies_to, source_spans}];
context_limits: [short strings].
source_spans are {start_line,end_line}; relation_identities are three-element
[source_id,relation_type,target_id] arrays. Retain useful old source qualifications
and links while updating the current state. Drop repetitive local example details,
but distinguish examples from general results. Introduced, proved, and applied are
different source statuses: node existence in a graph does not prove a theorem has
been proved or all source content has been covered. Preserve approximate scope and
source qualifications. Do not invent missing mathematical facts, quantities,
unprocessed topics, graph identities or relations. Graph records are status facts,
not substitutes for source evidence. Do not suggest repairs, future extraction
targets, scores, or Gold answers. Keep Chinese prose within about 500 Chinese
characters excluding identities and citations. Compress carefully; previous notes
may be incomplete. Keep important distinctions rather than a long chronological log.
"""
CONTRACT = """Incremental-state experiment input contract
rolling_reading_state summarizes only previously processed source and committed
graph history. It is a generated, incomplete navigational note, not source evidence
or an expected-answer list. Do not create nodes from it, treat its existence claims
as complete global coverage, or reuse an ID absent from graph_state.nodes. Source
wording governs on disagreement. The current chunk remains the extraction region.
Use an empty state as absence of a note, not absence of previous knowledge.
All original admission, evidence, and relation rules remain in force.

"""


def paired_requests(payload: dict, state: dict) -> dict:
    result = {}
    for arm in ARMS:
        row = copy.deepcopy(payload)
        row["rolling_reading_state"] = copy.deepcopy(state) if arm == "rolling_state" else None
        result[arm] = row
    left, right = (copy.deepcopy(result[arm]) for arm in ARMS)
    left.pop("rolling_reading_state")
    right.pop("rolling_reading_state")
    if left != right:
        raise ValueError("Rolling-state pair changed other inputs")
    return result


def check_state(note: dict, processed: list[dict], graph: dict) -> dict:
    expected = {"processed_chunk_ids", "reading_progress", "graph_notes", "scope_conditions", "context_limits"}
    if set(note) != expected:
        raise ValueError("Unexpected rolling-state schema")
    if note["processed_chunk_ids"] != [row["id"] for row in processed]:
        raise ValueError("State changes processing order or claims future completion")
    lines = set()
    for row in processed:
        lines.update(range(row["start_line"], row["end_line"] + 1))
    for row in [*note["reading_progress"], *note["scope_conditions"]]:
        for span in row["source_spans"]:
            start, end = span["start_line"], span["end_line"]
            if start > end or not set(range(start, end + 1)) <= lines:
                raise ValueError("State cites future or unsupplied source lines")
    for row in note["graph_notes"]:
        if not set(row["node_ids"]) <= set(graph["nodes"]):
            raise ValueError("State invents uncommitted graph identity")
        if any(identity not in graph["relations"] for identity in row["relation_identities"]):
            raise ValueError("State invents graph relation")
    return {"history_order": "passed", "source_range": "passed", "graph_references": "passed",
            "semantic_fidelity": "not certified by mechanical checks"}


def prepare(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    old = ROOT / "evaluation/context_state_2026-10-03/run04/fundamental"
    snapshot = output / "snapshot"
    snapshot.mkdir()
    for name in ("run_rolling_state_test.py", "run_memory_ablation.py", "extraction_context_state.py",
                 "audit_applicability_context_test.py"):
        shutil.copy2(ROOT / "scripts" / name, snapshot / name)
    shutil.copy2(old / "snapshot/agents/Extractor.py", snapshot / "Extractor.py")
    shutil.copy2(old / "snapshot/input/fundamental.md", snapshot / "fundamental.md")
    shutil.copy2(old / "snapshot/protocol/cross_topic_gold_v1.json", snapshot / "gold.json")
    chunks = json.loads((old / "chunks.json").read_text(encoding="utf-8"))
    write_json(output / "chunks.json", chunks)
    for name in ("requests", "commits"):
        shutil.copytree(old / "structured_state" / name, snapshot / name)
    original = (old / "structured_system_prompt.txt").read_text(encoding="utf-8").strip()
    (snapshot / "extraction_system_prompt.txt").write_text(CONTRACT + original, encoding="utf-8", newline="\n")
    (snapshot / "state_system_prompt.txt").write_text(STATE_PROMPT, encoding="utf-8", newline="\n")
    (output / "states").mkdir()
    (output / "paired_requests").mkdir()
    for arm in ARMS:
        (output / arm).mkdir()
    manifest = {"experiment": "incremental_rolling_state_frozen_history", "status": "prepared",
                "common": COMMON, "arms": list(ARMS), "chunk_ids": [c["id"] for c in chunks],
                "evaluated_chunk_ids": [c["id"] for c in chunks[1:]],
                "history": "Authoritative run04 committed graph; new outputs do not alter subsequent input memory",
                "state_update": "Previous note + just-processed chunk + committed graph delta; no future source",
                "calls": "6 state updates and 12 paired extractions; first chunk has no prior state and is not rerun",
                "repeats": 1, "scope": "Seven-chunk sequential diagnostic, not independently evolving end-to-end graphs",
                "criteria": {"core_slots": ["b_upper", "b_derivative", "b_existence", "b_fundamental"],
                    "main_relations": ["br1", "br2", "br3", "br4"],
                    "conditions": ["derivative_continuity", "derivative_endpoints",
                                   "fundamental_continuity", "fundamental_same_interval_primitive"],
                    "other": ["duplicate_create_against_visible_memory", "unrelated_example_nodes",
                              "summary_condition_loss", "summary_false_completion", "summary_future_leak"]},
                "extractor_sha256": digest(snapshot / "Extractor.py"),
                "extraction_system_sha256": digest(snapshot / "extraction_system_prompt.txt"),
                "state_system_sha256": digest(snapshot / "state_system_prompt.txt")}
    write_json(output / "manifest.json", manifest)
    return manifest


def execute(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    chunks = json.loads((output / "chunks.json").read_text(encoding="utf-8"))
    module = load_module(output / "snapshot/Extractor.py")
    c = manifest["common"]
    client = module.ProviderChatClient(provider=c["provider"], model=c["model"], base_url=c["base_url"],
        api_key_env=c["api_key_env"], temperature=c["temperature"], timeout=c["request_timeout"],
        max_retries=c["max_retries"], max_tokens=c["max_tokens"], trust_env=False,
        thinking=c["thinking"], reasoning_effort=c["reasoning_effort"])
    manifest["status"] = "running"
    write_json(output / "manifest.json", manifest)
    prompts = {}
    for role in ("extraction", "state"):
        path = output / "snapshot" / f"{role}_system_prompt.txt"
        if digest(path) != manifest[f"{role}_system_sha256"]:
            raise ValueError("Frozen prompt changed")
        prompts[role] = path.read_text(encoding="utf-8")

    def call(path: Path, prompt: str, payload: dict, label: str) -> dict:
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["payload_hash"] != data_hash(payload) or saved["prompt_hash"] != data_hash(prompt):
                raise ValueError("Existing response has a different input")
            return saved["response"]
        print(f"START {label}", flush=True)
        offset = len(client.request_history)
        try:
            response = client.complete_json(prompt, json.dumps(payload, ensure_ascii=False), label)
            write_json(path, {"response": response, "requests": client.request_history[offset:],
                              "payload_hash": data_hash(payload), "prompt_hash": data_hash(prompt)})
        except Exception as exc:
            write_json(path.with_suffix(".error.json"), {"error": str(exc), "requests": client.request_history[offset:]})
            raise
        print(f"DONE {label}", flush=True)
        time.sleep(c["request_delay"])
        return response

    previous = None
    for index, chunk in enumerate(chunks):
        chunk_id = chunk["id"]
        if index:
            saved = json.loads((output / "snapshot/requests" / f"{chunk_id}.json").read_text(encoding="utf-8"))
            original = saved["sent_payload"]
            paired = paired_requests(original, previous)
            for arm, row in paired.items():
                write_json(output / "paired_requests" / f"{chunk_id}_{arm}.json", row)
            order = ARMS if index % 2 else tuple(reversed(ARMS))
            for arm in order:
                call(output / arm / f"{chunk_id}.json", prompts["extraction"], paired[arm], f"{chunk_id}_{arm}")
        if index == len(chunks) - 1:
            break
        commit = json.loads((output / "snapshot/commits" / f"{chunk_id}.json").read_text(encoding="utf-8"))
        if commit["chunk_id"] != chunk_id:
            raise ValueError("Frozen graph commit out of source order")
        changed_ids = {event["node_id"] for event in commit["events"] if "node_id" in event}
        delta = {"events": commit["events"],
                 "changed_nodes": {node_id: commit["after"]["nodes"][node_id] for node_id in sorted(changed_ids)},
                 "total_committed_nodes": len(commit["after"]["nodes"]),
                 "total_committed_relations": len(commit["after"]["relations"])}
        state_input = {"previous_state": previous,
                       "just_processed_source": {key: chunk[key] for key in ("id", "start_line", "end_line", "text", "heading_path")},
                       "committed_graph_delta": delta,
                       "history_scope": {"processed_chunk_ids": [row["id"] for row in chunks[:index + 1]],
                           "processed_source_ranges": [[row["start_line"], row["end_line"]] for row in chunks[:index + 1]],
                           "processed_does_not_mean_complete_extraction": True}}
        write_json(output / "states" / f"{chunk_id}_request.json", state_input)
        previous = call(output / "states" / f"{chunk_id}_response.json", prompts["state"], state_input, f"state_after_{chunk_id}")
        write_json(output / "states" / f"{chunk_id}_reference_audit.json", check_state(previous, chunks[:index + 1], commit["after"]))
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
