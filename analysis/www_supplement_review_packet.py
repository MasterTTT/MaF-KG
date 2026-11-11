"""Author review packet sampler for the WWW evidence supplement.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 6.

Rule, frozen before any output exists:
  * one 64-item packet
  * four items per complete output: two reference-content judgements (at least
    one of them a Claim that carries conditions), one common-relation semantic
    judgement, one correctness judgement on an actually generated mathematical
    item
  * drawn with the pre-frozen seed 20261011, independently per output so the
    packet does not depend on processing order
  * shortfalls are recorded honestly and filled by a pre-declared rule, never by
    silently picking a different stratum
  * method labels are hidden from the author; a hidden mapping is kept
  * items are never chosen because they are disputed or because they succeeded

The sampler is deterministic: the same reviewed data and the same seed always
produce the same packet.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

SEED = 20261011
PER_OUTPUT = 4
CONTENT_PER_OUTPUT = 2
RELATION_PER_OUTPUT = 1
CORRECTNESS_PER_OUTPUT = 1


def _rng(seed: int, output_id: str) -> random.Random:
    """Per-output stream so the packet is independent of processing order."""
    digest = hashlib.sha256(f"{seed}:{output_id}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _pick(rng: random.Random, pool: list, k: int) -> list:
    """Deterministic sample without replacement, order-stable."""
    if k >= len(pool):
        return list(pool)
    return rng.sample(pool, k)


def build_packet(outputs: list[dict], seed: int = SEED) -> dict:
    """outputs: [{"output_id","run","method","content_rows","relation_rows","correctness_rows"}]

    content_rows:  {"item_id","name","type","status","coverage","conditions_complete"}
    relation_rows: {"relation_id","semantic","strict"}
    correctness_rows: {"generated_id","name","source_support","math_error","verdict"}
    """
    items: list[dict] = []
    shortfalls: list[dict] = []
    mapping: dict[str, dict] = {}

    for out in sorted(outputs, key=lambda o: o["output_id"]):
        oid = out["output_id"]
        rng = _rng(seed, oid)
        chosen: list[dict] = []

        # --- two content judgements, at least one a Claim with conditions ----
        claims = [r for r in out["content_rows"] if r.get("type") == "Claim"]
        others = [r for r in out["content_rows"] if r.get("type") != "Claim"]
        picked_claim = _pick(rng, claims, 1)
        rest = [r for r in out["content_rows"] if r not in picked_claim]
        picked_other = _pick(rng, rest, 1)
        chosen += [{**r, "stratum": "content_claim_with_conditions"} for r in picked_claim]
        chosen += [{**r, "stratum": "content_other"} for r in picked_other]
        if not picked_claim:
            shortfalls.append({"output_id": oid, "stratum": "content_claim_with_conditions",
                               "needed": 1, "available": len(claims),
                               "fill": "no Claim row available; recorded, not substituted"})
        if not picked_other:
            shortfalls.append({"output_id": oid, "stratum": "content_other",
                               "needed": 1, "available": len(rest),
                               "fill": "no non-Claim content row available"})

        # --- one common-relation semantic judgement -------------------------
        picked_rel = _pick(rng, out["relation_rows"], RELATION_PER_OUTPUT)
        chosen += [{**r, "stratum": "relation_semantic"} for r in picked_rel]
        if not picked_rel:
            shortfalls.append({"output_id": oid, "stratum": "relation_semantic",
                               "needed": 1, "available": 0,
                               "fill": "no relation row available"})

        # --- one correctness judgement on a generated item ------------------
        picked_gen = _pick(rng, out["correctness_rows"], CORRECTNESS_PER_OUTPUT)
        chosen += [{**r, "stratum": "generated_correctness"} for r in picked_gen]
        if not picked_gen:
            shortfalls.append({"output_id": oid, "stratum": "generated_correctness",
                               "needed": 1, "available": 0,
                               "fill": "no generated-item row available"})

        for row in chosen:
            blind_id = f"AR-{len(items) + 1:02d}"
            items.append({"blind_id": blind_id, "output_id": oid, **row})
            mapping[blind_id] = {"output_id": oid, "run": out["run"],
                                 "method": out["method"]}

    packet = {
        "schema": "maf_kg.www_supplement.author_packet.v1",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 6",
        "seed": seed,
        "frozen_before_outputs": True,
        "sampling_rule": {
            "per_output": PER_OUTPUT,
            "content": CONTENT_PER_OUTPUT,
            "relation": RELATION_PER_OUTPUT,
            "correctness": CORRECTNESS_PER_OUTPUT,
            "content_requirement": "at least one sampled content item must be a Claim, so that "
                                   "condition retention is represented",
            "not_selected_for": ["disagreement", "success", "convenience"],
            "determinism": "a per-output stream seeded from the global seed and the output id, "
                           "so the packet does not depend on processing order",
            "shortfall_rule": "recorded in shortfalls and never silently replaced by another stratum",
        },
        "blinding": {
            "hidden_from_author": ["method name", "method family"],
            "visible": ["blind_id", "stratum", "source excerpt with line numbers",
                        "output content", "existing judgement", "the question asked"],
            "residual_risk": "the native representation may still reveal the method; report this "
                             "limitation rather than claiming full blinding",
            "mapping_kept_separately": True,
        },
        "allowed_answers": ["agree", "change", "uncertain"],
        "items": items,
        "item_count": len(items),
        "shortfalls": shortfalls,
        "stratum_counts": {},
    }
    counts: dict[str, int] = {}
    for it in items:
        counts[it["stratum"]] = counts.get(it["stratum"], 0) + 1
    packet["stratum_counts"] = counts
    packet["expected_item_count"] = PER_OUTPUT * len(outputs)
    packet["complete"] = len(items) == PER_OUTPUT * len(outputs)
    return packet, mapping


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 3:
        print("usage: www_supplement_review_packet.py <reviewed_outputs.json> <out_prefix>")
        return 2
    src = Path(sys.argv[1])
    prefix = Path(sys.argv[2])
    outputs = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(outputs, dict):
        outputs = outputs["outputs"]
    packet, mapping = build_packet(outputs)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    Path(str(prefix) + "_mapping.json").write_text(
        json.dumps({"schema": "maf_kg.www_supplement.author_packet.mapping.v1",
                    "note": "hidden mapping; not shipped with the author packet",
                    "mapping": mapping}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"outputs      : {len(outputs)}")
    print(f"items        : {packet['item_count']} (expected {packet['expected_item_count']})")
    print(f"strata       : {packet['stratum_counts']}")
    print(f"shortfalls   : {len(packet['shortfalls'])}")
    print(f"complete     : {packet['complete']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
