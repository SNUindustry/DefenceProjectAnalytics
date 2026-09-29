"""C-2 authority is determined by the use of evidence, not its source domain."""

from __future__ import annotations

from pathlib import Path

import pytest

from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    generate_analysis_brief,
)
from defence_project_analytics.llm_analysis import (
    AnalysisPromptRequest,
    AnthropicAnalysisProvider,
    build_analysis_prompt,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.anthropic_transport import (
    build_anthropic_stage_context,
    build_compact_llm_payload,
    build_evidence_alias_table,
    build_warning_alias_table,
    decision_evidence_refs,
    expand_compact_evidence_items,
)
from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.validator import validate_response
from defence_project_analytics.observed_app_return import (
    TABLE_SPECS as B8_TABLES,
    assemble_bundle,
)
from defence_project_analytics.observed_uninstall import (
    TABLE_SPECS as R3D_TABLES,
    assemble_bundle as assemble_r3d,
    attribute_app_remove_events,
)
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.run_retention import (
    TABLE_SPECS as B7_TABLES,
    RunRetentionRequest,
    _assemble_bundle as assemble_b7,
)
from defence_project_analytics.reporting.warnings import RunRetentionThresholds
from llm_analysis_fixtures import make_c1_bundle, make_package, valid_response
from test_anthropic_provider import fake_client
from test_observed_app_return import NOW as B8_NOW, _frames as b8_frames, _request as b8_request
from test_observed_uninstall import (
    APP_REMOVE_COLUMNS,
    T0 as R3D_NOW,
    bridge as r3d_bridge,
    removal as r3d_removal,
    request as r3d_request,
)
from defence_project_analytics.ga_identity_bridge import GaSourceTable
from datetime import date, timedelta
import pandas as pd
from test_run_retention import NOW as B7_NOW, retention_frames


def _factual_package(tmp_path: Path, domain: str):
    if domain == "observedAppReturn":
        bundle = assemble_bundle(
            b8_request(), b8_frames(), estimated_bytes=0, generated_at_utc=B8_NOW
        )
        tables = B8_TABLES
    elif domain == "runRetention":
        bundle = assemble_b7(
            RunRetentionRequest("Test", 1, analysis_as_of_utc=B7_NOW),
            B7_NOW, retention_frames(threshold=False), estimated_bytes=0,
            thresholds=RunRetentionThresholds(), generated_at_utc=B7_NOW,
        )
        tables = B7_TABLES
    else:
        bridge = pd.DataFrame([r3d_bridge(R3D_NOW, "pseudo-r3d")])
        events = attribute_app_remove_events(
            pd.DataFrame([
                r3d_removal(R3D_NOW + timedelta(seconds=1), "pseudo-r3d")
            ], columns=APP_REMOVE_COLUMNS),
            bridge,
        )
        bundle = assemble_r3d(
            r3d_request(), events,
            (GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),),
            estimated_bytes=0, generated_at_utc=R3D_NOW,
        )
        tables = R3D_TABLES
    source = write_report_bundle(
        bundle, output_root=tmp_path, table_specs=tables,
        include_manifest=domain in {"observedAppReturn", "observedUninstall"},
    )
    brief = generate_analysis_brief(
        AnalysisBriefRequest("single-version", (source,)),
        output_root=tmp_path / "brief", workspace_root=tmp_path,
    )
    return build_analysis_prompt(
        AnalysisPromptRequest(brief), workspace_root=tmp_path
    )


def _factual_response(package):
    evidence_id = next(iter(package.source.evidence_by_id))
    identity = package.source.identity
    return {
        "analysisVersion": "1.0.0",
        "sourceBriefIdentity": {
            "semanticOutputDigest": identity.semantic_output_digest,
            "scopeHash": identity.scope_hash,
            "mode": identity.mode,
            "baselineContentVersion": identity.baseline_content_version,
            "candidateContentVersion": identity.candidate_content_version,
        },
        "comparisonDirectionAcknowledgement": "notApplicable",
        "executiveSummary": {
            "qualitativeOverview": "관측 사실은 추가 검토가 필요하다.",
            "observationIds": ["OBS-001"], "hypothesisIds": [],
            "evidenceGapIds": ["GAP-001"], "changeCandidateIds": [],
        },
        "observations": [{
            "id": "OBS-001", "findingType": "ObservedValue",
            "qualitativeStatement": "앱 복귀가 관측됐다.",
            "evidenceIds": [evidence_id], "importance": "Core",
        }],
        "interpretations": [{
            "id": "INT-001", "statement": "관측만으로 사람의 복귀 의도를 확정할 수 없다.",
            "evidenceIds": [evidence_id],
            "limitationEvidenceIds": [evidence_id],
            "limitationWarningCodes": [],
        }],
        "evidenceGaps": [{
            "id": "GAP-001", "question": "표본이 늘어나도 관측이 유지되는가?",
            "whyItMatters": "관측 범위의 한계를 확인해야 한다.",
            "relatedEvidenceIds": [evidence_id],
            "suggestedAnalysis": None, "requiresNewTelemetry": False,
        }],
        "hypotheses": [], "changeCandidates": [], "validationPlans": [],
    }


@pytest.mark.parametrize(
    "domain", ["observedAppReturn", "runRetention", "observedUninstall"]
)
def test_factual_only_evidence_reaches_prompt_alias_and_observation(
    tmp_path: Path, domain: str,
) -> None:
    package = _factual_package(tmp_path, domain)
    source_count = len(package.source.evidence_by_id)
    assert source_count > 0
    assert len(package.request_payload["evidence"]["evidenceItems"]) == source_count
    aliases = build_evidence_alias_table(package)
    assert len(aliases.provider_refs) == source_count
    assert decision_evidence_refs(package, aliases) == ()
    compact = build_compact_llm_payload(package)
    assert len(compact["evidence"]) == source_count
    stage_a = build_anthropic_stage_context(
        package, "A", evidence_aliases=aliases,
        warning_aliases=build_warning_alias_table(package),
    )
    assert len(stage_a["evidence"]) == source_count
    response = _factual_response(package)
    result = validate_response(package, response)
    assert result.observations[0].evidence_ids == (
        response["observations"][0]["evidenceIds"][0],
    )


def test_r3d_factual_evidence_cannot_support_decision(tmp_path: Path) -> None:
    package = _factual_package(tmp_path, "observedUninstall")
    evidence_id = next(iter(package.source.evidence_by_id))
    response = _factual_response(package)
    response["hypotheses"] = [{
        "id": "HYP-001", "statement": "관측을 변경 판단 근거로 가정한다.",
        "supportingEvidenceIds": [evidence_id], "counterEvidenceIds": [],
        "counterEvidenceSearchStatus": "NotIdentifiedInSuppliedBrief",
        "limitationWarningCodes": [], "assumptions": [],
        "alternativeExplanations": [], "evidenceGapIds": [],
        "falsificationChecks": [],
    }]
    with pytest.raises(AnalysisResponseValidationError) as error:
        validate_response(package, response)
    assert "DECISION_EVIDENCE_NOT_ELIGIBLE" in {
        issue["code"] for issue in error.value.issues
    }


def test_b8_factual_only_scripted_anthropic_roundtrip_and_writer(tmp_path: Path) -> None:
    package = _factual_package(tmp_path, "observedAppReturn")
    response = _factual_response(package)
    client = fake_client(response, package)
    provider = AnthropicAnalysisProvider(client=client, sleep=lambda _: None)
    output = run_analysis_with_provider(
        package.request, provider, output_root=tmp_path / "output",
        workspace_root=tmp_path,
    )
    assert len(client.messages.create_calls) == 3
    assert (output / "analysis.json").is_file()
    assert (output / "manifest.json").is_file()
    assert provider.last_run_metadata["evidenceAliasCount"] == len(
        package.source.evidence_by_id
    )


def test_b8_decision_target_and_guardrail_authority_stays_closed(tmp_path: Path) -> None:
    package = _factual_package(tmp_path, "observedAppReturn")
    evidence_id = next(iter(package.source.evidence_by_id))
    response = _factual_response(package)
    response["hypotheses"] = [{
        "id": "HYP-001", "statement": "변경 판단 근거로 가정한다.",
        "supportingEvidenceIds": [evidence_id], "counterEvidenceIds": [],
        "counterEvidenceSearchStatus": "NotIdentifiedInSuppliedBrief",
        "limitationWarningCodes": [], "assumptions": [],
        "alternativeExplanations": [], "evidenceGapIds": [],
        "falsificationChecks": [],
    }]
    with pytest.raises(AnalysisResponseValidationError) as error:
        validate_response(package, response)
    assert "DECISION_EVIDENCE_NOT_ELIGIBLE" in {
        issue["code"] for issue in error.value.issues
    }

    response = _factual_response(package)
    response["changeCandidates"] = [{
        "id": "CHG-001", "domain": "stageDifficulty",
        "target": {
            "targetType": "EvidenceMetric", "domain": "observedAppReturn",
            "entityType": None, "entityKey": None,
            "metricFamily": "return", "metric": "observedReturnRate",
            "description": None, "requiresGameDesignContext": False,
        },
        "actionType": "Investigate",
        "proposedChange": {
            "description": "추가 자료를 검토한다.", "parameter": None,
            "direction": "NotSpecified", "amountPercent": None,
            "heuristic": False, "magnitudeBasis": None,
        },
        "rationale": "추가 근거가 필요하다.",
        "supportingEvidenceIds": [evidence_id], "counterEvidenceIds": [],
        "counterEvidenceSearchStatus": "NotIdentifiedInSuppliedBrief",
        "limitationWarningCodes": [], "risks": [],
        "expectedObservableDirections": [], "validationPlanId": None,
    }]
    with pytest.raises(AnalysisResponseValidationError) as error:
        validate_response(package, response)
    codes = {issue["code"] for issue in error.value.issues}
    assert "DECISION_EVIDENCE_NOT_ELIGIBLE" in codes
    assert "TARGET_METRIC_NOT_ELIGIBLE" in codes

    response = _factual_response(package)
    metric = {
        "domain": "observedAppReturn", "metricFamily": "return",
        "metric": "observedReturnRate",
    }
    response["validationPlans"] = [{
        "id": "VAL-001", "changeCandidateId": "CHG-001",
        "analysesToRerun": [], "metricsToWatch": [metric],
        "guardrailMetrics": [metric], "minimumEvidenceRequirements": [],
        "comparisonPlan": "NotApplicable",
        "rollbackIndicators": [{"metric": metric, "condition": "UnexpectedDirection"}],
    }]
    with pytest.raises(AnalysisResponseValidationError) as error:
        validate_response(package, response)
    paths = {
        issue["path"] for issue in error.value.issues
        if issue["code"] == "TARGET_METRIC_NOT_ELIGIBLE"
    }
    assert any("metricsToWatch" in path for path in paths)
    assert any("guardrailMetrics" in path for path in paths)
    assert any("rollbackIndicators" in path for path in paths)


def test_decision_eligible_source_regression_and_mixed_projection(tmp_path: Path) -> None:
    _, original = make_package(tmp_path / "single")
    validate_response(original, valid_response(original))
    assert decision_evidence_refs(original, build_evidence_alias_table(original))

    make_c1_bundle(tmp_path / "mixed-stage")
    b8 = assemble_bundle(
        b8_request(content_version=4), b8_frames(),
        estimated_bytes=0, generated_at_utc=B8_NOW,
    )
    b8_path = write_report_bundle(
        b8, output_root=tmp_path / "mixed-b8", table_specs=B8_TABLES,
        include_manifest=True,
    )
    brief = generate_analysis_brief(
        AnalysisBriefRequest("single-version", (tmp_path / "mixed-stage" / "source-stage", b8_path)),
        output_root=tmp_path / "mixed-brief", workspace_root=tmp_path,
    )
    package = build_analysis_prompt(AnalysisPromptRequest(brief), workspace_root=tmp_path)
    compact = build_compact_llm_payload(package)
    assert len(compact["source"]["sourceBundles"]) == 2
    assert list(expand_compact_evidence_items(compact)) == package.request_payload[
        "evidence"
    ]["evidenceItems"]
