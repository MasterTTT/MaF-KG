"""One-factor heading-depth experiment; no production Organizer changes."""
import argparse
import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run_ku_prerequisite_phase import Native, MODEL, audit, load, read, sha, write
from run_ku_boundary_challenge import score

NUMBERED = re.compile(r"^(?:[一二三四五六七八九十百]+、|\d+[.．、]\s*|[（(][一二三四五六七八九十百]+[）)])")


def multilevel_objectives(section, cards):
    """Use terminal numbered headings; introductions stay in their own branch."""
    if not cards:
        raise ValueError("No Cards")
    paths = [tuple(str(h).strip() for h in c["heading_path"][1:]
                   if NUMBERED.match(str(h).strip())) for c in cards]
    unique = list(dict.fromkeys(paths))
    leaves = [p for p in unique if p and not any(len(q) > len(p) and q[:len(p)] == p for q in unique)]
    if not leaves:
        leaves = [()]
    labels = []
    for i, p in enumerate(paths):
        if p in leaves:
            labels.append(p)
            continue
        descendants = [q for q in leaves if q[:len(p)] == p]
        if not descendants:
            raise ValueError("Heading branch has no terminal group")
        following = next((q for q in paths[i+1:] if q in descendants), None)
        preceding = next((q for q in reversed(paths[:i]) if q in descendants), None)
        chosen = following or preceding
        if chosen is None:
            raise ValueError("Unattached introductory Card")
        labels.append(chosen)
    groups = {}
    for card, label in zip(cards, labels, strict=True):
        groups.setdefault(label, []).append(card["card_id"])
    objectives = []
    for i, (path, ids) in enumerate(groups.items(), 1):
        label = " / ".join(path) if path else section
        objectives.append({"goal_id": f"g{i}", "core_question": f"{label}集中讲授什么内容？",
            "objective": f"学习{label}。", "boundary_reason": "按教材最深编号标题固定边界；导语保持在所属分支，不作语义拆分或合并。",
            "card_ids": ids})
    return objectives


def prepare(base, out):
    original = read(base / "manifest.json")
    audit(original)
    if original["status"] != "completed":
        raise ValueError("Baseline must be complete")
    out.mkdir(parents=True, exist_ok=False)
    snap = out / "snapshot"
    shutil.copytree(base / "snapshot", snap)
    # The shared source/reference snapshots are byte-identical to the baseline.
    shutil.copy2(base / "reference.json", out / "reference.json")
    shutil.copy2(Path(__file__), snap / "scripts" / Path(__file__).name)
    bindings = [{"path": str(p), "sha256": sha(p)} for p in snap.rglob("*") if p.is_file()]
    bindings.append({"path": str(out / "reference.json"), "sha256": sha(out / "reference.json")})
    jobs = []
    for old in original["jobs"]:
        plan = read(old["plan"])
        goals = multilevel_objectives(old["section"], plan["cards"])
        pp = out / "plans" / (old["id"] + ".json")
        write(pp, {**plan, "objectives": goals})
        bindings.append({"path": str(pp), "sha256": sha(pp)})
        jobs.append({**{k: old[k] for k in ("id", "chapter", "section", "node_count")},
            "graph": str(snap / Path(old["graph"]).relative_to(base / "snapshot")),
            "source": str(snap / Path(old["source"]).relative_to(base / "snapshot")),
            "plan": str(pp), "heading_count": len(goals), "status": "pending"})
    for j in jobs:
        data = read(j["graph"])
        module = load(snap / "scripts/build_knowledge_units.py", "multilevel_preflight_" + j["id"])
        plan = read(j["plan"])
        module._require_objective_plan({"objectives": plan["objectives"]}, j["section"], plan["cards"])
        module._node_candidate_goals(j["section"], plan["nodes"], plan["cards"], plan["objectives"])
    write(out / "manifest.json", {"status": "prepared", "snapshot": str(snap), "bindings": bindings, "jobs": jobs,
        "provider": "opencode-go", "model": MODEL, "thinking": "enabled", "reasoning_effort": "low",
        "max_tokens": original["max_tokens"], "timeout_seconds": original["timeout_seconds"],
        "usd_limit": .50, "token_limit": 500000, "attempts": 1, "fallback": False, "json_repair": False,
        "deadline": (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(), "session": str(uuid.uuid4()),
        "reference_sha256": sha(out / "reference.json"), "baseline_manifest_sha256": sha(base / "manifest.json"),
        "baseline": str(base), "changed_factor": "terminal numbered heading depth only",
        "scope": "Post-baseline registered development adaptation, not independent generalization"})
    print([(j["id"], j["heading_count"]) for j in jobs], flush=True)


def execute(out):
    m = read(out / "manifest.json")
    audit(m)
    if m["status"] != "prepared":
        raise ValueError("No implicit rerun")
    snap = Path(m["snapshot"])
    module = load(snap / "scripts/build_knowledge_units.py", "multilevel_execute")
    variation = load(snap / "scripts/run_ku_multilevel_heading.py", "frozen_multilevel")
    module.heading_objectives = variation.multilevel_objectives
    module._llm_json = Native(out, m).call
    m["status"] = "running"
    write(out / "manifest.json", m)
    for j in m["jobs"]:
        output = out / "units" / (j["id"] + ".json")
        output.parent.mkdir(exist_ok=True)
        sys.argv = ["build_knowledge_units.py", j["graph"], "--out", str(output), "--src-files", j["source"],
            "--sections", j["section"], "--chapter", j["chapter"], "--model", MODEL,
            "--router-prompt", str(snap / "prompts/knowledge_unit_organizer.txt"),
            "--finalizer-prompt", str(snap / "prompts/knowledge_unit_finalizer.txt")]
        try:
            if module.main() != 0:
                raise ValueError("Organizer structural gate failed")
            j.update(status="completed", output=str(output), sha256=sha(output))
            # Do not mutate the original Organizer's recorded strategy in its output.
            write(out / "units" / (j["id"] + ".strategy.json"), {
                "actual_strategy": "terminal-numbered-heading", "base_organizer_strategy_field": "textbook-subheading",
                "injection_sha256": sha(snap / "scripts/run_ku_multilevel_heading.py"), "output_sha256": sha(output)})
        except Exception as exc:
            j.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            print("FAILED", j["id"], type(exc).__name__, flush=True)
        write(out / "manifest.json", m)
    m["status"] = "completed" if all(j["status"] == "completed" for j in m["jobs"]) else "partial_failure"
    write(out / "manifest.json", m)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "execute", "score"))
    parser.add_argument("base", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare(args.base.resolve(), args.out.resolve())
    else:
        globals()[args.stage](args.out.resolve())
