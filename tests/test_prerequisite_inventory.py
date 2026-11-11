import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "audit_prerequisite_inventory", Path(__file__).resolve().parents[1] / "scripts/audit_prerequisite_inventory.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_chain_alternative_path_is_not_cycle():
    data = {"depends_on": [{"source_ku": s, "target_ku": t}
                           for s, t in [("a", "b"), ("b", "c"), ("a", "c")]]}
    metrics, rows = module.inventory(data, {k: {"name": k} for k in "abcd"})
    assert metrics["pairs_with_alternative_path"] == 1
    assert metrics["cycle_edges"] == 0
    assert metrics["units_without_incident_edges"] == 1


def test_cycle_and_duplicate_detected():
    data = {"depends_on": [{"source_ku": s, "target_ku": t}
                           for s, t in [("a", "b"), ("b", "a"), ("a", "b")]]}
    metrics, _ = module.inventory(data, {k: {"name": k} for k in "ab"})
    assert metrics["cycle_edges"] == 2
    assert metrics["duplicate_records"] == 1


def test_review_flags_are_not_inferred_from_filename():
    data = {"depends_on": [{"source_ku": "a", "target_ku": "b"}],
            "audit": {"reviewed_edges": [{"source_ku": "a", "target_ku": "b",
                                          "audit": {"baseline": True, "reviewed": False}}]}}
    metrics, _ = module.inventory(data, {k: {"name": k} for k in "ab"})
    assert metrics["review_flags"] == {"not_reviewed": 1}
