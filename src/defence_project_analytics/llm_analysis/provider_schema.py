"""Anthropic Structured Outputs schema derived from the C-2 response contract."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

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
from defence_project_analytics.metric_registry import target_metric_keys


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


def anthropic_flat_wire_properties(
    *,
    evidence_reference_schema: Mapping[str, Any] | None = None,
    provider_alias_fields: bool = False,
    metric_reference_schema: Mapping[str, Any] | None = None,
    provider_metric_alias_fields: bool = False,
    warning_reference_schema: Mapping[str, Any] | None = None,
    provider_warning_alias_fields: bool = False,
    provider_warning_empty_only: bool = False,
    provider_derived_counter_status: bool = False,
    validation_plan_reference_schema: Mapping[str, Any] | None = None,
    provider_validation_plan_ref_fields: bool = False,
    provider_validation_plan_count: int | None = None,
    provider_host_assigned_stage_b_ids: bool = False,
    evidence_gap_reference_schema: Mapping[str, Any] | None = None,
    provider_evidence_gap_ref_fields: bool = False,
    executive_reference_schemas: Mapping[str, Mapping[str, Any]] | None = None,
    provider_executive_ref_fields: bool = False,
) -> Mapping[str, Any]:
    """Return reusable flat DTO fields for canonical or provider-local transport."""

    string = {"type": "string"}
    numeric_free_string = {
        "type": "string",
        "pattern": "^[^0-9]*$",
        "description": (
            "Numeric-free qualitative prose. Do not write digits, counts, percentages, "
            "ratios, deltas, measurements, or tuning magnitudes; cite structured Evidence instead."
        ),
    }
    change_description_string = {
        **numeric_free_string,
        "description": (
            "Numeric-free proposed action to test. Use tentative prospective wording. Do not "
            "claim that an observed metric caused another outcome or that this action will "
            "certainly improve it."
        ),
    }
    evidence_reference = dict(evidence_reference_schema or string)
    metric_reference = dict(metric_reference_schema or string)
    warning_reference = dict(warning_reference_schema or string)

    def evidence_field(canonical: str, provider: str) -> str:
        return provider if provider_alias_fields else canonical

    def warning_field() -> str:
        return (
            "limitationWarningRefs"
            if provider_warning_alias_fields
            else "limitationWarningCodes"
        )

    def wire_array(items: Mapping[str, Any]) -> dict[str, Any]:
        return {"type": "array", "items": dict(items)}

    def evidence_array() -> dict[str, Any]:
        return {
            "type": "array",
            "items": dict(evidence_reference),
            "maxItems": 20,
            "uniqueItems": True,
            "description": "Use no more than twenty unique Evidence refs.",
        }

    def warning_array() -> dict[str, Any]:
        result = wire_array(warning_reference)
        if provider_warning_empty_only:
            result["maxItems"] = 0
        return result

    observation = _object({
        "id": string,
        "findingType": _enum(FINDING_TYPES),
        "qualitativeStatement": numeric_free_string,
        evidence_field("evidenceIds", "evidenceRefs"): evidence_array(),
        "importance": _enum(IMPORTANCE_VALUES),
    })
    interpretation = _object({
        "id": string,
        "statement": numeric_free_string,
        evidence_field("evidenceIds", "evidenceRefs"): evidence_array(),
        evidence_field(
            "limitationEvidenceIds", "limitationEvidenceRefs"
        ): evidence_array(),
        warning_field(): warning_array(),
    })
    hypothesis_properties: dict[str, Any] = {
        "statement": numeric_free_string,
        evidence_field(
            "supportingEvidenceIds", "supportingEvidenceRefs"
        ): evidence_array(),
        evidence_field(
            "counterEvidenceIds", "counterEvidenceRefs"
        ): evidence_array(),
    }
    if not provider_derived_counter_status:
        hypothesis_properties["counterEvidenceSearchStatus"] = _enum(
            COUNTER_SEARCH_VALUES
        )
    evidence_gap_ref_array = wire_array(dict(evidence_gap_reference_schema or string))
    if (
        provider_evidence_gap_ref_fields
        and not (evidence_gap_reference_schema or {}).get("enum")
    ):
        evidence_gap_ref_array["maxItems"] = 0
    hypothesis_properties.update({
        warning_field(): warning_array(),
        "assumptions": wire_array(numeric_free_string),
        "alternativeExplanations": wire_array(numeric_free_string),
        (
            "evidenceGapRefs"
            if provider_evidence_gap_ref_fields
            else "evidenceGapIds"
        ): evidence_gap_ref_array,
        "falsificationChecks": wire_array(numeric_free_string),
    })
    if not provider_host_assigned_stage_b_ids:
        hypothesis_properties = {"id": string, **hypothesis_properties}
    hypothesis = _object(hypothesis_properties)
    evidence_gap = _object({
        "id": string,
        "question": numeric_free_string,
        "whyItMatters": numeric_free_string,
        evidence_field(
            "relatedEvidenceIds", "relatedEvidenceRefs"
        ): evidence_array(),
        "suggestedAnalysis": _enum(frozenset({*KNOWN_ANALYSES, "None"})),
        "requiresNewTelemetry": {"type": "boolean"},
    })
    change_candidate_properties: dict[str, Any] = {
        "domain": (
            _enum(frozenset({*KNOWN_ANALYSES, "crossDomain"}))
            if provider_host_assigned_stage_b_ids
            else string
        ),
        "targetType": _enum(TARGET_TYPES),
        "targetDomain": (
            _enum(frozenset({*KNOWN_ANALYSES, "crossDomain"}))
            if provider_host_assigned_stage_b_ids
            else string
        ),
        "targetEntityType": string,
        "targetEntityKey": string,
        # Anthropic target authority is intentionally split by target type.
        # The conceptual description is ignored for structured targets, while
        # the structured context flag is ignored for Conceptual targets.
        "conceptualTargetDescription": numeric_free_string,
        "structuredTargetRequiresGameDesignContext": {"type": "boolean"},
        "actionType": _enum(ACTION_TYPES),
        "changeDescription": change_description_string,
        "changeParameter": numeric_free_string,
        "changeDirection": _enum(DIRECTIONS),
        "changeAmountPercentPresent": {"type": "boolean"},
        "changeAmountPercent": {"type": "number"},
        "changeHeuristic": {"type": "boolean"},
        "changeMagnitudeBasis": numeric_free_string,
        "rationale": numeric_free_string,
        evidence_field(
            "supportingEvidenceIds", "supportingEvidenceRefs"
        ): evidence_array(),
        evidence_field(
            "counterEvidenceIds", "counterEvidenceRefs"
        ): evidence_array(),
    }
    if not provider_derived_counter_status:
        change_candidate_properties["counterEvidenceSearchStatus"] = _enum(
            COUNTER_SEARCH_VALUES
        )
    change_candidate_properties.update({
        warning_field(): warning_array(),
        "risks": wire_array(numeric_free_string),
        (
            "includeValidationPlan"
            if provider_host_assigned_stage_b_ids
            else "validationPlanId"
        ): (
            {"type": "boolean"}
            if provider_host_assigned_stage_b_ids
            else string
        ),
    })
    if provider_metric_alias_fields:
        change_candidate_properties["expectedObservables"] = wire_array(_object({
            "metricRef": metric_reference,
            "direction": _enum(OBSERVABLE_DIRECTIONS),
        }))
    else:
        change_candidate_properties["expectedMetricRefs"] = wire_array(
            metric_reference
        )
        change_candidate_properties["expectedDirections"] = wire_array(
            _enum(OBSERVABLE_DIRECTIONS)
        )
    if not provider_host_assigned_stage_b_ids:
        change_candidate_properties = {"id": string, **change_candidate_properties}
    if provider_metric_alias_fields:
        metric_enum = metric_reference.get("enum")
        if (
            metric_reference.get("type") != "integer"
            or not isinstance(metric_enum, list)
            or not metric_enum
            or 0 in metric_enum
        ):
            raise ValueError(
                "Anthropic target metric aliases require a nonempty exact integer enum"
            )
        change_candidate_properties["targetMetricRef"] = {
            "type": "integer",
            "enum": [0, *metric_enum],
        }
    else:
        change_candidate_properties["targetMetricFamily"] = string
        change_candidate_properties["targetMetric"] = string
    change_candidate = _object(change_candidate_properties)
    validation_plan_properties: dict[str, Any] = {
        "analysesToRerun": wire_array(
            _enum(KNOWN_ANALYSES)
            if provider_validation_plan_ref_fields
            else string
        ),
        "metricsToWatchRefs": wire_array(metric_reference),
        "guardrailMetricRefs": wire_array(metric_reference),
        "minimumEvidenceRequirements": wire_array(
            _enum(MINIMUM_REQUIREMENTS)
            if provider_validation_plan_ref_fields
            else string
        ),
        "comparisonPlan": _enum(COMPARISON_PLANS),
    }
    if provider_validation_plan_ref_fields:
        validation_plan_properties["rollbackIndicators"] = wire_array(_object({
            "metricRef": metric_reference,
            "condition": _enum(ROLLBACK_CONDITIONS),
        }))
    else:
        validation_plan_properties["rollbackMetricRefs"] = wire_array(
            metric_reference
        )
        validation_plan_properties["rollbackConditions"] = wire_array(string)
    if provider_validation_plan_ref_fields:
        if validation_plan_reference_schema is None:
            raise ValueError("Anthropic Stage C requires ValidationPlan refs")
        validation_plan_properties = {
            "validationPlanRef": dict(validation_plan_reference_schema),
            **validation_plan_properties,
        }
    else:
        validation_plan_properties = {
            "id": string,
            "changeCandidateId": string,
            **validation_plan_properties,
        }
    validation_plan = _object(validation_plan_properties)
    validation_plans = wire_array(validation_plan)
    # Exact ValidationPlan cardinality is enforced by the request-local ref
    # table during reconstruction. Anthropic's strict-tool wire subset does
    # not support maxItems, so the provider schema must not duplicate that
    # host-owned invariant with minItems/maxItems.
    executive_schemas = executive_reference_schemas or {}

    def executive_field(canonical: str, provider: str) -> tuple[str, dict[str, Any]]:
        if provider_executive_ref_fields:
            result = wire_array(dict(executive_schemas[provider]))
            if not executive_schemas[provider].get("enum"):
                result["maxItems"] = 0
            return provider, result
        return canonical, wire_array(string)

    executive_fields = dict((
        executive_field("executiveObservationIds", "executiveObservationRefs"),
        executive_field("executiveHypothesisIds", "executiveHypothesisRefs"),
        executive_field("executiveEvidenceGapIds", "executiveEvidenceGapRefs"),
        executive_field("executiveChangeCandidateIds", "executiveChangeCandidateRefs"),
    ))
    return {
        "executiveQualitativeOverview": numeric_free_string,
        **executive_fields,
        "observations": wire_array(observation),
        "interpretations": wire_array(interpretation),
        "hypotheses": wire_array(hypothesis),
        "evidenceGaps": wire_array(evidence_gap),
        "changeCandidates": wire_array(change_candidate),
        "validationPlans": validation_plans,
    }


def anthropic_flat_wire_schema() -> Mapping[str, Any]:
    """Return the monolithic flat DTO schema for reference-only tests."""

    return _object(anthropic_flat_wire_properties())


ANTHROPIC_STRICT_TOOL_VERSION = "1.0.0"
ANTHROPIC_STRICT_TOOL_SECTIONS = (
    ("submit_observations", ("observations",)),
    ("submit_interpretations", ("interpretations",)),
    ("submit_hypotheses", ("hypotheses",)),
    ("submit_evidence_gaps", ("evidenceGaps",)),
    ("submit_change_candidates", ("changeCandidates",)),
    ("submit_validation_plans", ("validationPlans",)),
    (
        "submit_executive_summary",
        (
            "executiveQualitativeOverview",
            "executiveObservationIds",
            "executiveHypothesisIds",
            "executiveEvidenceGapIds",
            "executiveChangeCandidateIds",
        ),
    ),
)

ANTHROPIC_THREE_STAGE_STRICT_TOOL_VERSION = "1.10.0"
ANTHROPIC_STAGE_SPECS = {
    "A": (
        ("submit_observations", ("observations",)),
        ("submit_interpretations", ("interpretations",)),
        ("submit_evidence_gaps", ("evidenceGaps",)),
    ),
    "B": (
        ("submit_hypotheses", ("hypotheses",)),
        ("submit_change_candidates", ("changeCandidates",)),
    ),
    "C": (
        ("submit_validation_plans", ("validationPlans",)),
        (
            "submit_executive_summary",
            (
                "executiveQualitativeOverview",
                "executiveObservationIds",
                "executiveHypothesisIds",
                "executiveEvidenceGapIds",
                "executiveChangeCandidateIds",
            ),
        ),
    ),
}

ANTHROPIC_PROVIDER_STAGE_SPECS = {
    **ANTHROPIC_STAGE_SPECS,
    "C": (
        ("submit_validation_plans", ("validationPlans",)),
        (
            "submit_executive_summary",
            (
                "executiveQualitativeOverview",
                "executiveObservationRefs",
                "executiveHypothesisRefs",
                "executiveEvidenceGapRefs",
                "executiveChangeCandidateRefs",
            ),
        ),
    ),
}

_ANTHROPIC_TOOL_DESCRIPTIONS = {
    "submit_observations": "Submit the complete observations section exactly once.",
    "submit_interpretations": "Submit the complete interpretations section exactly once.",
    "submit_hypotheses": "Submit the complete hypotheses section exactly once.",
    "submit_evidence_gaps": "Submit the complete evidence gaps section exactly once.",
    "submit_change_candidates": "Submit the complete change candidates section exactly once.",
    "submit_validation_plans": "Submit the complete validation plans section exactly once.",
    "submit_executive_summary": "Submit the complete executive summary exactly once.",
}


def _strict_tools_for_sections(
    sections: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    properties: Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    selected_properties = properties or anthropic_flat_wire_properties()
    return tuple(
        {
            "name": name,
            "description": _ANTHROPIC_TOOL_DESCRIPTIONS[name],
            "strict": True,
            "input_schema": _object({
                field: selected_properties[field] for field in fields
            }),
        }
        for name, fields in sections
    )


def _validated_evidence_refs(
    evidence_refs: Sequence[int], *, allow_subset: bool = False
) -> tuple[int, ...]:
    refs = tuple(evidence_refs)
    if (
        not refs
        or any(not isinstance(value, int) or isinstance(value, bool) for value in refs)
        or refs != tuple(sorted(set(refs)))
        or (not allow_subset and refs != tuple(range(1, len(refs) + 1)))
    ):
        expectation = (
            "sorted unique positive integers"
            if allow_subset
            else "contiguous integers starting at 1"
        )
        raise ValueError(f"Anthropic Evidence aliases must be {expectation}")
    if refs[0] < 1:
        raise ValueError("Anthropic Evidence aliases must be positive integers")
    return refs


def _validated_metric_refs(metric_refs: Sequence[int]) -> tuple[int, ...]:
    refs = tuple(metric_refs)
    if (
        not refs
        or any(not isinstance(value, int) or isinstance(value, bool) for value in refs)
        or refs != tuple(sorted(set(refs)))
        or refs[0] < 1
    ):
        raise ValueError("Anthropic metric aliases must be sorted unique positive integers")
    return refs


def _validated_warning_refs(warning_refs: Sequence[int]) -> tuple[int, ...]:
    refs = tuple(warning_refs)
    if (
        any(not isinstance(value, int) or isinstance(value, bool) for value in refs)
        or refs != tuple(range(1, len(refs) + 1))
    ):
        raise ValueError("Anthropic warning aliases must be contiguous integers starting at 1")
    return refs


def _validated_validation_plan_refs(
    validation_plan_refs: Sequence[int],
) -> tuple[int, ...]:
    refs = tuple(validation_plan_refs)
    if (
        any(not isinstance(value, int) or isinstance(value, bool) for value in refs)
        or refs != tuple(range(1, len(refs) + 1))
    ):
        raise ValueError(
            "Anthropic ValidationPlan refs must be contiguous integers starting at 1"
        )
    return refs


def _validated_output_refs(values: Sequence[int], label: str) -> tuple[int, ...]:
    refs = tuple(values)
    if (
        any(not isinstance(value, int) or isinstance(value, bool) for value in refs)
        or refs != tuple(range(1, len(refs) + 1))
    ):
        raise ValueError(
            f"Anthropic {label} refs must be contiguous integers starting at 1"
        )
    return refs


def _provider_ref_schema(refs: tuple[int, ...]) -> Mapping[str, Any]:
    return (
        {"type": "integer", "enum": list(refs)}
        if refs
        else {"type": "integer"}
    )


def anthropic_stage_strict_tools(
    stage: str,
    *,
    evidence_refs: Sequence[int] | None = None,
    metric_refs: Sequence[int] | None = None,
    warning_refs: Sequence[int] | None = None,
    validation_plan_refs: Sequence[int] | None = None,
    evidence_gap_refs: Sequence[int] | None = None,
    observation_refs: Sequence[int] | None = None,
    hypothesis_refs: Sequence[int] | None = None,
    change_candidate_refs: Sequence[int] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Build the sole production strict-tool schema for one C-2A stage."""

    try:
        sections = ANTHROPIC_PROVIDER_STAGE_SPECS[stage]
    except KeyError as error:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}") from error
    evidence_schema: Mapping[str, Any] | None = None
    if stage in {"A", "B"}:
        if evidence_refs is None:
            raise ValueError(
                f"Anthropic Stage {stage} requires provider-local Evidence aliases"
            )
        refs = _validated_evidence_refs(evidence_refs, allow_subset=stage == "B")
        evidence_schema = {"type": "integer", "enum": list(refs)}
    metric_schema: Mapping[str, Any] | None = None
    if stage in {"B", "C"}:
        if metric_refs is None:
            metric_refs = tuple(range(1, len(target_metric_keys()) + 1))
        refs = _validated_metric_refs(metric_refs)
        metric_schema = {"type": "integer", "enum": list(refs)}
    warning_schema: Mapping[str, Any] | None = None
    warning_empty_only = False
    if stage in {"A", "B"}:
        if warning_refs is None:
            raise ValueError(
                f"Anthropic Stage {stage} requires provider-local warning aliases"
            )
        refs = _validated_warning_refs(warning_refs)
        warning_empty_only = not refs
        warning_schema = (
            {"type": "integer", "enum": list(refs)}
            if refs
            else {"type": "integer"}
        )
    validation_plan_schema: Mapping[str, Any] | None = None
    validation_plan_count: int | None = None
    if stage == "C":
        if validation_plan_refs is None:
            raise ValueError("Anthropic Stage C requires provider-local ValidationPlan refs")
        refs = _validated_validation_plan_refs(validation_plan_refs)
        validation_plan_count = len(refs)
        validation_plan_schema = (
            {"type": "integer", "enum": list(refs)}
            if refs
            else {"type": "integer"}
        )
    evidence_gap_schema: Mapping[str, Any] | None = None
    if stage == "B":
        if evidence_gap_refs is None:
            evidence_gap_refs = (1,)
        evidence_gap_schema = _provider_ref_schema(
            _validated_output_refs(evidence_gap_refs, "EvidenceGap")
        )
    executive_schemas: dict[str, Mapping[str, Any]] | None = None
    if stage == "C":
        supplied = {
            "executiveObservationRefs": (
                observation_refs if observation_refs is not None else (1,)
            ),
            "executiveHypothesisRefs": (
                hypothesis_refs if hypothesis_refs is not None else (1,)
            ),
            "executiveEvidenceGapRefs": (
                evidence_gap_refs if evidence_gap_refs is not None else (1,)
            ),
            "executiveChangeCandidateRefs": (
                change_candidate_refs
                if change_candidate_refs is not None else (1,)
            ),
        }
        executive_schemas = {
            field: _provider_ref_schema(
                _validated_output_refs(values or (), field)
            )
            for field, values in supplied.items()
        }
    properties = anthropic_flat_wire_properties(
        evidence_reference_schema=evidence_schema,
        provider_alias_fields=stage in {"A", "B"},
        metric_reference_schema=metric_schema,
        provider_metric_alias_fields=stage in {"B", "C"},
        warning_reference_schema=warning_schema,
        provider_warning_alias_fields=stage in {"A", "B"},
        provider_warning_empty_only=warning_empty_only,
        provider_derived_counter_status=stage == "B",
        validation_plan_reference_schema=validation_plan_schema,
        provider_validation_plan_ref_fields=stage == "C",
        provider_validation_plan_count=validation_plan_count,
        provider_host_assigned_stage_b_ids=stage == "B",
        evidence_gap_reference_schema=evidence_gap_schema,
        provider_evidence_gap_ref_fields=stage == "B",
        executive_reference_schemas=executive_schemas,
        provider_executive_ref_fields=stage == "C",
    )
    tools = _strict_tools_for_sections(sections, properties=properties)
    # Raw dict tools are not automatically normalized by messages.count_tokens().
    # Project every production schema onto Anthropic's documented supported
    # subset before it reaches either token counting or generation. Canonical
    # and exact-set validation remain authoritative on the host.
    return tuple({
        **tool,
        "input_schema": build_anthropic_output_schema(tool["input_schema"]),
    } for tool in tools)


def anthropic_reference_stage_strict_tools(
    stage: str,
) -> tuple[Mapping[str, Any], ...]:
    """Build the deprecated canonical-ID stage schema for regression comparison."""

    try:
        production_sections = ANTHROPIC_STAGE_SPECS[stage]
    except KeyError as error:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}") from error
    names = {name for name, _fields in production_sections}
    sections = tuple(
        item for item in ANTHROPIC_STRICT_TOOL_SECTIONS if item[0] in names
    )
    return _strict_tools_for_sections(sections)


def anthropic_strict_tools(
    package: AnalysisPromptPackage | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Build the deprecated all-seven schema for reference-only tests."""

    del package
    return _strict_tools_for_sections(ANTHROPIC_STRICT_TOOL_SECTIONS)


def anthropic_strict_tools_profile(
    tools: tuple[Mapping[str, Any], ...] | None = None,
) -> Mapping[str, Any]:
    """Return a deterministic, non-sensitive complexity profile for strict tools."""

    selected = tools or anthropic_strict_tools()

    def profile(value: Any, depth: int = 0) -> dict[str, int]:
        totals = {
            "objects": 0,
            "arrays": 0,
            "anyOf": 0,
            "nullable": 0,
            "optionalProperties": 0,
            "requiredProperties": 0,
            "enums": 0,
            "enumValues": 0,
            "logicalDepth": depth,
        }
        if isinstance(value, Mapping):
            value_type = value.get("type")
            if value_type == "object":
                totals["objects"] += 1
                properties = value.get("properties", {})
                required = value.get("required", ())
                if isinstance(properties, Mapping) and isinstance(required, list):
                    totals["requiredProperties"] += len(required)
                    totals["optionalProperties"] += len(properties) - len(required)
            if value_type == "array":
                totals["arrays"] += 1
            if "anyOf" in value:
                totals["anyOf"] += 1
            enum = value.get("enum")
            if isinstance(enum, list):
                totals["enums"] += 1
                totals["enumValues"] += len(enum)
            if value_type == "null" or (
                isinstance(value_type, list) and "null" in value_type
            ):
                totals["nullable"] += 1
            for child in value.values():
                child_totals = profile(child, depth + 1)
                for key in totals:
                    if key == "logicalDepth":
                        totals[key] = max(totals[key], child_totals[key])
                    else:
                        totals[key] += child_totals[key]
        elif isinstance(value, (list, tuple)):
            for child in value:
                child_totals = profile(child, depth + 1)
                for key in totals:
                    if key == "logicalDepth":
                        totals[key] = max(totals[key], child_totals[key])
                    else:
                        totals[key] += child_totals[key]
        return totals

    def evidence_ref_profile(value: Any) -> dict[str, int]:
        totals = {
            "evidenceRefFields": 0,
            "evidenceRefEnumOccurrences": 0,
            "evidenceRefEnumValues": 0,
        }
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name, schema in properties.items():
                    if name == "evidenceRefs" or name.endswith("EvidenceRefs"):
                        totals["evidenceRefFields"] += 1
                        if isinstance(schema, Mapping):
                            items = schema.get("items")
                            enum = items.get("enum") if isinstance(items, Mapping) else None
                            if isinstance(enum, list):
                                totals["evidenceRefEnumOccurrences"] += 1
                                totals["evidenceRefEnumValues"] += len(enum)
            for child in value.values():
                nested = evidence_ref_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        elif isinstance(value, (list, tuple)):
            for child in value:
                nested = evidence_ref_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        return totals

    def metric_ref_profile(value: Any) -> dict[str, int]:
        totals = {
            "metricRefFields": 0,
            "metricRefEnumOccurrences": 0,
            "metricRefEnumValues": 0,
        }
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name, schema in properties.items():
                    if name in {
                        "targetMetricRef",
                        "metricRef",
                        "metricsToWatchRefs",
                        "guardrailMetricRefs",
                    }:
                        totals["metricRefFields"] += 1
                        if isinstance(schema, Mapping):
                            if name in {"targetMetricRef", "metricRef"}:
                                enum = schema.get("enum")
                            else:
                                items = schema.get("items")
                                enum = (
                                    items.get("enum")
                                    if isinstance(items, Mapping)
                                    else None
                                )
                            if isinstance(enum, list):
                                totals["metricRefEnumOccurrences"] += 1
                                totals["metricRefEnumValues"] += len(enum)
            for child in value.values():
                nested = metric_ref_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        elif isinstance(value, (list, tuple)):
            for child in value:
                nested = metric_ref_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        return totals

    def warning_ref_profile(value: Any) -> dict[str, int]:
        totals = {
            "warningRefFields": 0,
            "warningRefEnumOccurrences": 0,
            "warningRefEnumValues": 0,
        }
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name, schema in properties.items():
                    if name == "limitationWarningRefs":
                        totals["warningRefFields"] += 1
                        items = schema.get("items") if isinstance(schema, Mapping) else None
                        enum = items.get("enum") if isinstance(items, Mapping) else None
                        if isinstance(enum, list):
                            totals["warningRefEnumOccurrences"] += 1
                            totals["warningRefEnumValues"] += len(enum)
            for child in value.values():
                nested = warning_ref_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        elif isinstance(value, (list, tuple)):
            for child in value:
                nested = warning_ref_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        return totals

    def stage_c_authority_profile(value: Any) -> dict[str, int]:
        totals = {
            "validationPlanRefFields": 0,
            "validationPlanRefEnumOccurrences": 0,
            "validationPlanRefEnumValues": 0,
            "minimumRequirementEnumOccurrences": 0,
            "minimumRequirementEnumValues": 0,
            "rollbackConditionEnumOccurrences": 0,
            "rollbackConditionEnumValues": 0,
        }
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name, schema in properties.items():
                    if not isinstance(schema, Mapping):
                        continue
                    if name == "validationPlanRef":
                        totals["validationPlanRefFields"] += 1
                        enum = schema.get("enum")
                        if isinstance(enum, list):
                            totals["validationPlanRefEnumOccurrences"] += 1
                            totals["validationPlanRefEnumValues"] += len(enum)
                    elif name == "minimumEvidenceRequirements":
                        items = schema.get("items")
                        enum = items.get("enum") if isinstance(items, Mapping) else None
                        if isinstance(enum, list):
                            totals["minimumRequirementEnumOccurrences"] += 1
                            totals["minimumRequirementEnumValues"] += len(enum)
                    elif name == "condition":
                        enum = schema.get("enum")
                        if isinstance(enum, list):
                            totals["rollbackConditionEnumOccurrences"] += 1
                            totals["rollbackConditionEnumValues"] += len(enum)
            for child in value.values():
                nested = stage_c_authority_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        elif isinstance(value, (list, tuple)):
            for child in value:
                nested = stage_c_authority_profile(child)
                for key in totals:
                    totals[key] += nested[key]
        return totals

    per_tool = {
        str(tool["name"]): {
            **profile(tool["input_schema"]),
            **evidence_ref_profile(tool["input_schema"]),
            **metric_ref_profile(tool["input_schema"]),
            **warning_ref_profile(tool["input_schema"]),
            **stage_c_authority_profile(tool["input_schema"]),
            "schemaChars": len(json.dumps(
                tool["input_schema"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )),
        }
        for tool in selected
    }
    combined = {
        key: (
            max(item[key] for item in per_tool.values())
            if key == "logicalDepth"
            else sum(item[key] for item in per_tool.values())
        )
        for key in next(iter(per_tool.values()))
    }
    return {"combined": combined, "perTool": per_tool}


ANTHROPIC_SERIALIZED_SECTION_FIELDS = (
    "observationsJson",
    "interpretationsJson",
    "hypothesesJson",
    "evidenceGapsJson",
    "changeCandidatesJson",
    "validationPlansJson",
    "executiveSummaryJson",
)


def anthropic_serialized_envelope_schema() -> Mapping[str, Any]:
    """Return the deprecated serialized-envelope schema for reference tests."""

    return _object({
        field: {"type": "string"}
        for field in ANTHROPIC_SERIALIZED_SECTION_FIELDS
    })


def structured_output_config(package: AnalysisPromptPackage) -> Mapping[str, Any]:
    """Return the deprecated serialized transport for reference-only tests."""
    # ``package`` remains part of the stable API even though the serialized wire
    # envelope is intentionally source-independent. Canonical semantics are
    # enforced after reconstruction by the existing local C-2 validator.
    del package
    return {
        "format": {
            "type": "json_schema",
            "schema": build_anthropic_output_schema(
                anthropic_serialized_envelope_schema()
            ),
        }
    }


def structured_output_transport_instruction() -> str:
    """Return deprecated serialized-envelope guidance for reference tests."""
    return (
        "# Anthropic Serialized Section Envelope\n\n"
        "Return exactly the seven required string fields in the Structured Outputs schema. "
        "Each string must contain plain JSON for its named canonical C-2 section: the first six "
        "sections are arrays and executiveSummaryJson is an object. Do not wrap inner JSON in a "
        "Markdown fence or add commentary. Source identity, analysis version, and comparison "
        "direction are host-controlled. The host parses the transport without repair and applies "
        "the authoritative local C-2 validator after reconstruction."
    )


__all__ = [
    "ANTHROPIC_SERIALIZED_SECTION_FIELDS",
    "ANTHROPIC_STAGE_SPECS",
    "ANTHROPIC_STRICT_TOOL_SECTIONS",
    "ANTHROPIC_STRICT_TOOL_VERSION",
    "ANTHROPIC_THREE_STAGE_STRICT_TOOL_VERSION",
    "analysis_response_json_schema",
    "anthropic_flat_wire_properties",
    "anthropic_flat_wire_schema",
    "anthropic_serialized_envelope_schema",
    "anthropic_stage_strict_tools",
    "anthropic_reference_stage_strict_tools",
    "anthropic_strict_tools",
    "anthropic_strict_tools_profile",
    "build_anthropic_output_schema",
    "canonical_anthropic_body_schema",
    "structured_output_config",
    "structured_output_transport_instruction",
]
