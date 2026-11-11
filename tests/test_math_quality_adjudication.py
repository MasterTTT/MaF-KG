import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from apply_math_quality_adjudication import apply_decision, summarize


def test_metadata_issue_does_not_become_full_acceptance():
    verdict = apply_decision("node", "accept_body_flag_metadata")
    result = summarize([{"node_reviews": [{"adjudicated_verdict": verdict}], "relation_reviews": []}])
    assert result["node_fully_accepted"]["hit"] == 0
    assert result["node_body_and_admission_accepted"]["hit"] == 1


def test_endpoint_rejection_is_distinct_from_relation_type_error():
    assert apply_decision("relation", "reject_separate_method") == "endpoint_admission_issue"
    assert apply_decision("relation", "reject_relation_type") == "relation_type_issue"


def test_conditional_denominator_is_explicit():
    result = summarize([{"node_reviews": [], "relation_reviews": [
        {"adjudicated_verdict": "valid"}, {"adjudicated_verdict": "endpoint_admission_issue"}]}])
    assert result["relation_fully_accepted"]["total"] == 2
    assert result["relation_accepted_given_admitted_endpoints"]["total"] == 1
