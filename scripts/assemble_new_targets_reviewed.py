"""Explicit offline supplement for reused Concept centers in statement goals."""
import argparse
import copy
from collections import defaultdict
from pathlib import Path

from run_ku_prerequisite_phase import audit, read, sha, write

CHAPTERS = {"第一章":1,"第二章":2,"第三章":3,"第四章":4,"第五章":5,"第六章":6,"第七章":7}


def resolve_reused_concept_centers(catalogue):
    result = copy.deepcopy(catalogue)
    owners = defaultdict(list)
    for u in result:
        for c in u["centers_on"]:
            owners[c["id"]].append((u,c))
    corrections = []
    for nid, values in owners.items():
        if len(values)<2:
            continue
        if len({(c["name"],c["type"]) for _,c in values})!=1 or values[0][1]["type"]!="Concept":
            raise ValueError("Only identically named reused Concept centers can be resolved")
        values.sort(key=lambda pair:(CHAPTERS[pair[0]["chapter"]],min(c["start_line"] for c in pair[0]["cards"])))
        primary = values[0][0]
        for u,c in values[1:]:
            remaining = [x for x in u["centers_on"] if x["id"]!=nid]
            if not any(x["type"] in {"Claim","Method"} for x in remaining):
                raise ValueError("Later goal has no distinct statement/method center; source review needed")
            before = {x["id"] for x in u["centers_on"]+u["contains"]}
            if any(x["id"]==nid for x in u["contains"]):
                raise ValueError("Duplicate internal membership")
            original_centers = copy.deepcopy(u["centers_on"])
            u["centers_on"] = remaining
            u["contains"].append(c)
            if before!={x["id"] for x in u["centers_on"]+u["contains"]}:
                raise ValueError("Correction changed mathematics membership")
            corrections.append({"node_id":nid,"node_name":c["name"],"canonical_center_ku":primary["id"],
                "later_ku":u["id"],"later_name":u["name"],"original_centers":original_centers,
                "corrected_centers":remaining,"membership_preserved":True,
                "policy":"Earlier concept center retained; later statement/method goal treats the same Concept as a reused member"})
    return result, corrections


def assemble(root):
    m=read(root/"manifest.json")
    audit(m)
    if not all(j["status"]=="completed" and sha(j["output"])==j["sha256"] for j in m["jobs"]):
        raise ValueError("No assembly of incomplete organization")
    out=root/"reviewed_assembly01"
    out.mkdir(exist_ok=False)
    catalogue=copy.deepcopy(read(Path(m["snapshot"])/"old_catalog.json")["knowledge_units"])
    old=read(Path(m["snapshot"])/"old_graph.json")
    nodes={n["id"]:n for n in old["nodes"]}
    rels={(r["source_id"],r["type"],r["target_id"]):r for r in old["relations"]}
    queries=[]
    for j in m["jobs"]:
        data=read(j["output"]); nd={n["id"]:n for n in read(j["plan"])["nodes"]}
        for u,g in zip(data["knowledge_units"],data["objective_plans"][j["section"]],strict=True):
            if j["select_goals"] and g["goal_id"] not in j["select_goals"]:
                continue
            member_ids=[c["id"] for c in u["centers_on"]+u["contains"]]
            catalogue.append({**u,"job_id":j["id"],"goal_id":g["goal_id"],"chapter":j["chapter"],
                "cards":[c for c in data["planner_cards"][j["section"]] if c["card_id"] in g["card_ids"]],
                "full_member_nodes":[nd[n] for n in member_ids]})
            if j["is_query"]:
                queries.append(u["id"])
            for n in member_ids:
                if n in nodes and (nodes[n]["name"],nodes[n]["type"])!=(nd[n]["name"],nd[n]["type"]):
                    raise ValueError("Conflicting actual node identity")
                nodes.setdefault(n,nd[n])
    catalogue,corrections=resolve_reused_concept_centers(catalogue)
    catalogue.sort(key=lambda u:(CHAPTERS[u["chapter"]],min(c["start_line"] for c in u["cards"]),u["id"]))
    for p in dict.fromkeys(j["graph"] for j in m["jobs"]):
        for r in read(p)["relations"]:
            if r["source_id"] in nodes and r["target_id"] in nodes:
                rels.setdefault((r["source_id"],r["type"],r["target_id"]),r)
    write(out/"catalog.json",{"knowledge_units":catalogue})
    write(out/"ku.json",{"knowledge_units":[{k:v for k,v in u.items() if k not in {"cards","full_member_nodes"}} for u in catalogue]})
    write(out/"graph.json",{"nodes":list(nodes.values()),"relations":list(rels.values())})
    write(out/"query_config.json",{"dependent_ids":queries,"max_proposals_per_query":3})
    write(out/"center_corrections.json",{"corrections":corrections,"mathematical_membership_changed":False,
        "reference_or_predictions_used":False,"new_model_requests":0})
    write(out/"manifest.json",{"status":"completed","original_organize_status":m["stages"]["organize"],
        "software_sha256":sha(__file__),"new_api_requests":0,"query_ids":queries,
        "bindings":[{"path":str(out/n),"sha256":sha(out/n)} for n in ["catalog.json","ku.json","graph.json","query_config.json","center_corrections.json"]]})
    print("Reviewed assembly",len(catalogue),"KUs,",len(queries),"new queries,",len(corrections),"reused-center correction")
    for u in catalogue:
        if u["id"] in queries or u["job_id"] in {"ch1_s3","ch5_s1","ch5_s2"}:
            print(u["job_id"],u["goal_id"],u["id"],u["name"],u["description"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("root",type=Path)
    assemble(p.parse_args().root.resolve())
