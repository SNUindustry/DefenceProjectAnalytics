"""Anthropic Structured Outputs schema derived from the C-2 response contract."""

from __future__ import annotations

from typing import Any, Mapping

from defence_project_analytics.llm_analysis.models import (
    ACTION_TYPES,
    ANALYSIS_VERSION,
    COMPARISON_PLANS,
    COUNTER_SEARCH_VALUES,
    DIRECTIONS,
    FINDING_TYPES,
    IMPORTANCE_VALUES,
    KNOWN_ANALYSES,
    MAX_EXECUTIVE_SUMMARY_IDS,
    MAX_OBSERVATIONS,
    MAX_RESPONSE_SECTION_ITEMS,
    MAX_ROLLBACK_INDICATORS,
    MINIMUM_REQUIREMENTS,
    OBSERVABLE_DIRECTIONS,
    ROLLBACK_CONDITIONS,
    TARGET_TYPES,
    AnalysisPromptPackage,
)


def _object(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(
    items: Mapping[str, Any],
    *,
    max_items: int,
    min_items: int = 0,
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": dict(items),
        "minItems": min_items,
        "maxItems": max_items,
    }


def _nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"anyOf": [dict(schema), {"type": "null"}]}


def _enum(values: set[str] | frozenset[str]) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(values)}


def _version_type(value: int | None) -> dict[str, Any]:
    return {"type": "null"} if value is None else {"type": "integer"}


def analysis_response_json_schema(
    package: AnalysisPromptPackage,
) -> Mapping[str, Any]:
    """Return the structured-output schema; local validation remains authoritative."""

    identity = package.source.identity
    # Exact membership and ID patterns are semantic constraints enforced by the
    # authoritative local validator. Repeating every selected ID throughout the
    # provider grammar can exceed Anthropic's schema-compilation complexity limit.
    evidence_id = {"type": "string"}
    string = {"type": "string", "minLength": 1, "maxLength": 2_000}
    nullable_string = _nullable(string)
    metric_ref = _object({
        "domain": string,
        "metricFamily": string,
        "metric": string,
    })
    expected_direction = _object({
        "domain": string,
        "metricFamily": string,
        "metric": string,
        "direction": _enum(OBSERVABLE_DIRECTIONS),
    })
    observation = _object({
        "id": string,
        "findingType": _enum(FINDING_TYPES),
        "qualitativeStatement": string,
        "evidenceIds": _array(evidence_id, min_items=1, max_items=20),
        "importance": _enum(IMPORTANCE_VALUES),
    })
    interpretation = _object({
        "id": string,
        "statement": string,
        "evidenceIds": _array(evidence_id, min_items=1, max_items=20),
        "limitationEvidenceIds": _array(evidence_id, max_items=20),
        "limitationWarningCodes": _array(string, max_items=10),
    })
    hypothesis = _object({
        "id": string,
        "statement": string,
        "supportingEvidenceIds": _array(evidence_id, min_items=1, max_items=20),
        "counterEvidenceIds": _array(evidence_id, max_items=20),
        "counterEvidenceSearchStatus": _enum(COUNTER_SEARCH_VALUES),
        "limitationWarningCodes": _array(string, max_items=10),
        "assumptions": _array(string, max_items=10),
        "alternativeExplanations": _array(string, max_items=5),
        "evidenceGapIds": _array(string, max_items=10),
        "falsificationChecks": _array(string, max_items=10),
    })
    evidence_gap = _object({
        "id": string,
        "question": string,
        "whyItMatters": string,
        "relatedEvidenceIds": _array(evidence_id, max_items=20),
        "suggestedAnalysis": _nullable(_enum(KNOWN_ANALYSES)),
        "requiresNewTelemetry": {"type": "boolean"},
    })
    change_target = _object({
        "targetType": _enum(TARGET_TYPES),
        "domain": string,
        "entityType": nullable_string,
        "entityKey": nullable_string,
        "metricFamily": nullable_string,
        "metric": nullable_string,
        "description": nullable_string,
        "requiresGameDesignContext": {"type": "boolean"},
    })
    proposed_change = _object({
        "description": string,
        "parameter": nullable_string,
        "direction": _enum(DIRECTIONS),
        "amountPercent": _nullable({"type": "number"}),
        "heuristic": {"type": "boolean"},
        "magnitudeBasis": nullable_string,
    })
    change_candidate = _object({
        "id": string,
        "domain": string,
        "target": change_target,
        "actionType": _enum(ACTION_TYPES),
        "proposedChange": proposed_change,
        "rationale": string,
        "supportingEvidenceIds": _array(evidence_id, min_items=1, max_items=20),
        "counterEvidenceIds": _array(evidence_id, max_items=20),
        "counterEvidenceSearchStatus": _enum(COUNTER_SEARCH_VALUES),
        "limitationWarningCodes": _array(string, max_items=10),
        "risks": _array(string, max_items=10),
        "expectedObservableDirections": _array(
            expected_direction, max_items=10
        ),
        "validationPlanId": nullable_string,
    })
    rollback = _object({
        "metric": metric_ref,
        "condition": _enum(ROLLBACK_CONDITIONS),
    })
    validation_plan = _object({
        "id": string,
        "changeCandidateId": string,
        "analysesToRerun": _array(_enum(KNOWN_ANALYSES), max_items=10),
        "metricsToWatch": _array(metric_ref, max_items=10),
        "guardrailMetrics": _array(metric_ref, max_items=10),
        "minimumEvidenceRequirements": _array(
            _enum(MINIMUM_REQUIREMENTS), max_items=10
        ),
        "comparisonPlan": _enum(COMPARISON_PLANS),
        "rollbackIndicators": _array(
            rollback, max_items=MAX_ROLLBACK_INDICATORS
        ),
    })
    executive_summary = _object({
        "qualitativeOverview": string,
        "observationIds": _array(string, max_items=MAX_EXECUTIVE_SUMMARY_IDS),
        "hypothesisIds": _array(string, max_items=MAX_EXECUTIVE_SUMMARY_IDS),
        "evidenceGapIds": _array(string, max_items=MAX_EXECUTIVE_SUMMARY_IDS),
        "changeCandidateIds": _array(string, max_items=MAX_EXECUTIVE_SUMMARY_IDS),
    })
    source_identity = _object({
        "semanticOutputDigest": string,
        "scopeHash": string,
        "mode": string,
        "baselineContentVersion": _version_type(identity.baseline_content_version),
        "candidateContentVersion": _version_type(identity.candidate_content_version),
    })
    schema = _object({
        "analysisVersion": {"type": "string", "enum": [ANALYSIS_VERSION]},
        "sourceBriefIdentity": source_identity,
        "comparisonDirectionAcknowledgement": _enum(
            frozenset({"candidateMinusBaseline", "notApplicable"})
        ),
        "executiveSummary": executive_summary,
        "observations": _array(observation, max_items=MAX_OBSERVATIONS),
        "interpretations": _array(
            interpretation, max_items=MAX_RESPONSE_SECTION_ITEMS
        ),
        "hypotheses": _array(hypothesis, max_items=MAX_RESPONSE_SECTION_ITEMS),
        "evidenceGaps": _array(
            evidence_gap, max_items=MAX_RESPONSE_SECTION_ITEMS
        ),
        "changeCandidates": _array(
            change_candidate, max_items=MAX_RESPONSE_SECTION_ITEMS
        ),
        "validationPlans": _array(
            validation_plan, max_items=MAX_RESPONSE_SECTION_ITEMS
        ),
    })
    return schema


_ANTHROPIC_UNSUPPORTED_CONSTRAINTS = frozenset({
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
})


def build_anthropic_output_schema(
    canonical_schema: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Project canonical JSON Schema onto Anthropic's supported wire subset.

    Unsupported constraints are retained only as concise model guidance. The
    unchanged local validator remains authoritative for every canonical limit.
    This mirrors the documented SDK transformation without depending on a
    private SDK module path.
    """

    def transform(value: Any) -> Any:
        if isinstance(value, list):
            return [transform(item) for item in value]
        if not isinstance(value, Mapping):
            return value
        result: dict[str, Any] = {}
        constraints: list[str] = []
        for key, item in value.items():
            if key in _ANTHROPIC_UNSUPPORTED_CONSTRAINTS:
                constraints.append(f"{key}={item}")
                continue
            if key == "minItems" and item not in {0, 1}:
                constraints.append(f"{key}={item}")
                continue
            result[key] = transform(item)
        if result.get("type") == "object":
            result["additionalProperties"] = False
        if constraints:
            guidance = "Canonical local constraints: " + ", ".join(constraints) + "."
            existing = result.get("description")
            result["description"] = (
                f"{existing}\n\n{guidance}" if isinstance(existing, str) else guidance
            )
        return result

    transformed = transform(canonical_schema)
    if not isinstance(transformed, Mapping):  # pragma: no cover - defensive
        raise TypeError("Anthropic output schema must remain an object")
    return transformed


def canonical_anthropic_body_schema(
    package: AnalysisPromptPackage,
) -> Mapping[str, Any]:
    full_schema = analysis_response_json_schema(package)
    body_fields = (
        "executiveSummary",
        "observations",
        "interpretations",
        "hypotheses",
        "evidenceGaps",
        "changeCandidates",
        "validationPlans",
    )
    return _object({
        field: full_schema["properties"][field] for field in body_fields
    })


def structured_output_config(package: AnalysisPromptPackage) -> Mapping[str, Any]:
    canonical_body = canonical_anthropic_body_schema(package)
    return {
        "format": {
            "type": "json_schema",
            "schema": build_anthropic_output_schema(canonical_body),
        }
    }


def structured_output_transport_instruction() -> str:
    return (
        "# Anthropic Structured Output Transport\n\n"
        "Return exactly the seven analysis fields required by the Structured Outputs schema. "
        "Source identity, analysis version, and comparison direction are host-controlled and must "
        "not be returned. Do not add a Markdown fence or commentary. Every generated field is also "
        "checked by the authoritative local C-2 validator."
    )


__all__ = [
    "analysis_response_json_schema",
    "build_anthropic_output_schema",
    "canonical_anthropic_body_schema",
    "structured_output_config",
    "structured_output_transport_instruction",
]
