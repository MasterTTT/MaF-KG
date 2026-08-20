#!/usr/bin/env python
"""Graph Verifier — deterministic lint (phase 1) + optional LLM judgment (phase 2).

The verifier is intentionally split in two layers:

  * Phase 1 (this file, no LLM): a set of small, deterministic rules scan the
    graph for *explicitly suspicious* items (isolated nodes, small components,
    Methods without uses, name->type drift, missing definition dependencies,
    example-specific formulas). Each rule is a plain function returning hits.
  * Phase 2 (optional --judge, not implemented here): batch all hits into one
    LLM call that emits a conservative fix action per hit
    (retag_type / add_relation / drop / merge / keep).

The rules are pluggable: register a (name, fn) pair in RULES. New chapters or
domains add/remove rules without touching the engine or the verifier prompt.

Usage:
  python scripts/verify_graph.py <kg.json> [--seed <seed.json>] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Hit: one suspicious item found by a rule.
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    rule: str
    name: str
    node_type: str
    node_id: str
    severity: str            # high / medium / low
    reason: str
    suggested_action: str    # link / retag_type / add_relation / drop / review
    is_seed: bool = False
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Graph wrapper: lazy, read-only view over a KG JSON.
# ---------------------------------------------------------------------------

class Graph:
    def __init__(self, data: dict, seed_path: str | None = None):
        self.nodes = data.get("nodes", [])
        self.relations = data.get("relations", [])
        self.by_id = {n["id"]: n for n in self.nodes}
        self.seed_ids: set[str] = set()
        if seed_path:
            seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
            self.seed_ids = {n["id"] for n in seed.get("nodes", [])}
        self.deg = Counter()
        for r in self.relations:
            self.deg[r["source_id"]] += 1
            self.deg[r["target_id"]] += 1
        self.components = self._components()

    def _components(self) -> list[list[str]]:
        par = {n["id"]: n["id"] for n in self.nodes}

        def find(x: str) -> str:
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        for r in self.relations:
            a, b = r["source_id"], r["target_id"]
            if a in par and b in par:
                ra, rb = find(a), find(b)
                if ra != rb:
                    par[rb] = ra
        groups: dict[str, list[str]] = defaultdict(list)
        for n in self.nodes:
            groups[find(n["id"])].append(n["id"])
        return list(groups.values())

    def is_seed(self, node_id: str) -> bool:
        return node_id in self.seed_ids

    def outgoing(self, node_id: str) -> list[dict]:
        return [r for r in self.relations if r["source_id"] == node_id]

    def name(self, node_id: str) -> str:
        n = self.by_id.get(node_id)
        return n["name"] if n else node_id


# ---------------------------------------------------------------------------
# Rules. Each fn(graph) -> list[Hit]. No LLM, no I/O.
# ---------------------------------------------------------------------------

def rule_isolated(g: Graph) -> list[Hit]:
    hits = []
    for n in g.nodes:
        if g.deg[n["id"]] == 0:
            hits.append(Hit(
                "isolated_node", n["name"], n["type"], n["id"], "high",
                "degree == 0 (no relation at all)",
                "link_or_drop", g.is_seed(n["id"]),
            ))
    return hits


def rule_small_component(g: Graph, min_size: int = 5) -> list[Hit]:
    hits = []
    main = max(g.components, key=len, default=[])
    for comp in g.components:
        if comp is main:
            continue
        if len(comp) < min_size:
            for nid in comp:
                n = g.by_id[nid]
                hits.append(Hit(
                    "small_component", n["name"], n["type"], nid, "medium",
                    f"in a {len(comp)}-node disconnected component",
                    "link", g.is_seed(nid), {"component_size": len(comp)},
                ))
    return hits


def rule_method_without_uses(g: Graph) -> list[Hit]:
    hits = []
    for n in g.nodes:
        if n["type"] != "Method":
            continue
        if not any(r["type"] in ("uses", "establishes") for r in g.outgoing(n["id"])):
            hits.append(Hit(
                "method_without_uses", n["name"], n["type"], n["id"], "medium",
                "Method has no uses/establishes edge",
                "add_uses", g.is_seed(n["id"]),
            ))
    return hits


def rule_claim_without_involves(g: Graph) -> list[Hit]:
    hits = []
    for n in g.nodes:
        if n["type"] != "Claim":
            continue
        outs = g.outgoing(n["id"])
        if any(r["type"] == "involves" for r in outs):
            continue
        basis = [g.name(r["target_id"]) for r in outs if r["type"] == "derived_from"]
        reason = "Claim has no involves anchor"
        if basis:
            reason += "; derived_from " + "、".join(basis) + "（继承的 involves 可能是有意省略）"
        hits.append(Hit(
            "claim_without_involves", n["name"], n["type"], n["id"], "low",
            reason, "add_involves", g.is_seed(n["id"]),
            {"derived_from_basis": basis},
        ))
    return hits


_VERB_PATTERN = re.compile(r"(显化|求解|求导|计算|推导)$")
_PROP_PATTERN = re.compile(r"不变性")


def rule_type_drift(g: Graph) -> list[Hit]:
    hits = []
    for n in g.nodes:
        if n["type"] != "Concept":
            continue
        nm = n["name"]
        if _VERB_PATTERN.search(nm):
            hits.append(Hit(
                "type_drift", nm, n["type"], n["id"], "medium",
                "name suggests a procedure (Method), not a Concept",
                "retag_type:Method", g.is_seed(n["id"]),
            ))
        if _PROP_PATTERN.search(nm):
            hits.append(Hit(
                "type_drift", nm, n["type"], n["id"], "medium",
                "name suggests a property statement (Claim), not a Concept",
                "retag_type:Claim/property", g.is_seed(n["id"]),
            ))
    return hits


def rule_definition_missing_limit(g: Graph) -> list[Hit]:
    hits = []
    for n in g.nodes:
        if n["type"] != "Concept":
            continue
        desc = n.get("description", "")
        if "极限" not in desc and "lim" not in desc.lower():
            continue
        targets = [
            g.name(r["target_id"])
            for r in g.outgoing(n["id"])
            if r["type"] == "defined_in_terms_of"
        ]
        if not any(("极限" in t) or ("lim" in t.lower()) for t in targets):
            hits.append(Hit(
                "definition_missing_limit", n["name"], n["type"], n["id"], "high",
                "definition uses limit but has no defined_in_terms_of -> limit concept",
                "add_relation:defined_in_terms_of->极限", g.is_seed(n["id"]),
            ))
    return hits


_SPECIFIC_FORMULA = re.compile(r"(\+C|C 的|sin|cos|tan|ω|1/)")
def rule_example_specific(g: Graph) -> list[Hit]:
    hits = []
    for n in g.nodes:
        if n["type"] != "Claim":
            continue
        roles = {ev.get("discourse_role") for ev in n.get("evidence", [])}
        if "example" not in roles:
            continue
        if _SPECIFIC_FORMULA.search(n["name"]):
            hits.append(Hit(
                "example_specific_formula", n["name"], n["type"], n["id"], "low",
                "example-evidenced claim with a specific-formula name",
                "drop_or_review", g.is_seed(n["id"]),
            ))
    return hits


RULES: list[tuple[str, Any]] = [
    ("isolated_node", rule_isolated),
    ("small_component", rule_small_component),
    ("method_without_uses", rule_method_without_uses),
    ("claim_without_involves", rule_claim_without_involves),
    ("type_drift", rule_type_drift),
    ("definition_missing_limit", rule_definition_missing_limit),
    ("example_specific_formula", rule_example_specific),
]


# ---------------------------------------------------------------------------
# Phase 2: batched LLM judgment (--judge). One call for all candidates.
# ---------------------------------------------------------------------------

JUDGE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("https://opencode.ai/zen/go/v1/chat/completions", "OPENCODE_API_KEY"),
    ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
)
JUDGE_MODEL = "deepseek-v4-flash"

_JUDGE_SYSTEM = (
    "你是高等数学知识图谱的修复判定器。下面给出一批被确定性规则标记为可疑的节点"
    "（孤立节点、类型漂移、缺定义依赖、Method 无 uses、例题局部公式等）。对每个节点"
    "独立判断该标记是否成立，并给出保守的修复动作。动作四选一：\n"
    "- keep：标记不成立或不应改（默认倾向，拿不准就 keep）\n"
    "- retag_type：类型标错，detail 写明正确类型（Concept/Claim/Method；若为 Claim 再写 claim_kind）\n"
    "- add_relation：缺一条关系，detail 写明关系类型（defined_in_terms_of/involves/uses/"
    "special_case_of/derived_from/establishes）和目标概念名\n"
    "- drop：该节点是纯记号或例题局部内容，应删除\n"
    "不要臆造关系；只有当描述明确支撑时才 add_relation 或 retag_type。\n"
    "注意：若某 Claim 已有 derived_from 指向基结论，其 involves 可能经基结论继承；此时只补该 Claim "
    "自身陈述所必需的核心概念锚，不要重复添加基结论已有的继承锚。\n"
    '只输出 JSON：{"decisions":[{"node_id":"...","name":"...","verdict":"keep|retag_type|add_relation|drop",'
    '"detail":"...","reason":"一句话"}]}'
)


def _llm_json(system: str, user: str) -> dict:
    import os
    import urllib.error
    import urllib.request

    payload = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    errors: list[str] = []
    for url, key_env in JUDGE_ENDPOINTS:
        api_key = os.getenv(key_env)
        if not api_key:
            errors.append(f"{key_env} not set")
            continue
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
            errors.append(f"{url}: {exc}")
            continue
    raise SystemExit("LLM judge failed on all endpoints: " + "; ".join(errors))


def judge(g: Graph, hits: list[Hit]) -> list[dict]:
    """Batch all hits into one LLM call; return per-node decisions."""
    by_node: dict[str, list[Hit]] = defaultdict(list)
    for h in hits:
        by_node[h.node_id].append(h)

    concepts = [n["name"] for n in g.nodes if n["type"] == "Concept"]
    methods = [n["name"] for n in g.nodes if n["type"] == "Method"]

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    order = []
    for node_id, hs in by_node.items():
        n = g.by_id[node_id]
        rels = [(r["type"], g.name(r["target_id"])) for r in g.outgoing(node_id)]
        reasons = "；".join(sorted({h.reason for h in hs}))
        actions = "；".join(sorted({h.suggested_action for h in hs}))
        sev = min((sev_rank.get(h.severity, 2) for h in hs))
        order.append((sev, node_id, n["name"], n["type"], n.get("description", "") or "", rels, reasons, actions))
    order.sort(key=lambda x: (x[0], x[1]))

    lines = []
    for sev, node_id, name, ntype, desc, rels, reasons, actions in order:
        lines.append(
            f"ID: {node_id}\n名称: {name}（类型 {ntype}）\n描述: {desc[:220]}\n"
            f"当前出边: {rels if rels else '（无）'}\n规则标记: {reasons}\n建议动作: {actions}"
        )
    user = (
        "图内 Concept 清单：" + "、".join(concepts) + "\n"
        "图内 Method 清单：" + "、".join(methods) + "\n\n"
        "待判定的可疑节点如下：\n\n" + "\n\n".join(lines)
    )
    resp = _llm_json(_JUDGE_SYSTEM, user)
    return resp.get("decisions", [])


def resolve_target(g: Graph, name: str) -> str | None:
    """Resolve a target concept name to a node id.

    Exact match first; then substring only when UNIQUE. An ambiguous substring
    returns None so the caller skips instead of picking an arbitrary node.
    """
    for n in g.nodes:
        if n["name"] == name:
            return n["id"]
    hits = [n["id"] for n in g.nodes if name in n["name"]]
    if len(hits) == 1:
        return hits[0]
    return None


def _select_evidence(src_node: dict, rel_type: str) -> list[dict]:
    evs = src_node.get("evidence", []) or []
    if rel_type == "defined_in_terms_of":
        defn = [e for e in evs if e.get("discourse_role") == "definition"]
        return list((defn or evs)[:1])
    return list(evs[:1])


def _source_chapter(src_node: dict) -> str:
    for ev in src_node.get("evidence", []):
        sp = ev.get("source_path", "")
        if "第一章" in sp:
            return "第一章"
        if "第二章" in sp:
            return "第二章"
    return "未知"


def _recompute_audit(data: dict) -> None:
    nodes = data.get("nodes", [])
    relations = data.get("relations", [])
    deg = Counter()
    for r in relations:
        deg[r["source_id"]] += 1
        deg[r["target_id"]] += 1
    iso = [n["id"] for n in nodes if deg[n["id"]] == 0]
    dg = data.setdefault("diagnostics", {})
    audit = dg.setdefault("audit", {})
    audit["isolated_node_count"] = len(iso)
    audit["isolated_node_ids"] = iso
    dg["node_count"] = len(nodes)
    dg["relation_count"] = len(relations)
    dg["relation_type_counts"] = dict(Counter(r["type"] for r in relations))
    dg["post_repair"] = True


def apply_decisions(data: dict, g: Graph, decisions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply add_relation decisions in place; retag_type/drop are only logged.

    Returns (applied, skipped). ``data`` is the full graph JSON, mutated; the
    caller writes it out after attaching the verifier_run record.
    """
    import secrets

    relations = data.setdefault("relations", [])
    existing = {(r["source_id"], r["type"], r["target_id"]) for r in relations}
    applied: list[dict] = []
    skipped: list[dict] = []

    for dec in decisions:
        verdict = dec.get("verdict", "keep")
        node_id = dec.get("node_id", "")
        name = dec.get("name", "")
        detail = dec.get("detail", "")
        if verdict == "add_relation":
            # Accept both "rel_type -> target" and "rel_type: target" detail
            # forms (and an optional "add_relation:" prefix) — the judge prompt
            # asks for the colon form, the parser previously only read arrows,
            # which silently dropped every decision.
            detail_stripped = detail.strip()
            if "->" in detail_stripped:
                rel_type, target_name = (part.strip() for part in detail_stripped.split("->", 1))
            else:
                match = re.match(r"^(?:add_relation\s*[:：]\s*)?([a-z_]+)\s*[:：]\s*(.+)$", detail_stripped)
                if not match:
                    skipped.append({"node_id": node_id, "name": name, "verdict": verdict,
                                    "detail": detail, "reason": "detail not parseable (rel_type: target)"})
                    continue
                rel_type, target_name = match.group(1).strip(), match.group(2).strip()
            target_id = resolve_target(g, target_name)
            if target_id is None:
                skipped.append({"node_id": node_id, "name": name, "verdict": verdict,
                                "detail": detail, "reason": f"target '{target_name}' not resolved"})
                continue
            if (node_id, rel_type, target_id) in existing:
                skipped.append({"node_id": node_id, "name": name, "verdict": verdict,
                                "detail": detail, "reason": "relation already exists"})
                continue
            src_node = g.by_id.get(node_id) or {}
            evidence = _select_evidence(src_node, rel_type)
            chapter = _source_chapter(src_node)
            relations.append({
                "id": "relation_" + secrets.token_hex(6),
                "type": rel_type,
                "source_id": node_id,
                "target_id": target_id,
                "evidence": evidence,
                "synthesized": True,
            })
            existing.add((node_id, rel_type, target_id))
            applied.append({
                "node_id": node_id, "name": name,
                "relation": f"{rel_type}->{target_name}",
                "target_id": target_id, "chapter": chapter,
            })
        elif verdict in ("retag_type", "drop"):
            skipped.append({"node_id": node_id, "name": name, "verdict": verdict,
                            "detail": detail, "reason": "manual confirm required"})
        # keep -> no-op

    _recompute_audit(data)
    return applied, skipped


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Deterministic lint over a semantic KG.")
    ap.add_argument("input", help="KG JSON file to lint.")
    ap.add_argument("--seed", help="Optional seed graph JSON; its node ids are marked is_seed.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ap.add_argument("--only-new", action="store_true", help="Only report hits on non-seed nodes.")
    ap.add_argument("--judge", action="store_true", help="Batch all hits into one LLM call for judgment.")
    ap.add_argument("--apply", metavar="OUT.json", help="Apply add_relation decisions and write the repaired graph to OUT.json (implies --judge).")
    args = ap.parse_args()

    path = Path(args.input)
    data = json.loads(path.read_text(encoding="utf-8"))
    g = Graph(data, args.seed)

    hits: list[Hit] = []
    for rule_name, fn in RULES:
        for hit in fn(g):
            if args.only_new and hit.is_seed:
                continue
            hits.append(hit)

    by_rule = Counter(h.rule for h in hits)
    by_severity = Counter(h.severity for h in hits)
    new_hits = [h for h in hits if not h.is_seed]

    if args.json:
        payload = {
            "input": str(path),
            "total_nodes": len(g.nodes),
            "total_relations": len(g.relations),
            "total_hits": len(hits),
            "new_hits": len(new_hits),
            "by_rule": dict(by_rule),
            "by_severity": dict(by_severity),
            "hits": [asdict(h) for h in hits],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Graph: {len(g.nodes)} nodes / {len(g.relations)} relations")
    print(f"Seed ids: {len(g.seed_ids)}  |  total hits: {len(hits)}  |  new-only hits: {len(new_hits)}")
    print()
    print("Hits by rule:")
    for rule_name, _ in RULES:
        c = by_rule.get(rule_name, 0)
        if c:
            print(f"  {rule_name:<28} {c}")
    print()
    print("Hits by severity:")
    for sev in ("high", "medium", "low"):
        c = by_severity.get(sev, 0)
        if c:
            print(f"  {sev:<8} {c}")
    print()
    print("--- detail (new nodes first) ---")
    for h in sorted(hits, key=lambda x: (x.is_seed, x.severity, x.rule)):
        tag = "seed" if h.is_seed else "NEW "
        print(f"[{h.severity:<6}] [{tag}] {h.rule:<26} {h.node_type:<7} {h.name}")
        print(f"           -> {h.reason}")
        print(f"           -> action: {h.suggested_action}")

    decisions: list[dict] = []
    if (args.judge or args.apply) and hits:
        print()
        print("--- LLM judgment (one batched call) ---")
        decisions = judge(g, hits)
        if not decisions:
            print("  (no decisions returned)")
        else:
            for dec in decisions:
                verdict = dec.get("verdict", "keep")
                name = dec.get("name", "?")
                detail = dec.get("detail", "")
                reason = dec.get("reason", "")
                print(f"  [{verdict:<12}] {name}")
                if detail:
                    print(f"               detail: {detail}")
                if reason:
                    print(f"               reason: {reason}")

    if args.apply and decisions:
        print()
        print("--- applying add_relation decisions ---")
        applied, skipped = apply_decisions(data, g, decisions)
        print(f"applied: {len(applied)}  |  skipped (manual): {len(skipped)}")
        for a in applied:
            print(f"  [applied ] {a['name']}  ->  {a['relation']}  ({a['chapter']})")
        for s in skipped:
            print(f"  [skipped ] {s['name']}  ({s['reason']})")

        import datetime
        verifier_run = {
            "model": JUDGE_MODEL,
            "endpoints": [url for url, _ in JUDGE_ENDPOINTS],
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "candidates": [asdict(h) for h in hits],
            "decisions": decisions,
            "applied": applied,
            "skipped": skipped,
        }
        data.setdefault("diagnostics", {})["verifier_run"] = verifier_run
        Path(args.apply).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote repaired graph -> {args.apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
