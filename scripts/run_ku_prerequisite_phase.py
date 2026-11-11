"""Frozen real-KU prerequisite experiment, Go-only and strict single-attempt JSON.

prepare freezes historical L1 inputs and current heading organizer; organize
creates NEW L2 outputs without re-extracting L1. Candidate generation and review
are separate stages. Draft references must be frozen before candidate generation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from prospective_native_client import Budget, payload, write

ROOT = Path(__file__).resolve().parents[1]
MODEL = "deepseek-v4-flash"
DEADLINE = datetime.fromisoformat("2026-10-06T03:32:00+08:00")
JOBS = [
    ("ch2_s2", "第二章", "第二节 函数的求导法则"),
    ("ch2_s3", "第二章", "第三节 高阶导数"),
    ("ch3_s1", "第三章", "第一节 微分中值定理"),
    ("ch3_s2", "第三章", "第二节 洛必达法则"),
    ("ch3_s3", "第三章", "第三节 泰勒公式"),
    ("ch4_s1", "第四章", "第一节 不定积分的概念与性质"),
    ("ch4_s3", "第四章", "第三节 分部积分法"),
    ("ch7_s2", "第七章", "第二节 可分离变量的微分方程"),
    ("ch7_s4", "第七章", "第四节 一阶线性微分方程"),
    ("ch7_s5", "第七章", "第五节 可降阶的高阶微分方程"),
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    snap = out / "snapshot"
    bindings = []

    def copy(src, relative):
        dst = snap / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        bindings.append({"original": str(src), "path": str(dst), "sha256": sha(dst)})
        return dst

    for filename in ("build_knowledge_units.py", "build_ku_depends_candidates.py",
                     "review_ku_depends_llm.py", "prospective_native_client.py"):
        copy(ROOT / "scripts" / filename, Path("scripts") / filename)
    copy(Path(__file__), Path("scripts") / Path(__file__).name)
    for filename in ("knowledge_unit_organizer.txt", "knowledge_unit_finalizer.txt",
                     "ku_depends_review.txt"):
        copy(ROOT / "prompts" / filename, Path("prompts") / filename)
    copy(ROOT / "docs/prerequisite_evaluation_protocol_2026-10-05.md", Path("protocol.md"))
    inputs = {}
    for chapter in dict.fromkeys(j[1] for j in JOBS):
        suffix = ("_全章_semantic_v1_7_0_opencode_flash_high_global_repaired.json"
                  if chapter == "第二章" else "_全章_semantic_v1_7_0_deepseek_flash_high_repaired.json")
        graph = copy(Path("E:/MaF-KG/KG") / (chapter + suffix), Path("graphs") / (chapter + ".json"))
        source = copy(Path("E:/MaF-KG/data/raw") / (chapter + "_全章_无习题.md"),
                      Path("input") / (chapter + "_全章_无习题.md"))
        inputs[chapter] = {"graph": str(graph), "source": str(source)}
    module = load(snap / "scripts/build_knowledge_units.py", "phase_prepare")
    jobs = []
    for job_id, chapter, section in JOBS:
        paths = inputs[chapter]
        graph = read(paths["graph"])
        outline = module.build_outline([paths["source"]])
        nodes = [n for n in graph["nodes"] if module.section_of(n, outline, chapter) == section]
        if not nodes:
            raise ValueError(f"No nodes for {job_id}")
        cards = module.build_planner_cards(section, nodes, outline, {Path(paths["source"]).name: graph})
        objectives = module.heading_objectives(section, cards)
        # Fail before any paid call if the current pipeline cannot form candidates.
        candidates = module._node_candidate_goals(section, nodes, cards, objectives)
        plan_path = out / "plans" / (job_id + ".json")
        write(plan_path, {"section": section, "nodes": nodes, "cards": cards,
                          "objectives": objectives, "member_candidates": candidates})
        bindings.append({"path": str(plan_path), "sha256": sha(plan_path)})
        jobs.append({"id": job_id, "chapter": chapter, "section": section, **paths,
                     "plan": str(plan_path), "node_count": len(nodes), "card_count": len(cards),
                     "heading_count": len(objectives), "status": "pending"})
    write(out / "manifest.json", {"status": "prepared", "session": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(), "provider": "opencode-go",
        "model": MODEL, "reasoning_effort": "low", "thinking": "enabled",
        "max_tokens": 12000, "timeout_seconds": 300, "attempts": 1,
        "usd_limit": 0.50, "token_limit": 400000, "deadline": DEADLINE.isoformat(),
        "snapshot": str(snap), "bindings": bindings, "jobs": jobs,
        "scope": "New heading L2 on fixed historical repaired Flash-high L1; not new Flash-low L1 or expert Gold.",
        "stages": {"organize": "pending", "candidates": "pending", "review": "pending"}})


def audit(manifest):
    for b in manifest["bindings"]:
        if sha(b["path"]) != b["sha256"]:
            raise ValueError(f"Frozen artifact changed: {b['path']}")


class Native:
    def __init__(self, out, manifest):
        self.out, self.manifest = out, manifest
        self.deadline = datetime.fromisoformat(manifest["deadline"])
        if self.deadline.tzinfo is None:
            raise ValueError("Experiment deadline must include timezone")
        self.budget = Budget(out / "budget.json", MODEL,
                             usd=manifest["usd_limit"], tokens=manifest["token_limit"])
        self.counter = len(self.budget.data["requests"])

    def call(self, system, user, model):
        if model != MODEL:
            raise ValueError("Model mismatch")
        if datetime.now(timezone.utc) >= self.deadline:
            raise RuntimeError("Experiment deadline reached")
        key = (os.environ.get("OPENCODE_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("OPENCODE_API_KEY missing")
        self.counter += 1
        label = f"request_{self.counter:03d}"
        route, body = payload(MODEL, system, user, self.manifest["max_tokens"])
        endpoint = "https://opencode.ai/zen/go/v1/" + route
        index = self.budget.reserve(body, self.manifest["max_tokens"], label)
        write(self.out / "native_requests" / (label + ".json"), {"endpoint": endpoint, "body": body})
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "User-Agent": "MaFKG-prospective-experiment/1.0",
                     "x-opencode-session": self.manifest["session"]}, method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.manifest["timeout_seconds"]) as response:
                raw = response.read().decode("utf-8")
                status = response.status
            if key in raw:
                raise RuntimeError("Credential echo; response not persisted")
            data = json.loads(raw)
            write(self.out / "native_responses" / (label + ".json"), data)
        except Exception as exc:
            error = {"type": type(exc).__name__}
            if isinstance(exc, urllib.error.HTTPError):
                error["status"] = exc.code
                error["body"] = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")[:3000]
            write(self.out / "native_errors" / (label + ".json"), error)
            self.budget.settle(index, None)
            raise RuntimeError("Single attempt failed; see native_errors") from None
        self.budget.settle(index, data.get("usage"))
        write(self.out / "native_transport" / (label + ".json"),
              {"status": status, "elapsed_seconds": time.monotonic() - started, "attempts": 1})
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Non-stop response; no repair")
        result = json.loads(choice["message"]["content"])
        if not isinstance(result, dict):
            raise ValueError("Response must be JSON object")
        return result, data.get("usage", {})


def organize(out):
    m = read(out / "manifest.json")
    audit(m)
    if m["stages"]["organize"] != "pending":
        raise ValueError("No automatic continuation; inspect previous run")
    module = load(Path(m["snapshot"]) / "scripts/build_knowledge_units.py", "phase_organizer")
    module._llm_json = Native(out, m).call
    m["stages"]["organize"] = "running"
    write(out / "manifest.json", m)
    for j in m["jobs"]:
        result_path = out / "units" / (j["id"] + ".json")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        sys.argv = ["build_knowledge_units.py", j["graph"], "--out", str(result_path),
                    "--src-files", j["source"], "--sections", j["section"],
                    "--chapter", j["chapter"], "--model", MODEL,
                    "--router-prompt", str(Path(m["snapshot"]) / "prompts/knowledge_unit_organizer.txt"),
                    "--finalizer-prompt", str(Path(m["snapshot"]) / "prompts/knowledge_unit_finalizer.txt")]
        j["status"] = "running"
        write(out / "manifest.json", m)
        try:
            if module.main() != 0:
                raise ValueError("Organizer structural gate failed")
            generated = read(result_path)
            generated["run"]["experimental_transport"] = {
                "provider": "opencode-go", "reasoning_effort": "low", "thinking": "enabled",
                "attempts": 1, "fallback": False, "json_repair": False}
            # Retain original production metadata, identify actual network path explicitly.
            write(result_path, generated)
            j.update(status="completed", output=str(result_path), sha256=sha(result_path))
        except Exception as exc:
            j.update(status="failed", error_type=type(exc).__name__)
            m["stages"]["organize"] = "failed"
            write(out / "manifest.json", m)
            raise
        write(out / "manifest.json", m)
    merge(out)


def merge(out):
    """Offline assembly; shared contains are retained, centers own projection."""
    m = read(out / "manifest.json")
    audit(m)
    if not all(j["status"] == "completed" and sha(j["output"]) == j["sha256"] for j in m["jobs"]):
        raise ValueError("Only complete, unchanged KU outputs can be assembled")
    units, nodes, relations = [], {}, {}
    shared = defaultdict(list)
    for j in m["jobs"]:
        units.extend(read(j["output"])["knowledge_units"])
        for n in read(j["plan"])["nodes"]:
            shared[n["id"]].append({"job": j["id"], "record": n})
            if n["id"] in nodes and (n["name"], n["type"]) != (nodes[n["id"]]["name"], nodes[n["id"]]["type"]):
                raise ValueError("Shared identity has conflicting name or type")
            nodes.setdefault(n["id"], n)
    owners = defaultdict(list)
    for u in units:
        for member in u["centers_on"]:
            owners[member["id"]].append(u["id"])
    if any(len(ids) > 1 for ids in owners.values()):
        raise ValueError("Shared center has multiple owners; human resolution required")
    write(out / "shared_membership_audit.json", {
        "shared_nodes": {k: v for k, v in shared.items() if len(v) > 1},
        "policy": "Retain actual KU members; candidate generator uses center-first ownership. No semantic dedup or rerouting.",
        "merge_implementation_sha256": sha(__file__),
        "scope": "Offline assembly correction after duplicate-ID gate; no model rerun or output modification."})
    relation_variants = defaultdict(list)
    for path in dict.fromkeys(j["graph"] for j in m["jobs"]):
        for r in read(path)["relations"]:
            if r["source_id"] in nodes and r["target_id"] in nodes:
                signature = (r["source_id"], r["type"], r["target_id"])
                relation_variants[signature].append({"source_graph": path, "record": r})
                relations.setdefault(signature, r)
    write(out / "relation_assembly_audit.json", {
        "repeated_signatures": [v for v in relation_variants.values() if len(v) > 1],
        "policy": "Candidate generation uses directed typed signatures; first native record retained, all repeated metadata preserved here."})
    write(out / "ku.json", {"schema_version": "maf-kg.knowledge-unit.v1", "knowledge_units": units,
                           "run": {"boundary_strategy": "textbook-subheading", "provider": "opencode-go",
                                   "model": MODEL, "reasoning_effort": "low"}})
    write(out / "graph.json", {"nodes": list(nodes.values()), "relations": list(relations.values()),
                              "scope": m["scope"]})
    assembled_runner = out / "assembly_executor.py"
    shutil.copy2(__file__, assembled_runner)
    for path in (out / "ku.json", out / "graph.json", out / "shared_membership_audit.json",
                 out / "relation_assembly_audit.json", assembled_runner):
        m["bindings"].append({"path": str(path), "sha256": sha(path)})
    m["stages"]["organize"] = "completed"
    write(out / "manifest.json", m)


def candidates(out):
    m = read(out / "manifest.json")
    audit(m)
    if m["stages"]["organize"] != "completed" or m["stages"]["candidates"] != "pending":
        raise ValueError("Candidate stage not ready")
    reference = out / "reference_draft.json"
    if not reference.exists():
        raise ValueError("Freeze independent reference before generating candidates")
    m["reference_sha256"] = sha(reference)
    m["bindings"].append({"path": str(reference), "sha256": sha(reference)})
    for filename in ("endpoint_catalog.json", "endpoint_catalog.md", "pair_universe.json"):
        path = out / filename
        if not path.exists():
            raise ValueError("Export real endpoint catalog before candidate generation")
        m["bindings"].append({"path": str(path), "sha256": sha(path)})
    executor = out / "candidate_review_executor.py"
    shutil.copy2(__file__, executor)
    m["bindings"].append({"path": str(executor), "sha256": sha(executor)})
    write(out / "manifest.json", m)
    subprocess.run([sys.executable, "-X", "utf8", str(Path(m["snapshot"]) / "scripts/build_ku_depends_candidates.py"),
                    str(out / "ku.json"), str(out / "graph.json"), "--out", str(out / "candidates.json")], check=True)
    m["bindings"].append({"path": str(out / "candidates.json"), "sha256": sha(out / "candidates.json")})
    m["stages"]["candidates"] = "completed"
    write(out / "manifest.json", m)


def review(out):
    m = read(out / "manifest.json")
    audit(m)
    if m["stages"]["candidates"] != "completed" or m["stages"]["review"] != "pending":
        raise ValueError("Review stage not ready")
    native = Native(out, m)
    formatter = load(Path(m["snapshot"]) / "scripts/review_ku_depends_llm.py", "phase_formatter")
    units = {u["id"]: u for u in read(out / "ku.json")["knowledge_units"]}
    pool = read(out / "candidates.json")["candidates"]
    system = (Path(m["snapshot"]) / "prompts/ku_depends_review.txt").read_text(encoding="utf-8")
    verdicts = []
    m["stages"]["review"] = "running"
    write(out / "manifest.json", m)
    try:
        for start in range(0, len(pool), 8):
            batch = list(enumerate(pool[start:start+8], start))
            # Same production payload, no reference labels or manual hints.
            user = formatter.build_user_prompt(batch, units)
            result, usage = native.call(system, user, MODEL)
            rows = result.get("verdicts")
            if not isinstance(rows, list):
                raise ValueError("Missing verdicts list")
            indices = [v.get("index") for v in rows]
            if any(type(i) is not int for i in indices) or len(set(indices)) != len(indices) or set(indices) != {i for i, c in batch}:
                raise ValueError("Missing/duplicate/out-of-pool indices")
            for row in rows:
                if type(row.get("accept")) is not bool or row.get("strength") not in ("strong", "weak") or not isinstance(row.get("reason"), str):
                    raise ValueError("Invalid verdict fields")
                c = pool[row["index"]]
                verdicts.append({**row, "source_ku": c["source_ku"], "target_ku": c["target_ku"]})
            write(out / "predictions.json", {"verdicts": verdicts})
            print(f"Reviewed {len(verdicts)}/{len(pool)} candidates", flush=True)
    except Exception as exc:
        m["stages"]["review"] = "failed"
        m["review_error_type"] = type(exc).__name__
        write(out / "manifest.json", m)
        raise
    m["stages"]["review"] = "completed"
    m["status"] = "completed_execution_pending_human_adjudication"
    write(out / "manifest.json", m)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out", type=Path)
    p.add_argument("stage", choices=("prepare", "organize", "merge", "candidates", "review"))
    a = p.parse_args()
    globals()[a.stage](a.out.resolve())
