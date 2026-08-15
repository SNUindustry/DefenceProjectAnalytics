from __future__ import annotations

import copy
from pathlib import Path

import pytest

from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.policy import actionability, evidence_strength
from defence_project_analytics.llm_analysis.validator import validate_response
from llm_analysis_fixtures import make_package, valid_response


def issue_codes(error: AnalysisResponseValidationError) -> set[str]:
    return {item["code"] for item in error.issues}


def test_valid_limited_response_is_normalized_without_high_actionability(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    result = validate_response(package, valid_response(package))
    assert result.overall_assessment == "EvidenceLimited"
    assert result.hypotheses[0].evidence_strength == "Limited"
    assert result.change_candidates[0].actionability == "Investigate"
    assert result.invalid_evidence_reference_count == 0


def test_unknown_evidence_and_numeric_observation_are_rejected(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["observations"][0]["evidenceIds"] = ["EV-unknown"]
    response["observations"][0]["qualitativeStatement"] = "비율은 50%다."
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert {"UNKNOWN_EVIDENCE_ID", "FREEFORM_NUMERIC_CLAIM"} <= issue_codes(captured.value)


def test_counter_absence_is_only_a_supplied_brief_search_result(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    result = validate_response(package, response)
    assert result.hypotheses[0].counter_evidence_search_status == "NotIdentifiedInSuppliedBrief"
    assert result.hypotheses[0].evidence_strength == "Limited"
    response["hypotheses"][0]["counterEvidenceSearchStatus"] = "FoundInSuppliedBrief"
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert "COUNTER_EVIDENCE_INVARIANT" in issue_codes(captured.value)


def test_strong_scope_and_highest_actionability_are_not_production_approval() -> None:
    support = [
        {"domain": "stageDifficulty", "metricFamily": "outcome", "status": "Comparable", "warningCodes": []},
        {"domain": "weaponPerformance", "metricFamily": "combat", "status": "Comparable", "warningCodes": []},
    ]
    assert evidence_strength("Ready", support) == "Strong"
    assert evidence_strength("Ready", support, support[:1]) == "Moderate"
    assert actionability(
        action_type="BalanceChange", strength="Strong", brief_status="Ready",
        has_design_objective=True, conceptual_target=False, heuristic_numeric=False,
    ) == "HumanReviewCandidate"
    assert actionability(
        action_type="BalanceChange", strength="Strong", brief_status="Ready",
        has_design_objective=True, conceptual_target=False, heuristic_numeric=True,
    ) == "ExperimentCandidate"

