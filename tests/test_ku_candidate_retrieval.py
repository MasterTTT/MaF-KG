import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from retrieve_ku_prerequisite_candidates import retrieve, texts, tokenize
from run_semantic_ku_candidate_experiment import validate_proposals


def unit(i, name, goal):
    node = {"id": "n"+str(i), "name": name, "type": "Claim", "description": goal, "aliases": []}
    return {"id": "u"+str(i), "name": name, "description": goal, "chapter": "chapter",
            "cards": [{"start_line": i*10+1}], "centers_on": [{"id": node["id"]}], "full_member_nodes": [node]}


def catalog():
    return {"knowledge_units": [unit(0, "加密算法", "掌握加密算法"), unit(1, "数据存储", "记录信息"),
                                unit(2, "网络发送", "发送消息"), unit(3, "数组操作", "处理数组"),
                                unit(4, "字符操作", "处理字符"), unit(5, "加密应用", "应用加密算法") ]}


def test_tokenizer_does_not_index_single_letter_formula_variables():
    assert "x" not in tokenize("f(x) 使用加密算法， AES")
    assert "加密" in tokenize("使用加密算法")
    assert "aes" in tokenize("AES")


def test_retrieval_is_limited_and_never_asserts_graph_facts():
    data = catalog()
    result = retrieve(data, {"candidates": []}, ["u5"], top_k=1)
    assert len(result["new_candidates"]) == 1
    c = result["new_candidates"][0]
    assert c["target_ku"] == "u0"
    assert c["signals"] == []
    assert c["n_edges"] == 0
    assert c["retrieval"]["rank"] == 1
    assert c["target_ku"] != c["source_ku"]


def test_labels_cannot_change_retrieval_and_baseline_is_preserved():
    data = catalog()
    altered = copy.deepcopy(data)
    altered["reference_labels"] = {"u5->u0": "false"}
    base = {"candidates": [{"source_ku": "u2", "target_ku": "u1", "note": "baseline"}]}
    a = retrieve(data, base, ["u5"])
    b = retrieve(altered, base, ["u5"])
    assert a == b
    assert a["candidates"][0] == base["candidates"][0]


def test_contains_generic_concept_is_not_target_index_anchor():
    u = unit(0, "专门方法", "处理专门任务")
    u["full_member_nodes"].append({"id": "borrowed", "name": "通用概念", "type": "Concept", "description": "罕见术语"})
    query, document = texts(u)
    assert "罕见术语" not in document
    assert "罕见术语" not in query


def test_invalid_k_and_unknown_source_fail():
    with pytest.raises(ValueError):
        retrieve(catalog(), {"candidates": []}, ["u5"], top_k=0)
    with pytest.raises(ValueError):
        retrieve(catalog(), {"candidates": []}, ["unknown"])


def proposal_fixture():
    units = {"s": {"name": "source"}, **{f"t{i}": {"name": "target"} for i in range(4)}}
    tasks = [{"source": {"id": "s", "members": [{"id": "n", "description": "应用核心算法完成目标"}]},
              "eligible_targets": [{"id": "t"+str(i)} for i in range(4)]}]
    row = {"source_ku": "s", "target_ku": "t0", "witness_node_id": "n", "witness_quote": "应用核心算法", "reason": "candidate"}
    return units, tasks, row


def test_semantic_witness_must_be_exact_and_from_source():
    units, tasks, row = proposal_fixture()
    out = validate_proposals({"proposals": [row]}, tasks, units)
    assert out[0]["signals"] == []
    with pytest.raises(ValueError, match="witness"):
        validate_proposals({"proposals": [{**row, "witness_quote": "模型新编的事实"}]}, tasks, units)


def test_semantic_scope_cap_and_duplicates_are_hard_gates():
    units, tasks, row = proposal_fixture()
    with pytest.raises(ValueError, match="Duplicate"):
        validate_proposals({"proposals": [row, row]}, tasks, units)
    with pytest.raises(ValueError, match="cap"):
        validate_proposals({"proposals": [{**row, "target_ku": "t"+str(i)} for i in range(4)]}, tasks, units)
    with pytest.raises(ValueError, match="scope"):
        validate_proposals({"proposals": [{**row, "target_ku": "unknown"}]}, tasks, units)
