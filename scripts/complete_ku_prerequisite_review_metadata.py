"""Complete a review whose rejected verdicts used native strength='none'.

Original failed stage remains intact. No JSON, accept decisions, or reasons are
rewritten; the supplementary gate accepts 'none' only for rejection metadata.
Existing complete first response is validated offline, remaining batches use
the same frozen payload, model, budget, deadline and single-attempt transport.
"""
import argparse
import json
import shutil
from pathlib import Path

from run_ku_prerequisite_phase import Native, MODEL, audit, load, read, sha, write


def validate(response, batch):
    rows = response.get("verdicts")
    if not isinstance(rows, list):
        raise ValueError("Missing verdicts")
    indices = [v.get("index") for v in rows]
    if any(type(i) is not int for i in indices) or len(set(indices)) != len(indices) or set(indices) != {i for i, _ in batch}:
        raise ValueError("Invalid index coverage")
    for row in rows:
        if type(row.get("accept")) is not bool or not isinstance(row.get("reason"), str):
            raise ValueError("Invalid accept/reason")
        allowed = {"strong", "weak"} if row["accept"] else {"strong", "weak", "none"}
        if row.get("strength") not in allowed:
            raise ValueError("Invalid strength under supplemental gate")
    return rows


def execute(root):
    m = read(root / "manifest.json")
    audit(m)
    if m["stages"]["review"] != "failed":
        raise ValueError("Expected preserved failed primary review")
    out = root / "review_completion01"
    out.mkdir(exist_ok=False)
    shutil.copy2(__file__, out / Path(__file__).name)
    formatter = load(Path(m["snapshot"]) / "scripts/review_ku_depends_llm.py", "supplement_format")
    units = {u["id"]: u for u in read(root / "ku.json")["knowledge_units"]}
    pool = read(root / "candidates.json")["candidates"]
    system = (Path(m["snapshot"]) / "prompts/ku_depends_review.txt").read_text(encoding="utf-8")
    first_request = root / "native_requests/request_012.json"
    first_response = root / "native_responses/request_012.json"
    batch = list(enumerate(pool[:8]))
    user = formatter.build_user_prompt(batch, units)
    sent = read(first_request)["body"]
    if sent["messages"] != [{"role": "system", "content": system}, {"role": "user", "content": user}]:
        raise ValueError("First request does not match frozen batch")
    raw = read(first_response)
    if raw["choices"][0]["finish_reason"] != "stop":
        raise ValueError("First response incomplete")
    result = json.loads(raw["choices"][0]["message"]["content"])
    verdicts = []

    def append(rows):
        for row in rows:
            c = pool[row["index"]]
            verdicts.append({**row, "source_ku": c["source_ku"], "target_ku": c["target_ku"]})
        write(out / "predictions.json", {"verdicts": verdicts})

    sm = {"status": "running", "primary_review_status": "failed", "supplement_reason":
        "Rejected native verdicts use strength=none. Accept/reason/JSON kept unchanged; metadata gate extended, no model retry.",
        "first_request_sha256": sha(first_request), "first_response_sha256": sha(first_response),
        "reference_sha256": sha(root / "reference_draft.json"), "candidate_sha256": sha(root / "candidates.json"),
        "first_response_reused_without_api": True, "new_calls": 0,
        "model": MODEL, "reasoning_effort": "low", "fallback": False, "json_repair": False}
    write(out / "manifest.json", sm)
    append(validate(result, batch))
    native = Native(root, m)
    try:
        for start in range(8, len(pool), 8):
            batch = list(enumerate(pool[start:start+8], start))
            user = formatter.build_user_prompt(batch, units)
            result, usage = native.call(system, user, MODEL)
            sm["new_calls"] += 1
            append(validate(result, batch))
            write(out / "manifest.json", sm)
            print(f"Supplement reviewed {len(verdicts)}/{len(pool)}", flush=True)
    except Exception as exc:
        sm.update(status="failed", error_type=type(exc).__name__)
        write(out / "manifest.json", sm)
        raise
    sm.update(status="completed", verdict_count=len(verdicts))
    write(out / "manifest.json", sm)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    execute(p.parse_args().root.resolve())
