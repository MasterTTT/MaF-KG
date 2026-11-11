"""Record an inspected stopped worker without retrying or releasing reserved spend."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from prospective_native_client import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--model",required=True)
    parser.add_argument("--section",required=True)
    parser.add_argument("--arm",required=True)
    parser.add_argument("--reason",required=True)
    args = parser.parse_args()
    manifest = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
    job = next(j for j in manifest["jobs"] if (j["model"],j["section"],j["name"]) ==
               (args.model,args.section,args.arm))
    if job["status"] != "failed":
        raise ValueError("Only an already-stopped failed worker may be annotated")
    path = args.output / "budgets" / f"{args.model}.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    budget = json.loads(path.read_text(encoding="utf-8"))
    matched = []
    for row in budget["requests"]:
        if row["status"] == "reserved" and row["label"].startswith(job["directory"] + "\\"):
            row.update(status="unknown_usage_keep_full_reservation", interruption_reason=args.reason)
            matched.append(row["label"])
    if not matched:
        raise ValueError("No matching unresolved reservation")
    write(path,budget)
    write(Path(job["directory"]) / "external_interruption.json", {
        "date":datetime.now(timezone.utc).isoformat(),"reason":args.reason,
        "budget_before_sha256":before,"budget_after_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
        "reservations_retained":matched,"usage_known":False,"automatic_retry":False})


if __name__ == "__main__":
    main()
