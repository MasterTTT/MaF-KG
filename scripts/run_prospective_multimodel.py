"""Frozen five-model paired L1 pilot; one repeat, serial and budget-gated."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import run_direct_extraction_baseline as baseline
from prospective_native_client import MODELS, STREAM_MODELS, install, payload
from run_memory_ablation import COMMON, digest, load_module, write_json

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {**COMMON, "provider":"opencode-go", "base_url":"https://opencode.ai/zen/go/v1",
          "api_key_env":"OPENCODE_API_KEY", "max_retries":0, "request_delay":3,
          "request_timeout":600,
          "use_response_format":False}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def setup_baseline():
    baseline.COMMON = CONFIG
    baseline.REFERENCE = ROOT / "evaluation/prospective_2026-10-04/reference_draft_v1.json"
    baseline.PROTOCOL = ROOT / "docs/prospective_multimodel_protocol_2026-10-04.md"


def prepare(output, reuse_run=None, budget_decision=None):
    setup_baseline()
    manifest = baseline.prepare(output)
    snapshot = output / "snapshot"
    for relative in ("scripts/run_prospective_multimodel.py", "scripts/prospective_native_client.py",
                     "scripts/probe_go_streaming.py", "tests/test_go_streaming.py",
                     "tests/test_prospective_native_client.py", "tests/test_prospective_reference.py",
                     "docs/opencode_five_model_api_check_2026-10-04.md",
                     "evaluation/prospective_2026-10-04/boundary_decisions_v1.json",
                     "evaluation/prospective_2026-10-04/prerequisite_scope_decision_v1.json",
                     "evaluation/prospective_2026-10-04/integration_prerequisite_scope_decision_v1.json"):
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
        manifest["artifacts"].append(baseline.bind(target))
    decision = ROOT / "evaluation/prospective_2026-10-04/timeout_decision_v1.json"
    timeout_target = snapshot / decision.relative_to(ROOT)
    shutil.copy2(decision, timeout_target)
    manifest["artifacts"].append(baseline.bind(timeout_target))
    (snapshot / "git_diff.patch").write_bytes(subprocess.check_output(["git","diff","--binary","HEAD"], cwd=ROOT))
    manifest["artifacts"].append(baseline.bind(snapshot / "git_diff.patch"))
    manifest.update(experiment="prospective-five-model-paired-l1", session=str(uuid.uuid4()),
                    models=list(MODELS), per_model_budget_usd=0.50, per_model_token_limit=1_000_000,
                    streaming_models=list(STREAM_MODELS),
                    reference_provenance="assistant source-review; KU/scope decisions user-approved",
                    reasoning_configuration={MODELS[0]:"thinking enabled, low",
                                             MODELS[1]:"native gateway default; no DS thinking parameter",
                                             MODELS[2]:"Responses reasoning low; store false",
                                             MODELS[3]:"native gateway default; no DS thinking parameter",
                                             MODELS[4]:"native gateway default; no DS thinking parameter",
                                             MODELS[5]:"native gateway default; no DS thinking parameter"},
                    scope="Prospective within-book frozen pilot, not pristine unseen evaluation; one repeat; no equal reasoning budget claim.")
    decision = None
    if budget_decision is not None:
        decision = read(budget_decision)
        if (decision.get("decision_source") != "human-user-in-chat"
                or decision.get("model") != "kimi-k2.6"
                or decision.get("old_usd_limit") != 0.50
                or decision.get("new_usd_limit") != 1.50):
            raise ValueError("Unrecognized approved budget decision")
        target = snapshot / "evaluation/prospective_2026-10-04/kimi_budget_decision_v1.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(budget_decision, target)
        manifest["artifacts"].append(baseline.bind(target))
        manifest["model_budget_overrides"] = {"kimi-k2.6": 1.50}
        manifest["budget_decision"] = str(target)
    manifest["jobs"] = []
    for section_index, section in enumerate(manifest["sections"]):
        for model_index, model in enumerate(MODELS):
            arms = ("sequential","direct") if (section_index + model_index) % 2 else ("direct","sequential")
            for arm in arms:
                manifest["jobs"].append({"model":model,"section":section["id"],"name":arm,
                    "repeat":1,"memory_node_bytes":12000,"memory_relation_bytes":6000,
                    "directory":str(output / model / section["id"] / arm),"status":"pending"})
    if reuse_run is not None:
        previous = read(reuse_run / "manifest.json")
        if decision is None and previous.get("model_budget_overrides"):
            manifest["model_budget_overrides"] = previous["model_budget_overrides"]
            manifest["budget_decision"] = previous["budget_decision"]
        if previous.get("model_token_overrides"):
            manifest["model_token_overrides"] = previous["model_token_overrides"]
            manifest["token_budget_decision"] = previous["token_budget_decision"]
        if "longcat-2.0" not in previous["models"]:
            extension = ROOT / "evaluation/prospective_2026-10-04/longcat_supplement_decision_v1.json"
            target = snapshot / extension.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extension, target)
            manifest["artifacts"].append(baseline.bind(target))
            manifest["supplementary_model"] = "longcat-2.0"
            manifest["extension_note"] = "GLM failures retained; sixth attempted family is an explicit supplement, not a replacement of recorded failures."
        before, after = previous["common"], manifest["common"]
        if before != after:
            unchanged = lambda c: {k:v for k,v in c.items() if k != "request_timeout"}
            if not (unchanged(before) == unchanged(after) and before["request_timeout"] == 180
                    and after["request_timeout"] == 600 and read(timeout_target)["decision_source"] == "human-user-in-chat"):
                raise ValueError("Reuse configuration differs beyond approved timeout increase")
            manifest["approved_timeout_change"] = {"old":180,"new":600,
                "note":"Completed identical requests returned under the stricter former allowance; failures remain recorded."}
        for relative in ("agents/Extractor.py", "prompts/extraction_system.txt"):
            if digest(reuse_run / "snapshot" / relative) != digest(snapshot / relative):
                raise ValueError("Reuse production snapshot differs")
        if digest(Path(previous["reference"])) != digest(Path(manifest["reference"])):
            raise ValueError("Reuse reference differs")
        for prompt in (snapshot / "prompts").glob("*.txt"):
            if digest(prompt) != digest(reuse_run / "snapshot/prompts" / prompt.name):
                raise ValueError("Reuse prompt differs")
        # Keep the exact old input path and templates, not just the same text.
        manifest["sections"] = previous["sections"]
        for artifact in previous["artifacts"]:
            if digest(Path(artifact["path"])) != artifact["sha256"]:
                raise ValueError("Reuse artifact changed")
            manifest["artifacts"].append(artifact)
        for job in manifest["jobs"]:
            failed = next((j for j in previous["jobs"] if
                (j["model"],j["section"],j["name"]) ==
                (job["model"],job["section"],job["name"]) and j["status"] == "failed"), None)
            if failed:
                log = (Path(failed["directory"]) / "stdout.log").read_text(encoding="utf-8")
                if (decision is not None and job["model"] == decision["model"]
                        and "Pre-request USD budget gate reached" in log):
                    job["previous_failed_attempt"] = failed
                    job["retry_reason"] = "Human-approved budget increase; original failed attempt retained"
                else:
                    job.update(failed)
                continue
            old = next((j for j in previous["jobs"] if
                        (j["model"],j["section"],j["name"]) ==
                        (job["model"],job["section"],job["name"]) and j["status"] == "completed"), None)
            if old:
                if digest(Path(old["directory"]) / "graph.json") != old["graph_sha256"]:
                    raise ValueError("Reuse graph changed")
                for request_path in (Path(old["directory"]) / "native_requests").glob("*.json"):
                    record = read(request_path)
                    old_body = record["body"]
                    if job["model"] == "gpt-6-luna":
                        system, user = old_body["instructions"], old_body["input"]
                        limit = old_body["max_output_tokens"]
                    else:
                        system, user = [m["content"] for m in old_body["messages"]]
                        limit = old_body["max_tokens"]
                    route, expected = payload(job["model"],system,user,limit)
                    if job["model"] in STREAM_MODELS:
                        expected.update(stream=True,stream_options={"include_usage":True})
                    if expected != old_body or record["endpoint"] != "https://opencode.ai/zen/go/v1/"+route:
                        raise ValueError("Reuse actual native request differs")
                if not list((Path(old["directory"]) / "native_requests").glob("*.json")):
                    raise ValueError("Reuse has no auditable native request")
                job.update(old)
                job["reused_from"] = str(reuse_run)
        for path in (reuse_run / "budgets").glob("*.json"):
            target = output / "budgets" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if decision is not None and path.stem == decision["model"]:
                ledger = read(target)
                if ledger["usd_limit"] != decision["old_usd_limit"]:
                    raise ValueError("Unexpected previous Kimi budget")
                if any(r["status"] == "reserved" for r in ledger["requests"]):
                    raise ValueError("Cannot change an active budget")
                ledger["usd_limit"] = decision["new_usd_limit"]
                ledger["approved_budget_change"] = decision
                write_json(target, ledger)
        manifest["reuse_note"] = "Completed jobs reused only after exact config, production code, prompt, input plan and graph hash checks. Prior request spend included via budget ledger."
    write_json(output / "manifest.json", manifest)
    baseline.audit_prepared(manifest)
    return manifest


def worker(output, index):
    setup_baseline()
    manifest = read(output / "manifest.json")
    baseline.audit_prepared(manifest)
    job = manifest["jobs"][index]
    section = next(s for s in manifest["sections"] if s["id"] == job["section"])
    directory = Path(job["directory"])
    for folder in ("requests","responses","raw"):
        (directory / folder).mkdir()
    module = load_module(directory / "rt/agents/Extractor.py")
    install(module, job["model"], directory, output / "budgets" / f"{job['model']}.json", manifest["session"],
            usd_limit=manifest.get("model_budget_overrides", {}).get(
                job["model"], manifest.get("per_model_budget_usd", 0.50)),
            token_limit=manifest.get("model_token_overrides", {}).get(
                job["model"], manifest.get("per_model_token_limit", 1_000_000)))
    baseline.install_hooks(module, section, job, directory)
    config = {**CONFIG, "model":job["model"], "input_path":section["input"],
              "output_path":directory / "graph.json", "resume":False, "verbose":True,
              "memory_node_bytes":12000, "memory_relation_bytes":6000}
    if job["model"] in ("glm-5.3-flash", "mimo-v2.6-flash", "kimi-k2.6", "longcat-2.0"):
        config.update(thinking="disabled", reasoning_effort="")
    extractor = module.Extractor(**config)
    extractor.run()
    graph = read(directory / "graph.json")
    graph["diagnostics"]["native_experiment_configuration"] = {
        "model":job["model"], "reasoning":manifest["reasoning_configuration"][job["model"]],
        "note":"Native request artifacts are authoritative; generic extractor telemetry is not cross-model reasoning equivalence."}
    write_json(directory / "graph.json", graph)


def execute(output, max_jobs, only_models=None, skip_recorded_failures=False):
    setup_baseline()
    manifest = read(output / "manifest.json")
    baseline.audit_prepared(manifest)
    if not os.environ.get("OPENCODE_API_KEY"):
        raise RuntimeError("OPENCODE_API_KEY missing; no requests started")
    selection = list(only_models or manifest["models"])
    write_json(output / f"execution_selection_{time.time_ns()}.json", {
        "models":selection, "runner_sha256":digest(Path(__file__)),
        "skip_recorded_failures":skip_recorded_failures,
        "note":"Explicit execution selection only; frozen worker/code/input artifacts unchanged. Previously failed jobs are not retried or removed."})
    completed_now = 0
    for index, job in enumerate(manifest["jobs"]):
        if job["model"] not in selection:
            continue
        if job["status"] == "completed":
            continue
        if job["status"] == "failed" and skip_recorded_failures:
            continue
        if job["status"] != "pending":
            raise RuntimeError("Failed/interrupted job requires inspection; no automatic rerun")
        if completed_now >= max_jobs:
            break
        directory = Path(job["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        for name in ("agents","prompts"):
            shutil.copytree(output / "snapshot" / name, directory / "rt" / name)
        args = [sys.executable,"-X","utf8",str(output / "snapshot/scripts/run_prospective_multimodel.py"),
                "--output",str(output),"--worker",str(index)]
        write_json(directory / "command.json", args)
        job["status"], manifest["status"] = "running", "running"
        write_json(output / "manifest.json", manifest)
        print(f"Starting {job['model']} {job['section']} {job['name']}", flush=True)
        started = time.monotonic()
        with (directory / "stdout.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(args, cwd=output / "snapshot", stdout=log, stderr=subprocess.STDOUT)
            code = process.wait()
        job.update(exit_code=code, elapsed_seconds=round(time.monotonic()-started,3),
                   status="completed" if code == 0 else "failed")
        if code == 0:
            graph = read(directory / "graph.json")
            job.update(nodes=len(graph["nodes"]),relations=len(graph["relations"]),
                       graph_sha256=digest(directory / "graph.json"))
        else:
            manifest["status"] = "stopped_on_failure"
            write_json(output / "manifest.json", manifest)
            print(f"Stopped on failure; inspect {directory / 'stdout.log'}", flush=True)
            return 1
        completed_now += 1
        write_json(output / "manifest.json", manifest)
        print(f"Completed: {job['nodes']} nodes, {job['relations']} relations", flush=True)
    manifest["status"] = "completed" if all(j["status"] == "completed" for j in manifest["jobs"]) else "prepared_remaining"
    if any(j["status"] == "failed" for j in manifest["jobs"]):
        manifest["status"] = "partial_with_recorded_failures"
    write_json(output / "manifest.json", manifest)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=30)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--reuse-run", type=Path)
    parser.add_argument("--budget-decision", type=Path)
    parser.add_argument("--skip-recorded-failures", action="store_true")
    parser.add_argument("--only-models", nargs="+", choices=MODELS)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.worker is not None:
        worker(output, args.worker)
        return 0
    if args.prepare:
        prepare(output, args.reuse_run.resolve() if args.reuse_run else None,
                args.budget_decision.resolve() if args.budget_decision else None)
    return execute(output, args.max_jobs, args.only_models, args.skip_recorded_failures) if args.execute else 0


if __name__ == "__main__":
    raise SystemExit(main())
