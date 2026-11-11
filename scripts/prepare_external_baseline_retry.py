"""Prepare an explicitly authorized retry without replacing historical outcomes."""
import argparse
import copy
import json
import uuid
from pathlib import Path

from external_baseline_runtime import sha256
from prospective_native_client import write


def prepare(parent: Path, output: Path):
    parent_manifest = parent / "manifest.json"
    manifest = json.loads(parent_manifest.read_text(encoding="utf-8"))
    jobs = [copy.deepcopy(j) for j in manifest["jobs"] if j["status"] == "failed"]
    if not jobs:
        raise ValueError("No failed jobs to retry")
    if any(j["status"] in {"running", "pending"} for j in manifest["jobs"]):
        raise ValueError("Parent execution must finish before explicit retries")
    output.mkdir(parents=True, exist_ok=False)
    for job in jobs:
        original = copy.deepcopy(job)
        for key in ("exit_code", "elapsed_seconds", "graph_sha256"):
            job.pop(key, None)
        job.update(directory=str(output / f"r{job['repeat']}" / job["section"] / job["method"]),
                   status="pending", original_attempt=original, retry_attempt=1)
    manifest.update(jobs=jobs, status="frozen", session=str(uuid.uuid4()),
                    retry_parent=str(parent_manifest), retry_parent_sha256=sha256(parent_manifest),
                    retry_authorization="User explicitly requested rerunning the three failed tasks on 2026-10-09",
                    retry_policy="Same frozen configuration and cumulative ledgers; no automatic retries or result replacement")
    write(output / "manifest.json", manifest)
    write(output / "retry_plan_frozen.json", manifest)
    print(f"Prepared {len(jobs)} explicit retries; original outcomes and shared budgets unchanged")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    prepare(args.parent.resolve(), args.output.resolve())
