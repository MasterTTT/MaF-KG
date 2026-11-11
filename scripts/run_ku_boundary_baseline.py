"""Frozen L2 pilot: semantic boundary planning versus fixed heading groups."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

from run_memory_ablation import digest, write_json as _write_json

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("ch3_s2", "ch5_s4", "ch7_s4")
PROMPTS = {"planner": "knowledge_unit_objective_planner.txt",
           "audit": "knowledge_unit_boundary_auditor.txt",
           "router": "knowledge_unit_organizer.txt", "finalizer": "knowledge_unit_finalizer.txt"}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, value)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def heading_objectives(section: str, cards: list[dict]) -> list[dict]:
    labels = []
    for card in cards:
        hp = card["heading_path"]
        labels.append(hp[1] if len(hp) > 1 else None)
    for i, label in enumerate(labels):
        if label is None:
            labels[i] = next((value for value in labels[i + 1:] if value is not None),
                             next((value for value in reversed(labels[:i]) if value is not None), section))
    groups = {}
    for card, label in zip(cards, labels):
        groups.setdefault(label, []).append(card["card_id"])
    return [{"goal_id": f"g{i}", "core_question": f"{label}集中讲授什么内容？",
             "objective": f"学习{label}。", "boundary_reason": "按教材一级小标题固定边界，不作语义拆分或合并。",
             "card_ids": ids} for i, (label, ids) in enumerate(groups.items(), 1)]


def prepare(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    files = [Path(__file__), ROOT / "scripts/build_knowledge_units.py", ROOT / "agents/Extractor.py",
             ROOT / "docs/ku_boundary_baseline_protocol_2026-10-03.md"]
    files += [ROOT / "prompts" / filename for filename in PROMPTS.values()]
    records = []

    def copy(source: Path, relative: Path) -> Path:
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        records.append({"source": str(source), "snapshot": str(destination), "sha256": digest(destination)})
        return destination

    for source in files:
        copy(source, source.relative_to(ROOT))
    previous = ROOT / "evaluation/direct_baseline_2026-10-03/run02"
    old_manifest = read(previous / "manifest.json")
    reference = copy(Path(old_manifest["reference"]), Path("reference.json"))
    organizer = load(snapshot / "scripts/build_knowledge_units.py", "ku_pilot_prepare")
    sections = []
    for section_id in SECTIONS:
        previous_section = next(s for s in old_manifest["sections"] if s["id"] == section_id)
        job = next(j for j in old_manifest["jobs"]
                   if j["section"] == section_id and j["repeat"] == 1 and j["name"] == "sequential")
        graph_path = copy(Path(job["directory"]) / "graph.json", Path("graphs") / f"{section_id}.json")
        copy(Path(job["directory"]) / "review.json", Path("alignments") / f"{section_id}.json")
        source_path = copy(Path(previous_section["input"]), Path("input") / f"{section_id}.md")
        graph = read(graph_path)
        outline = organizer.build_outline([str(source_path)])
        if len(outline) != 1:
            raise ValueError("Pilot inputs must contain exactly one section")
        title = next(iter(outline))
        cards = organizer.build_planner_cards(title, graph["nodes"], outline, {source_path.name: graph})
        baseline = organizer._require_objective_plan(
            {"objectives": heading_objectives(title, cards)}, title, cards)
        organizer._node_candidate_goals(title, graph["nodes"], cards, baseline)
        plan = output / "plans" / f"{section_id}.json"
        write_json(plan, {"section": title, "cards": cards, "heading_objectives": baseline})
        records.append({"snapshot": str(plan), "sha256": digest(plan)})
        sections.append({"id": section_id, "title": title, "graph": str(graph_path),
                         "source": str(source_path), "plan": str(plan), "nodes": len(graph["nodes"]),
                         "cards": len(cards), "heading_groups": len(baseline)})
    jobs = []
    for i, section in enumerate(sections):
        for arm in (("semantic", "heading") if i % 2 == 0 else ("heading", "semantic")):
            jobs.append({"section": section["id"], "arm": arm, "status": "pending",
                         "directory": str(output / section["id"] / arm)})
    manifest = {"status": "prepared", "snapshot": str(snapshot), "reference": str(reference),
                "model": "deepseek-v4-flash", "provider": "opencode-go", "thinking": "enabled",
                "reasoning_effort": "low", "sections": sections, "jobs": jobs, "artifacts": records,
                "scope": "One pair per exposed development section; fixed r1 sequential L1; no L1 repairs."}
    write_json(output / "manifest.json", manifest)
    return manifest


def audit(manifest: dict) -> None:
    for artifact in manifest["artifacts"]:
        if digest(Path(artifact["snapshot"])) != artifact["sha256"]:
            raise ValueError("Frozen L2 input or software changed")


def run_job(manifest: dict, job: dict) -> None:
    snapshot = Path(manifest["snapshot"])
    organizer = load(snapshot / "scripts/build_knowledge_units.py", "ku_pilot_runtime")
    extractor = load(snapshot / "agents/Extractor.py", "ku_pilot_client")
    section = next(s for s in manifest["sections"] if s["id"] == job["section"])
    graph = read(Path(section["graph"]))
    plan = read(Path(section["plan"]))
    cards = plan["cards"]
    directory = Path(job["directory"])
    directory.mkdir(parents=True, exist_ok=False)
    client = extractor.ProviderChatClient(
        provider="opencode-go", model=manifest["model"], base_url="https://opencode.ai/zen/go/v1",
        api_key_env="OPENCODE_API_KEY", temperature=0.1, timeout=180, max_retries=4,
        max_tokens=12000, thinking="enabled", reasoning_effort="low", trust_env=False)
    original_execute = client._execute_request

    def execute(request, transport, attempt, label):
        def captured():
            raw = request()
            write_json(directory / "raw" / f"{label}_{attempt}_{transport}.json", raw)
            return raw
        return original_execute(captured, transport, attempt, label)

    client._execute_request = execute
    call_count = 0

    def call(system, user, model):
        nonlocal call_count
        if model != manifest["model"]:
            raise ValueError("Model changed within job")
        if call_count:
            time.sleep(8)
        call_count += 1
        label = f"call_{call_count:02d}"
        write_json(directory / "requests" / f"{label}.json", {
            "endpoint": client.endpoint_url, "provider": client.provider, "session_id": client.session_id,
            "body": client.adapter.build_payload(
                model=model, system_prompt=system, user_prompt=user, temperature=0.1,
                max_tokens=12000, use_response_format=True, thinking="enabled", reasoning_effort="low")})
        try:
            response = client.complete_json(system, user, label)
            write_json(directory / "responses" / f"{label}.json", response)
            return response, client.request_history[-1].get("usage", {})
        finally:
            write_json(directory / "api_requests.json", client.request_history)

    organizer._llm_json = call
    prompts = {name: (snapshot / "prompts" / filename).read_text(encoding="utf-8")
               for name, filename in PROMPTS.items()}
    if job["arm"] == "semantic":
        candidates, _ = organizer.plan_objectives(section["title"], cards, prompts["planner"], manifest["model"])
        write_json(directory / "candidate_objectives.json", candidates)
        objectives, operations, _ = organizer.audit_boundaries(
            section["title"], cards, candidates, prompts["audit"], manifest["model"])
        write_json(directory / "audit_operations.json", operations)
    else:
        objectives = plan["heading_objectives"]
    write_json(directory / "objectives.json", objectives)
    units, candidates, routing, _, _, _ = organizer.assign_members(
        section["title"], graph["nodes"], graph["relations"], cards, objectives,
        prompts["router"], prompts["finalizer"], manifest["model"])
    members = [n for unit in units for n in unit["centers_on"] + unit["contains"]]
    if len(members) != len(set(members)) or set(members) != {n["id"] for n in graph["nodes"]}:
        raise ValueError("L2 members do not form an exact partition of frozen L1")
    if any(not unit["centers_on"] for unit in units):
        raise ValueError("KU has no semantic center")
    for unit in units:
        unit["id"] = organizer.stable_ku_id(section["title"], unit["centers_on"], unit["contains"])
    write_json(directory / "units.json", {"section": section["title"], "arm": job["arm"],
               "l1_sha256": digest(Path(section["graph"])), "units": units,
               "member_candidates": candidates, "routing": routing})
    job.update({"request_count": call_count, "units": len(units), "nodes": len(members),
                "units_sha256": digest(directory / "units.json"),
                "total_tokens": sum(a.get("usage", {}).get("total_tokens", 0) for a in client.request_history)})


def execute_all(output: Path) -> dict:
    manifest = read(output / "manifest.json")
    audit(manifest)
    for job in manifest["jobs"]:
        if job["status"] in {"completed", "failed"}:
            continue
        if job["status"] != "pending":
            raise ValueError("Interrupted job requires explicit inspection")
        job["status"] = "running"
        write_json(output / "manifest.json", manifest)
        started = time.perf_counter()
        print(f"Starting {job['section']} {job['arm']}", flush=True)
        try:
            run_job(manifest, job)
            job["status"] = "completed"
        except Exception as exc:
            job.update({"status": "failed", "error": str(exc)})
        job["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        write_json(output / "manifest.json", manifest)
        print(f"Finished {job['section']} {job['arm']}: {job['status']}", flush=True)
    manifest["status"] = "completed" if all(j["status"] == "completed" for j in manifest["jobs"]) else "incomplete"
    write_json(output / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = prepare(args.directory.resolve()) if args.prepare else execute_all(args.directory.resolve())
    print(result["status"])
