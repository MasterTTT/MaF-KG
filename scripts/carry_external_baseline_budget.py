"""Explicitly carry settled pilot spend into the shared bounded experiment ledger."""
import argparse
import json
from pathlib import Path

from prospective_native_client import write
from external_baseline_runtime import sha256


def carry(source, destination, usd=1.50, tokens=2_500_000):
    for path in sorted((source / "budgets").glob("*.json")):
        target = destination / path.name
        if target.exists():
            raise FileExistsError(f"Cannot overwrite a live budget: {target}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if any(r["status"] == "reserved" for r in data["requests"]):
            raise ValueError("Pilot has unsettled requests")
        old = {"usd_limit": data["usd_limit"], "token_limit": data["token_limit"]}
        data.update(usd_limit=usd, token_limit=tokens, carry_from={"path": str(path.resolve()),
            "sha256": sha256(path), "old_limits": old,
            "decision_provenance": "assistant conservative resource planning under approved baseline task",
            "reason": "Native EDC stages need more total calls; include pilot spend, no quality-based tuning."})
        write(target, data)
    print("Settled pilot spend carried without resetting accounting.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    args = p.parse_args()
    carry(args.source.resolve(), args.destination.resolve())
