import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_external_baseline_execution import edc_stage_counts


def test_canonicalization_losses_do_not_replace_final_graph_with_open_graph():
    audit = {"result": {"open_triplets": [[["A", "r", "B"], ["A", "condition", "C"]]],
                        "canonical_triplets": [[["A", "uses", "B"], None]],
                        "target_schema_unchanged": True},
             "parse_events": [{"dropped": [{"reason": "choice rejected"}]}, {"dropped": []}]}
    sizes = edc_stage_counts(audit)
    assert sizes["extracted_parsed_triplets"] == 2
    assert sizes["canonical_null_slots"] == 1
    assert sizes["canonical_triplet_slots"] == 2
    assert sizes["parser_drop_events"] == 1
    assert sizes["target_schema_unchanged"] is True
