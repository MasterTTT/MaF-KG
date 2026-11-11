"""Bind existing reviews and explicit chat decisions to label-masked packets."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consolidate(packet_dir, previous_path, scope_path=None):
    mapping_path = packet_dir / "private/mapping.json"
    chat_path = packet_dir / "chat_adjudication_v1.json"
    mapping, previous, chat = read(mapping_path), read(previous_path), read(chat_path)
    prior = {row["graph_sha256"]: row for row in previous["jobs"]}
    human = {item["review_id"]: item for batch in chat["batches"] for item in batch["items"]}
    if len(human) != sum(len(batch["items"]) for batch in chat["batches"]):
        raise ValueError("Overlapping chat decisions")
    result = []
    pending = {"P02-N001", "P02-N002", "P02-N003"}
    scope = read(scope_path) if scope_path is not None else None
    if scope is not None:
        if scope["decision"] != "accept_fixed_background_converse" or set(scope["review_ids"]) != pending:
            raise ValueError("Unexpected scope decision")
        if sha(packet_dir / (scope["packet_id"] + ".json")) != scope["packet_sha256"]:
            raise ValueError("Scope decision packet changed")
    for row in mapping["packets"]:
        packet_path = packet_dir / (row["packet_id"] + ".json")
        packet = read(packet_path)
        graph_path = Path(row["graph_path"])
        digest = sha(graph_path)
        if digest != row["graph_sha256"] or digest not in prior:
            raise ValueError("Graph binding changed")
        for packet_id, expected in chat["packet_sha256"].items():
            if packet_id == row["packet_id"] and sha(packet_path) != expected:
                raise ValueError("Chat packet changed")
        prior_row = prior[digest]
        review_path = graph_path.parent / "review.json"
        if sha(review_path) != prior_row["original_review_sha256"]:
            raise ValueError("Previous review changed")
        entries = []
        for kind, key in (("node", "nodes"), ("relation", "relations")):
            if kind == "node":
                originals = {v: k for k, v in row["node_mapping"].items()}
            else:
                originals = row["relation_mapping"]
            old = {item["id"]: item for item in prior_row[kind + "_reviews"]}
            if set(originals.values()) != set(old):
                raise ValueError("Review coverage differs")
            for item in packet[key]:
                code = item["review_id"]
                original = old[originals[code]]
                entry = {"review_id": code, "kind": kind,
                         "verdict": original["adjudicated_verdict"],
                         "provenance": original["adjudication_source"],
                         "reason": original.get("adjudication_reason", original["reason"]),
                         "prior_verdict": original["adjudicated_verdict"]}
                if code in human:
                    entry.update(verdict="valid", provenance="explicit current chat acceptance",
                                 reason="User accepted presented item; no separate dimension scores inferred")
                elif code in pending:
                    if scope is None:
                        entry.update(verdict="body_valid_metadata_scope_pending",
                                     provenance="assistant consolidation; new schema-scope question",
                                     reason="Body matches source. converse_status=proven needs a fixed-background versus all-premises reverse scope convention.")
                    else:
                        entry.update(verdict="valid", provenance="explicit user fixed-background scope decision",
                                     reason="Converse accepted with domain and antiderivative assumptions fixed; not a reverse implication to all premises.")
                entries.append(entry)
        result.append({"packet_id": row["packet_id"], "section": packet["section"],
                       "packet_sha256": sha(packet_path), "graph_sha256": digest,
                       "entries": entries})
    by_kind = {kind: dict(Counter(e["verdict"] for p in result for e in p["entries"] if e["kind"] == kind))
               for kind in ("node", "relation")}
    inputs = (mapping_path, chat_path, previous_path, Path(__file__))
    if scope_path is not None:
        inputs += (scope_path,)
    return {"status": "consolidated existing evidence; no pending items" if scope else "consolidated existing evidence; metadata scope pending",
            "inputs": {str(p): sha(p) for p in inputs},
            "packets": result, "counts": by_kind,
            "provenance_counts": dict(Counter(e["provenance"] for p in result for e in p["entries"])),
            "pending_ids": [] if scope else sorted(pending),
            "limits": ["Not independent new annotation: unchanged decisions inherit explicit previous review provenance.",
                       "Current chat acceptance covers P01 only; not extrapolated to other predictions.",
                       "Graphs, Gold and historical scores are unchanged.",
                       "Pending metadata is not classified as a false mathematical body."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet_dir", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--scope-decision", type=Path)
    args = parser.parse_args()
    result = consolidate(args.packet_dir, args.previous, args.scope_decision)
    with args.out.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({k: result[k] for k in ("counts", "provenance_counts", "pending_ids")},
                     ensure_ascii=False, indent=2))
