from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.anthropic_transport import (
    build_compact_llm_payload,
)
from defence_project_analytics.llm_analysis.policy import (
    CausalMode,
    actionability,
    evidence_strength,
    prohibited_language,
)
from defence_project_analytics.llm_analysis.validator import (
    validate_response,
    validate_stage_a,
)
from defence_project_analytics.llm_analysis.warning_authority import (
    collect_allowed_warning_codes,
    warning_authority_digest,
)
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


@pytest.mark.parametrize("section", ["hypotheses", "changeCandidates"])
@pytest.mark.parametrize(
    ("counter_ids_present", "status"),
    [
        (False, "FoundInSuppliedBrief"),
        (True, "NotIdentifiedInSuppliedBrief"),
    ],
)
def test_canonical_counter_evidence_mismatches_remain_invalid(
    tmp_path: Path,
    section: str,
    counter_ids_present: bool,
    status: str,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    evidence_id = next(iter(package.source.evidence_by_id))
    response[section][0]["counterEvidenceIds"] = (
        [evidence_id] if counter_ids_present else []
    )
    response[section][0]["counterEvidenceSearchStatus"] = status
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


def _package_with_source_status_warning(package, *codes: str):
    brief = copy.deepcopy(package.source.brief)
    brief["sourceStatusByDomain"] = {
        "contentVersionCompare": {
            "analysisType": "contentVersionCompare",
            "warningCodes": list(codes),
        }
    }
    brief["criticalWarnings"] = []
    source = replace(package.source, brief=brief)
    return replace(package, source=source)


def test_source_status_only_warning_is_valid_for_partial_and_full_validation(
    tmp_path: Path,
) -> None:
    _, original = make_package(tmp_path)
    package = _package_with_source_status_warning(
        original,
        "BASELINE_VALUE_MISSING",
        "CANDIDATE_VALUE_MISSING",
    )
    response = valid_response(package)
    response["interpretations"][0]["limitationWarningCodes"] = [
        "BASELINE_VALUE_MISSING",
        "CANDIDATE_VALUE_MISSING",
    ]
    stage_a = {
        key: response[key]
        for key in ("observations", "interpretations", "evidenceGaps")
    }
    validate_stage_a(package, stage_a)
    validate_response(package, response)
    allowed = collect_allowed_warning_codes(package)
    assert "BASELINE_VALUE_MISSING" in allowed
    assert "CANDIDATE_VALUE_MISSING" in allowed
    visible = build_compact_llm_payload(package)["brief"]["sourceStatusByDomain"]
    assert visible["contentVersionCompare"]["warningCodes"] == [
        "BASELINE_VALUE_MISSING",
        "CANDIDATE_VALUE_MISSING",
    ]


def test_truly_unknown_warning_fails_partial_and_full_validation(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["interpretations"][0]["limitationWarningCodes"] = [
        "TOTALLY_FAKE_WARNING"
    ]
    stage_a = {
        key: response[key]
        for key in ("observations", "interpretations", "evidenceGaps")
    }
    with pytest.raises(AnalysisResponseValidationError) as partial:
        validate_stage_a(package, stage_a)
    with pytest.raises(AnalysisResponseValidationError) as full:
        validate_response(package, response)
    assert issue_codes(partial.value) == {"UNKNOWN_WARNING_CODE"}
    assert issue_codes(full.value) == {"UNKNOWN_WARNING_CODE"}
    assert partial.value.issues == full.value.issues


def test_warning_authority_is_sorted_and_digest_is_deterministic(tmp_path: Path) -> None:
    _, original = make_package(tmp_path)
    package = _package_with_source_status_warning(original, "Z_WARNING", "A_WARNING")
    first = collect_allowed_warning_codes(package)
    second = collect_allowed_warning_codes(package)
    assert first == tuple(sorted(first)) == second
    assert warning_authority_digest(package) == warning_authority_digest(package)


@pytest.mark.parametrize(
    ("section", "field", "text"),
    [
        ("observations", "qualitativeStatement", "The observed death rate was higher."),
        ("interpretations", "statement", "Deaths were associated with shorter runs in this sample."),
        (
            "hypotheses",
            "statement",
            "One hypothesis is that stage difficulty contributes to abandonment.",
        ),
    ],
)
def test_bounded_observation_association_and_explicit_hypothesis_are_accepted(
    tmp_path: Path,
    section: str,
    field: str,
    text: str,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response[section][0][field] = text
    validate_response(package, response)


@pytest.mark.parametrize(
    ("text", "expected_path"),
    [
        ("Weapon X caused more deaths.", "$.interpretations[0].statement"),
        ("Stage difficulty led to abandonment.", "$.interpretations[0].statement"),
        ("ObservedUninstall proves churn.", "$.interpretations[0].statement"),
    ],
)
def test_unsupported_causal_conclusions_remain_rejected(
    tmp_path: Path,
    text: str,
    expected_path: str,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["interpretations"][0]["statement"] = text
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert captured.value.issues[0]["code"] == "UNSUPPORTED_CAUSAL_LANGUAGE"
    assert captured.value.issues[0]["path"] == expected_path


def test_previous_live_caused_regression_remains_rejected_at_stage_a(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["interpretations"][0]["statement"] = "This caused the observed outcome."
    stage_a = {
        key: response[key]
        for key in ("observations", "interpretations", "evidenceGaps")
    }
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_stage_a(package, stage_a)
    assert captured.value.issues == (
        {
            "code": "UNSUPPORTED_CAUSAL_LANGUAGE",
            "path": "$.interpretations[0].statement",
            "message": "prohibited wording: caused",
        },
    )


@pytest.mark.parametrize(
    "text",
    [
        "This change may increase difficulty.",
        "This adjustment could reduce build diversity.",
        "The proposed tuning might worsen survivability.",
        "A possible risk is lower engagement.",
        "이 변경으로 생존성이 악화될 수 있다.",
        "빌드 다양성이 줄어들 가능성이 있다.",
        "이 조정이 특정 무기 의존도를 높일 수 있다.",
        "이 변경이 악화의 원인이 될 수 있다.",
        "이 변경이 악화의 원인일 가능성이 있다.",
    ],
)
def test_prospective_risk_mode_allows_modal_downsides(text: str) -> None:
    assert prohibited_language(
        text,
        causal_mode=CausalMode.PROSPECTIVE_RISK,
    ) is None


@pytest.mark.parametrize(
    "text",
    [
        "This change caused lower engagement.",
        "This change will cause lower engagement.",
        "This change is the reason engagement declines.",
        "This adjustment definitively results in more deaths.",
        "This change may definitely cause lower engagement.",
        "This change could certainly result in lower engagement.",
        "이 변경이 생존성 악화의 원인이다.",
        "이 변경은 반드시 사망률을 높인다.",
        "이 조정 때문에 빌드 다양성이 감소한다.",
        "이 변경이 더 많은 사망을 초래한다.",
        "이 변경은 반드시 악화될 수 있다.",
    ],
)
def test_prospective_risk_mode_rejects_causal_certainty(text: str) -> None:
    assert prohibited_language(
        text,
        causal_mode=CausalMode.PROSPECTIVE_RISK,
    ) is not None


def test_only_change_candidate_risks_use_prospective_mode(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    modal = "이 변경이 악화의 원인이 될 수 있다."
    response = valid_response(package)
    response["changeCandidates"][0]["risks"] = [modal]
    assert validate_response(package, response).change_candidates[0].risks == (modal,)

    response = valid_response(package)
    response["observations"][0]["qualitativeStatement"] = modal
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert captured.value.issues[0]["code"] == "UNSUPPORTED_CAUSAL_LANGUAGE"
    assert captured.value.issues[0]["path"] == "$.observations[0].qualitativeStatement"


@pytest.mark.parametrize(
    "text",
    [
        "이 변화가 다음 실행률 저하의 원인인지 현재 자료만으로 판단하기 어렵다.",
        "두 지표 사이의 관계가 인과적인지 확인할 추가 근거가 필요하다.",
        "난이도 변화가 이탈에 영향을 주는지 검증할 데이터가 부족하다.",
        "어느 요인이 변화의 원인인지 현재 Evidence로 구분할 수 없다.",
        "이 차이가 무기 성능 때문인지 플레이어 구성 차이 때문인지 확인해야 한다.",
        "Whether X caused Y remains unclear.",
        "Additional evidence is needed to determine whether X contributes to Y.",
        "It is not possible to determine whether the change caused the observed decline.",
        "The data does not distinguish whether X or Y explains the observed difference.",
    ],
)
def test_investigation_gap_mode_allows_unresolved_causal_questions(text: str) -> None:
    assert prohibited_language(
        text,
        causal_mode=CausalMode.INVESTIGATION_GAP,
    ) is None


@pytest.mark.parametrize(
    "text",
    [
        "난이도 상승이 이탈 증가의 원인이다.",
        "무기 성능 저하 때문에 클리어율이 감소했다.",
        "이 변경이 사망률을 높였다.",
        "무기 X가 플레이 지속률을 떨어뜨렸다.",
        "이 결과는 난이도 조정의 영향으로 발생했다.",
        "X caused Y.",
        "Y increased because of X.",
        "The change reduced retention.",
        "X led to the observed decline.",
    ],
)
def test_investigation_gap_mode_rejects_established_causal_assertions(text: str) -> None:
    assert prohibited_language(
        text,
        causal_mode=CausalMode.INVESTIGATION_GAP,
    ) is not None


@pytest.mark.parametrize("field", ["question", "whyItMatters"])
def test_only_evidence_gap_fields_use_investigation_mode(
    tmp_path: Path,
    field: str,
) -> None:
    _, package = make_package(tmp_path)
    investigative = "어느 요인이 차이의 원인이 되는지 확인할 추가 근거가 필요하다."
    response = valid_response(package)
    response["evidenceGaps"][0][field] = investigative
    validated = validate_response(package, response)
    assert (
        validated.evidence_gaps[0].question
        if field == "question"
        else validated.evidence_gaps[0].why_it_matters
    ) == investigative

    response = valid_response(package)
    response["observations"][0]["qualitativeStatement"] = investigative
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert captured.value.issues[0]["code"] == "UNSUPPORTED_CAUSAL_LANGUAGE"
    assert captured.value.issues[0]["path"] == "$.observations[0].qualitativeStatement"


@pytest.mark.parametrize("field", ["question", "whyItMatters"])
def test_evidence_gap_assertion_still_fails_at_its_exact_path(
    tmp_path: Path,
    field: str,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["evidenceGaps"][0][field] = "난이도 상승이 이탈 증가의 원인이다."
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert captured.value.issues[0]["code"] == "UNSUPPORTED_CAUSAL_LANGUAGE"
    assert captured.value.issues[0]["path"] == f"$.evidenceGaps[0].{field}"


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("이 차이가 10% 변화의 원인인지 확인해야 한다.", "FREEFORM_NUMERIC_CLAIM"),
        ("runId 값이 차이의 원인인지 확인해야 한다.", "FORBIDDEN_PRIVATE_TEXT"),
    ],
)
def test_investigation_gap_preserves_other_prose_policies(
    tmp_path: Path,
    text: str,
    expected_code: str,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["evidenceGaps"][0]["whyItMatters"] = text
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert expected_code in issue_codes(captured.value)


@pytest.mark.parametrize(
    ("risk", "expected_code"),
    [
        ("이 변경은 반드시 생존성을 악화한다.", "UNSUPPORTED_CAUSAL_LANGUAGE"),
        ("사망률이 10% 증가할 수 있다.", "FREEFORM_NUMERIC_CLAIM"),
        ("runId 노출 위험이 있다.", "FORBIDDEN_PRIVATE_TEXT"),
    ],
)
def test_risk_mode_preserves_other_prose_policies(
    tmp_path: Path,
    risk: str,
    expected_code: str,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["changeCandidates"][0]["risks"] = [risk]
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_response(package, response)
    assert expected_code in issue_codes(captured.value)
    assert any(
        item["path"] == "$.changeCandidates[0].risks[0]"
        for item in captured.value.issues
        if item["code"] == expected_code
    )
