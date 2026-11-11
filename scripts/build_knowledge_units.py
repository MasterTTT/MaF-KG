#!/usr/bin/env python
"""Layer-2 KnowledgeUnit organizer with deterministic heading boundaries.

Textbook subsection headings fix the Card groups. Candidate routing assigns
layer-1 members, then the finalizer selects centers and names each fixed group.
There are no model-based objective planning or boundary-audit calls in main.

Engineering:
  * stable KU ids: content-hash (sha1 of section + member ids), not sequential.
  * model / source files / prompt are CLI-configurable (no hardcode).
  * output records prompt hashes, heading groups, and token usage.
  * each completed section is checkpointed with a strict run fingerprint.

Usage:
  python scripts/build_knowledge_units.py <kg.json> --out <ku.json>
      [--model deepseek-v4-flash] [--src-files a.md,b.md]
      [--structure-graphs graph-a.json,graph-b.json]
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "maf-kg.knowledge-unit.v1"

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_PLANNER_PROMPT = ROOT / "prompts" / "knowledge_unit_objective_planner.txt"
DEFAULT_AUDIT_PROMPT = ROOT / "prompts" / "knowledge_unit_boundary_auditor.txt"
DEFAULT_ROUTER_PROMPT = ROOT / "prompts" / "knowledge_unit_organizer.txt"
DEFAULT_FINALIZER_PROMPT = ROOT / "prompts" / "knowledge_unit_finalizer.txt"
CHECKPOINT_SCHEMA = "maf-kg.knowledge-unit.organizer-checkpoint.v3"
DEFAULT_SRC_FILES = [
    "E:/MaF-KG/data/raw/第一章前五节_合并_无习题.md",
    "E:/MaF-KG/data/raw/第一章后五节_合并_无习题.md",
]
JUDGE_ENDPOINTS = (
    ("https://opencode.ai/zen/go/v1/chat/completions", "OPENCODE_API_KEY"),
    ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
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
        for attempt in range(1, 4):
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
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                    json.JSONDecodeError, KeyError) as exc:
                errors.append(f"{url} attempt {attempt}/3: {exc}")
                if attempt < 3:
                    time.sleep(3 * attempt)
    raise SystemExit("LLM organizer failed on all endpoints: " + "; ".join(errors))


def _file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_fingerprint(
    *,
    input_path: str,
    structure_graph_paths: list[str],
    src_files: list[str],
    router_prompt_sha: str,
    finalizer_prompt_sha: str,
    model: str,
    chapter: str,
    sections: list[str],
) -> str:
    payload = {
        "input": [str(Path(input_path).resolve()), _file_sha256(input_path)],
        "structure_graphs": [
            [str(Path(path).resolve()), _file_sha256(path)] for path in structure_graph_paths
        ],
        "src_files": [[str(Path(path).resolve()), _file_sha256(path)] for path in src_files],
        "boundary_strategy": "textbook-subheading",
        "router_prompt_sha256": router_prompt_sha,
        "finalizer_prompt_sha256": finalizer_prompt_sha,
        "model": model,
        "chapter": chapter,
        "sections": sections,
        "pipeline_sha256": _file_sha256(__file__),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_checkpoint(path: Path, fingerprint: str, completed_sections: dict[str, dict]) -> None:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA,
        "fingerprint": fingerprint,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "completed_sections": completed_sections,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_checkpoint(path: Path, fingerprint: str) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != CHECKPOINT_SCHEMA:
        raise ValueError(f"Unsupported checkpoint schema in {path}")
    if payload.get("fingerprint") != fingerprint:
        raise ValueError(
            f"Checkpoint fingerprint mismatch: {path}. "
            "Use --no-resume to start a new run explicitly."
        )
    completed = payload.get("completed_sections")
    if not isinstance(completed, dict):
        raise ValueError(f"Checkpoint has invalid completed_sections: {path}")
    return completed


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
                cur = {"file": base, "path": str(Path(path).resolve()),
                       "start": i, "end": len(lines), "subs": []}
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
    """Identify explicit section headings at any depth, prioritizing this chapter."""
    usable: list[str] = []
    for ev in node.get("evidence", []):
        hp = ev.get("heading_path", [])
        section = next((h.lstrip("# ").strip() for h in hp
                        if isinstance(h, str) and _SECTION_RE.match("# " + h.lstrip("# ").strip())), None)
        if section:
            if hp and isinstance(hp[0], str) and chapter and hp[0].lstrip("# ").startswith(chapter):
                return section
            usable.append(section)
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

def stable_ku_id(section: str, centers: list[str], contains: list[str]) -> str:
    members = "|".join(sorted(set(centers) | set(contains)))
    h = hashlib.sha1(f"{section}|{members}".encode()).hexdigest()[:12]
    return f"ku_{h}"


def _node_summary(nodes: list[dict], aliases: dict[str, str] | None = None) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        prefix = f"{aliases[node['id']]} " if aliases else ""
        desc = (node.get("description", "") or "").replace("\n", " ")[:120]
        lines.append(f"{prefix}[{node['type']}] {node['name']} | {desc}")
    return lines


def _compact_span(lines: list[str], start_line: int, end_line: int) -> str:
    content = [re.sub(r"\s+", " ", line).strip() for line in lines[start_line - 1:end_line]]
    content = [line for line in content if line and not line.startswith("#")]
    if not content:
        return ""
    selected = [content[0]]
    signal_pattern = re.compile(
        r"(定义|定理|推论|性质|方法|步骤|一般地|取.+为|设.+区间|近似|元素|于是|从而)"
    )
    signal = next((line for line in content[1:-1] if signal_pattern.search(line)), None)
    if signal:
        selected.append(signal)
    if len(content) > 1:
        selected.append(content[-1])
    compact = " … ".join(dict.fromkeys(selected))
    return compact[:700]


def build_planner_cards(section_name: str, nodes: list[dict], outline: dict[str, dict],
                        structure_by_source: dict[str, dict]) -> list[dict]:
    """Project persisted structure-aware chunks into compact pedagogical cards."""
    entry = outline.get(section_name)
    if not entry:
        raise ValueError(f"{section_name}: source outline is unavailable")
    structure_data = structure_by_source.get(entry["file"])
    if structure_data is None:
        raise ValueError(
            f"{section_name}: no structure graph matches source {entry['file']!r}; "
            "pass --structure-graphs with matching metadata"
        )
    diagnostics = structure_data.get("diagnostics", {})
    chunk_spans = diagnostics.get("chunk_spans")
    if not isinstance(chunk_spans, list) or not chunk_spans:
        raise ValueError(f"{section_name}: structure graph has no diagnostics.chunk_spans")

    section_spans = [
        span for span in chunk_spans
        if isinstance(span.get("start_line"), int) and isinstance(span.get("end_line"), int)
        and span["start_line"] <= entry["end"] and entry["start"] <= span["end_line"]
    ]
    if not section_spans:
        raise ValueError(f"{section_name}: no persisted structured chunks fall inside section")

    node_by_id = {node["id"]: node for node in nodes}
    nodes_by_chunk: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for evidence in node.get("evidence", []):
            source_name = Path(evidence.get("source_path", "")).name
            start = evidence.get("start_line")
            end = evidence.get("end_line")
            if (source_name != entry["file"] or not isinstance(start, int)
                    or not isinstance(end, int) or end < entry["start"] or start > entry["end"]):
                continue
            matching_chunks = [
                span["id"] for span in section_spans
                if span["start_line"] <= end and start <= span["end_line"]
            ]
            if not matching_chunks:
                raise ValueError(
                    f"{section_name}:{node['name']}: evidence L{start}-L{end} does not overlap "
                    "a persisted section chunk"
                )
            for chunk_id in matching_chunks:
                if node["id"] not in nodes_by_chunk[chunk_id]:
                    nodes_by_chunk[chunk_id].append(node["id"])

    represented = {
        node_id for node_ids in nodes_by_chunk.values() for node_id in node_ids
    }
    missing = [node["name"] for node in nodes if node["id"] not in represented]
    if missing:
        raise ValueError(
            f"{section_name}: nodes are not represented by persisted section chunks: {missing}"
        )

    source_lines = Path(entry["path"]).read_text(encoding="utf-8").splitlines()
    cards: list[dict] = []
    for index, span in enumerate(sorted(section_spans, key=lambda value: value["start_line"]), start=1):
        card_start = max(entry["start"], span["start_line"])
        card_end = min(entry["end"], span["end_line"])
        heading_path = [str(value) for value in span.get("heading_path", [])]
        if section_name in heading_path:
            heading_path = heading_path[heading_path.index(section_name):]
        else:
            heading_path = [section_name]
        node_ids = nodes_by_chunk.get(span["id"], [])
        roles = [str(role) for role in span.get("block_types", []) if role != "heading"]
        cards.append({
            "card_id": f"c{index}",
            "source_chunk_id": span["id"],
            "source_path": entry["path"],
            "start_line": card_start,
            "end_line": card_end,
            "heading_path": heading_path,
            "discourse_roles": roles,
            "teaching_unit_kind": span.get("teaching_unit_kind", ""),
            "anchor_text": _compact_span(source_lines, card_start, card_end),
            "node_ids": node_ids,
            "node_summary": [
                {"type": node_by_id[node_id]["type"], "name": node_by_id[node_id]["name"]}
                for node_id in node_ids
            ],
        })
    return cards


def _usage_add(total: dict[str, int], usage: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] += int(usage.get(key, 0))


def heading_objectives(section_name: str, cards: list[dict]) -> list[dict]:
    """Fix groups by first subheading; attach introductory Cards forward."""
    labels = []
    for card in cards:
        path = card["heading_path"]
        labels.append(path[1] if len(path) > 1 else None)
    for index, label in enumerate(labels):
        if label is None:
            labels[index] = next(
                (value for value in labels[index + 1:] if value is not None),
                next((value for value in reversed(labels[:index]) if value is not None), section_name),
            )
    groups: dict[str, list[str]] = {}
    for card, label in zip(cards, labels):
        groups.setdefault(label, []).append(card["card_id"])
    objectives = [
        {"goal_id": f"g{index}", "core_question": f"{label}集中讲授什么内容？",
         "objective": f"学习{label}。",
         "boundary_reason": "按教材一级小标题固定边界，不作语义拆分或合并。",
         "card_ids": ids}
        for index, (label, ids) in enumerate(groups.items(), 1)
    ]
    return _require_objective_plan({"objectives": objectives}, section_name, cards)


def _require_objective_plan(resp: dict, section_name: str, cards: list[dict]) -> list[dict]:
    objectives = resp.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        raise ValueError(f"{section_name}: planner must return a non-empty objectives list")
    expected_ids = [f"g{i}" for i in range(1, len(objectives) + 1)]
    actual_ids: list[str] = []
    for objective in objectives:
        if not isinstance(objective, dict):
            raise ValueError(f"{section_name}: every objective must be an object")
        if set(objective) != {
            "goal_id", "core_question", "objective", "boundary_reason", "card_ids"
        }:
            raise ValueError(f"{section_name}: invalid planner objective fields: {sorted(objective)}")
        text_fields = ("goal_id", "core_question", "objective", "boundary_reason")
        if not all(isinstance(objective[k], str) and objective[k].strip() for k in text_fields):
            raise ValueError(f"{section_name}: planner objective fields must be non-empty strings")
        if (not isinstance(objective["card_ids"], list) or not objective["card_ids"]
                or any(not isinstance(card_id, str) for card_id in objective["card_ids"])):
            raise ValueError(f"{section_name}: every objective must contain card IDs")
        actual_ids.append(objective["goal_id"])
    if actual_ids != expected_ids:
        raise ValueError(
            f"{section_name}: planner goal IDs must be contiguous {expected_ids}, got {actual_ids}"
        )
    expected_cards = {card["card_id"] for card in cards}
    returned_cards = [card_id for objective in objectives for card_id in objective["card_ids"]]
    invalid_cards = sorted(set(returned_cards) - expected_cards)
    missing_cards = sorted(expected_cards - set(returned_cards))
    duplicate_cards = sorted({card_id for card_id in returned_cards if returned_cards.count(card_id) > 1})
    if invalid_cards or missing_cards or duplicate_cards:
        raise ValueError(
            f"{section_name}: invalid card partition; invalid={invalid_cards}, "
            f"missing={missing_cards}, duplicate={duplicate_cards}"
        )
    return objectives


def _boundary_signals(cards: list[dict], objectives: list[dict]) -> list[dict]:
    card_by_id = {card["card_id"]: card for card in cards}
    signals: list[dict] = []
    for objective in objectives:
        selected = [card_by_id[card_id] for card_id in objective["card_ids"]]
        leaf_headings = []
        roles: set[str] = set()
        kinds: set[str] = set()
        for card in selected:
            path = card.get("heading_path") or []
            if path:
                leaf_headings.append(path[-1])
            roles.update(card.get("discourse_roles") or [])
            kind = str(card.get("teaching_unit_kind") or "").strip()
            if kind:
                kinds.add(kind)
        signals.append({
            "goal_id": objective["goal_id"],
            "card_count": len(selected),
            "distinct_leaf_headings": list(dict.fromkeys(leaf_headings)),
            "discourse_roles": sorted(roles),
            "teaching_unit_kinds": sorted(kinds),
            "cards_without_nodes": [
                card["card_id"] for card in selected if not card.get("node_summary")
            ],
        })
    return signals


def _require_boundary_audit(
    resp: dict,
    section_name: str,
    cards: list[dict],
    candidates: list[dict],
) -> tuple[list[dict], list[dict]]:
    operations = resp.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError(f"{section_name}: boundary audit must return non-empty operations")

    candidate_ids = [objective["goal_id"] for objective in candidates]
    targeted: list[str] = []
    audited: list[dict] = []
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != {
            "action", "targets", "objectives", "reason"
        }:
            raise ValueError(f"{section_name}: invalid boundary-audit operation fields")
        action = operation["action"]
        targets = operation["targets"]
        outputs = operation["objectives"]
        reason = operation["reason"]
        if action not in {"keep", "split", "merge"}:
            raise ValueError(f"{section_name}: unsupported boundary action {action!r}")
        if (not isinstance(targets, list) or not targets
                or any(not isinstance(value, str) for value in targets)):
            raise ValueError(f"{section_name}: boundary targets must be goal IDs")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{section_name}: boundary operation reason must be non-empty")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError(f"{section_name}: boundary operation must return objectives")
        if action == "keep" and (len(targets) != 1 or len(outputs) != 1):
            raise ValueError(f"{section_name}: keep must map one target to one objective")
        if action == "split" and (len(targets) != 1 or len(outputs) < 2):
            raise ValueError(f"{section_name}: split must map one target to multiple objectives")
        if action == "merge" and (len(targets) < 2 or len(outputs) != 1):
            raise ValueError(f"{section_name}: merge must map multiple targets to one objective")
        targeted.extend(targets)
        audited.extend(outputs)

    invalid_targets = sorted(set(targeted) - set(candidate_ids))
    missing_targets = sorted(set(candidate_ids) - set(targeted))
    duplicate_targets = sorted({goal_id for goal_id in targeted if targeted.count(goal_id) > 1})
    if invalid_targets or missing_targets or duplicate_targets:
        raise ValueError(
            f"{section_name}: invalid audit targets; invalid={invalid_targets}, "
            f"missing={missing_targets}, duplicate={duplicate_targets}"
        )

    candidate_cards = {
        objective["goal_id"]: set(objective["card_ids"]) for objective in candidates
    }
    for operation in operations:
        allowed_cards = set().union(*(candidate_cards[target] for target in operation["targets"]))
        returned_cards = {
            card_id
            for objective in operation["objectives"]
            for card_id in objective.get("card_ids", [])
        }
        if returned_cards != allowed_cards:
            raise ValueError(
                f"{section_name}: audit operation may only repartition its target Cards; "
                f"expected={sorted(allowed_cards)}, got={sorted(returned_cards)}"
            )

    normalized: list[dict] = []
    for index, objective in enumerate(audited, start=1):
        if not isinstance(objective, dict):
            raise ValueError(f"{section_name}: audited objective must be an object")
        normalized.append({**objective, "goal_id": f"g{index}"})
    return _require_objective_plan({"objectives": normalized}, section_name, cards), operations


def plan_objectives(section_name: str, cards: list[dict],
                    system: str, model: str) -> tuple[list[dict], dict]:
    prompt_cards = [
        {key: value for key, value in card.items() if key not in {"source_path", "node_ids"}}
        for card in cards
    ]
    user = (
        f"节：{section_name}\n"
        + "\n已有结构感知分块生成的 Planner Cards：\n"
        + json.dumps(prompt_cards, ensure_ascii=False, indent=2)
        + "\n\n请把每张 Card 恰好归入一个中心教学目标。"
    )
    resp, usage = _llm_json(system, user, model)
    return _require_objective_plan(resp, section_name, cards), usage


def audit_boundaries(
    section_name: str,
    cards: list[dict],
    candidates: list[dict],
    system: str,
    model: str,
) -> tuple[list[dict], list[dict], dict]:
    prompt_cards = [
        {key: value for key, value in card.items() if key not in {"source_path", "node_ids"}}
        for card in cards
    ]
    user = (
        f"节：{section_name}\n"
        + "候选教学目标：\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
        + "\n\n程序生成的边界信号：\n"
        + json.dumps(_boundary_signals(cards, candidates), ensure_ascii=False, indent=2)
        + "\n\nPlanner Cards：\n"
        + json.dumps(prompt_cards, ensure_ascii=False, indent=2)
        + "\n\n请对每个候选目标恰好执行一次 keep、split 或 merge。"
    )
    resp, usage = _llm_json(system, user, model)
    objectives, operations = _require_boundary_audit(
        resp, section_name, cards, candidates
    )
    return objectives, operations, usage


def _node_candidate_goals(
    section_name: str,
    nodes: list[dict],
    cards: list[dict],
    objectives: list[dict],
) -> dict[str, list[str]]:
    card_goal = {
        card_id: objective["goal_id"]
        for objective in objectives
        for card_id in objective["card_ids"]
    }
    candidates: dict[str, set[str]] = {node["id"]: set() for node in nodes}
    for card in cards:
        goal_id = card_goal[card["card_id"]]
        for node_id in card["node_ids"]:
            if node_id in candidates:
                candidates[node_id].add(goal_id)
    missing = [node["name"] for node in nodes if not candidates[node["id"]]]
    if missing:
        raise ValueError(f"{section_name}: nodes have no candidate KnowledgeUnit: {missing}")
    order = {objective["goal_id"]: index for index, objective in enumerate(objectives)}
    return {
        node_id: sorted(goal_ids, key=order.__getitem__)
        for node_id, goal_ids in candidates.items()
    }


def route_members(
    section_name: str,
    nodes: list[dict],
    relations: list[dict],
    cards: list[dict],
    objectives: list[dict],
    candidates: dict[str, list[str]],
    system: str,
    model: str,
) -> tuple[dict[str, str], list[dict], dict, int]:
    ids = [n["id"] for n in nodes]
    alias = {full: f"n{i+1}" for i, full in enumerate(ids)}
    alias_to_id = {a: f for f, a in alias.items()}
    owner = {
        node_id: goal_ids[0] for node_id, goal_ids in candidates.items() if len(goal_ids) == 1
    }
    ambiguous = [node for node in nodes if len(candidates[node["id"]]) > 1]
    if not ambiguous:
        return owner, [], {}, 0

    node_by_id = {node["id"]: node for node in nodes}
    relation_context: list[str] = []
    ambiguous_ids = {node["id"] for node in ambiguous}
    card_goal = {
        card_id: objective["goal_id"]
        for objective in objectives
        for card_id in objective["card_ids"]
    }
    for relation in relations:
        source_id = relation.get("source_id")
        target_id = relation.get("target_id")
        if source_id not in node_by_id or target_id not in node_by_id:
            continue
        if source_id not in ambiguous_ids and target_id not in ambiguous_ids:
            continue
        source_label = alias[source_id] if source_id in ambiguous_ids else node_by_id[source_id]["name"]
        target_label = alias[target_id] if target_id in ambiguous_ids else node_by_id[target_id]["name"]
        relation_context.append(f"{source_label} -[{relation['type']}]-> {target_label}")

    ambiguous_input = [
        {
            "node_alias": alias[node["id"]],
            "type": node["type"],
            "name": node["name"],
            "description": (node.get("description", "") or "")[:160],
            "candidate_goal_ids": candidates[node["id"]],
            "candidate_evidence": [
                {
                    "goal_id": goal_id,
                    "occurrences": [
                        {
                            "card_id": card["card_id"],
                            "heading_path": card.get("heading_path", []),
                            "discourse_roles": card.get("discourse_roles", []),
                            "anchor_text": str(card.get("anchor_text", ""))[:260],
                        }
                        for card in cards
                        if node["id"] in card.get("node_ids", [])
                        and card_goal[card["card_id"]] == goal_id
                    ],
                }
                for goal_id in candidates[node["id"]]
            ],
        }
        for node in ambiguous
    ]
    user = (
        f"节：{section_name}\n"
        + "冻结目标：\n" + json.dumps(objectives, ensure_ascii=False, indent=2)
        + "\n\n仅以下节点存在多个候选目标：\n"
        + json.dumps(ambiguous_input, ensure_ascii=False, indent=2)
        + "\n\n与歧义节点直接相关的第一层关系：\n"
        + ("\n".join(relation_context) if relation_context else "（无）")
        + "\n\n请为每个歧义节点从其 candidate_goal_ids 中选择唯一目标。"
    )
    resp, usage = _llm_json(system, user, model)
    assignments = resp.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError(f"{section_name}: router must return an assignments list")
    expected_aliases = [alias[node["id"]] for node in ambiguous]
    returned_aliases: list[str] = []
    routing_records: list[dict] = []
    for assignment in assignments:
        if not isinstance(assignment, dict) or set(assignment) != {
            "node_alias", "goal_id", "reason"
        }:
            raise ValueError(f"{section_name}: invalid router assignment fields")
        node_alias = assignment["node_alias"]
        goal_id = assignment["goal_id"]
        reason = assignment["reason"]
        if (node_alias not in alias_to_id or not isinstance(goal_id, str)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError(f"{section_name}: invalid router assignment values")
        node_id = alias_to_id[node_alias]
        if goal_id not in candidates[node_id]:
            raise ValueError(
                f"{section_name}:{node_alias}: goal {goal_id} outside candidates "
                f"{candidates[node_id]}"
            )
        owner[node_id] = goal_id
        returned_aliases.append(node_alias)
        routing_records.append({
            "node_id": node_id,
            "node_name": node_by_id[node_id]["name"],
            "candidate_goal_ids": candidates[node_id],
            "selected_goal_id": goal_id,
            "reason": reason,
        })
    if returned_aliases != expected_aliases:
        raise ValueError(
            f"{section_name}: router aliases must exactly match {expected_aliases}, "
            f"got {returned_aliases}"
        )
    if set(owner) != set(ids):
        raise ValueError(f"{section_name}: router did not produce a global unique member table")
    return owner, routing_records, usage, 1


def finalize_units(
    section_name: str,
    nodes: list[dict],
    relations: list[dict],
    objectives: list[dict],
    owner: dict[str, str],
    system: str,
    model: str,
) -> tuple[list[dict], dict]:
    ids = [node["id"] for node in nodes]
    alias = {node_id: f"n{index + 1}" for index, node_id in enumerate(ids)}
    alias_to_id = {value: key for key, value in alias.items()}
    node_by_id = {node["id"]: node for node in nodes}
    members_by_goal: dict[str, list[str]] = {
        objective["goal_id"]: [] for objective in objectives
    }
    for node_id in ids:
        members_by_goal[owner[node_id]].append(node_id)
    empty = [goal_id for goal_id, members in members_by_goal.items() if not members]
    if empty:
        raise ValueError(f"{section_name}: frozen objectives have no routed members: {empty}")

    groups = []
    for objective in objectives:
        goal_id = objective["goal_id"]
        member_ids = members_by_goal[goal_id]
        internal_relations = [
            f"{alias[relation['source_id']]} -[{relation['type']}]-> {alias[relation['target_id']]}"
            for relation in relations
            if relation.get("source_id") in member_ids and relation.get("target_id") in member_ids
        ]
        groups.append({
            "goal_id": goal_id,
            "core_question": objective["core_question"],
            "objective": objective["objective"],
            "boundary_reason": objective["boundary_reason"],
            "members": [
                {
                    "node_alias": alias[node_id],
                    "type": node_by_id[node_id]["type"],
                    "name": node_by_id[node_id]["name"],
                    "description": (node_by_id[node_id].get("description", "") or "")[:120],
                }
                for node_id in member_ids
            ],
            "internal_relations": internal_relations,
        })

    user = (
        f"节：{section_name}\n"
        + "以下目标边界和成员已经冻结。每个目标独立选择中心并命名：\n"
        + json.dumps(groups, ensure_ascii=False, indent=2)
    )
    resp, usage = _llm_json(system, user, model)
    finalized = resp.get("units")
    if not isinstance(finalized, list):
        raise ValueError(f"{section_name}: finalizer must return a units list")
    planned_ids = [objective["goal_id"] for objective in objectives]
    returned_ids: list[str] = []
    units: list[dict] = []
    for item in finalized:
        if not isinstance(item, dict) or set(item) != {
            "goal_id", "name", "description", "centers_on"
        }:
            raise ValueError(f"{section_name}: invalid finalizer unit fields")
        goal_id = item["goal_id"]
        name = item["name"]
        description = item["description"]
        centers = item["centers_on"]
        if (goal_id not in members_by_goal or not isinstance(name, str) or not name.strip()
                or not isinstance(description, str) or not description.strip()
                or not isinstance(centers, list)
                or any(not isinstance(value, str) for value in centers)):
            raise ValueError(f"{section_name}: invalid finalizer values for {goal_id!r}")
        member_aliases = {alias[node_id] for node_id in members_by_goal[goal_id]}
        if any(value not in member_aliases for value in centers):
            raise ValueError(f"{section_name}:{goal_id}: center outside frozen member set")
        if len(centers) != len(set(centers)) or len(centers) > 3:
            raise ValueError(f"{section_name}:{goal_id}: centers_on must be unique and at most 3")
        center_ids = [alias_to_id[value] for value in centers]
        units.append({
            "goal_id": goal_id,
            "name": name,
            "description": description,
            "centers_on": center_ids,
            "contains": [
                node_id for node_id in members_by_goal[goal_id] if node_id not in center_ids
            ],
        })
        returned_ids.append(goal_id)
    if returned_ids != planned_ids:
        raise ValueError(
            f"{section_name}: finalizer goal IDs must exactly match {planned_ids}, "
            f"got {returned_ids}"
        )
    return units, usage


def assign_members(
    section_name: str,
    nodes: list[dict],
    relations: list[dict],
    cards: list[dict],
    objectives: list[dict],
    router_system: str,
    finalizer_system: str,
    model: str,
) -> tuple[list[dict], dict[str, list[str]], list[dict], dict, dict, int]:
    candidates = _node_candidate_goals(section_name, nodes, cards, objectives)
    owner, routing_records, router_usage, router_requests = route_members(
        section_name, nodes, relations, cards, objectives, candidates, router_system, model
    )
    units, finalizer_usage = finalize_units(
        section_name, nodes, relations, objectives, owner, finalizer_system, model
    )
    return units, candidates, routing_records, router_usage, finalizer_usage, router_requests + 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Layer-2 KnowledgeUnit organizer (LLM).")
    ap.add_argument("input", help="Layer-1 chapter KG JSON.")
    ap.add_argument("--out", required=True, help="Output KnowledgeUnit JSON.")
    ap.add_argument("--structure-graphs",
                    help="Comma-separated graphs carrying chunk_spans for source files (default: input).")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Judge model name.")
    ap.add_argument("--src-files", default=",".join(DEFAULT_SRC_FILES),
                    help="Comma-separated source markdown files.")
    ap.add_argument("--router-prompt", default=str(DEFAULT_ROUTER_PROMPT),
                    help=f"Ambiguous-node router prompt (default: {DEFAULT_ROUTER_PROMPT}).")
    ap.add_argument("--finalizer-prompt", default=str(DEFAULT_FINALIZER_PROMPT),
                    help=f"KU finalizer prompt (default: {DEFAULT_FINALIZER_PROMPT}).")
    ap.add_argument("--sections", help="Comma list of section names to process (default: all).")
    ap.add_argument("--chapter", default="第一章",
                    help="Chapter prefix for evidence-based section attribution (default: 第一章).")
    ap.add_argument("--checkpoint",
                    help="Section checkpoint path (default: <out>.checkpoint.json).")
    ap.add_argument("--no-resume", action="store_true",
                    help="Discard a matching or stale checkpoint and start from the first section.")
    args = ap.parse_args()

    src_files = [s.strip() for s in args.src_files.split(",") if s.strip()]
    router_system = Path(args.router_prompt).read_text(encoding="utf-8")
    finalizer_system = Path(args.finalizer_prompt).read_text(encoding="utf-8")
    router_prompt_sha = hashlib.sha256(router_system.encode()).hexdigest()
    finalizer_prompt_sha = hashlib.sha256(finalizer_system.encode()).hexdigest()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    structure_graph_paths = [
        value.strip() for value in (args.structure_graphs or args.input).split(",")
        if value.strip()
    ]
    structure_by_source: dict[str, dict] = {}
    for structure_graph_path in structure_graph_paths:
        structure_data = json.loads(Path(structure_graph_path).read_text(encoding="utf-8"))
        source_name = Path(structure_data.get("diagnostics", {}).get("input_path", "")).name
        if not source_name:
            raise ValueError(f"Structure graph has no diagnostics.input_path: {structure_graph_path}")
        if source_name in structure_by_source:
            raise ValueError(f"Duplicate structure graphs for source {source_name!r}")
        structure_by_source[source_name] = structure_data
    nodes = data["nodes"]
    relations = data["relations"]
    nid = {n["id"]: n for n in nodes}
    outline = build_outline(src_files)

    by_section: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_section[section_of(n, outline, args.chapter)].append(n)

    if args.sections:
        only = [x.strip() for x in args.sections.split(",") if x.strip()]
        missing = [section for section in only if section not in by_section]
        if missing:
            raise ValueError(f"Requested sections have no attributed nodes: {missing}")
        sections = only
    else:
        sections = [section for section in outline if section in by_section]

    checkpoint_path = Path(args.checkpoint) if args.checkpoint else Path(args.out + ".checkpoint.json")
    fingerprint = _run_fingerprint(
        input_path=args.input,
        structure_graph_paths=structure_graph_paths,
        src_files=src_files,
        router_prompt_sha=router_prompt_sha,
        finalizer_prompt_sha=finalizer_prompt_sha,
        model=args.model,
        chapter=args.chapter,
        sections=sections,
    )
    if args.no_resume:
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            print(f"Discarded checkpoint: {checkpoint_path}", flush=True)
        completed_sections: dict[str, dict] = {}
    elif checkpoint_path.exists():
        completed_sections = _load_checkpoint(checkpoint_path, fingerprint)
        unexpected = sorted(set(completed_sections) - set(sections))
        if unexpected:
            raise ValueError(f"Checkpoint contains unexpected sections: {unexpected}")
        print(
            f"Loaded checkpoint: {len(completed_sections)}/{len(sections)} sections complete",
            flush=True,
        )
    else:
        completed_sections = {}

    kus: list[dict] = []
    diagnostics: dict[str, Any] = {
        "uncovered": {},
        "invalid_refs": [],
        "duplicate_assignments": [],
        "multi_center": [],
        "virtual_aggregate": [],
        "empty_unit": [],
    }
    usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    request_count = 0
    center_seen: dict[str, str] = {}  # GLOBAL across sections: a node may be centers_on in at most one unit
    objective_plans: dict[str, list[dict]] = {}
    member_candidates: dict[str, dict[str, list[str]]] = {}
    ambiguous_routing: dict[str, list[dict]] = {}
    section_metrics: dict[str, dict] = {}
    planner_cards: dict[str, list[dict]] = {}

    for sec in sections:
        sec_nodes = by_section[sec]
        print(f"[{sec}] building structured Cards from {len(sec_nodes)} nodes", flush=True)
        cards = build_planner_cards(sec, sec_nodes, outline, structure_by_source)
        planner_cards[sec] = cards
        if sec in completed_sections:
            saved = completed_sections[sec]
            if saved.get("planner_cards") != cards:
                raise ValueError(
                    f"Checkpoint Planner Cards changed for {sec}; use --no-resume explicitly."
                )
            objectives = saved["objectives"]
            candidates_by_node = saved["member_candidates"]
            routing_records = saved["routing_records"]
            units = saved["units"]
            router_usage = saved.get("router_usage", {})
            finalizer_usage = saved.get("finalizer_usage", {})
            section_requests = int(saved.get("request_count", 0))
            print(f"[{sec}] resume: {len(objectives)} objectives, {len(units)} units", flush=True)
        else:
            objectives = heading_objectives(sec, cards)
            print(
                f"[{sec}] Heading groups fixed: {len(objectives)}; Candidate routing start",
                flush=True,
            )
            units, candidates_by_node, routing_records, router_usage, finalizer_usage, member_requests = assign_members(
                sec, sec_nodes, relations, cards, objectives,
                router_system, finalizer_system, args.model
            )
            ambiguous_count = sum(1 for values in candidates_by_node.values() if len(values) > 1)
            print(
                f"[{sec}] Candidate routing done: {ambiguous_count} ambiguous nodes; "
                f"Finalizer done: {len(units)} units",
                flush=True,
            )
            section_requests = member_requests
            completed_sections[sec] = {
                "planner_cards": cards,
                "objectives": objectives,
                "member_candidates": candidates_by_node,
                "routing_records": routing_records,
                "units": units,
                "router_usage": router_usage,
                "finalizer_usage": finalizer_usage,
                "request_count": section_requests,
            }
            _write_checkpoint(checkpoint_path, fingerprint, completed_sections)
            print(
                f"[{sec}] checkpoint saved: {len(completed_sections)}/{len(sections)}",
                flush=True,
            )
        objective_plans[sec] = objectives
        member_candidates[sec] = candidates_by_node
        ambiguous_routing[sec] = routing_records
        section_metrics[sec] = {
            "node_count": len(sec_nodes),
            "directly_assigned_nodes": sum(
                1 for values in candidates_by_node.values() if len(values) == 1
            ),
            "ambiguous_nodes": sum(
                1 for values in candidates_by_node.values() if len(values) > 1
            ),
            "request_count": section_requests,
            "usage": {
                "router": router_usage,
                "finalizer": finalizer_usage,
            },
        }
        request_count += section_requests
        _usage_add(usage_totals, router_usage)
        _usage_add(usage_totals, finalizer_usage)
        sec_ids = {n["id"] for n in sec_nodes}
        objective_by_id = {objective["goal_id"]: objective for objective in objectives}

        assigned: set[str] = set()
        assignment_owner: dict[str, str] = {}
        for u in units:
            objective = objective_by_id[u["goal_id"]]
            unit_name = u["name"]
            centers = u["centers_on"]
            contains = u["contains"]
            invalid = [c for c in centers + contains if c not in sec_ids]
            if invalid:
                diagnostics["invalid_refs"].extend(
                    f"{sec}:{unit_name}:{node_id}" for node_id in invalid
                )
            if not centers:
                bucket = "virtual_aggregate" if contains else "empty_unit"
                diagnostics[bucket].append(f"{sec}:{unit_name}")
            for node_id in centers + contains:
                if node_id in assignment_owner:
                    diagnostics["duplicate_assignments"].append(
                        f"{sec}:{nid[node_id]['name']} 同时属于 "
                        f"{assignment_owner[node_id]} 和 {unit_name}"
                    )
                else:
                    assignment_owner[node_id] = unit_name
            for c in centers:
                if c in center_seen:
                    diagnostics["multi_center"].append(
                        f"{c} 在 {center_seen[c]} 和 {sec}:{unit_name} 都做中心")
                center_seen[c] = f"{sec}:{unit_name}"
            assigned |= set(centers) | set(contains)
            ku_id = stable_ku_id(sec, centers, contains)
            # KU evidence: carry the section outline (file + line range) as a synthetic evidence
            ev = []
            o = outline.get(sec)
            if o:
                ev = [{"source_path": o["file"], "start_line": o["start"], "end_line": o["end"],
                       "text": f"{sec}（{unit_name}）"}]
            kus.append({
                "id": ku_id,
                "type": "KnowledgeUnit",
                "name": unit_name,
                "aliases": [],
                "description": u["description"],
                "aggregation_kind": "anchored" if centers else "virtual",
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
        "structure_graphs": structure_graph_paths,
        "knowledge_units": kus,
        "planner_cards": planner_cards,
        "objective_plans": objective_plans,
        "member_candidates": member_candidates,
        "ambiguous_routing": ambiguous_routing,
        "section_metrics": section_metrics,
        "diagnostics": diagnostics,
        "run": {
            "model": args.model,
            "boundary_strategy": "textbook-subheading",
            "router_prompt": str(Path(args.router_prompt).resolve()),
            "router_prompt_sha256": router_prompt_sha,
            "finalizer_prompt": str(Path(args.finalizer_prompt).resolve()),
            "finalizer_prompt_sha256": finalizer_prompt_sha,
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
          f"duplicate: {len(diagnostics['duplicate_assignments'])}  "
          f"multi-center: {len(diagnostics['multi_center'])}  "
          f"virtual: {len(diagnostics['virtual_aggregate'])}  "
          f"empty: {len(diagnostics['empty_unit'])}")
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
    for entry in diagnostics["duplicate_assignments"]:
        problems.append(f"  duplicate assignment: {entry}")
    for entry in diagnostics["invalid_refs"]:
        problems.append(f"  invalid ref: {entry}")
    for entry in diagnostics["empty_unit"]:
        problems.append(f"  empty unit: {entry}")
    if problems:
        print("FAIL (structural violations):", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print("Retry hint: re-run the failing section(s) with --sections after reviewing the output.",
              file=sys.stderr)
        return 1
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print(f"Removed completed checkpoint: {checkpoint_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
