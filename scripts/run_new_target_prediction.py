"""Prospective query-scoped baseline and semantic supplementation on frozen inputs."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from run_ku_candidate_retrieval_experiment import endpoint
from run_semantic_ku_candidate_experiment import PROPOSER, validate_proposals, position
from run_ku_prerequisite_phase import Native, MODEL, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def run(root):
    state=root/"prediction_stage.json"
    if state.exists():
        raise ValueError("No automatic continuation/retry of new-target prediction")
    inputs=root/"frozen_inputs01"
    fm=read(inputs/"manifest.json")
    for b in fm["bindings"]:
        if sha(b["path"])!=b["sha256"]:
            raise ValueError("Prospective frozen input/reference changed")
    source_config=read(root/"manifest.json")
    working=root/"prediction01"; working.mkdir(exist_ok=False)
    software=working/"software"; software.mkdir()
    for name in (Path(__file__).name,"run_ku_prerequisite_phase.py","run_ku_candidate_retrieval_experiment.py",
                 "run_semantic_ku_candidate_experiment.py","prospective_native_client.py","score_ku_prerequisite_evaluation.py"):
        shutil.copy2(Path(__file__).with_name(name),software/name)
    keys=("model","provider","reasoning_effort","max_tokens","timeout_seconds","usd_limit","token_limit","deadline","session")
    st={"status":"prepared","settings":{k:source_config[k] for k in keys},"input_manifest_sha256":sha(inputs/"manifest.json"),
        "reference_sha256":sha(inputs/"reference.json"),"query_only":True,"attempts":1,"fallback":False,"json_repair":False,
        "software_bindings":[{"path":str(p),"sha256":sha(p)} for p in software.iterdir()]}
    write(state,st)
    subprocess.run([sys.executable,"-X","utf8",str(Path(source_config["snapshot"])/"scripts/build_ku_depends_candidates.py"),
        str(inputs/"ku.json"),str(inputs/"graph.json"),"--out",str(working/"all_candidates.json")],check=True,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    query_ids=set(read(inputs/"query_config.json")["dependent_ids"])
    baseline=[c for c in read(working/"all_candidates.json")["candidates"] if c["source_ku"] in query_ids]
    write(working/"baseline_candidates.json",{"candidates":baseline})
    catalogue=read(inputs/"catalog.json")["knowledge_units"]; units={u["id"]:u for u in catalogue}
    chapters=list(dict.fromkeys(u["chapter"] for u in catalogue))
    proposal_tasks=[]
    for u in catalogue:
        if u["id"] not in query_ids:
            continue
        centers={n["id"] for n in u["centers_on"]}
        src={"id":u["id"],"name":u["name"],"goal":u["description"],
            "members":[{"id":n["id"],"name":n["name"],"description":n.get("description","")}
                       for n in u["full_member_nodes"] if n["id"] in centers or n["type"] in {"Claim","Method"}]}
        eligible=[]
        for v in catalogue:
            if position(v,chapters)<position(u,chapters):
                vc={n["id"] for n in v["centers_on"]}
                eligible.append({"id":v["id"],"name":v["name"],"goal":v["description"],
                    "centers":[{"name":n["name"],"aliases":n.get("aliases",[]),"description":n.get("description","")[:350]}
                               for n in v["full_member_nodes"] if n["id"] in vc]})
        proposal_tasks.append({"source":src,"eligible_targets":eligible})
    write(working/"proposal_input.json",proposal_tasks)
    (working/"proposal_system.txt").write_text(PROPOSER,encoding="utf-8")
    st.update(status="running",baseline_candidate_count=len(baseline),
              proposal_input_sha256=sha(working/"proposal_input.json"),proposal_system_sha256=sha(working/"proposal_system.txt"))
    write(state,st)
    native=Native(root,source_config)
    system=(Path(source_config["snapshot"])/"review_system.txt").read_text(encoding="utf-8")

    def judge(pool,folder):
        folder.mkdir(exist_ok=False)
        tasks=[{"index":i,"edge":c["source_name"]+" depends_on "+c["target_name"],
                "source":endpoint(units[c["source_ku"]]),"target":endpoint(units[c["target_ku"]]),
                "signals":c["signals"],"chain_via":c["chain_via"]} for i,c in enumerate(pool)]
        for start in range(0,len(tasks),8):
            write(folder/f"batch_{start//8+1}.json",tasks[start:start+8])
        write(folder/"payload_manifest.json",{"system_sha256":sha(Path(source_config["snapshot"])/"review_system.txt"),
            "bindings":[{"path":str(p),"sha256":sha(p)} for p in sorted(folder.glob("batch_*.json"))]})
        verdicts=[]
        for p in sorted(folder.glob("batch_*.json")):
            batch=read(p); answer,usage=native.call(system,json.dumps(batch,ensure_ascii=False),MODEL)
            rows=answer.get("verdicts")
            if not isinstance(rows,list) or len(rows)!=len(batch) or {r.get("index") for r in rows}!={r["index"] for r in batch}:
                raise ValueError("Invalid review index coverage")
            for r in rows:
                if type(r.get("index")) is not int or not isinstance(r.get("reason"),str):
                    raise ValueError("Invalid review fields")
                role=r.get("role")
                if role not in {"required","principle_support","proof_support","related_or_none","uncertain"}:
                    raise ValueError("Invalid review role")
                if role=="required" and r.get("directness") not in {"direct","indirect"}:
                    raise ValueError("Missing directness")
                if role!="required" and r.get("directness") is not None:
                    raise ValueError("Nonrequired verdict with directness")
                c=pool[r["index"]]
                v={"index":r["index"],"source_ku":c["source_ku"],"target_ku":c["target_ku"],"label":role,"reason":r["reason"]}
                if role=="required":v["directness"]=r["directness"]
                verdicts.append(v)
            write(folder/"predictions.json",{"verdicts":verdicts})
        write(folder/"predictions.json",{"verdicts":verdicts})
        return verdicts

    try:
        # Proposal is made before any query verdict is sent back to model orchestration.
        response,usage=native.call(PROPOSER,json.dumps(proposal_tasks,ensure_ascii=False),MODEL)
        proposals=validate_proposals(response,proposal_tasks,units)
        oldpairs={(c["source_ku"],c["target_ku"]) for c in baseline}
        new=[c for c in proposals if (c["source_ku"],c["target_ku"]) not in oldpairs]
        write(working/"proposal_validation.json",{"valid_proposals":proposals,"new_candidates":new,
              "witnesses_exact":True,"reference_not_sent":True})
        union=baseline+new
        write(working/"semantic_candidates.json",{"candidates":union})
        original=judge(baseline,working/"baseline")
        print("Baseline review complete",len(baseline),flush=True)
        extra=judge(new,working/"new_candidates")
        joined=original+[{**v,"index":v["index"]+len(baseline)} for v in extra]
        write(working/"semantic_predictions.json",{"verdicts":joined})
        refs=read(inputs/"reference.json")["annotations"]
        for label,pool,verdicts in (("baseline",baseline,original),("semantic",union,joined)):
            data={"endpoints":read(inputs/"ku.json")["knowledge_units"],"candidates":pool,
                  "predictions":{"verdicts":verdicts},"reference":refs}
            write(working/f"{label}_score_input.json",data)
            write(working/f"{label}_scores.json",score_evaluation(data))
    except Exception as exc:
        st.update(status="failed",error_type=type(exc).__name__)
        write(state,st)
        raise
    st.update(status="completed",baseline_candidates=len(baseline),new_candidates=len(new),union_candidates=len(union),
              reused_common_verdicts=len(baseline),valid_proposals=len(proposals))
    write(state,st)
    print("Completed",len(baseline),"baseline +",len(new),"new candidates")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("root",type=Path)
    run(p.parse_args().root.resolve())
