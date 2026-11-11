"""Reuse frozen notes to isolate source-scope contract from summary content."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from run_memory_ablation import ROOT, digest, write_json
from run_reading_summary_test import EXTRACTION_CONTRACT, execute, prepare as prepare_summary

SOURCE_SCOPE_CONTRACT = """Source role and scope contract
current_evidence determines which mathematical semantic units are eligible.
source_applicability_context is additional original source, not a new extraction
region. It may supply source-explicit qualifications for an eligible unit when
the source relationship warrants it. Preserve such applicable qualifications in
the complete description and logical_form when present. Do not create standalone
nodes from supplemental text, import unrelated conditions, or infer unstated facts.
Qualification text in a proof may clarify an eligible main statement without
turning intermediate proof steps into separate nodes.
reading_state_summary is optional navigational context, not evidence or expected
output. Check it against original supplied text and preserve original qualifications
even if the note compresses them. Graph state remains an incomplete identity view.
The existing evidence object records the current passage's primary span. Additional
source provenance stays in source_applicability_context in this diagnostic request;
do not pretend that an earlier qualification appeared in the primary evidence span.
This is a request-level experiment, not a production merged-graph evidence policy.

"""
REPLACEMENTS = {
    "- current_evidence is the only source region for this request's extraction.":
        "- current_evidence determines eligible units; source_applicability_context may qualify them.",
    "Use only information explicitly present in the current chunk. Prefer omission":
        "Use only supplied original source. Admit units only from the current chunk;\nsource_applicability_context may qualify them. Prefer omission",
    "The line-numbered current_evidence.text remains the sole source of evidence and must govern":
        "The line-numbered current_evidence.text and supplied original qualifications must govern",
}


def transform_prompt(prompt: str) -> str:
    if not prompt.startswith(EXTRACTION_CONTRACT):
        raise ValueError("Unexpected frozen prompt prefix")
    original = prompt[len(EXTRACTION_CONTRACT):]
    for old, new in REPLACEMENTS.items():
        if original.count(old) != 1:
            raise ValueError("Source-scope clause was missing or duplicated")
        original = original.replace(old, new)
    return SOURCE_SCOPE_CONTRACT + original


def prepare(output: Path) -> dict:
    manifest = prepare_summary(output)
    previous = ROOT / "evaluation/context_state_2026-10-03/run07"
    previous_manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
    if previous_manifest["status"] != "completed":
        raise ValueError("Frozen summary baseline is incomplete")
    for case in manifest["cases"]:
        folder = output / case["name"]
        prior_folder = previous / case["name"]
        if digest(folder / "base_request.json") != digest(prior_folder / "base_request.json"):
            raise ValueError("Frozen source or graph memory changed")
        if digest(folder / "summary_request.json") != digest(prior_folder / "summary_request.json"):
            raise ValueError("Frozen summary input changed")
        prompt = folder / "system_prompt.txt"
        updated = transform_prompt(prompt.read_text(encoding="utf-8"))
        prompt.write_text(updated, encoding="utf-8", newline="\n")
        case["system_prompt_sha256"] = digest(prompt)
        shutil.copy2(prior_folder / "generated_summary.json", folder / "generated_summary.json")
        case["reused_summary_sha256"] = digest(folder / "generated_summary.json")
    snapshot = output / "snapshot"
    for name in ("run_summary_scope_contract_test.py", "audit_reading_summary_test.py"):
        shutil.copy2(ROOT / "scripts" / name, snapshot / name)
    manifest.update(experiment="explicit_source_scope_with_frozen_reading_summary",
                    arms=["source_context", "source_and_summary"],
                    summary_policy="Reuse byte-identical run07 summaries; zero new summary calls",
                    primary_contrast="Same explicit source-scope contract: original source alone versus source plus frozen summary",
                    historical_contrast="Run07 is historical contract control, not simultaneous randomized baseline",
                    contract_replacements=REPLACEMENTS,
                    evidence_limit="Primary current span plus request-level supplementary provenance; no production graph merge")
    write_json(output / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        prepare(args.output)
    if not args.prepare_only:
        execute(args.output)
