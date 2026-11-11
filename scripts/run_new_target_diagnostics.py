"""Bounded post-hoc judge coverage and source-evidence context diagnostics."""
import argparse
import copy
import json
import shutil
from collections import Counter
from pathlib import Path

from run_ku_candidate_retrieval_experiment import endpoint
from run_semantic_ku_candidate_experiment import PROPOSER, position
from run_ku_prerequisite_phase import Native, MODEL, read, sha, write
from score_ku_prerequisite_evaluation import score_evaluation


def passages(u,limit=12000):
    result=[]; remaining=limit
    for c in u["cards"]:
        if c.get("teaching_unit_kind")=="example":
            continue
        p=Path(c["source_path"]); lines=p.read_text(encoding="utf-8").splitlines()
        start,end=c["start_line"],c["end_line"]
        text="\n".join(lines[start-1:end])
        if not text or remaining<=0:
            continue
        clipped=text[:remaining]; remaining-=len(clipped)
        result.append({"passage_id":c["card_id"],"source":p.name,"start_line":start,"end_line":end,
            "roles":c.get("discourse_roles",[]),"kind":c.get("teaching_unit_kind"),"text":clipped,
            "truncated":len(clipped)<len(text)})
    return result


def validate_enhanced(answer,tasks,units):
    rows=answer.get("proposals")
    if not isinstance(rows,list):raise ValueError("Missing proposals")
    ti={t["source"]["id"]:t for t in tasks}; seen=set(); counts=Counter(); out=[]
    for r in rows:
        s,t=r.get("source_ku"),r.get("target_ku")
        if s not in ti or t not in {a["id"] for a in ti[s]["eligible_targets"]}:
            raise ValueError("Out-of-scope proposal")
        if (s,t) in seen:raise ValueError("Duplicate proposal")
        seen.add((s,t)); counts[s]+=1
        if counts[s]>3:raise ValueError("Proposal cap exceeded")
        members={n["id"]:n for n in ti[s]["source"]["members"]}
        n=members.get(r.get("witness_node_id")); q=r.get("witness_quote")
        if n is None or not isinstance(q,str) or len(q)<4:raise ValueError("Invalid witness")
        witness={"node_id":n["id"],"quote":q}
        if r.get("evidence_source")=="source_passage":
            ps={p["passage_id"]:p for p in ti[s]["source_passages"]}
            p=ps.get(r.get("passage_id"))
            if p is None or q not in p["text"]:raise ValueError("Source quote mismatch")
            witness.update(passage_id=p["passage_id"],source=p["source"],start_line=p["start_line"],end_line=p["end_line"])
        elif r.get("evidence_source")=="member_description":
            if q not in n["description"]:raise ValueError("Member quote mismatch")
        else:raise ValueError("Missing evidence source")
        if not isinstance(r.get("reason"),str):raise ValueError("Missing reason")
        out.append({"source_ku":s,"target_ku":t,"source_name":units[s]["name"],"target_name":units[t]["name"],
            "signals":[],"chain_via":[],"chain_total":0,"n_edges":0,"proposal_witness":witness,
            "proposal_reason":r["reason"],"note":"Source-backed candidate, not asserted dependency"})
    return out


def run(root):
    out=root/"diagnostics01";out.mkdir(exist_ok=False)
    inp=root/"frozen_inputs01"; cfg=read(root/"manifest.json")
    for b in read(inp/"manifest.json")["bindings"]:
        if sha(b["path"])!=b["sha256"]:raise ValueError("Frozen inputs changed")
    units={u["id"]:u for u in read(inp/"catalog.json")["knowledge_units"]}
    reference=read(inp/"reference.json")["annotations"]
    native=Native(root,cfg)
    system=(Path(cfg["snapshot"])/"review_system.txt").read_text(encoding="utf-8")
    protocol=Path(__file__).parents[1]/"docs/prerequisite_new_target_diagnostics_protocol_2026-10-08.md"
    shutil.copy2(protocol,out/"protocol.md");shutil.copy2(__file__,out/Path(__file__).name)
    m={"status":"running","post_hoc":True,"reference_sha256":sha(inp/"reference.json"),
       "protocol_sha256":sha(out/"protocol.md"),"model":MODEL,"reasoning_effort":"low","budget_shared":True,
       "judge":"running","source_context":"pending","deadline":cfg["deadline"]}
    write(out/"manifest.json",m)

    def judge(pool,folder,extra_context=False):
        folder.mkdir(exist_ok=False);tasks=[]
        for i,c in enumerate(pool):
            v={"index":i,"edge":c["source_name"]+" depends_on "+c["target_name"],
               "source":endpoint(units[c["source_ku"]]),"target":endpoint(units[c["target_ku"]]),
               "signals":c.get("signals",[]),"chain_via":c.get("chain_via",[])}
            if extra_context:v["source_passages"]=passages(units[c["source_ku"]])
            tasks.append(v)
        for start in range(0,len(tasks),8):write(folder/f"batch_{start//8+1}.json",tasks[start:start+8])
        write(folder/"payload_manifest.json",{"system_sha256":sha(Path(cfg["snapshot"])/"review_system.txt"),
            "bindings":[{"path":str(p),"sha256":sha(p)} for p in sorted(folder.glob("batch_*.json"))]})
        pred=[]
        for p in sorted(folder.glob("batch_*.json")):
            batch=read(p);answer,usage=native.call(system,json.dumps(batch,ensure_ascii=False),MODEL)
            rows=answer.get("verdicts")
            if not isinstance(rows,list) or len(rows)!=len(batch) or {r.get("index") for r in rows}!={r["index"] for r in batch}:
                raise ValueError("Invalid review indices")
            for r in rows:
                if type(r.get("index")) is not int or not isinstance(r.get("reason"),str):raise ValueError("Invalid fields")
                role=r.get("role")
                if role not in {"required","proof_support","principle_support","related_or_none","uncertain"}:raise ValueError("Invalid role")
                if role=="required" and r.get("directness") not in {"direct","indirect"}:raise ValueError("Missing directness")
                if role!="required" and r.get("directness") is not None:raise ValueError("Unexpected directness")
                c=pool[r["index"]];v={"source_ku":c["source_ku"],"target_ku":c["target_ku"],"label":role,"reason":r["reason"]}
                if role=="required":v["directness"]=r["directness"]
                pred.append(v)
            write(folder/"predictions.json",{"verdicts":pred})
        write(folder/"predictions.json",{"verdicts":pred});return pred
    try:
        # Labels/reasons/directness are stripped; this is classifier-only, not retrieval.
        pool=[{"source_ku":r["target_ku"],"target_ku":r["prerequisite_ku"],"source_name":r["target_name"],
               "target_name":r["prerequisite_name"],"signals":[],"chain_via":[]} for r in reference]
        pred=judge(pool,out/"judge_all_pairs")
        data={"endpoints":list(units.values()),"candidates":pool,"predictions":{"verdicts":pred},"reference":reference}
        write(out/"judge_all_pairs/score_input.json",data);write(out/"judge_all_pairs/scores.json",score_evaluation(data))
        m["judge"]="completed";m["source_context"]="running";write(out/"manifest.json",m)
        print("Independent classifier diagnostic completed",len(pool),flush=True)
        original_tasks=read(root/"prediction01/proposal_input.json")
        tasks=copy.deepcopy(original_tasks)
        for t in tasks:t["source_passages"]=passages(units[t["source"]["id"]])
        enhanced_system=PROPOSER+"\n证据扩展：可以引用source_passages的真实原文。每条增加evidence_source=member_description或source_passage；后者必须给passage_id和精确witness_quote。witness_node_id仍必须来自source.members。原文角色与行号只是上下文，不是答案。"
        (out/"enhanced_system.txt").write_text(enhanced_system,encoding="utf-8");write(out/"enhanced_input.json",tasks)
        m["enhanced_input_sha256"]=sha(out/"enhanced_input.json");m["enhanced_system_sha256"]=sha(out/"enhanced_system.txt");write(out/"manifest.json",m)
        answer,usage=native.call(enhanced_system,json.dumps(tasks,ensure_ascii=False),MODEL)
        proposals=validate_enhanced(answer,tasks,units)
        baseline=read(root/"prediction01/baseline_candidates.json")["candidates"]
        pairs={(c["source_ku"],c["target_ku"]) for c in baseline}
        new=[c for c in proposals if (c["source_ku"],c["target_ku"]) not in pairs]
        write(out/"enhanced_proposals.json",{"valid_proposals":proposals,"new_candidates":new})
        extra=judge(new,out/"enhanced_new_review",extra_context=True)
        original=read(root/"prediction01/baseline/predictions.json")["verdicts"]
        combined=original+extra
        # Index fields are not used for scoring; ensure per-pair uniqueness instead.
        data={"endpoints":list(units.values()),"candidates":baseline+new,"predictions":{"verdicts":combined},"reference":reference}
        write(out/"enhanced_score_input.json",data);write(out/"enhanced_scores.json",score_evaluation(data))
        write(out/"enhanced_predictions.json",{"verdicts":combined})
        m.update(status="completed",source_context="completed",enhanced_new_candidates=len(new),
                 enhanced_union_candidates=len(baseline)+len(new),common_verdicts_reused=len(original))
        write(out/"manifest.json",m)
        print("Source context diagnostic completed",len(new),"new candidates",flush=True)
    except Exception as exc:
        m.update(status="failed",error_type=type(exc).__name__);write(out/"manifest.json",m);raise


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("root",type=Path)
    run(p.parse_args().root.resolve())
