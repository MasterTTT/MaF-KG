import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("math_quality", Path(__file__).resolve().parents[1] / "scripts/evaluate_mathematical_graph_quality.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def graph():
    return {"nodes": [{"id": k, "name": k, "type": "Concept"} for k in "abcd"],
            "relations": [{"id": "r1", "source_id": "a", "target_id": "b", "type": "involves"},
                          {"id": "r2", "source_id": "b", "target_id": "c", "type": "uses"}]}


def test_fixed_node_universe_without_involves():
    full = module.topology(graph())
    filtered = module.topology(graph(), {"involves"})
    assert full["node_count"] == filtered["node_count"] == 4
    assert full["isolated"]["hit"] == 1
    assert filtered["isolated"]["hit"] == 2
    assert full["component_sizes"] == [3, 1]
    assert filtered["component_sizes"] == [2, 1, 1]


def test_invalid_and_self_edges_do_not_connect_nodes():
    g = graph()
    g["relations"] = [{"id": "self", "source_id": "a", "target_id": "a", "type": "uses"},
                      {"id": "bad", "source_id": "b", "target_id": "missing", "type": "uses"}]
    t = module.topology(g)
    assert t["isolated"]["hit"] == 4
    assert t["self_loop_relation_ids"] == ["self"]
    assert t["missing_endpoint_relation_ids"] == ["bad"]


def test_multirelation_edges_do_not_inflate_density():
    g = graph()
    g["relations"].append({"id": "r3", "source_id": "a", "target_id": "b", "type": "uses"})
    g["relations"].append(g["relations"][0].copy())
    t = module.topology(g)
    assert t["simple_directed_density"] == 2 / 12
    assert t["duplicate_triple_extra"] == 1


def test_empty_graph_does_not_claim_perfect_quality():
    t = module.topology({"nodes": [], "relations": []})
    assert t["isolated"]["rate"] is None
    assert t["simple_directed_density"] is None
