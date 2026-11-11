"""Freeze reviewed input scope and source-backed prospective reference before prediction."""
import argparse
import copy
import shutil
from collections import Counter
from pathlib import Path

from run_ku_prerequisite_phase import read, sha, write


def freeze(root):
    if (root/"prediction_stage.json").exists():
        raise ValueError("Do not revise reference after prediction")
    assembly=root/"reviewed_assembly01"
    for b in read(assembly/"manifest.json")["bindings"]:
        if sha(b["path"])!=b["sha256"]:
            raise ValueError("Reviewed assembly changed")
    raw=read(root/"prospective_source_review.json")
    catalog=copy.deepcopy(read(assembly/"catalog.json"))
    correction=raw["goal_correction"]
    u=next(u for u in catalog["knowledge_units"] if (u["job_id"],u["goal_id"])==(correction["job_id"],correction["goal_id"]))
    original=u["description"]
    u["description"]=correction["description"]
    out=root/"frozen_inputs01"; out.mkdir(exist_ok=False)
    write(out/"catalog.json",catalog)
    write(out/"ku.json",{"knowledge_units":[{k:v for k,v in u.items() if k not in {"cards","full_member_nodes"}} for u in catalog["knowledge_units"]]})
    shutil.copy2(assembly/"graph.json",out/"graph.json")
    shutil.copy2(assembly/"query_config.json",out/"query_config.json")
    write(out/"goal_correction.json",{"original":original,"reviewed":correction,"first_layer_modified":False,
         "members_modified":False,"scope_review_before_prerequisite_prediction":True})
    slots={k:next(u for u in catalog["knowledge_units"] if (u["job_id"],u["goal_id"])==tuple(v)) for k,v in raw["slots"].items()}
    srcnames={"ch5":"第五章_全章_无习题.md","ch7":"第七章_全章_无习题.md"}

    def bind(row):
        a,b=slots[row["source"]],slots[row["prerequisite"]]
        result={"id":row.get("id","bridge"),"target_ku":a["id"],"prerequisite_ku":b["id"],
                "target_name":a["name"],"prerequisite_name":b["name"],"role":row["role"],
                "reason":row["reason"],"provenance":"assistant_source_review_before_prediction","evidence":[]}
        if row["role"]=="required":
            result["directness"]=row["directness"]
        if "required_scope" in row:
            result["required_scope"]=row["required_scope"]
        if "via" in row:
            result["via_ku"]=slots[row["via"]]["id"]
        for c in row["citations"]:
            ch,r=c.split(":"); start,end=map(int,r.split("-"))
            p=root/"snapshot/input"/srcnames[ch]; lines=p.read_text(encoding="utf-8").splitlines()
            if not 1<=start<=end<=len(lines):
                raise ValueError("Source evidence range invalid")
            result["evidence"].append({"source_path":str(p),"sha256":sha(p),"start_line":start,
                "end_line":end,"text":"\n".join(lines[start-1:end])})
        return result
    annotations=[bind(r) for r in raw["annotations"]]
    if len(annotations)!=len({(r["target_ku"],r["prerequisite_ku"]) for r in annotations}):
        raise ValueError("Duplicate prospective pair")
    query_ids=set(read(out/"query_config.json")["dependent_ids"])
    if any(r["target_ku"] not in query_ids for r in annotations):
        raise ValueError("Nonquery reference mixed into new-target score")
    write(out/"reference.json",{"annotations":annotations,"bridges":[bind(r) for r in raw["explanation_bridges"]],
         "status":"Frozen source-reviewed prospective finite reference, not independent expert Gold",
         "labels_precede_predictions":True,"unlisted_pairs":"Unlabeled, not negatives","outside_scope":raw["outside_scope"]})
    files=list(out.iterdir())
    write(out/"manifest.json",{"status":"frozen_before_prediction","software_sha256":sha(__file__),
         "source_review_sha256":sha(root/"prospective_source_review.json"),
         "bindings":[{"path":str(p),"sha256":sha(p)} for p in files],
         "role_counts":dict(Counter(r["role"] for r in annotations)),
         "direct_positive_count":sum(r["role"]=="required" and r["directness"]=="direct" for r in annotations)})
    print(read(out/"manifest.json")["role_counts"],"direct",read(out/"manifest.json")["direct_positive_count"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("root",type=Path)
    freeze(p.parse_args().root.resolve())
