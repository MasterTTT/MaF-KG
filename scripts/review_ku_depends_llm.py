#!/usr/bin/env python
"""LLM semantic review of KU depends_on candidates (P2 step).

Loads the deterministic candidate JSON from build_ku_depends_candidates.py,
asks the judge model to accept/reject each candidate as a genuine prerequisite
dependency between teaching objectives, then merges:

  * every baseline edge (see --baseline, usually the deterministic v3 file) -
    kept unconditionally so a validated edge cannot regress silently, and
  * every candidate the judge accepted.

Every merged edge carries audit fields (verdict, strength, reason, evidence
edges, optional chain paths, original derived_from ids) while keeping the
Neo4j-compatible depends_on shape (source_ku/target_ku/count/derived_from_edges).

Usage:
  python scripts/review_ku_depends_llm.py <candidates.json> <ku.json> --out <verdicts.json>
      [--baseline v3-depends.json] [--merged-out final.json] [--model ...]
      [--prompt prompts/ku_depends_review.txt] [--batch-size 15] [--dry]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "maf-kg.knowledge-unit.v1.depends_on.review"
JUDGE_ENDPOINTS = (
    ("https://opencode.ai/zen/go/v1/chat/completions", "OPENCODE_API_KEY"),
    ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
)
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_PROMPT = "prompts/ku_depends_review.txt"


def _llm_json(system: str, user: str, model: str) -> tuple[dict, dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    errors: list[str] = []
    for url, key_env in JUDGE_ENDPOINTS:
        api_key = (os.getenv(key_env) or "").strip()  # tolerate CRLF-tainted env files
        if not api_key:
            errors.append(f"{key_env} not set")
            continue
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            usage = data.get("usage", {})
            return json.loads(data["choices"][0]["message"]["content"]), usage
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
            errors.append(f"{url}: {exc}")
            continue
    raise SystemExit("LLM review failed on all endpoints: " + "; ".join(errors))


def format_candidate(idx: int, c: dict, ku_by_id: dict[str, dict]) -> str:
    def centers(u: dict) -> str:
        names = [m["name"] for m in u.get("centers_on", [])]
        return "、".join(names) if names else "（无）"

    src = ku_by_id[c["source_ku"]]
    tgt = ku_by_id[c["target_ku"]]
    lines = [f"[{idx}] {c['source_name']} depends_on {c['target_name']}",
             f"    源中心: {centers(src)}",
             f"    目标中心: {centers(tgt)}"]
    if c.get("signals"):
        for sig in c["signals"]:
            details = "；".join(
                f"{e['source_name']}→{e['target_name']}" for e in sig["edges"][:4])
            more = f" 等{sig['count']}条" if sig["count"] > 4 else ""
            lines.append(f"    证据 {sig['type']} ×{sig['count']}: {details}{more}")
    for ch in c.get("chain_via", [])[:2]:
        lines.append("    链: %s -[%s]-> %s -[%s]-> %s" % (
            ch["hop1_names"][0], c.get("signals") and "定义/推导" or "构成", ch["mid_name"],
            "定义/推导", ch["hop2_names"][1]))
    return "\n".join(lines)


def build_user_prompt(batch: list[tuple[int, dict]], ku_by_id: dict[str, dict]) -> str:
    body = "\n\n".join(format_candidate(i, c, ku_by_id) for i, c in batch)
    return (
        "请审核以下候选 depends_on 边。对每个 index 输出 accept / strength / reason。\n\n"
        + body
        + "\n\n只输出 JSON：{\"verdicts\":[{\"index\":N,\"accept\":true|false,"
          "\"strength\":\"strong\"|\"weak\",\"reason\":\"...\"}]}，覆盖所有 index。"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="LLM semantic review of KU depends_on candidates.")
    ap.add_argument("candidates", help="Candidate JSON from build_ku_depends_candidates.py.")
    ap.add_argument("ku", help="KnowledgeUnit JSON (v6).")
    ap.add_argument("--out", required=True, help="Output verdicts JSON.")
    ap.add_argument("--baseline", help="Optional baseline depends_on JSON (e.g. v3).")
    ap.add_argument("--merged-out", help="Optional merged depends_on JSON (Neo4j-compatible).")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="System prompt file.")
    ap.add_argument("--batch-size", type=int, default=15)
    ap.add_argument("--dry", action="store_true", help="Print prompts and exit without calling the LLM.")
    args = ap.parse_args()

    cand_data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    ku = json.loads(Path(args.ku).read_text(encoding="utf-8"))
    ku_by_id = {u["id"]: u for u in ku["knowledge_units"]}
    candidates = cand_data["candidates"]
    system = Path(args.prompt).read_text(encoding="utf-8")
    prompt_sha = hashlib.sha256(system.encode()).hexdigest()

    batches = [(bi * args.batch_size, candidates[bi * args.batch_size:(bi + 1) * args.batch_size])
               for bi in range(0, (len(candidates) + args.batch_size - 1) // args.batch_size)]
    if args.dry:
        for offset, b in batches:
            print("=" * 30, f"batch of {len(b)} (offset {offset})")
            print(build_user_prompt([(offset + i, c) for i, c in enumerate(b)], ku_by_id))
            print()
        print(f"would send {len(batches)} requests for {len(candidates)} candidates")
        return 0

    verdicts: list[dict[str, Any]] = []
    usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    request_count = 0
    for bi, (offset, b) in enumerate(batches):
        indexed = [(offset + i, c) for i, c in enumerate(b)]
        user = build_user_prompt(indexed, ku_by_id)
        resp, usage = _llm_json(system, user, args.model)
        request_count += 1
        for k in usage_totals:
            usage_totals[k] += int(usage.get(k, 0))
        got = {v["index"]: v for v in resp.get("verdicts", [])}
        for i, c in indexed:
            v = got.get(i)
            if v is None:
                print(f"FAIL: batch {bi} missed index {i}", file=sys.stderr)
                return 1
            verdicts.append({"index": i, "source_ku": c["source_ku"], "target_ku": c["target_ku"],
                             "source_name": c["source_name"], "target_name": c["target_name"],
                             "accept": bool(v.get("accept")), "strength": v.get("strength", ""),
                             "reason": v.get("reason", "")})
        print(f"batch {bi + 1}/{len(batches)} done: "
              f"{sum(1 for v in verdicts if v['accept'])} accepted so far")

    # merge: baseline (unconditional) + accepted candidates
    baseline: list[dict[str, Any]] = []
    if args.baseline:
        base_data = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        baseline = [{"source_ku": e["source_ku"], "target_ku": e["target_ku"],
                     "count": e.get("count", 1),
                     "derived_from_edges": e.get("derived_from_edges", [])}
                    for e in base_data.get("depends_on", [])]

    cand_by_idx = {i: c for i, c in enumerate(candidates)}
    accepted = [v for v in verdicts if v["accept"]]
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for e in baseline:
        merged[(e["source_ku"], e["target_ku"])] = {
            "source_ku": e["source_ku"], "target_ku": e["target_ku"],
            "source_name": ku_by_id[e["source_ku"]]["name"],
            "target_name": ku_by_id[e["target_ku"]]["name"],
            "count": e["count"], "derived_from_edges": e["derived_from_edges"],
            "audit": {"baseline": True, "reviewed": False},
        }
    for v in accepted:
        c = cand_by_idx[v["index"]]
        key = (c["source_ku"], c["target_ku"])
        derived = [e["source_id"] + "->" + e["target_id"]
                   for s in c.get("signals", []) if s["type"] == "derived_from"
                   for e in s["edges"]]
        entry = {
            "source_ku": c["source_ku"], "target_ku": c["target_ku"],
            "source_name": c["source_name"], "target_name": c["target_name"],
            "count": c.get("n_edges", len(derived)),
            "derived_from_edges": derived or None,
            "audit": {
                "baseline": False, "reviewed": True,
                "strength": v["strength"], "judge_reason": v["reason"],
                "evidence": [{"type": s["type"], "count": s["count"],
                              "edges": s["edges"]} for s in c.get("signals", [])],
                "chain_via": c.get("chain_via", []),
            },
        }
        if key in merged:  # baseline pair also accepted by judge -> annotate
            merged[key]["audit"]["reviewed"] = True
            merged[key]["audit"]["strength"] = v["strength"]
            merged[key]["audit"]["judge_reason"] = v["reason"]
            merged[key]["audit"]["evidence"] = [
                {"type": s["type"], "count": s["count"], "edges": s["edges"]}
                for s in c.get("signals", [])]
        else:
            merged[key] = entry

    rejected = [v for v in verdicts if not v["accept"]]
    baseline_flags = [
        {"source": v["source_name"], "target": v["target_name"], "reason": v["reason"]}
        for v in rejected if (v["source_ku"], v["target_ku"]) in
        {(e["source_ku"], e["target_ku"]) for e in baseline}
    ]

    out = {
        "schema_version": SCHEMA,
        "candidates": candidates,
        "verdicts": verdicts,
        "merged_depends_on": [merged[k] for k in sorted(merged)],
        "diagnostics": {
            "total_candidates": len(candidates),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "baseline_edges": len(baseline),
            "merged_edges": len(merged),
            "baseline_rejected_by_judge": baseline_flags,
        },
        "run": {
            "model": args.model,
            "prompt_sha256": prompt_sha,
            "endpoints": [url for url, _ in JUDGE_ENDPOINTS],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_count": request_count,
            "usage_totals": usage_totals,
        },
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdicts: {len(verdicts)}  accepted={len(accepted)} rejected={len(rejected)}")
    print(f"baseline edges: {len(baseline)}  merged: {len(merged)}")
    for f in baseline_flags:
        print(f"  NOTE baseline rejected by judge: {f['source']} -> {f['target']}: {f['reason']}")

    if args.merged_out:
        final = {
            "schema_version": "maf-kg.knowledge-unit.v1.depends_on",
            "knowledge_units": [{"id": u["id"], "name": u["name"]} for u in ku["knowledge_units"]],
            "depends_on": [
                {k: e[k] for k in ("source_ku", "target_ku", "count", "derived_from_edges")
                 if k in e and e[k] is not None} for e in merged.values()
            ],
            "audit": {
                "reviewed_edges": [
                    {"source_ku": k[0], "target_ku": k[1], **v} for k, v in merged.items()
                ],
                "verdicts_json": str(Path(args.out).resolve()),
                "note": "merged = baseline (unconditional) + judge-accepted candidates",
            },
            "run": {
                "model": args.model,
                "prompt_sha256": prompt_sha,
                "endpoints": [url for url, _ in JUDGE_ENDPOINTS],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_count": request_count,
                "usage_totals": usage_totals,
            },
        }
        Path(args.merged_out).write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote -> {args.merged_out}")
    print(f"wrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())