#!/usr/bin/env python
"""Layer-2 KnowledgeUnit organizer (LLM, per v1 design).

Single-agent KnowledgeUnitOrganizer: process one section at a time, feed the
section's layer-1 nodes + heading hierarchy to the LLM, which outputs the
KnowledgeUnits (central teaching objectives) with centers_on/contains.

Heading is only a *candidate* boundary; the organizer may merge sections,
skip pure-structural headings, or split a coarse subsection.

Engineering:
  * stable KU ids: content-hash (sha1 of section + name), not sequential.
  * model / source files / prompt are CLI-configurable (no hardcode).
  * output records run metadata (model, prompt sha, config, token usage).

Usage:
  python scripts/build_knowledge_units.py <kg.json> --out <ku.json>
      [--model deepseek-v4-flash] [--src-files a.md,b.md] [--prompt file.txt]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "maf-kg.knowledge-unit.v1"

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SRC_FILES = [
    "E:/MaF-KG/data/raw/第一章前五节_合并_无习题.md",
    "E:/MaF-KG/data/raw/第一章后五节_合并_无习题.md",
]
JUDGE_ENDPOINTS = (
    ("https://opencode.ai/zen/go/v1/chat/completions", "OPENCODE_API_KEY"),
    ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
)

DEFAULT_SYSTEM_PROMPT = (
    "你是高等数学知识图谱第二层（KnowledgeUnit）的组织器。\n"
    "第二层不抽新语义，只把第一层 Concept/Claim/Method 按教材的『中心教学目标』组织成知识单元。\n"
    "规则：\n"
    "- 一个知识单元 = 一个可教学、可复习的中心教学目标（如『数列极限的定义』『收敛数列的性质』）。\n"
    "- 标题只是候选边界，不是硬边界：你可以合并多个小节、跳过纯结构标题、或把一个粗小节拆成多个单元。\n"
    "- 不要把两个不同的中心教学目标合并成一个单元（例如『单调有界准则』与『自然常数 e / 第二个重要极限』应拆成两个单元）。\n"
    "- 每个单元选 centers_on：直接定义该单元中心目标的节点（定义性 Concept 或核心定理 Claim），选 1–3 个，宁缺毋滥。\n"
    "- 其余支撑节点放 contains：该单元明确教授的支撑内容（性质、推论、方法、例子里的一般结论）。\n"
    "- 一个节点可被多个单元 contains，但最多在一个单元做 centers_on。\n"
    "- 只引用给出的节点别名（n1、n2…），不要新造节点，不要用『其他』单元：每个节点都要归入有明确教学目标的具体单元。\n"
    '只输出 JSON：{"units":[{"name":"...","objective":"一句话中心教学目标","centers_on":["n2"],"contains":["n1","n5"]}]}'
)


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
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            usage = data.get("usage", {})
            return json.loads(data["choices"][0]["message"]["content"]), usage
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
            errors.append(f"{url}: {exc}")
            continue
    raise SystemExit("LLM organizer failed on all endpoints: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# outline: section -> {file, start, end, subs[]}
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^#\s*第[一二三四五六七八九十]+节")
_SUB_RE = re.compile(r"^#\s*[一二三四五六七八九十]+、")


def build_outline(src_files: list[str]) -> dict[str, dict]:
    outline: dict[str, dict] = {}
    for path in src_files:
        lines = Path(path).read_text(encoding="utf-8").split("\n")
        base = Path(path).name
        cur: dict | None = None
        for i, line in enumerate(lines, start=1):
            if _SECTION_RE.match(line):
                if cur is not None:
                    cur["end"] = i - 1
                name = re.sub(r"^#\s*", "", line.strip())
                cur = {"file": base, "start": i, "end": len(lines), "subs": []}
                outline[name] = cur
            elif _SUB_RE.match(line) and cur is not None:
                cur["subs"].append(re.sub(r"^#\s*", "", line.strip()))
        if cur is not None:
            cur["end"] = len(lines)
    return outline


# ---------------------------------------------------------------------------
# section attribution (same-chapter evidence preference, line fallback)
# ---------------------------------------------------------------------------

def heading_section(node: dict, chapter: str) -> str | None:
    """Pick the section from evidence heading_path, preferring same-chapter evidence.

    A node may carry evidence from several files (e.g. a concept re-used by a
    later chapter). Blindly taking the *first* evidence puts the node into a
    phantom section of another chapter (observed bug: 函数的增量 landed in a
    one-node "第一节 导数概念" bucket). Prefer evidence whose heading_path[0]
    starts with the chapter being processed; fall back to first usable evidence.
    """
    usable: list[str] = []
    for ev in node.get("evidence", []):
        hp = ev.get("heading_path", [])
        if len(hp) > 1:
            if hp[0] and chapter and hp[0].startswith(chapter):
                return hp[1]
            usable.append(hp[1])
    return usable[0] if usable else None


def section_from_line(node: dict, outline: dict[str, dict]) -> str | None:
    for ev in node.get("evidence", []):
        base = Path(ev.get("source_path", "")).name
        sl = ev.get("start_line")
        if base and sl is not None:
            for name, o in outline.items():
                if o["file"] == base and o["start"] <= sl <= o["end"]:
                    return name
    return None


def section_of(node: dict, outline: dict[str, dict], chapter: str) -> str:
    return heading_section(node, chapter) or section_from_line(node, outline) or "(未定位)"


# ---------------------------------------------------------------------------
# organizer
# ---------------------------------------------------------------------------

def stable_ku_id(section: str, name: str) -> str:
    h = hashlib.sha1(f"{section}|{name}".encode()).hexdigest()[:12]
    return f"ku_{h}"


def organize_section(section_name: str, nodes: list[dict], relations: list[dict],
                     subs: list[str], system: str, model: str) -> tuple[list[dict], dict]:
    ids = [n["id"] for n in nodes]
    alias = {full: f"n{i+1}" for i, full in enumerate(ids)}
    alias_to_id = {a: f for f, a in alias.items()}

    lines = []
    for i, n in enumerate(nodes):
        a = alias[n["id"]]
        desc = (n.get("description", "") or "").replace("\n", " ")[:70]
        rels = [
            r["type"] + "->" + alias.get(r["target_id"], "?")
            for r in relations if r["source_id"] == n["id"] and r["target_id"] in alias
        ]
        lines.append(f"{a} [{n['type']}] {n['name']} | {desc}" + (f" | 关系:{','.join(rels)}" if rels else ""))

    user = (
        f"节：{section_name}\n"
        + (f"小节候选：{'、'.join(subs)}\n" if subs else "（本节无 一、二、… 小节标题）\n")
        + "\n第一层节点（用别名引用）：\n" + "\n".join(lines)
        + "\n\n请组织该节的 KnowledgeUnit。"
    )

    resp, usage = _llm_json(system, user, model)
    units = resp.get("units", [])
    for u in units:
        u["centers_on"] = [alias_to_id[a] for a in u.get("centers_on", []) if a in alias_to_id]
        u["contains"] = [alias_to_id[a] for a in u.get("contains", []) if a in alias_to_id]
    return units, usage


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Layer-2 KnowledgeUnit organizer (LLM).")
    ap.add_argument("input", help="Chapter-1 KG JSON.")
    ap.add_argument("--out", required=True, help="Output KnowledgeUnit JSON.")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Judge model name.")
    ap.add_argument("--src-files", default=",".join(DEFAULT_SRC_FILES),
                    help="Comma-separated source markdown files.")
    ap.add_argument("--prompt", help="Optional system prompt file (defaults to built-in).")
    ap.add_argument("--sections", help="Comma list of section names to process (default: all).")
    ap.add_argument("--chapter", default="第一章",
                    help="Chapter prefix for evidence-based section attribution (default: 第一章).")
    args = ap.parse_args()

    src_files = [s.strip() for s in args.src_files.split(",") if s.strip()]
    system = Path(args.prompt).read_text(encoding="utf-8") if args.prompt else DEFAULT_SYSTEM_PROMPT
    prompt_sha = hashlib.sha256(system.encode()).hexdigest()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    nodes = data["nodes"]
    relations = data["relations"]
    nid = {n["id"]: n for n in nodes}
    outline = build_outline(src_files)

    by_section: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_section[section_of(n, outline, args.chapter)].append(n)

    only = set(x.strip() for x in (args.sections or "").split(",")) if args.sections else None
    sections = [s for s in only if s in by_section] if only else [s for s in by_section]

    kus: list[dict] = []
    diagnostics: dict[str, Any] = {"uncovered": {}, "invalid_refs": [], "multi_center": [], "zero_center": []}
    usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    request_count = 0
    center_seen: dict[str, str] = {}  # GLOBAL across sections: a node may be centers_on in at most one unit

    for sec in sections:
        sec_nodes = by_section[sec]
        subs = outline.get(sec, {}).get("subs", [])
        units, usage = organize_section(sec, sec_nodes, relations, subs, system, args.model)
        request_count += 1
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage_totals[k] += int(usage.get(k, 0))
        sec_ids = {n["id"] for n in sec_nodes}

        assigned: set[str] = set()
        for u in units:
            centers = [c for c in u.get("centers_on", []) if c in sec_ids]
            contains = [c for c in u.get("contains", []) if c in sec_ids]
            invalid = [c for c in u.get("centers_on", []) + u.get("contains", []) if c not in sec_ids]
            if invalid:
                diagnostics["invalid_refs"].extend(f"{sec}:{u.get('name')}:{x}" for x in invalid)
            if not centers:
                diagnostics["zero_center"].append(f"{sec}:{u.get('name')}")
            for c in centers:
                if c in center_seen:
                    diagnostics["multi_center"].append(
                        f"{c} 在 {center_seen[c]} 和 {sec}:{u.get('name')} 都做中心")
                center_seen[c] = f"{sec}:{u.get('name')}"
            assigned |= set(centers) | set(contains)
            ku_id = stable_ku_id(sec, u.get("name", ""))
            # KU evidence: carry the section outline (file + line range) as a synthetic evidence
            ev = []
            o = outline.get(sec)
            if o:
                ev = [{"source_path": o["file"], "start_line": o["start"], "end_line": o["end"],
                       "text": f"{sec}（{u.get('name','')}）"}]
            kus.append({
                "id": ku_id,
                "type": "KnowledgeUnit",
                "name": u.get("name", ""),
                "aliases": [],
                "description": u.get("objective", ""),
                "evidence": ev,
                "centers_on": [{"id": c, "type": nid[c]["type"], "name": nid[c]["name"],
                                "description": (nid[c].get("description", "") or "")[:80]} for c in centers],
                "contains": [{"id": c, "type": nid[c]["type"], "name": nid[c]["name"],
                              "description": (nid[c].get("description", "") or "")[:80]} for c in contains],
            })
        uncovered = sec_ids - assigned
        if uncovered:
            diagnostics["uncovered"][sec] = [nid[c]["name"] for c in uncovered]

    out = {
        "schema_version": SCHEMA,
        "source_graphs": [args.input],
        "knowledge_units": kus,
        "diagnostics": diagnostics,
        "run": {
            "model": args.model,
            "prompt_sha256": prompt_sha,
            "endpoints": [url for url, _ in JUDGE_ENDPOINTS],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "src_files": src_files,
            "request_count": request_count,
            "usage_totals": usage_totals,
        },
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"KnowledgeUnits: {len(kus)}")
    for k in kus:
        print(f"  {k['id']}  centers={len(k['centers_on'])} contains={len(k['contains'])}  {k['name']}")
    print(f"uncovered: {sum(len(v) for v in diagnostics['uncovered'].values())}  "
          f"invalid: {len(diagnostics['invalid_refs'])}  "
          f"multi-center: {len(diagnostics['multi_center'])}  "
          f"zero-center: {len(diagnostics['zero_center'])}")
    print(f"tokens: {usage_totals['total_tokens']}  requests: {request_count}")
    print(f"wrote -> {args.out}")

    # Fail-loud gates: structural violations must not pass silently (the output
    # is still written above so the failure can be inspected and retried with
    # --sections). Coverage gaps are surfaced the same way instead of being
    # recorded-and-forgotten.
    problems: list[str] = []
    for sec, nodes in sorted(diagnostics["uncovered"].items()):
        problems.append(f"  uncovered in {sec}: {', '.join(nodes)}")
    for entry in diagnostics["multi_center"]:
        problems.append(f"  multi-center: {entry}")
    for entry in diagnostics["zero_center"]:
        problems.append(f"  zero-center unit: {entry}")
    if problems:
        print("FAIL (structural violations):", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print("Retry hint: re-run the failing section(s) with --sections after reviewing the output.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
