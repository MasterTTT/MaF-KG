"""Freeze BM25 development retrieval and judge only newly retrieved candidates."""
import argparse
import copy
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from retrieve_ku_prerequisite_candidates import retrieve
from run_ku_prerequisite_phase import Native, MODEL, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def prepare(base, reference, out):
    out.mkdir(parents=True, exist_ok=False)
    snap = out / "snapshot"
    snap.mkdir()
    sources = {"catalog.json": base / "endpoint_catalog.json", "baseline_candidates.json": base / "candidates.json",
               "baseline_predictions.json": base / "scoped_review01/predictions.json", "reference.json": reference,
               "system.txt": base / "scoped_review01/system.txt"}
    root = Path(__file__).resolve().parents[1]
    for name in ("retrieve_ku_prerequisite_candidates.py", "run_ku_prerequisite_phase.py",
                 "prospective_native_client.py", "score_ku_prerequisite_evaluation.py", Path(__file__).name):
        sources[name] = root / "scripts" / name
    sources["protocol.md"] = root / "docs/prerequisite_candidate_retrieval_protocol_2026-10-07.md"
    for name, src in sources.items():
        shutil.copy2(src, snap / name)
    catalog = read(snap / "catalog.json")
    # Select the original three test sections, not pairs with particular labels.
    query_ids = [u["id"] for u in catalog["knowledge_units"]
                 if u["job_id"] in {"ch3_s3", "ch4_s3", "ch7_s5"}]
    if len(query_ids) != 5:
        raise ValueError("Original target sections must contain five actual KUs")
    cfg = {"dependent_ids": query_ids, "top_k": 3, "reference_is_not_generator_input": True,
           "selection": "Original three target sections; all available earlier actual KUs searchable"}
    write(out / "retrieval_config.json", cfg)
    bindings = [{"path": str(p), "sha256": sha(p)} for p in snap.iterdir()]
    bindings.append({"path": str(out / "retrieval_config.json"), "sha256": sha(out / "retrieval_config.json")})
    m = {"status": "prepared", "snapshot": str(snap), "bindings": bindings, "model": MODEL,
         "provider": "opencode-go", "reasoning_effort": "low", "max_tokens": 12000,
         "timeout_seconds": 300, "usd_limit": .30, "token_limit": 800000,
         "deadline": (datetime.now(timezone.utc) + timedelta(minutes=90)).isoformat(),
         "session": str(uuid.uuid4()), "attempts": 1, "fallback": False, "json_repair": False,
         "scope": "Post-adjudication development experiment, not held-out or full-graph Gold"}
    write(out / "manifest.json", m)
    result = retrieve(catalog, read(snap / "baseline_candidates.json"), query_ids, top_k=3)
    write(out / "retrieval.json", result)
    write(out / "candidates.json", {"candidates": result["candidates"]})
    baseline_preds = read(snap / "baseline_predictions.json")
    inputs = {"endpoints": catalog["knowledge_units"], "candidates": result["candidates"],
              "predictions": baseline_preds, "reference": read(snap / "reference.json")["annotations"]}
    # This stage reports retrieval coverage only, not scores for absent reviews.
    score = score_evaluation(inputs)
    write(out / "retrieval_diagnostics.json", {"candidate_direct_recall": score["candidate_direct_recall"],
          "baseline_candidate_count": len(read(snap / "baseline_candidates.json")["candidates"]),
          "union_candidate_count": len(result["candidates"]), "new_candidate_count": len(result["new_candidates"]),
          "note": "New candidates unreviewed; no final quality claimed at prepare stage"})
    print(json.dumps(read(out / "retrieval_diagnostics.json"), ensure_ascii=False, indent=2))


def endpoint(u):
    return {"id": u["id"], "name": u["name"], "goal": u["description"],
            "centers": [n["id"] for n in u["centers_on"]],
            "members": [{"id": n["id"], "name": n["name"], "type": n["type"],
                         "description": n.get("description", "")[:350],
                         "evidence": [{"source": Path(e.get("source_path", "")).name,
                                       "start_line": e.get("start_line"), "end_line": e.get("end_line"),
                                       "quote": e.get("text", "")[:200]} for e in n.get("evidence", [])[:2]]}
                        for n in u["full_member_nodes"]]}


def execute(out):
    m = read(out / "manifest.json")
    if m["status"] != "prepared":
        raise ValueError("No automatic retry")
    for b in m["bindings"]:
        if sha(b["path"]) != b["sha256"]:
            raise ValueError("Frozen input changed")
    snap = Path(m["snapshot"])
    catalog = read(snap / "catalog.json")
    units = {u["id"]: u for u in catalog["knowledge_units"]}
    result = read(out / "retrieval.json")
    tasks = []
    offset = len(read(snap / "baseline_candidates.json")["candidates"])
    for i, c in enumerate(result["new_candidates"], offset):
        tasks.append({"index": i, "edge": c["source_name"] + " depends_on " + c["target_name"],
                      "source": endpoint(units[c["source_ku"]]), "target": endpoint(units[c["target_ku"]]),
                      "signals": [], "chain_via": [], "retrieval_signal": c["retrieval"]})
    for start in range(0, len(tasks), 8):
        write(out / f"batch_{start//8+1}.json", tasks[start:start+8])
    m["payload_bindings"] = [{"path": str(p), "sha256": sha(p)} for p in sorted(out.glob("batch_*.json"))]
    m["retrieval_sha256"] = sha(out / "retrieval.json")
    m["status"] = "running"
    write(out / "manifest.json", m)
    client = Native(out, m)
    system = (snap / "system.txt").read_text(encoding="utf-8")
    predictions = copy.deepcopy(read(snap / "baseline_predictions.json")["verdicts"])
    try:
        for binding in m["payload_bindings"]:
            batch = read(binding["path"])
            user = "检索相似或教材位置较早只代表候选，不代表先修成立。请审核：\n" + json.dumps(batch, ensure_ascii=False)
            answer, usage = client.call(system, user, MODEL)
            rows = answer.get("verdicts")
            if not isinstance(rows, list) or len(rows) != len(batch) or {r.get("index") for r in rows} != {r["index"] for r in batch}:
                raise ValueError("Invalid index coverage")
            for v in rows:
                if type(v.get("index")) is not int or not isinstance(v.get("reason"), str):
                    raise ValueError("Invalid verdict fields")
                role = v.get("role")
                if role not in {"required", "principle_support", "proof_support", "related_or_none", "uncertain"}:
                    raise ValueError("Invalid role")
                if role == "required" and v.get("directness") not in {"direct", "indirect"}:
                    raise ValueError("Missing directness")
                if role != "required" and v.get("directness") is not None:
                    raise ValueError("Nonrequired verdict has directness")
                c = result["new_candidates"][v["index"]-offset]
                row = {"index": v["index"], "source_ku": c["source_ku"], "target_ku": c["target_ku"],
                       "label": role, "reason": v["reason"]}
                if role == "required":
                    row["directness"] = v["directness"]
                predictions.append(row)
            write(out / "predictions.json", {"verdicts": predictions})
            print(f"Reviewed {len(predictions)-offset}/{len(tasks)} new candidates", flush=True)
    except Exception as exc:
        m.update(status="failed", error_type=type(exc).__name__)
        write(out / "manifest.json", m)
        raise
    write(out / "predictions.json", {"verdicts": predictions})
    ref = read(snap / "reference.json")["annotations"]
    for label, annotations in (("mixed", ref), ("human_only", [r for r in ref if r["provenance"] == "human_user_adjudicated"])):
        data = {"endpoints": catalog["knowledge_units"], "candidates": result["candidates"],
                "predictions": {"verdicts": predictions}, "reference": annotations}
        write(out / f"{label}_input.json", data)
        write(out / f"{label}_scores.json", score_evaluation(data))
    m.update(status="completed", reused_verdicts=offset, new_verdicts=len(tasks),
             api_requests=len(m["payload_bindings"]))
    write(out / "manifest.json", m)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("prepare", "execute"))
    p.add_argument("base", type=Path)
    p.add_argument("reference", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    if a.stage == "prepare":
        prepare(a.base.resolve(), a.reference.resolve(), a.out.resolve())
    else:
        execute(a.out.resolve())
