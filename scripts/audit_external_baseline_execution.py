"""Summarize native execution evidence without inventing semantic scores."""
import argparse
import json
from pathlib import Path

from external_baseline_runtime import ENDPOINT, MODEL, sha256
from prospective_native_client import write


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def edc_stage_counts(value):
    stages = value.get("result", {})
    canonical = stages.get("canonical_triplets", [])
    return {"extracted_parsed_triplets": sum(len(x) for x in stages.get("open_triplets", [])),
            "canonical_triplet_slots": sum(len(x) for x in canonical),
            "canonical_null_slots": sum(t is None for x in canonical for t in x),
            "parser_drop_events": sum(len(e.get("dropped", [])) for e in value.get("parse_events", [])),
            "target_schema_unchanged": stages.get("target_schema_unchanged")}


def audit(run: Path, output: Path):
    manifest = read(run / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        directory = Path(job["directory"])
        native = directory / "native" if job["method"] in {"maf", "direct"} else directory
        requests = list((native / "requests").glob("*.json"))
        issues = []
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        known = 0
        for path in requests:
            request = read(path)
            body = request["body"]
            if (request["endpoint"] != ENDPOINT or body["model"] != MODEL or
                    body.get("reasoning_effort") != "low" or body.get("thinking") != {"type": "enabled"} or
                    body.get("stream") != manifest.get("streaming", False) or
                    body["max_tokens"] != manifest["common"]["max_tokens"]):
                issues.append({"request": path.name, "issue": "native parameter mismatch"})
            response = native / "responses" / path.name
            if response.exists():
                value = read(response)
                usage = value.get("usage", {})
                if isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
                    known += 1
                    totals["prompt_tokens"] += usage["prompt_tokens"]
                    totals["completion_tokens"] += usage["completion_tokens"]
                    totals["reasoning_tokens"] += usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0) or 0
                finish = value.get("choices", [{}])[0].get("finish_reason")
                if finish != "stop":
                    issues.append({"request": path.name, "issue": "non-complete output", "finish_reason": finish})
        if job["status"] == "completed" and sha256(directory / "graph.json") != job["graph_sha256"]:
            issues.append({"issue": "graph hash changed"})
        row = {**job, "actual_requests": len(requests), "usage_known_requests": known,
               "native_usage": totals, "issues": issues, "quality_status": "not_scored",
               "request_error_artifacts": len(list((native / "errors").glob("*.json"))),
               "stream_event_artifacts": len(list((native / "events_raw").glob("*.json")))}
        if job["method"] == "edc" and (directory / "edc_audit.json").exists():
            row["edc_stage_sizes"] = edc_stage_counts(read(directory / "edc_audit.json"))
        graph_path = directory / "graph.json"
        if graph_path.exists():
            graph = read(graph_path)
            if isinstance(graph, list):
                row["native_triplet_slots"] = sum(len(x) for x in graph)
                row["native_null_triplets"] = sum(t is None for x in graph for t in x)
            elif "nodes" in graph:
                row.update(nodes=len(graph["nodes"]), relations=len(graph["relations"]))
            else:
                row.update(entities=len(graph["entities"]), relationships=len(graph["relationships"]))
        rows.append(row)
    result = {"scope": manifest["scope"], "jobs": rows,
              "completed": sum(r["status"] == "completed" for r in rows),
              "failed": sum(r["status"] == "failed" for r in rows),
              "pending": sum(r["status"] == "pending" for r in rows),
              "no_quality_claim": "Transport completion and graph sizes do not measure mathematical quality."}
    write(output, result)
    print(f"Completed={result['completed']}, failed={result['failed']}, pending={result['pending']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    audit(args.run.resolve(), args.output.resolve())
