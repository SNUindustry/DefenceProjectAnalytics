from __future__ import annotations

import json
from pathlib import Path

import pytest

from defence_project_analytics import cli
from defence_project_analytics.analysis_brief import AnalysisBriefRequest, generate_analysis_brief
from defence_project_analytics.contract_validation import validate_r4c_contracts
from defence_project_analytics.llm_analysis import (
    AnalysisPromptRequest,
    ScriptedAnalysisProvider,
    build_analysis_prompt,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.anthropic_transport import (
    build_anthropic_stage_context,
    build_compact_llm_payload,
    build_evidence_alias_table,
    expand_compact_evidence_items,
)
from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.validator import (
    _R4_SEMANTIC_OVERREACH,
    validate_response,
    validate_stage_a,
    validate_stage_b,
)
from defence_project_analytics.metric_registry import (
    RETENTION_EVIDENCE,
    comparison_metric_keys,
    decision_metric_keys,
    monitor_metric_keys,
    target_metric_keys,
)
from defence_project_analytics.retention_evidence import generate_retention_evidence_report
from defence_project_analytics.retention_evidence_policy import (
    ComparisonPlan,
    HorizonContract,
    SourceCut,
    SourceFinalizationState,
)
from llm_analysis_fixtures import make_c1_bundle, valid_response
from test_retention_evidence import AS_OF, artifacts, request


def _comparison_plan() -> ComparisonPlan:
    cut = SourceCut(
        "cohort", "a" * 64, SourceFinalizationState.FINAL, AS_OF,
    )
    return ComparisonPlan(
        horizon=HorizonContract(7, 24),
        minimum_mature_anchors_per_cohort=1,
        maximum_censoring_rate=1.0,
        baseline_mature_anchors=2,
        candidate_mature_anchors=2,
        baseline_censoring_rate=0.0,
        candidate_censoring_rate=0.0,
        backend_compatible=True,
        environment_compatible=True,
        content_release_scope_compatible=True,
        horizon_compatible=True,
        upload_grace_compatible=True,
        source_cut_compatible=True,
        baseline_source_cut=cut,
        candidate_source_cut=cut,
    )


def _r4_bundle(tmp_path: Path, *, comparison_allowed: bool) -> Path:
    sources = artifacts(tmp_path / "r4-source")
    output = generate_retention_evidence_report(
        request(comparison_plan=_comparison_plan() if comparison_allowed else None),
        gameplay_artifact=sources[0],
        app_artifact=sources[1],
        uninstall_artifact=sources[2],
        output_root=tmp_path / "r4-generated",
        restricted_output_root=tmp_path / "r4-restricted",
    )
    return output.report_path


def _combined_package(tmp_path: Path, *, comparison_allowed: bool):
    # Build the established stage source fixture, then compile it with R4-B.
    make_c1_bundle(tmp_path / "stage-fixture")
    stage_source = tmp_path / "stage-fixture" / "source-stage"
    stage_metadata_path = stage_source / "metadata.json"
    stage_metadata = json.loads(stage_metadata_path.read_text(encoding="utf-8"))
    stage_metadata["scope"]["contentVersion"] = 7
    stage_metadata["scope"]["releaseId"] = "r1"
    stage_metadata_path.write_text(
        json.dumps(stage_metadata, indent=2, sort_keys=True), encoding="utf-8",
    )
    r4 = _r4_bundle(tmp_path, comparison_allowed=comparison_allowed)
    brief = generate_analysis_brief(
        AnalysisBriefRequest(
            mode="singleVersion", source_report_paths=(stage_source, r4),
        ),
        output_root=tmp_path / "combined",
        workspace_root=tmp_path,
    )
    return r4, brief, build_analysis_prompt(
        AnalysisPromptRequest(brief, analysis_objective="검증 가능한 변경을 평가한다."),
        workspace_root=tmp_path,
    )


def _r4_metric_reference(package) -> tuple[dict[str, str], str]:
    for evidence_id, item in package.source.evidence_by_id.items():
        if (
            item["domain"] == RETENTION_EVIDENCE
            and item["metricFamily"] == "gameplayReturn"
            and item["metric"] == "returnedWithinHorizonRate"
        ):
            return {
                "domain": item["domain"],
                "metricFamily": item["metricFamily"],
                "metric": item["metric"],
            }, evidence_id
    raise AssertionError("R4 gameplay return rate evidence was not selected")


def _add_validation_plan(response: dict, r4_metric: dict[str, str]) -> None:
    candidate = response["changeCandidates"][0]
    candidate["validationPlanId"] = "VAL-001"
    stage_metric = {
        "domain": candidate["target"]["domain"],
        "metricFamily": candidate["target"]["metricFamily"],
        "metric": candidate["target"]["metric"],
    }
    response["validationPlans"] = [{
        "id": "VAL-001",
        "changeCandidateId": candidate["id"],
        "analysesToRerun": ["stageDifficulty", "retentionEvidence"],
        "metricsToWatch": [stage_metric, r4_metric],
        "guardrailMetrics": [],
        "minimumEvidenceRequirements": ["ClearExistingLowSampleWarning"],
        "comparisonPlan": "NewContentVersionVsCurrentUsingContentVersionCompare",
        "rollbackIndicators": [],
    }]


def test_r4c_c1_authority_privacy_and_provider_round_trip(tmp_path: Path) -> None:
    r4, brief, package = _combined_package(tmp_path, comparison_allowed=True)
    assert validate_r4c_contracts(r4).ready
    evidence_document = json.loads((brief / "evidence.json").read_text(encoding="utf-8"))
    r4_items = [
        item for item in evidence_document["evidenceItems"]
        if item["domain"] == RETENTION_EVIDENCE
    ]
    assert len(r4_items) == 11
    assert all(item["authority"]["factualEligible"] for item in r4_items)
    assert all(not item["authority"]["decisionEligible"] for item in r4_items)
    assert all(not item["authority"]["targetEligible"] for item in r4_items)
    assert all(not item["authority"]["guardrailEligible"] for item in r4_items)
    assert all(not item["authority"]["rollbackEligible"] for item in r4_items)
    comparison_items = [item for item in r4_items if item["authority"]["comparisonEligible"]]
    assert len(comparison_items) == 6
    assert all(item["authority"]["monitorOnlyEligible"] for item in comparison_items)
    assert {
        "DirectBehaviorObservation", "DirectLifecycleObservation",
        "DirectRemovalObservation", "BoundedAbsenceObservation",
        "CensoredObservation", "SourceQualityObservation",
    }.issubset({item["authority"]["evidenceClass"] for item in r4_items})
    assert any(
        item["value"].get("boundedAbsenceCount") == 1 for item in r4_items
    )
    encoded = json.dumps(evidence_document, ensure_ascii=False)
    for forbidden in (
        "telemetryPlayerId", "canonicalProfileId", "retentionBridgeId",
        "userPseudoId", "episodeId",
    ):
        assert forbidden not in encoded

    compact = build_compact_llm_payload(package)
    expanded = expand_compact_evidence_items(compact)
    canonical = tuple(package.source.evidence["evidenceItems"])
    assert expanded == canonical
    projected = [item for item in compact["evidence"] if item["domain"] == RETENTION_EVIDENCE]
    assert projected and all("authority" in item for item in projected)


def test_r4_monitor_only_allowed_but_decision_target_guardrail_rollback_denied(
    tmp_path: Path,
) -> None:
    _, _, package = _combined_package(tmp_path, comparison_allowed=True)
    response = valid_response(package)
    metric_ref, r4_evidence_id = _r4_metric_reference(package)
    _add_validation_plan(response, metric_ref)
    validate_response(package, response)
    stage_a = validate_stage_a(package, {
        "observations": response["observations"],
        "interpretations": response["interpretations"],
        "evidenceGaps": response["evidenceGaps"],
    })
    stage_b = validate_stage_b(package, {
        "hypotheses": response["hypotheses"],
        "changeCandidates": response["changeCandidates"],
    }, stage_a)
    aliases = build_evidence_alias_table(package)
    stage_b_context = build_anthropic_stage_context(
        package, "B", stage_a=stage_a, evidence_aliases=aliases,
    )
    assert all(
        item["domain"] != RETENTION_EVIDENCE
        for item in stage_b_context["evidence"]
    )
    assert all(
        not item["key"].startswith("retentionEvidence::")
        for item in stage_b_context["metricCatalog"]
    )
    stage_c_context = build_anthropic_stage_context(
        package, "C", stage_a=stage_a, stage_b=stage_b,
    )
    assert sum(
        item["key"].startswith("retentionEvidence::")
        for item in stage_c_context["metricCatalog"]
    ) == 6
    output = run_analysis_with_provider(
        package.request,
        ScriptedAnalysisProvider(
            response=response, expected_prompt_digest=package.prompt_digest,
        ),
        output_root=tmp_path / "scripted-output",
        workspace_root=tmp_path,
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    analysis = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    assert manifest["validationStatus"] == "Valid"
    assert any(
        metric == metric_ref
        for plan in analysis["validationPlans"]
        for metric in plan["metricsToWatch"]
    )

    for field in ("guardrailMetrics",):
        rejected = json.loads(json.dumps(response))
        rejected["validationPlans"][0][field].append(metric_ref)
        with pytest.raises(AnalysisResponseValidationError):
            validate_response(package, rejected)
    rejected = json.loads(json.dumps(response))
    rejected["validationPlans"][0]["rollbackIndicators"].append({
        "metric": metric_ref, "condition": "UnexpectedDirection",
    })
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, rejected)
    rejected = json.loads(json.dumps(response))
    rejected["hypotheses"][0]["supportingEvidenceIds"] = [r4_evidence_id]
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, rejected)
    rejected = json.loads(json.dumps(response))
    rejected["changeCandidates"][0]["supportingEvidenceIds"] = [r4_evidence_id]
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, rejected)
    rejected = json.loads(json.dumps(response))
    rejected["changeCandidates"][0]["target"].update({
        "domain": metric_ref["domain"],
        "metricFamily": metric_ref["metricFamily"],
        "metric": metric_ref["metric"],
    })
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, rejected)


def test_r4_runtime_denied_fails_monitor_only_closed(tmp_path: Path) -> None:
    _, _, package = _combined_package(tmp_path, comparison_allowed=False)
    response = valid_response(package)
    metric_ref, _ = _r4_metric_reference(package)
    _add_validation_plan(response, metric_ref)
    with pytest.raises(AnalysisResponseValidationError) as exc:
        validate_response(package, response)
    assert any(
        item["code"] == "MONITOR_METRIC_RUNTIME_AUTHORITY_DENIED"
        for item in exc.value.issues
    )


def test_r4_semantic_overreach_is_rejected_for_direct_citation(tmp_path: Path) -> None:
    _, _, package = _combined_package(tmp_path, comparison_allowed=True)
    response = valid_response(package)
    _, evidence_id = _r4_metric_reference(package)
    response["observations"][0].update({
        "findingType": "ObservedValue",
        "qualitativeStatement": "This proves users churned.",
        "evidenceIds": [evidence_id],
    })
    with pytest.raises(AnalysisResponseValidationError) as exc:
        validate_response(package, response)
    assert any(item["code"] == "R4_SEMANTIC_OVERREACH" for item in exc.value.issues)


@pytest.mark.parametrize(
    "text",
    [
        "5 of 6 anchors had a new attempt observed.",
        "No app_remove observation is available.",
        "B-8 had 0 eligible anchors.",
        "Retention evidence is limited by lifecycle eligibility.",
    ],
)
def test_r4_exact_event_vocabulary_has_no_player_state_overreach(text: str) -> None:
    assert _R4_SEMANTIC_OVERREACH.search(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "5 of 6 players were retained.",
        "1 player churned.",
        "The right-censored anchor represents churn.",
    ],
)
def test_r4_player_state_vocabulary_remains_semantic_overreach(text: str) -> None:
    assert _R4_SEMANTIC_OVERREACH.search(text) is not None


def test_r4c_registry_and_local_cli_contract(tmp_path: Path, monkeypatch, capsys) -> None:
    assert len({key for key in comparison_metric_keys() if key[0] == RETENTION_EVIDENCE}) == 6
    assert monitor_metric_keys() == {
        key for key in comparison_metric_keys() if key[0] == RETENTION_EVIDENCE
    }
    assert not {key for key in decision_metric_keys() if key[0] == RETENTION_EVIDENCE}
    assert not {key for key in target_metric_keys() if key[0] == RETENTION_EVIDENCE}
    bundle = _r4_bundle(tmp_path, comparison_allowed=True)
    monkeypatch.setattr(
        "defence_project_analytics.cli.get_client",
        lambda *_a, **_k: pytest.fail("R4-C validation must stay local"),
    )
    assert cli.main([
        "validate-r4c-contracts", "--normal-bundle", str(bundle),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
