"""BM25 candidate retrieval from real KU objectives; never reads Gold labels."""
import argparse
import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi


def tokenize(text):
    tokens = []
    for segment in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z]{3,}", text):
        if segment.isascii():
            tokens.append(segment.casefold())
        else:
            for size in (2, 3):
                tokens.extend(segment[i:i+size] for i in range(len(segment)-size+1))
    return tokens


def texts(unit):
    centers = {n["id"] for n in unit["centers_on"]}
    query = [unit["description"]]
    document = [unit["name"], unit["description"]]
    for n in unit["full_member_nodes"]:
        if n["id"] in centers or n["type"] in {"Claim", "Method"}:
            query.extend([n["name"], n.get("description", "")])
        if n["id"] in centers:
            document.extend([n["name"], *n.get("aliases", []), n.get("description", "")])
    return "\n".join(query), "\n".join(dict.fromkeys(document))


def retrieve(catalog, base, dependent_ids, top_k=3):
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    units = catalog["knowledge_units"]
    by_id = {u["id"]: u for u in units}
    if len(by_id) != len(units) or set(dependent_ids) - set(by_id):
        raise ValueError("Invalid endpoint IDs")
    chapters = list(dict.fromkeys(u["chapter"] for u in units))

    def position(u):
        if not u["cards"]:
            raise ValueError("Missing structural location")
        return chapters.index(u["chapter"]), min(c["start_line"] for c in u["cards"])
    text = {u["id"]: texts(u) for u in units}
    docs = [tokenize(text[u["id"]][1]) for u in units]
    if not any(docs):
        raise ValueError("No indexable objectives")
    index = BM25Okapi(docs, k1=1.5, b=.75)
    pairs = {(c["source_ku"], c["target_ku"]): c for c in base["candidates"]}
    if len(pairs) != len(base["candidates"]):
        raise ValueError("Duplicate baseline candidates")
    if any(a not in by_id or b not in by_id for a, b in pairs):
        raise ValueError("Unknown baseline endpoint")
    added, rankings = [], []
    for dependent in dependent_ids:
        u = by_id[dependent]
        query_tokens = sorted(set(tokenize(text[dependent][0])))
        scores = index.get_scores(query_tokens)
        eligible = [(float(scores[i]), v) for i, v in enumerate(units)
                    if v["id"] != dependent and position(v) < position(u) and scores[i] > 0]
        eligible.sort(key=lambda item: (-item[0], item[1]["id"]))
        ranking = []
        for rank, (score, v) in enumerate(eligible, 1):
            shared = sorted(set(query_tokens) & set(tokenize(text[v["id"]][1])))
            row = {"target_ku": v["id"], "target_name": v["name"], "rank": rank,
                   "bm25_score": score, "shared_tokens": shared[:20], "selected": rank <= top_k}
            ranking.append(row)
            if rank <= top_k and (dependent, v["id"]) not in pairs:
                c = {"source_ku": dependent, "target_ku": v["id"], "source_name": u["name"],
                     "target_name": v["name"], "signals": [], "chain_via": [], "chain_total": 0,
                     "n_edges": 0, "retrieval": {"channel": "objective_bm25", **row},
                     "note": "Search candidate, not an asserted prerequisite or new L1 fact"}
                pairs[(dependent, v["id"])] = c
                added.append(c)
        rankings.append({"source_ku": dependent, "source_name": u["name"],
                         "query": text[dependent][0], "ranking": ranking})
    return {"candidates": list(pairs.values()), "new_candidates": added, "rankings": rankings,
            "index_documents": {u["id"]: text[u["id"]][1] for u in units},
            "configuration": {"top_k": top_k, "earlier_only": True, "k1": 1.5, "b": .75,
                              "query_ids": dependent_ids, "reference_input": False}}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("catalog", type=Path)
    p.add_argument("baseline", type=Path)
    p.add_argument("config", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    load = lambda f: json.loads(f.read_text(encoding="utf-8"))
    cfg = load(a.config)
    result = retrieve(load(a.catalog), load(a.baseline), cfg["dependent_ids"], cfg["top_k"])
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
