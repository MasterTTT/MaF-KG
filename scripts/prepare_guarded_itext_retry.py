"""Freeze the user-approved progress-bounded variant separately from original trials."""
import argparse
import json
import shutil
from pathlib import Path

from external_baseline_runtime import sha256
from prepare_external_baseline_retry import prepare as prepare_retry
from prospective_native_client import write

ROOT = Path(__file__).resolve().parents[1]


def record_interruption(run):
    path = run / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    ledger_path = Path(value["budget_root"]) / "itext2kg.json"
    if ledger_path.with_suffix(".lock").exists():
        raise RuntimeError("Ledger still locked; verify workers have stopped before recording interruption")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for row in ledger["requests"]:
        if row["status"] == "reserved":
            label = Path(row["label"])
            label.relative_to(run)
            row.update(status="unknown_usage_keep_full_reservation",
                       interruption="User approved switching to separately frozen no-progress protection; workers stopped")
    write(ledger_path, ledger)
    for job in value["jobs"]:
        if job["status"] == "running":
            job.update(status="interrupted", interruption_reason="Approved switch after repeated empty relation responses")
        elif job["status"] == "pending":
            job.update(status="cancelled_before_start")
    value["status"] = "interrupted"
    write(path, value)


def prepare(parent, output):
    manifest = prepare_retry(parent, output)
    previous = Path(manifest["snapshot"])
    snapshot = output / "snapshot"
    shutil.copytree(previous, snapshot)
    replacements = ("external_itext2kg.py", "run_external_baselines.py", "itext_progress_guard.py")
    for name in replacements:
        shutil.copy2(ROOT / "scripts" / name, snapshot / "scripts" / name)
    artifacts = []
    for entry in manifest["artifacts"]:
        path = Path(entry["path"])
        try:
            path = snapshot / path.relative_to(previous)
        except ValueError:
            pass
        artifacts.append({"path": str(path), "sha256": sha256(path)})
    guard = snapshot / "scripts" / "itext_progress_guard.py"
    artifacts.append({"path": str(guard), "sha256": sha256(guard)})
    manifest.update(snapshot=str(snapshot), artifacts=artifacts, itext_no_progress_limit=3,
                    method_variant="itext2kg-v0.0.5-progress-bound-3",
                    guard_authorization="User explicitly approved retaining isolated nodes after consecutive no-progress calls, separately labelled experiment",
                    guard_scope="Only loop termination changes; no fabricated relationships, source/prompt/model/threshold/normalization changes")
    write(output / "manifest.json", manifest)
    write(output / "retry_plan_frozen.json", manifest)
    print("Frozen three-task progress-bounded variant; shared budget and historical outcomes preserved")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record-interruption", "prepare"))
    parser.add_argument("parent", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "record-interruption":
        record_interruption(args.parent.resolve())
    else:
        if args.output is None:
            parser.error("prepare requires an output path")
        prepare(args.parent.resolve(), args.output.resolve())
