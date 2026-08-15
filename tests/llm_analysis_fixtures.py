from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    generate_analysis_brief,
)
from defence_project_analytics.brief.registry import required_tables
from defence_project_analytics.llm_analysis import AnalysisPromptRequest, build_analysis_prompt


def make_c1_bundle(tmp_path: Path) -> Path:
    source = tmp_path / "source-stage"
    (source / "tables").mkdir(parents=True)
    metadata = {
        "reportContractVersion": "1.0.0",
        "analysisType": "stageDifficulty",
        "analysisVersion": "1.0.0",
        "generatedAtUtc": "2026-08-15T00:00:00Z",
        "scope": {
            "environment": "Test", "stageKey": "stage1", "contentVersion": 4,
            "appVersion": None, "releaseId": None, "releaseChannel": None,
            "releaseType": None, "isDevelopmentBuild": None,
            "uploadedAtUtcStart": None, "uploadedAtUtcEnd": "2026-08-15T00:00:00Z",
        },
        "sample": {"finalAttempts": 10, "uniquePlayers": 2, "deaths": 5},
        "quality": {
            "telemetryCompleteRate": {"count": 9, "denominator": 10, "ratio": 0.9},
            "detailCoverageRate": {"count": 8, "denominator": 10, "ratio": 0.8},
        },
        "definitions": {"population": "final attempts"},
        "warnings": [{"code": "LOW_SAMPLE_ATTEMPTS", "message": "Small sample."}],
        "dryRunEstimatedBytes": 1,
    }
    metrics = {
        "outcome": {
            "finalAttempts": 10, "uniquePlayers": 2, "clears": 2, "deaths": 5,
            "abandons": 3, "unrecognizedOutcomes": 0,
            "clearRate": {"count": 2, "denominator": 7, "ratio": 2 / 7},
        },
        "survival": {"observedCount": 10, "missingCount": 0, "p25": 20, "median": 40, "p75": 70},
        "deathTiming": {}, "deathConcentration": {}, "deathCauses": {},
        "incomingDamage": {}, "threat": {}, "playerStateAtDeath": {},
        "dataQuality": metadata["quality"],
    }
    (source / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (source / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (source / "report.md").write_text("# Source report\n", encoding="utf-8")
    for filename, columns in required_tables("stageDifficulty").items():
        (source / "tables" / filename).write_text(",".join(columns) + "\n", encoding="utf-8")
    return generate_analysis_brief(
        AnalysisBriefRequest(mode="singleVersion", source_report_paths=(source,)),
        output_root=tmp_path / "generated",
        workspace_root=tmp_path,
    )


def make_package(tmp_path: Path):
    source = make_c1_bundle(tmp_path)
    package = build_analysis_prompt(
        AnalysisPromptRequest(source, analysis_objective="난이도 의도를 검토한다."),
        workspace_root=tmp_path,
    )
    return source, package


def valid_response(package) -> dict[str, Any]:
    evidence_id = next(iter(package.source.evidence_by_id))
    evidence = package.source.evidence_by_id[evidence_id]
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
            "qualitativeOverview": "제공된 증거는 추가 검토가 필요하다.",
            "observationIds": ["OBS-001"],
            "hypothesisIds": ["HYP-001"],
            "evidenceGapIds": ["GAP-001"],
            "changeCandidateIds": ["CHG-001"],
        },
        "observations": [{
            "id": "OBS-001", "findingType": "LimitedOrUnavailable",
            "qualitativeStatement": "표본 제한이 있는 결과가 관측됐다.",
            "evidenceIds": [evidence_id], "importance": "Core",
        }],
        "interpretations": [{
            "id": "INT-001", "statement": "현재 결과만으로 방향을 확정하기 어렵다.",
            "evidenceIds": [evidence_id], "limitationEvidenceIds": [evidence_id],
            "limitationWarningCodes": list(evidence.get("warningCodes", []))[:1],
        }],
        "hypotheses": [{
            "id": "HYP-001", "statement": "표본 안정성이 결과 해석에 관련될 가능성이 있다.",
            "supportingEvidenceIds": [evidence_id], "counterEvidenceIds": [],
            "counterEvidenceSearchStatus": "NotIdentifiedInSuppliedBrief",
            "limitationWarningCodes": list(evidence.get("warningCodes", []))[:1],
            "assumptions": ["선택된 brief가 검토 범위다."],
            "alternativeExplanations": ["모집단 구성이 다를 가능성이 있다."],
            "evidenceGapIds": ["GAP-001"],
            "falsificationChecks": ["관련 분석을 다시 실행해 패턴을 확인한다."],
        }],
        "evidenceGaps": [{
            "id": "GAP-001", "question": "표본 제한이 해소된 뒤에도 패턴이 유지되는가?",
            "whyItMatters": "관측 패턴의 안정성을 구분하는 데 필요하다.",
            "relatedEvidenceIds": [evidence_id], "suggestedAnalysis": "stageDifficulty",
            "requiresNewTelemetry": False,
        }],
        "changeCandidates": [{
            "id": "CHG-001", "domain": str(evidence["domain"]),
            "target": {
                "targetType": "EvidenceMetric", "domain": str(evidence["domain"]),
                "entityType": None, "entityKey": None,
                "metricFamily": str(evidence["metricFamily"]), "metric": str(evidence["metric"]),
                "description": None, "requiresGameDesignContext": False,
            },
            "actionType": "Investigate",
            "proposedChange": {
                "description": "추가 분석으로 관측 패턴을 확인한다.", "parameter": None,
                "direction": "NotSpecified", "amountPercent": None,
                "heuristic": False, "magnitudeBasis": None,
            },
            "rationale": "제한된 evidence를 변경 근거로 바로 사용하지 않는다.",
            "supportingEvidenceIds": [evidence_id], "counterEvidenceIds": [],
            "counterEvidenceSearchStatus": "NotIdentifiedInSuppliedBrief",
            "limitationWarningCodes": list(evidence.get("warningCodes", []))[:1],
            "risks": ["추가 표본에서도 불확실성이 남을 수 있다."],
            "expectedObservableDirections": [], "validationPlanId": None,
        }],
        "validationPlans": [],
    }

