"""Freeze and execute native-method L1 development comparisons, without repair."""
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
from external_baseline_runtime import Embeddings, TextClient, sha256
from prospective_native_client import write
from run_memory_ablation import COMMON, load_module

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evaluation/external_baselines_2026-10-08"
METHODS = ("maf", "direct", "itext2kg", "edc")
CONFIG = {**COMMON, "provider": "opencode-go", "base_url": "https://opencode.ai/zen/go/v1",
          "api_key_env": "OPENCODE_API_KEY", "max_retries": 0, "request_delay": 3,
          "request_timeout": 600, "use_response_format": False}
DOMAIN = """Shared mathematics-textbook extraction scope (not an answer key):
Extract explicitly taught reusable concepts/definitions, general mathematical
results, and explicitly developed methods. Exclude example-local objects,
particular calculations and proof-intermediate statements. A generally stated
result is not excluded merely for appearing near an example. Preserve material
conditions, quantifiers, formulas and direction of a result in the native output
representation. Do not infer absent knowledge, prerequisites or implied facts.
Line numbers identify original evidence, not entities. Document headings and
illustrations are navigation, not facts. Use Chinese canonical names when the
source is Chinese. Preserve your native method's output format.
"""
TARGET_SCHEMA = {
    "special_case_of": "The subject is a mathematical subclass or special case of the object.",
    "defined_in_terms_of": "The subject's explicit definition uses the object as a defining ingredient.",
    "uses": "A mathematical method explicitly uses a stated result, concept, or method.",
    "derived_from": "The subject result is explicitly derived from the object result in the source.",
    "equivalent_to": "The source explicitly states mathematical equivalence, not mere similarity.",
    "implies": "The subject statement is sufficient for the object conclusion under preserved conditions.",
    "has_property": "The source explicitly assigns the stated mathematical property to its subject.",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def setup():
    baseline.COMMON = CONFIG
    baseline.PROTOCOL = ROOT / "docs/www_external_baseline_protocol_2026-10-08.md"


def pack_native_sections(text: str, target_chars=2400):
    """Pack original numbered lines; avoid isolated heading-only input tasks."""
    sections, lines, size = [], [], 0
    for line in text.splitlines():
        lines.append(line)
        size += len(line) + 1
        if size >= target_chars:
            sections.append("\n".join(lines))
            lines, size = [], 0
    if lines:
        tail = "\n".join(lines)
        if sections and size < target_chars // 2:
            sections[-1] += "\n" + tail
        else:
            sections.append(tail)
    if baseline.numbered_lines("\n".join(sections)) != baseline.numbered_lines(text):
        raise ValueError("Native serialization changed original evidence")
    return sections


def add_artifact(manifest, source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest["artifacts"].append(baseline.bind(target))


def prepare(output: Path, embedding_lock: Path):
    setup()
    manifest = baseline.prepare(output)
    snapshot = output / "snapshot"
    for name in ("run_external_baselines.py", "external_baseline_runtime.py",
                 "external_itext2kg.py", "external_edc.py", "prospective_native_client.py", "probe_go_streaming.py"):
        add_artifact(manifest, ROOT / "scripts" / name, snapshot / "scripts" / name)
    add_artifact(manifest, embedding_lock, snapshot / "embedding_lock.json")
    dependency = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    (snapshot / "requirements.lock.txt").write_text(dependency, encoding="utf-8", newline="\n")
    manifest["artifacts"].append(baseline.bind(snapshot / "requirements.lock.txt"))
    for method in ("itext2kg", "edc"):
        vendor = BASE / "vendor" / method
        if not vendor.exists():
            raise FileNotFoundError(vendor)
        for path in vendor.rglob("*"):
            if path.is_file() and not any(p in {".git", "__pycache__", ".venv"} for p in path.relative_to(vendor).parts):
                add_artifact(manifest, path, snapshot / "vendor" / method / path.relative_to(vendor))
    write(snapshot / "domain.json", {"instruction": DOMAIN, "edc_target_schema": TARGET_SCHEMA})
    manifest["artifacts"].append(baseline.bind(snapshot / "domain.json"))
    write(snapshot / "semantic_reference.json", {
        "status": "Existing chat-reviewed development reference; relation subset assistant-provisional",
        "nodes": read(manifest["reference"])["nodes"],
        "relations": [r for r in read(manifest["reference"])["relations_provisional"]
                      if r["type"] != "involves"],
        "alignment": "Actual native output must self-sufficiently express meaning; no source-as-output supplementation.",
        "no_total_f1": "Required recall and all-prediction source support are not one closed evaluation universe."})
    manifest["artifacts"].append(baseline.bind(snapshot / "semantic_reference.json"))
    manifest.update(experiment="external-l1-native-method-development", status="frozen",
        session=str(uuid.uuid4()), methods=list(METHODS), common=CONFIG,
        shared_domain_instruction=DOMAIN, embedding_lock=str(snapshot / "embedding_lock.json"),
        per_method_budget_usd=1.50, per_method_token_limit=2_500_000,
        budget_root=str((BASE / "budgets").resolve()),
        task_max_calls=120, task_max_seconds=5400, streaming=True,
        input_serialization={"maf": "structured chunks with native navigation",
                             "direct": "one section core-evidence union",
                             "itext2kg": "ordered original lines packed to 2400 chars; tail <1200 joined",
                             "edc": "one native text unit containing section core-evidence union"},
        scope="Three exposed development sections, two repeats; not independent heldout samples or equal total compute.",
        versions="See frozen vendor locks and adapter audits. Foreign output is native, not converted into our Claim ontology.")
    jobs = []
    for repeat in (1, 2):
        for i, section in enumerate(manifest["sections"]):
            order = list(METHODS)
            shift = (repeat + i) % len(order)
            order = order[shift:] + order[:shift]
            for method in order:
                jobs.append({"method": method, "section": section["id"], "repeat": repeat,
                    "directory": str(output / f"r{repeat}" / section["id"] / method), "status": "pending"})
    manifest["jobs"] = jobs
    write(output / "manifest.json", manifest)
    baseline.audit_prepared(manifest)
    return manifest


def production(module, manifest, section, job, directory, client):
    def complete(self, system_prompt, user_prompt, label="request"):
        text = client([{"role": "system", "content": system_prompt},
                       {"role": "user", "content": user_prompt}], label)
        synthetic = {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
        return self._execute_request(lambda: synthetic, "bounded-native-text", 1, label)

    module.ProviderChatClient.complete_json = complete
    arm = "sequential" if job["method"] == "maf" else "direct"
    hook_job = {"name": arm, "memory_node_bytes": 12000, "memory_relation_bytes": 6000}
    for folder in ("requests", "responses", "raw"):
        (directory / folder).mkdir(parents=True, exist_ok=True)
    # Hook artifacts use separate paths from the authoritative native payloads.
    native_dir = client.directory
    client.directory = native_dir / "native"
    baseline.install_hooks(module, section, hook_job, directory)
    extractor = module.Extractor(**{**CONFIG, "input_path": section["input"],
        "output_path": directory / "graph.json", "resume": False, "verbose": True,
        "memory_node_bytes": 12000, "memory_relation_bytes": 6000})
    extractor.run()
    return read(directory / "graph.json")


def worker(output: Path, index: int):
    setup()
    manifest = read(output / "manifest.json")
    baseline.audit_prepared(manifest)
    job = manifest["jobs"][index]
    section = next(s for s in manifest["sections"] if s["id"] == job["section"])
    directory = Path(job["directory"])
    client = TextClient(directory, Path(manifest["budget_root"]) / f"{job['method']}.json", manifest["session"],
        max_tokens=CONFIG["max_tokens"], timeout=CONFIG["request_timeout"],
        max_calls=manifest["task_max_calls"], max_seconds=manifest["task_max_seconds"],
        usd_limit=manifest["per_method_budget_usd"], token_limit=manifest["per_method_token_limit"],
        domain_instruction=manifest["shared_domain_instruction"])
    if job["method"] in {"maf", "direct"}:
        module = load_module(Path(manifest["snapshot"]) / "agents/Extractor.py")
        result = production(module, manifest, section, job, directory, client)
    else:
        lock = read(manifest["embedding_lock"])
        for item in lock["files"]:
            if sha256(Path(item["path"])) != item["sha256"]:
                raise ValueError("Embedding asset changed")
        embed = Embeddings(directory, lock["model"], Path(lock["cache_dir"]), Path(lock["model_path"]))
        chunks = read(Path(section["plan_dir"]) / "chunks.json")
        texts = [c["text"] for c in chunks]
        vendor = Path(manifest["snapshot"]) / "vendor" / job["method"]
        if job["method"] == "itext2kg":
            from external_itext2kg import run
            texts = pack_native_sections(read(Path(section["plan_dir"]) / "direct_chunk.json")["text"])
            result = run(vendor, texts, client, embed, directory,
                         no_progress_limit=manifest.get("itext_no_progress_limit"))
        else:
            from external_edc import run
            texts = [read(Path(section["plan_dir"]) / "direct_chunk.json")["text"]]
            result = run(vendor, texts, client, embed, directory, TARGET_SCHEMA)
        write(directory / "graph.json", result)
    write(directory / "completion.json", {"status": "completed", "calls": client.calls,
        "graph_sha256": sha256(directory / "graph.json"), "method": job["method"]})
    return result


def execute(output: Path, *, section=None, methods=None, repeat=None, max_jobs=24):
    setup()
    manifest = read(output / "manifest.json")
    baseline.audit_prepared(manifest)
    if not os.environ.get("OPENCODE_API_KEY"):
        raise RuntimeError("OPENCODE_API_KEY missing; nothing sent")
    completed = 0
    for index, job in enumerate(manifest["jobs"]):
        if (section and job["section"] != section or methods and job["method"] not in methods
                or repeat and job["repeat"] != repeat):
            continue
        if job["status"] in {"completed", "failed", "gated"}:
            continue
        if job["status"] != "pending":
            raise RuntimeError("Interrupted job needs explicit audit; cannot repeat silently")
        if completed >= max_jobs:
            break
        directory = Path(job["directory"])
        directory.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        job["status"] = "running"
        write(output / "manifest.json", manifest)
        args = [sys.executable, "-X", "utf8", str(Path(manifest["snapshot"]) /
                "scripts/run_external_baselines.py"), "worker", str(output), "--index", str(index)]
        print(f"Starting r{job['repeat']} {job['section']} {job['method']}", flush=True)
        with (directory / "stdout.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=manifest["task_max_seconds"] + 30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                code = -1
        job.update(status="completed" if code == 0 else "failed", exit_code=code,
                   elapsed_seconds=time.monotonic()-started)
        if code == 0:
            job["graph_sha256"] = sha256(directory / "graph.json")
        write(output / "manifest.json", manifest)
        completed += 1
        print(f"Finished {job['method']}: {job['status']}", flush=True)
        if code != 0:
            # Other frozen jobs remain executable, but no failure is hidden.
            print(f"Failure preserved in {directory / 'stdout.log'}", flush=True)
    manifest["status"] = "completed" if all(j["status"] == "completed" for j in manifest["jobs"]) else "partial"
    write(output / "manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute", "worker", "smoke"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--embedding-lock", type=Path)
    parser.add_argument("--index", type=int)
    parser.add_argument("--section")
    parser.add_argument("--methods", nargs="+", choices=METHODS)
    parser.add_argument("--repeat", type=int, choices=(1, 2))
    parser.add_argument("--max-jobs", type=int, default=24)
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.embedding_lock:
            parser.error("prepare requires --embedding-lock")
        prepare(args.output.resolve(), args.embedding_lock.resolve())
    elif args.action == "execute":
        execute(args.output.resolve(), section=args.section, methods=args.methods,
                repeat=args.repeat, max_jobs=args.max_jobs)
    elif args.action == "worker":
        worker(args.output.resolve(), args.index)
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        c = TextClient(args.output, args.output / "ledger.json", str(uuid.uuid4()),
                       max_tokens=1024, max_calls=1, usd_limit=0.02, token_limit=10000)
        value = c([{"role": "user", "content": "Reply with exactly READY."}], "connectivity")
        write(args.output / "result.json", {"output": value, "purpose": "transport only; not quality evaluation"})
        print("Smoke response saved; no textbook content sent.")


if __name__ == "__main__":
    main()
