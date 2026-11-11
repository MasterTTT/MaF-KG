"""Offline closeout audit; never imports or invokes the API client."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(root):
    bindings = read(root / "frozen_inputs01/manifest.json")["bindings"]
    for binding in bindings:
        data = Path(binding["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != binding["sha256"]:
            raise ValueError("Frozen binding changed: " + binding["path"])
    result = {"frozen_bindings_verified": len(bindings), "new_api_requests": 0,
              "main_status": read(root / "prediction_stage.json")["status"],
              "classifier_status": read(root / "diagnostics01/manifest.json")["judge"],
              "source_context_status": "failed_transport_no_prediction",
              "source_context_error": read(root / "native_errors/request_016.json"),
              "retry": False, "reason_no_retry": "Frozen API deadline expired"}
    for arm in ("baseline", "semantic"):
        result[arm] = read(root / f"prediction01/{arm}_scores.json")["end_to_end_direct"]
    diag = read(root / "diagnostics01/judge_all_pairs/scores.json")
    result["classifier_only"] = diag["candidate_pool_review"]
    ledger = read(root / "budget.json")
    result["accounting"] = {
        "request_count": len(ledger["requests"]),
        "reported_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in ledger["requests"]),
        "reported_peak_rate_estimate_usd": sum(r.get("estimated_usd", 0) for r in ledger["requests"]),
        "reserved_accounting_tokens": ledger["accounting_tokens"],
        "reserved_accounting_usd": ledger["accounting_usd"],
        "actual_provider_billing": "unknown; local bounds are not subscription charges"}
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "closeout_audit.json":
            continue
        data = path.read_bytes()
        if path.suffix in {".json", ".md", ".txt", ".py"}:
            text = data.decode("utf-8-sig", errors="strict")
            if "\ufffd" in text:
                raise ValueError("Replacement character: " + str(path))
        records.append({"path": str(path.relative_to(root)), "sha256": hashlib.sha256(data).hexdigest()})
    result["artifact_hashes"] = records
    target = root / "closeout_audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "artifact_hashes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    audit(parser.parse_args().root.resolve())
