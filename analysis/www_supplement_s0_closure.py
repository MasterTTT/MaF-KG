"""S0 closure check: verify the offline preparation is internally consistent.

Runs no model calls. Checks the things that would waste money if they were wrong:

  * every frozen artifact still hashes as recorded,
  * the three runner references agree with the exported inputs and their hashes,
  * the composite snapshot provenance matches the files on disk,
  * the budget plan totals match the per-task detail,
  * no ledger lock files are left behind,
  * no scratch directories are left in the tree.

Output: evaluation/www_evidence_supplement_2026-10-11/reports/s0_closure.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
S = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    out: dict = {"schema": "maf_kg.www_supplement.s0_closure.v1",
                 "checks": [], "problems": [], "warnings": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        out["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            out["problems"].append(f"{name}: {detail}")

    # 1. freeze record artifacts
    freeze = json.loads((S / "freeze/freeze_record.json").read_text(encoding="utf-8"))
    missing, changed = [], []
    for rel, meta in freeze.get("frozen_artifacts", {}).items():
        p = WORKSPACE / rel
        if not p.exists():
            missing.append(rel)
        elif meta.get("sha256") and sha(p).upper() != meta["sha256"].upper():
            changed.append(rel)
    check("frozen artifacts present", not missing, f"missing={missing}")
    check("frozen artifacts unchanged", not changed, f"changed={changed}")

    # 2. runner references vs exported inputs
    imap = json.loads((S / "e1_content/inputs/input_map.json").read_text(encoding="utf-8"))
    by_id = {e["input_id"]: e for e in imap["inputs"]}
    for name, expect in (("references/runner_reference_v1.json", ("I", "O")),
                         ("references/runner_reference_prefix_v1.json", ("I1", "I2"))):
        ref = json.loads((S / name).read_text(encoding="utf-8"))
        ids = [s["id"] for s in ref["sections"]]
        check(f"{name} sections", len(ids) == len(expect),
              f"got {ids}")
        for s in ref["sections"]:
            src = Path(s["source"])
            check(f"{name} source hash {s['id']}",
                  src.exists() and sha(src).upper() == s["source_sha256"].upper(),
                  f"{src}")

    # input files on disk match the map
    for e in imap["inputs"]:
        p = WORKSPACE / e["input_file"]
        check(f"input {e['input_id']} hash",
              p.exists() and sha(p).upper() == e["input_sha256"].upper(), str(p))

    # 3. composite snapshot provenance
    prov_path = S / "runner/composite_snapshot/composite_provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    mismatch = []
    for rel, meta in prov["files"].items():
        p = S / "runner/composite_snapshot" / rel
        if p.is_file() and meta.get("sha256") and sha(p) != meta["sha256"]:
            mismatch.append(rel)
    check("composite snapshot matches provenance", not mismatch, f"{mismatch}")
    check("composite patch has 2 hunks", prov["runner_patch"]["hunks"] == 2,
          str(prov["runner_patch"]["hunks"]))

    # guards the run depends on
    for rel, needle in (("scripts/external_edc.py", "parse_compat"),
                        ("scripts/external_itext2kg.py", "preserve_symbols"),
                        ("scripts/run_external_baselines.py", "parse_compat"),
                        ("scripts/run_external_baselines.py", "memory_node_bytes")):
        p = S / "runner/composite_snapshot" / rel
        check(f"guard {rel} has {needle}", needle in p.read_text(encoding="utf-8"), "")

    # 4. budget plan totals
    bp = json.loads((S / "budgets/budget_plan.json").read_text(encoding="utf-8"))
    tok = sum(t["est_tokens"] for t in bp["tasks"])
    usd = round(sum(t["est_usd"] for t in bp["tasks"]), 2)
    check("budget tokens sum", tok == bp["totals"]["est_tokens"],
          f"{tok} vs {bp['totals']['est_tokens']}")
    check("budget usd sum", abs(usd - bp["totals"]["est_usd"]) < 0.01,
          f"{usd} vs {bp['totals']['est_usd']}")
    runs = sum(t.get("method_runs", 0) for t in bp["tasks"])
    check("method run count is 32", runs == 32, str(runs))

    # 5. ledgers. A lock is legitimate while a run of that method is in flight;
    # it is a problem only when no job is running, because then it is stale and
    # will block the next run (this happened once already).
    running_methods = set()
    for rel in ("e1_content/run_a2", "e1_prefix/run", "e3_memory/run"):
        mf = S / rel / "manifest.json"
        if mf.exists():
            for j in json.loads(mf.read_text(encoding="utf-8")).get("jobs", []):
                if j.get("status") == "running":
                    running_methods.add(j.get("method"))
    locks = list((S / "budgets").rglob("*.lock"))
    stale = [str(p) for p in locks if p.stem not in running_methods]
    live = [str(p) for p in locks if p.stem in running_methods]
    check("no stale ledger locks", not stale, str(stale))
    if live:
        out["warnings"].append(f"live ledger locks held by running jobs (expected): {live}")

    # 6. scratch
    scratch = list((WORKSPACE / "tmp").glob("v_*")) + list((WORKSPACE / "tmp").glob("*dryrun*")) \
        + list((WORKSPACE / "tmp").glob("composite_test"))
    check("no scratch dirs left", not scratch, str([p.name for p in scratch]))

    # 7. every planned phase has a reference and a caps file
    for rel in ("references/runner_reference_v1.json",
                "references/runner_reference_prefix_v1.json",
                "budgets/e1_caps.json",
                "freeze/evaluation_contract.json",
                "e2_prerequisite/target_selection.json"):
        check(f"present {rel}", (S / rel).exists(), "")

    # 8. every supplement JSON parses. The vendored VS Code file is JSONC and is
    #    byte-identical to the frozen vendor tree, so it is excluded by name.
    bad_json = []
    for p in sorted(S.rglob("*.json")):
        if p.name == "launch.json" and ".vscode" in p.parts:
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            bad_json.append(f"{p.relative_to(S)}: {str(exc)[:60]}")
    check("all supplement JSON parses", not bad_json, "; ".join(bad_json))

    # 9. the E2 reference extension acceptance still describes the live file
    ext = S / "e2_prerequisite/reference_v4_extension.json"
    acc_path = S / "e2_prerequisite/reference_v4_extension_acceptance.json"
    if ext.exists() and acc_path.exists():
        acc = json.loads(acc_path.read_text(encoding="utf-8"))
        live = sha(ext).upper()[:16]
        check("E2 extension matches its acceptance hash",
              live == acc.get("accepted_revision_sha256"),
              f"live {live} vs accepted {acc.get('accepted_revision_sha256')}")
        ref = json.loads(ext.read_text(encoding="utf-8"))
        from collections import Counter
        roles = dict(Counter(p["role"] for p in ref["pairs"]))
        check("E2 extension roles match acceptance",
              roles == acc["structure"]["roles"], f"{roles}")
        check("E2 extension pair count matches acceptance",
              len(ref["pairs"]) == acc["structure"]["pairs"], str(len(ref["pairs"])))

    # 10. the offline analysis tools the later phases depend on
    for rel in ("analysis/www_supplement_e1_score.py",
                "analysis/www_supplement_review_packet.py",
                "analysis/www_supplement_s0_verify_references.py"):
        check(f"present {rel}", (WORKSPACE / rel).exists(), "")

    # 11. the runner-facing reference must not carry extraction-method labels.
    #     Note: the mathematical node type "Method" is not an extraction method
    #     and must not trip this check.
    rr = json.loads((S / "references/runner_reference_v1.json").read_text(encoding="utf-8"))
    extraction_methods = ("maf", "direct", "edc", "itext2kg")
    leaked = []
    for n in rr["nodes"] + rr["relations"]:
        blob = json.dumps(n, ensure_ascii=False).lower()
        if any(m in blob for m in extraction_methods):
            leaked.append(n.get("id"))
    check("runner reference carries no extraction-method labels", not leaked, str(leaked[:5]))

    out["ok"] = not out["problems"]
    (S / "reports/s0_closure.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    for c in out["checks"]:
        print(f"  [{'ok ' if c['ok'] else 'FAIL'}] {c['check']}"
              + (f"  ({c['detail']})" if not c["ok"] and c["detail"] else ""))
    print(f"\nproblems: {len(out['problems'])}")
    for p in out["problems"]:
        print(f"  - {p}")
    print(f"checks: {len(out['checks'])}, all ok: {out['ok']}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
