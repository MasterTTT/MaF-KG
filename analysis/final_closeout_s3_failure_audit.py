#!/usr/bin/env python
"""S3 §6.3/§6.5 and §3: failure, retry and residual audit.

Collects, from artifacts only, the things the closeout plan requires to be disclosed rather than
smoothed over:

  * the prefix runs: how many calls were actually made, which of them failed, and with what error
  * the Direct S1 gap: the contract violations are kept as evidence and were not resampled to fill
    the table
  * the legacy 27-pair condition B: 7 batches but 8 calls, and the missing first wrapper
  * the iText2KG symbol residual: verbatim delivery passed, identity collision remains
  * the EDC v2 empty graph vs the parse-compatible rerun

No model is called.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits/.

Usage:
    python analysis/final_closeout_s3_failure_audit.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits"

INPUT_SCOPE = REPO / "evaluation/paper_closeout_2026-10-10/input_scope"
CONTEXT = REPO / "evaluation/paper_closeout_2026-10-10/prerequisite_context"
MERGED = REPO / "evaluation/external_baselines_merged_2026-10-09"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_entries(path: Path) -> dict:
    data = read_json(path)
    entries = []
    for index, row in enumerate(data.get("requests", [])):
        usage = row.get("usage") or {}
        label = str(row.get("label", ""))
        entries.append(
            {
                "index": index,
                "run": label.split("input_scope\\runs\\")[-1] if "input_scope\\runs\\" in label else label,
                "status": row.get("status"),
                "total_tokens": usage.get("total_tokens"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "estimated_usd": row.get("estimated_usd"),
            }
        )
    return {
        "path": path.relative_to(REPO).as_posix(),
        "sha256": sha256_file(path),
        "model": data.get("model"),
        "accounting_tokens": data.get("accounting_tokens"),
        "accounting_usd": data.get("accounting_usd"),
        "token_limit": data.get("token_limit"),
        "usd_limit": data.get("usd_limit"),
        "entry_count": len(entries),
        "entries": entries,
        "sequence_basis": (
            "the ledger requests array is append-ordered, so the index is the time order; it is not a "
            "sort of labels"
        ),
    }


def debug_merge_files() -> list[dict]:
    root = INPUT_SCOPE / "snapshot/KG/_debug"
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*_merge_invalid.json.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text.strip()[:400]
        out.append(
            {
                "file": path.relative_to(REPO).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "head": head,
            }
        )
    return out


def prefix_runs() -> dict:
    units = []
    for repeat in ("r1", "r2"):
        for scope in ("s1", "s2"):
            for method in ("direct", "maf"):
                run_dir = INPUT_SCOPE / "runs" / repeat / scope / method
                completion = run_dir / "completion.json"
                graph = run_dir / "graph.json"
                entry = {
                    "unit": f"{repeat}/{scope}/{method}",
                    "run_dir": run_dir.relative_to(REPO).as_posix(),
                    "completion": read_json(completion) if completion.is_file() else None,
                    "graph_bytes": graph.stat().st_size if graph.is_file() else None,
                }
                if graph.is_file():
                    data = read_json(graph)
                    entry["graph_nodes"] = len(data.get("nodes", []))
                    entry["graph_relations"] = len(data.get("relations", []))
                stdout = run_dir / "stdout.log"
                if stdout.is_file():
                    lines = [
                        line.strip()
                        for line in stdout.read_text(encoding="utf-8", errors="replace").splitlines()
                        if line.strip()
                    ]
                    entry["stdout_last_line"] = lines[-1][:300] if lines else None
                    entry["stdout_error_lines"] = [
                        line[:300] for line in lines if "Error" in line or "error" in line
                    ][-2:]
                    entry["stdout_log"] = stdout.relative_to(REPO).as_posix()
                units.append(entry)

    expected = {
        f"{repeat}/{scope}/{method}"
        for repeat in ("r1", "r2")
        for scope in ("s1", "s2")
        for method in ("direct", "maf")
    }
    produced = {unit["unit"] for unit in units if unit["completion"]}
    return {
        "units": units,
        "expected_units": len(expected),
        "units_with_final_graph": len(produced),
        "missing_units": sorted(expected - produced),
        "direct_call_ledger": ledger_entries(INPUT_SCOPE / "budgets/direct.json"),
        "maf_call_ledger": ledger_entries(INPUT_SCOPE / "budgets/maf.json"),
        "contract_failure_artifacts": debug_merge_files(),
        "reading": (
            "Eight units were planned. Seven produced a final graph. The missing one is r1/s1/direct: "
            "the ledger shows two calls for that unit and the debug directory keeps both rejected "
            "merge payloads, so the unit failed twice on two different contract violations. The "
            "r1/s2/direct unit also shows two calls: its first attempt failed and the authorised "
            "rerun succeeded. No unit was filled with a zero and none was resampled to complete the "
            "grid beyond what the ledger already records."
        ),
    }


def condition_b_accounting() -> dict:
    ledger = read_json(CONTEXT / "budgets/review.json")
    entries = []
    for row in ledger.get("requests", []):
        usage = row.get("usage") or {}
        entries.append(
            {
                "label": row.get("label"),
                "status": row.get("status"),
                "total_tokens": usage.get("total_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            }
        )
    responses = {}
    total = 0
    for path in sorted((CONTEXT / "armB/responses").glob("*.json")):
        data = read_json(path)
        tokens = (data.get("usage") or {}).get("total_tokens")
        total += tokens or 0
        responses[path.name] = {
            "total_tokens": tokens,
            "finish_reason": data["choices"][0].get("finish_reason"),
            "sha256": sha256_file(path),
        }
    unparsed = read_json(CONTEXT / "armB/unparsed/batch_2.json")
    first_content = json.loads(unparsed["content"])
    second_content = json.loads(
        read_json(CONTEXT / "armB/responses/batch_2.json")["choices"][0]["message"]["content"]
    )
    return {
        "ledger": {
            "path": (CONTEXT / "budgets/review.json").relative_to(REPO).as_posix(),
            "entry_count": len(entries),
            "accounting_tokens": ledger.get("accounting_tokens"),
            "accounting_usd": ledger.get("accounting_usd"),
            "entries": entries,
        },
        "archived_responses": {
            "count": len(responses),
            "sum_total_tokens": total,
            "files": responses,
        },
        "difference_ledger_minus_responses": (ledger.get("accounting_tokens") or 0) - total,
        "missing_wrapper": {
            "found": False,
            "what_is_missing": (
                "the first batch_2 call has no archived native wrapper (no reasoning field, no raw "
                "response object); only its extracted content survives in armB/unparsed/batch_2.json"
            ),
            "searched": [
                (CONTEXT / "armB").relative_to(REPO).as_posix() + "/ (requests, responses, transport, unparsed, verdicts)",
                "no native/ directory exists for this arm",
            ],
            "preserved_evidence": [
                (CONTEXT / "armB/unparsed/batch_2.json").relative_to(REPO).as_posix(),
                (CONTEXT / "budgets/review.json").relative_to(REPO).as_posix(),
            ],
        },
        "first_call_error": unparsed.get("error"),
        "first_call_roles": [
            {"index": v["index"], "role": v["role"], "directness": v.get("directness")}
            for v in first_content["verdicts"]
        ],
        "second_call_roles": [
            {"index": v["index"], "role": v["role"], "directness": v.get("directness")}
            for v in second_content["verdicts"]
        ],
        "favourable_role_selection_observed": False,
        "reading": (
            "Condition B made 8 calls over 7 batches. The first batch_2 attempt failed inside the "
            "harness (list index out of range) and was resampled; both attempts returned "
            "required/direct for every index they covered, so no favourable-role selection is visible. "
            "The first attempt's full wrapper is not archived and is not reconstructed."
        ),
    }


def itext_residual() -> dict:
    test = read_json(MERGED / "itext_symbol_native_test.json")
    audit = read_json(MERGED / "itext_entity_name_audit.json")
    arms = {}
    for key, value in audit.get("arms", {}).items():
        arms[key] = {
            "final_entity_count": value.get("final_entity_count"),
            "final_relation_count": value.get("final_relation_count"),
            "verbatim_entity_names": value.get("verbatim_entity_names"),
            "mangled_entity_names": value.get("mangled_entity_names"),
            "verbatim_relation_names": value.get("verbatim_relation_names"),
            "mangled_relation_names": value.get("mangled_relation_names"),
        }
    return {
        "delivery_check_passed": test["checks"]["all_passed"],
        "checks": test["checks"],
        "residual_identity_collision": test["dedup_semantics"],
        "arms": arms,
        "reading": (
            "The adapter delivers the model strings verbatim and the positive control still detects "
            "the pinned damage. What remains is the pinned identity rule: names differing only by "
            "case, separator or Greek capital/lowercase still collapse to one identity (for example "
            "delta-y against Delta-y). This is disclosed as a residual and was not repaired by "
            "changing the upstream identity rule, so the result must not be described as fully "
            "mathematically symbol-safe."
        ),
    }


def edc_states() -> dict:
    out = {}
    for label, rel in (
        ("edc_v2_r1", "runs/r1/merged/edc/graph.json"),
        ("edc_v2_r2", "runs/r2/merged/edc/graph.json"),
        ("edc_parse_compatible_r1", "edc_v3/r1/merged/edc/graph.json"),
        ("edc_parse_compatible_r2", "edc_v3/r2/merged/edc/graph.json"),
    ):
        path = MERGED / rel
        data = read_json(path)
        slots = data[0] if isinstance(data, list) and data else []
        triples = sum(1 for slot in slots if isinstance(slot, list) and len(slot) >= 3)
        out[label] = {
            "graph": path.relative_to(REPO).as_posix(),
            "slots": len(slots),
            "triples": triples,
            "null_slots": sum(1 for slot in slots if slot is None),
            "sha256": sha256_file(path),
        }
    out["reading"] = (
        "The v2 runs completed with every alignment slot null: the pinned parser only accepts an "
        "ASCII colon while the model answered with the fullwidth form, so no definition was parsed "
        "and the alignment stage never ran. That is a parser-compatibility fault, not a capability "
        "result, and it is never scored as zero coverage."
    )
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dsh.final_closeout.s3_failure_audit.v1",
        "no_new_model_calls": True,
        "prefix_runs": prefix_runs(),
        "condition_b_accounting": condition_b_accounting(),
        "itext2kg_symbol_residual": itext_residual(),
        "edc_states": edc_states(),
    }
    out = OUT_DIR / "failure_and_retry_audit.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    prefix = payload["prefix_runs"]
    print(f"wrote {out.relative_to(REPO).as_posix()}")
    print(f"  prefix units with graph: {prefix['units_with_final_graph']}/{prefix['expected_units']} "
          f"missing={prefix['missing_units']}")
    print(f"  direct calls={prefix['direct_call_ledger']['entry_count']} "
          f"tokens={prefix['direct_call_ledger']['accounting_tokens']} | "
          f"maf calls={prefix['maf_call_ledger']['entry_count']} "
          f"tokens={prefix['maf_call_ledger']['accounting_tokens']}")
    print(f"  contract failure artifacts: {len(prefix['contract_failure_artifacts'])}")
    b = payload["condition_b_accounting"]
    print(f"  condition B: ledger entries={b['ledger']['entry_count']} "
          f"archived responses={b['archived_responses']['count']} "
          f"difference={b['difference_ledger_minus_responses']} wrapper_found={b['missing_wrapper']['found']}")
    print(f"  iText residual conflation: {payload['itext2kg_symbol_residual']['residual_identity_collision']['kept_names']}")
    for label, state in payload["edc_states"].items():
        if isinstance(state, dict):
            print(f"  EDC {label}: slots={state['slots']} triples={state['triples']} nulls={state['null_slots']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
