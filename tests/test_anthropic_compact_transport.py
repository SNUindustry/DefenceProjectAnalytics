from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from defence_project_analytics.llm_analysis.anthropic_transport import (
    build_anthropic_prompt_parts,
    build_anthropic_stage_context,
    build_anthropic_stage_prompt_parts,
    build_compact_llm_payload,
    build_evidence_alias_table,
    build_metric_alias_table,
    build_warning_alias_table,
    build_validation_plan_ref_table,
    build_stage_output_ref_tables,
    EvidenceAliasTable,
    MetricAliasTable,
    WarningAliasTable,
    ValidationPlanRefTable,
    OutputRefTable,
    StageOutputRefTables,
    compact_payload_digest,
    collect_anthropic_tool_response,
    collect_anthropic_stage_tool_response,
    encode_metric_reference,
    expand_compact_evidence_items,
    flatten_canonical_response,
    merge_anthropic_stage_sections,
    project_compact_evidence_aliases,
    project_canonical_prior_metric_aliases,
    project_canonical_stage_metric_aliases,
    project_canonical_stage_warning_aliases,
    project_canonical_stage_validation_plan_refs,
    project_canonical_stage_b_authority,
    project_canonical_stage_c_summary_refs,
    parse_anthropic_serialized_response_text,
    reconstruct_anthropic_flat_response,
    reconstruct_anthropic_serialized_response,
    restore_compact_evidence_aliases,
    restore_provider_prior_metric_aliases,
    restore_provider_stage_metric_aliases,
    restore_provider_stage_warning_aliases,
    restore_provider_stage_validation_plan_refs,
    restore_provider_stage_b_authority,
    restore_provider_stage_c_summary_refs,
    serialize_canonical_response_sections,
    split_anthropic_flat_tool_inputs,
    split_anthropic_stage_tool_inputs,
)
from defence_project_analytics.llm_analysis.errors import (
    AnalysisResponseValidationError,
)
from defence_project_analytics.llm_analysis.provider_errors import (
    AnthropicDuplicateToolUseError,
    AnthropicEvidenceAliasError,
    AnthropicFlatReconstructionError,
    AnthropicInvalidToolInputError,
    AnthropicMalformedResponseError,
    AnthropicMetricAliasError,
    AnthropicOutputRefError,
    AnthropicWarningAliasError,
    AnthropicValidationPlanRefError,
    AnthropicRequiredToolMissingError,
    AnthropicUnknownToolUseError,
)
from defence_project_analytics.llm_analysis.provider_schema import (
    ANTHROPIC_STAGE_SPECS,
    anthropic_stage_strict_tools,
    anthropic_strict_tools,
    structured_output_config,
)
from defence_project_analytics.llm_analysis.validator import (
    validate_response,
    validate_stage_a,
    validate_stage_b,
    validate_stage_c,
)
from defence_project_analytics.reporting.renderers import to_external
from defence_project_analytics.metric_registry import monitor_metric_keys, target_metric_keys
from defence_project_analytics.llm_analysis.warning_authority import (
    collect_allowed_warning_codes,
)
from defence_project_analytics.llm_analysis.models import (
    MINIMUM_REQUIREMENTS,
    ROLLBACK_CONDITIONS,
)
from llm_analysis_fixtures import make_package, valid_response


def _package_with_evidence_count(package, count: int):
    original = to_external(package.source.evidence)["evidenceItems"][0]
    items = []
    for index in range(count):
        item = deepcopy(original)
        item["evidenceId"] = f"EV-stage-test-{index:012x}"
        item["provenance"]["sourceRowKey"] = f"metric=outcome.finalAttempts;row={index:03d}"
        items.append(item)
    evidence = {
        "analysisBriefVersion": package.source.evidence["analysisBriefVersion"],
        "evidenceItems": items,
    }
    source = replace(
        package.source,
        evidence=evidence,
        evidence_by_id={item["evidenceId"]: item for item in items},
    )
    return replace(package, source=source)


def _package_with_entity_evidence(package):
    evidence = deepcopy(dict(package.source.evidence))
    items = deepcopy(list(evidence["evidenceItems"]))
    items[0]["entityType"] = "weaponFamily"
    items[0]["entityKey"] = "mortar2.basic"
    evidence["evidenceItems"] = items
    source = replace(
        package.source,
        evidence=evidence,
        evidence_by_id={item["evidenceId"]: item for item in items},
    )
    return replace(package, source=source)


def _warning_aliases_from_flat(flat: dict) -> WarningAliasTable:
    codes: set[str] = set()
    for section in ("interpretations", "hypotheses", "changeCandidates"):
        for item in flat.get(section, []):
            codes.update(item.get("limitationWarningCodes", []))
    return WarningAliasTable(tuple(sorted(codes)))


def _optional_property_count(value: object) -> int:
    if isinstance(value, dict):
        properties = value.get("properties")
        required = value.get("required")
        here = 0
        if isinstance(properties, dict):
            here = len(properties) - (len(required) if isinstance(required, list) else 0)
        return here + sum(_optional_property_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_optional_property_count(item) for item in value)
    return 0


def _instance_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_instance_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_instance_depth(item) for item in value), default=0)
    return 0


def test_compact_projection_preserves_106_exact_canonical_evidence_rows(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    package = _package_with_evidence_count(package, 106)
    canonical = to_external(package.source.evidence)["evidenceItems"]

    first = build_compact_llm_payload(package)
    second = build_compact_llm_payload(package)
    reconstructed = list(expand_compact_evidence_items(first))

    assert len(first["evidence"]) == len(canonical) == 106
    assert reconstructed == canonical
    assert compact_payload_digest(first) == compact_payload_digest(second)
    canonical_ids = [item["evidenceId"] for item in canonical]
    compact_ids = [item["evidenceId"] for item in first["evidence"]]
    assert compact_ids == canonical_ids
    assert len(set(compact_ids)) == 106
    assert all("canonicalIdentity" not in item for item in first["evidence"])
    assert all("provenance" not in item for item in first["evidence"])
    assert all("warningCodes" not in item for item in first["evidence"])
    assert len(first["qualitySets"]) == 1
    assert len(first["artifacts"]) == 1


def test_warning_alias_table_is_deterministic_bijective_and_fail_closed(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    first = build_warning_alias_table(package)
    second = build_warning_alias_table(package)
    assert first.canonical_warning_codes == collect_allowed_warning_codes(package)
    assert first.provider_refs == tuple(range(1, len(first.canonical_warning_codes) + 1))
    assert first.digest == second.digest
    assert tuple(first.canonical_code(ref) for ref in first.provider_refs) == (
        first.canonical_warning_codes
    )
    for code in first.canonical_warning_codes:
        assert first.canonical_code(first.provider_ref(code)) == code
    for invalid in (0, len(first.provider_refs) + 1, "1", True, False, 1.0):
        with pytest.raises(AnthropicWarningAliasError):
            first.canonical_code(invalid)
    with pytest.raises(AnthropicWarningAliasError):
        first.provider_ref("NO_PLAYER_LEVEL_LINKAGE")
    with pytest.raises(AnthropicWarningAliasError, match="duplicate"):
        WarningAliasTable(("A", "A"))
    with pytest.raises(AnthropicWarningAliasError, match="ordered"):
        WarningAliasTable(("B", "A"))


def test_empty_warning_universe_has_host_controlled_empty_wire_contract() -> None:
    aliases = WarningAliasTable(())
    assert aliases.provider_refs == ()
    assert len(aliases.digest) == 64
    tools = anthropic_stage_strict_tools(
        "A", evidence_refs=(1,), warning_refs=aliases.provider_refs
    )
    interpretation = next(
        tool for tool in tools if tool["name"] == "submit_interpretations"
    )["input_schema"]["properties"]["interpretations"]["items"]["properties"]
    schema = interpretation["limitationWarningRefs"]
    assert schema == {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Canonical local constraints: maxItems=0.",
    }
    Draft202012Validator(schema).validate([])
    # The API-supported wire schema carries guidance only; the empty alias
    # table remains the fail-closed host authority for membership.
    with pytest.raises(AnthropicWarningAliasError):
        aliases.canonical_code(1)


def test_stage_a_wire_schema_carries_numeric_free_prose_guidance() -> None:
    tools = anthropic_stage_strict_tools(
        "A", evidence_refs=(1,), warning_refs=(1,)
    )
    interpretation = next(
        tool for tool in tools if tool["name"] == "submit_interpretations"
    )["input_schema"]["properties"]["interpretations"]["items"]["properties"]
    statement = interpretation["statement"]
    assert "pattern" not in statement
    assert "Numeric-free qualitative prose" in statement["description"]
    assert "pattern=^[^0-9]*$" in statement["description"]
    evidence_refs = interpretation["evidenceRefs"]
    assert "maxItems" not in evidence_refs and "uniqueItems" not in evidence_refs
    assert "maxItems=20" in evidence_refs["description"]
    assert "uniqueItems=True" in evidence_refs["description"]


def test_stage_b_wire_schema_marks_change_description_as_prospective() -> None:
    tools = anthropic_stage_strict_tools(
        "B", evidence_refs=(1,), metric_refs=(1,), warning_refs=(1,),
        evidence_gap_refs=(),
    )
    candidate = next(
        tool for tool in tools if tool["name"] == "submit_change_candidates"
    )["input_schema"]["properties"]["changeCandidates"]["items"]["properties"]
    assert "proposed action to test" in candidate["changeDescription"]["description"]
    assert "certainly improve" in candidate["changeDescription"]["description"]


def test_warning_refs_round_trip_and_strict_schema_exclude_canonical_strings(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    for stage in ("A", "B"):
        stage_fields = {
            field for _name, fields in ANTHROPIC_STAGE_SPECS[stage] for field in fields
        }
        canonical = {field: deepcopy(flat[field]) for field in stage_fields}
        projected = project_canonical_stage_warning_aliases(
            canonical, stage, warning_aliases
        )
        encoded = json.dumps(projected, sort_keys=True)
        assert "limitationWarningCodes" not in encoded
        assert "limitationWarningRefs" in encoded
        assert restore_provider_stage_warning_aliases(
            projected, stage, warning_aliases
        ) == canonical
        tools = anthropic_stage_strict_tools(
            stage,
            evidence_refs=evidence_aliases.provider_refs,
            warning_refs=warning_aliases.provider_refs,
        )
        schemas = {tool["name"]: tool["input_schema"] for tool in tools}
        for name, tool_input in split_anthropic_stage_tool_inputs(
            canonical,
            stage,
            evidence_aliases=evidence_aliases,
            warning_aliases=warning_aliases,
        ):
            Draft202012Validator(schemas[name]).validate(tool_input)
            invalid = deepcopy(tool_input)
            rows = next((value for value in invalid.values() if isinstance(value, list)), [])
            if rows and "limitationWarningRefs" in rows[0]:
                rows[0]["limitationWarningRefs"] = [
                    warning_aliases.canonical_warning_codes[0]
                ]
                with pytest.raises(ValidationError):
                    Draft202012Validator(schemas[name]).validate(invalid)


def test_unknown_warning_alias_fails_before_canonical_validation(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    fields = {
        field for _name, values in ANTHROPIC_STAGE_SPECS["A"] for field in values
    }
    staged = {field: flat[field] for field in fields}
    blocks = [
        SimpleNamespace(type="tool_use", id=f"A-{index}", name=name, input=value)
        for index, (name, value) in enumerate(
            split_anthropic_stage_tool_inputs(
                staged,
                "A",
                evidence_aliases=evidence_aliases,
                warning_aliases=warning_aliases,
            ),
            start=1,
        )
    ]
    interpretation = _tool_block(blocks, "submit_interpretations")
    interpretation.input["interpretations"][0]["limitationWarningRefs"] = [
        len(warning_aliases.provider_refs) + 1
    ]
    with pytest.raises(AnthropicFlatReconstructionError) as captured:
        collect_anthropic_stage_tool_response(
            blocks,
            "A",
            evidence_aliases=evidence_aliases,
            warning_aliases=warning_aliases,
        )
    assert captured.value.details["reconstructionInvariant"] == "UNKNOWN_WARNING_ALIAS"
    assert "NO_PLAYER_LEVEL_LINKAGE" not in json.dumps(captured.value.details)


def test_provider_integer_alias_is_deterministic_bijective_and_round_trips(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    package = _package_with_evidence_count(package, 106)
    first = build_evidence_alias_table(package)
    second = build_evidence_alias_table(package)
    compact = build_compact_llm_payload(package)
    projected = project_compact_evidence_aliases(compact, first)

    assert first.provider_refs == tuple(range(1, 107))
    assert first.digest == second.digest
    assert tuple(first.canonical_id(ref) for ref in first.provider_refs) == (
        first.canonical_evidence_ids
    )
    assert tuple(first.provider_ref(value) for value in first.canonical_evidence_ids) == (
        first.provider_refs
    )
    assert restore_compact_evidence_aliases(projected, first) == compact
    assert [row["evidenceRef"] for row in projected["evidence"]] == list(range(1, 107))
    assert all("evidenceId" not in row for row in projected["evidence"])


def test_provider_integer_alias_rejects_invalid_mapping_and_primitives() -> None:
    with pytest.raises(AnthropicEvidenceAliasError, match="duplicate"):
        EvidenceAliasTable(("EV-one", "EV-one"))
    aliases = EvidenceAliasTable(("EV-one", "EV-two"))
    for value in (0, 3, True, "1", "E001", "EV-one"):
        with pytest.raises(AnthropicEvidenceAliasError):
            aliases.canonical_id(value)
    with pytest.raises(AnthropicEvidenceAliasError):
        aliases.provider_ref("EV-missing")
    with pytest.raises(ValueError, match="contiguous"):
        anthropic_stage_strict_tools("A", evidence_refs=(1, 3), warning_refs=(1,))
    subset_tools = anthropic_stage_strict_tools(
        "B", evidence_refs=(1, 3), metric_refs=(1,), warning_refs=(1,),
        evidence_gap_refs=(),
    )
    assert len(subset_tools) == 2


def test_metric_alias_is_registry_derived_bijective_and_exact() -> None:
    first = build_metric_alias_table()
    second = build_metric_alias_table()
    expected = tuple(sorted(target_metric_keys() | monitor_metric_keys()))

    assert first.canonical_metric_keys == expected
    assert first.provider_refs == tuple(range(1, len(expected) + 1))
    assert first.digest == second.digest
    assert tuple(first.canonical_key(ref) for ref in first.provider_refs) == expected
    assert tuple(first.provider_ref(key) for key in expected) == first.provider_refs

    for value in (0, len(expected) + 1, True, "1", "M001", expected[0][0]):
        with pytest.raises(AnthropicMetricAliasError):
            first.canonical_key(value)
    with pytest.raises(AnthropicMetricAliasError):
        first.provider_ref(("stageDifficulty", "missing", "metric"))
    with pytest.raises(AnthropicMetricAliasError, match="duplicate"):
        MetricAliasTable((expected[0], expected[0]))
    with pytest.raises(AnthropicMetricAliasError, match="exact metric registry"):
        MetricAliasTable(expected[:-1])


def test_stage_b_c_metric_alias_projection_round_trips_exactly(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    aliases = build_metric_alias_table()
    flat = flatten_canonical_response(valid_response(package))
    for stage in ("B", "C"):
        fields = {
            field for _name, stage_fields in ANTHROPIC_STAGE_SPECS[stage]
            for field in stage_fields
        }
        canonical = {field: deepcopy(flat[field]) for field in fields}
        projected = project_canonical_stage_metric_aliases(
            canonical, stage, aliases
        )
        assert restore_provider_stage_metric_aliases(projected, stage, aliases) == canonical
        assert "::" not in json.dumps(projected, ensure_ascii=False)
        if stage == "B":
            target_ref = projected["changeCandidates"][0]["targetMetricRef"]
            assert isinstance(target_ref, int) and not isinstance(target_ref, bool)
            assert "targetMetricRefs" not in projected["changeCandidates"][0]
            assert "expectedMetricRefs" not in projected["changeCandidates"][0]
            assert "expectedDirections" not in projected["changeCandidates"][0]
            assert "expectedObservables" in projected["changeCandidates"][0]
        else:
            assert projected["validationPlans"] == []


def test_stage_b_c_metric_schemas_are_exact_integer_enums() -> None:
    aliases = build_metric_alias_table()
    expected_array_names = {
        "B": set(),
        "C": {"metricsToWatchRefs", "guardrailMetricRefs"},
    }
    for stage in ("B", "C"):
        tools = anthropic_stage_strict_tools(
            stage,
            evidence_refs=tuple(range(1, 107)) if stage == "B" else None,
            metric_refs=aliases.provider_refs,
            warning_refs=(1,) if stage == "B" else None,
            validation_plan_refs=(1,) if stage == "C" else None,
        )
        found_arrays: dict[str, dict] = {}
        target_schema = None
        encoded = json.dumps(tools, ensure_ascii=False)
        assert '"minItems"' not in encoded
        assert '"maxItems"' not in encoded
        assert "targetMetricFamily" not in encoded
        assert '"targetMetric"' not in encoded
        assert '"targetMetricRefs"' not in encoded
        for tool in tools:
            for section in tool["input_schema"]["properties"].values():
                for name, schema in section.get("items", {}).get("properties", {}).items():
                    if name in expected_array_names[stage]:
                        found_arrays[name] = schema["items"]
                    if name == "targetMetricRef":
                        target_schema = schema
        assert set(found_arrays) == expected_array_names[stage]
        assert all(
            schema == {"type": "integer", "enum": list(aliases.provider_refs)}
            for schema in found_arrays.values()
        )
        for schema in found_arrays.values():
            validator = Draft202012Validator(schema)
            validator.validate(1)
            validator.validate(len(aliases.provider_refs))
            for invalid in (0, len(aliases.provider_refs) + 1, "1", "M001"):
                with pytest.raises(ValidationError):
                    validator.validate(invalid)
        if stage == "B":
            assert target_schema == {
                "type": "integer",
                "enum": [0, *aliases.provider_refs],
            }
            change_properties = next(
                tool for tool in tools
                if tool["name"] == "submit_change_candidates"
            )["input_schema"]["properties"]["changeCandidates"]["items"]["properties"]
            assert change_properties["conceptualTargetDescription"]["type"] == "string"
            assert change_properties[
                "structuredTargetRequiresGameDesignContext"
            ] == {"type": "boolean"}
            assert "targetDescription" not in change_properties
            assert "targetRequiresGameDesignContext" not in change_properties
            expected_item = change_properties["expectedObservables"]["items"]
            assert expected_item["required"] == ["metricRef", "direction"]
            assert expected_item["additionalProperties"] is False
            assert expected_item["properties"]["metricRef"] == {
                "type": "integer", "enum": list(aliases.provider_refs),
            }
            assert expected_item["properties"]["direction"] == {
                "type": "string",
                "enum": ["Decrease", "Increase", "MonitorOnly", "NoAssumedDirection"],
            }
            assert "expectedMetricRefs" not in change_properties
            assert "expectedDirections" not in change_properties
            validator = Draft202012Validator(target_schema)
            for valid in (0, 1, len(aliases.provider_refs)):
                validator.validate(valid)
            for invalid in (
                [1], [1, 2, 3], "1", "M001", True,
                len(aliases.provider_refs) + 1,
            ):
                with pytest.raises(ValidationError):
                    validator.validate(invalid)
        else:
            assert target_schema is None
            plan_properties = next(
                tool for tool in tools if tool["name"] == "submit_validation_plans"
            )["input_schema"]["properties"]["validationPlans"]["items"]["properties"]
            rollback_item = plan_properties["rollbackIndicators"]["items"]
            assert rollback_item["required"] == ["metricRef", "condition"]
            assert rollback_item["additionalProperties"] is False
            assert rollback_item["properties"]["metricRef"] == {
                "type": "integer", "enum": list(aliases.provider_refs),
            }
            assert "rollbackMetricRefs" not in plan_properties
            assert "rollbackConditions" not in plan_properties


def _validated_stage_b_with_plan(package, *, action_type: str = "Investigate"):
    canonical = valid_response(package)
    canonical["changeCandidates"][0]["actionType"] = action_type
    canonical["changeCandidates"][0]["validationPlanId"] = "VAL-001"
    stage_a = validate_stage_a(package, {
        "observations": canonical["observations"],
        "interpretations": canonical["interpretations"],
        "evidenceGaps": canonical["evidenceGaps"],
    })
    stage_b = validate_stage_b(package, {
        "hypotheses": canonical["hypotheses"],
        "changeCandidates": canonical["changeCandidates"],
    }, stage_a)
    return canonical, stage_a, stage_b


def test_validation_plan_refs_are_deterministic_bijective_and_include_declared_non_actionable(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    _canonical, _stage_a, stage_b = _validated_stage_b_with_plan(package)
    first = build_validation_plan_ref_table(stage_b)
    second = build_validation_plan_ref_table(stage_b)
    assert first.canonical_pairs == (("VAL-001", "CHG-001"),)
    assert first.provider_refs == (1,)
    assert first.digest == second.digest
    assert first.provider_ref("VAL-001", "CHG-001") == 1
    assert first.canonical_pair(1) == ("VAL-001", "CHG-001")
    with pytest.raises(AnthropicValidationPlanRefError, match="not in the ref mapping"):
        first.provider_ref("VAL-001", "CHG-002")
    for invalid in (0, 2, "1", True, False):
        with pytest.raises(AnthropicValidationPlanRefError):
            first.canonical_pair(invalid)
    with pytest.raises(AnthropicValidationPlanRefError, match="duplicate"):
        ValidationPlanRefTable((("VAL-001", "CHG-001"), ("VAL-001", "CHG-002")))


def test_stage_c_schema_uses_closed_enums_and_host_plan_ref_only() -> None:
    tools = anthropic_stage_strict_tools("C", validation_plan_refs=(1, 2))
    plan_schema = next(
        tool for tool in tools if tool["name"] == "submit_validation_plans"
    )["input_schema"]["properties"]["validationPlans"]
    properties = plan_schema["items"]["properties"]
    assert "minItems" not in plan_schema
    assert "maxItems" not in plan_schema
    assert properties["validationPlanRef"] == {"type": "integer", "enum": [1, 2]}
    assert "id" not in properties and "changeCandidateId" not in properties
    assert properties["minimumEvidenceRequirements"]["items"] == {
        "type": "string", "enum": sorted(MINIMUM_REQUIREMENTS)
    }
    rollback_item = properties["rollbackIndicators"]["items"]
    assert rollback_item["required"] == ["metricRef", "condition"]
    assert rollback_item["additionalProperties"] is False
    assert rollback_item["properties"]["condition"] == {
        "type": "string", "enum": sorted(ROLLBACK_CONDITIONS)
    }
    requirement_validator = Draft202012Validator(
        properties["minimumEvidenceRequirements"]["items"]
    )
    rollback_validator = Draft202012Validator(
        rollback_item["properties"]["condition"]
    )
    for value in MINIMUM_REQUIREMENTS:
        requirement_validator.validate(value)
    for value in ROLLBACK_CONDITIONS:
        rollback_validator.validate(value)
    for invalid in ("Collect at least 100 runs", "UnknownRequirement"):
        with pytest.raises(ValidationError):
            requirement_validator.validate(invalid)
    for invalid in ("RollbackIfWorse", "UnexpectedOutcome"):
        with pytest.raises(ValidationError):
            rollback_validator.validate(invalid)


def test_validation_plan_ref_projection_inverse_and_exact_coverage(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical, _stage_a, stage_b = _validated_stage_b_with_plan(package)
    table = build_validation_plan_ref_table(stage_b)
    canonical["validationPlans"] = [{
        "id": "VAL-001",
        "changeCandidateId": "CHG-001",
        "analysesToRerun": ["stageDifficulty"],
        "metricsToWatch": [],
        "guardrailMetrics": [],
        "minimumEvidenceRequirements": [],
        "comparisonPlan": "NewContentVersionVsCurrentUsingContentVersionCompare",
        "rollbackIndicators": [],
    }]
    flat = flatten_canonical_response(canonical)
    stage_flat = {
        field: flat[field]
        for _name, fields in ANTHROPIC_STAGE_SPECS["C"] for field in fields
    }
    projected = project_canonical_stage_validation_plan_refs(stage_flat, table)
    assert projected["validationPlans"][0]["validationPlanRef"] == 1
    assert "id" not in projected["validationPlans"][0]
    assert restore_provider_stage_validation_plan_refs(projected, table) == stage_flat

    duplicate = deepcopy(projected)
    duplicate["validationPlans"].append(deepcopy(duplicate["validationPlans"][0]))
    with pytest.raises(AnthropicValidationPlanRefError, match="exactly cover"):
        restore_provider_stage_validation_plan_refs(duplicate, table)
    missing = deepcopy(projected)
    missing["validationPlans"] = []
    with pytest.raises(AnthropicValidationPlanRefError, match="exactly cover"):
        restore_provider_stage_validation_plan_refs(missing, table)
    for invalid in (0, 2, "1", True, False):
        malformed = deepcopy(projected)
        malformed["validationPlans"][0]["validationPlanRef"] = invalid
        with pytest.raises(AnthropicValidationPlanRefError):
            restore_provider_stage_validation_plan_refs(malformed, table)


@pytest.mark.parametrize("include_plan", [False, True])
def test_stage_b_host_assigns_hypothesis_change_and_plan_identity(
    tmp_path: Path, include_plan: bool
) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    if include_plan:
        canonical["changeCandidates"][0]["validationPlanId"] = "VAL-001"
    stage_a = validate_stage_a(package, {
        "observations": canonical["observations"],
        "interpretations": canonical["interpretations"],
        "evidenceGaps": canonical["evidenceGaps"],
    })
    output_refs = build_stage_output_ref_tables(stage_a)
    flat = flatten_canonical_response(canonical)
    stage_flat = {
        field: flat[field]
        for _name, fields in ANTHROPIC_STAGE_SPECS["B"] for field in fields
    }
    projected = project_canonical_stage_b_authority(stage_flat, output_refs)
    assert "id" not in projected["hypotheses"][0]
    assert "id" not in projected["changeCandidates"][0]
    assert "validationPlanId" not in projected["changeCandidates"][0]
    assert projected["changeCandidates"][0]["includeValidationPlan"] is include_plan
    assert projected["hypotheses"][0]["evidenceGapRefs"] == [1]
    assert restore_provider_stage_b_authority(projected, output_refs) == stage_flat


def test_stage_b_schema_makes_bookkeeping_ids_impossible() -> None:
    tools = anthropic_stage_strict_tools(
        "B",
        evidence_refs=(1,),
        warning_refs=(),
        evidence_gap_refs=(1, 2),
    )
    hypotheses = next(
        tool for tool in tools if tool["name"] == "submit_hypotheses"
    )["input_schema"]["properties"]["hypotheses"]["items"]["properties"]
    changes = next(
        tool for tool in tools if tool["name"] == "submit_change_candidates"
    )["input_schema"]["properties"]["changeCandidates"]["items"]["properties"]
    assert "id" not in hypotheses
    assert "evidenceGapIds" not in hypotheses
    assert hypotheses["evidenceGapRefs"]["items"] == {
        "type": "integer", "enum": [1, 2]
    }
    assert "id" not in changes
    assert "validationPlanId" not in changes
    assert changes["includeValidationPlan"] == {"type": "boolean"}
    expected_domains = sorted({
        "stageDifficulty", "weaponPerformance", "upgradeChoice",
        "progressionNextRun", "postRunBehavior", "contentVersionCompare",
            "retentionEvidence",
        "crossDomain",
    })
    assert changes["domain"]["enum"] == expected_domains
    assert changes["targetDomain"]["enum"] == expected_domains


def test_stage_c_summary_uses_exact_refs_and_round_trips(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    stage_a = validate_stage_a(package, {
        "observations": canonical["observations"],
        "interpretations": canonical["interpretations"],
        "evidenceGaps": canonical["evidenceGaps"],
    })
    stage_b = validate_stage_b(package, {
        "hypotheses": canonical["hypotheses"],
        "changeCandidates": canonical["changeCandidates"],
    }, stage_a)
    output_refs = build_stage_output_ref_tables(stage_a, stage_b)
    flat = flatten_canonical_response(canonical)
    stage_flat = {
        field: flat[field]
        for _name, fields in ANTHROPIC_STAGE_SPECS["C"] for field in fields
    }
    projected = project_canonical_stage_c_summary_refs(stage_flat, output_refs)
    assert projected["executiveObservationRefs"] == [1]
    assert projected["executiveHypothesisRefs"] == [1]
    assert projected["executiveEvidenceGapRefs"] == [1]
    assert projected["executiveChangeCandidateRefs"] == [1]
    assert "executiveObservationIds" not in projected
    assert restore_provider_stage_c_summary_refs(projected, output_refs) == stage_flat

    tools = anthropic_stage_strict_tools(
        "C",
        validation_plan_refs=(),
        observation_refs=(1,), hypothesis_refs=(1,),
        evidence_gap_refs=(1,), change_candidate_refs=(1,),
    )
    summary = next(
        tool for tool in tools if tool["name"] == "submit_executive_summary"
    )["input_schema"]["properties"]
    assert "executiveObservationIds" not in summary
    assert summary["executiveObservationRefs"]["items"] == {
        "type": "integer", "enum": [1]
    }
    plan = next(
        tool for tool in tools if tool["name"] == "submit_validation_plans"
    )["input_schema"]["properties"]["validationPlans"]["items"]["properties"]
    assert plan["analysesToRerun"]["items"]["enum"] == sorted({
        "stageDifficulty", "weaponPerformance", "upgradeChoice",
        "progressionNextRun", "postRunBehavior", "contentVersionCompare",
            "retentionEvidence",
    })


def test_output_ref_tables_reject_duplicates_and_unknown_refs() -> None:
    with pytest.raises(AnthropicOutputRefError, match="duplicate"):
        OutputRefTable("change", ("CHG-001", "CHG-001"))
    table = OutputRefTable("change", ("CHG-001",))
    for invalid in (0, 2, "1", True, False):
        with pytest.raises(AnthropicOutputRefError):
            table.canonical_id(invalid)

    refs = StageOutputRefTables(
        observations=OutputRefTable("observation", ("OBS-001",)),
        evidence_gaps=OutputRefTable("gap", ("GAP-001",)),
        hypotheses=OutputRefTable("hypothesis", ("HYP-001",)),
        change_candidates=table,
    )
    with pytest.raises(AnthropicOutputRefError, match="duplicates"):
        restore_provider_stage_c_summary_refs({
            "executiveObservationRefs": [1, 1],
            "executiveHypothesisRefs": [],
            "executiveEvidenceGapRefs": [],
            "executiveChangeCandidateRefs": [],
        }, refs)


def test_expected_direction_schema_uses_canonical_enum() -> None:
    tools = anthropic_stage_strict_tools(
        "B",
        evidence_refs=tuple(range(1, 107)),
        metric_refs=build_metric_alias_table().provider_refs,
        warning_refs=(1,),
    )
    change_tool = next(tool for tool in tools if tool["name"] == "submit_change_candidates")
    item = change_tool["input_schema"]["properties"]["changeCandidates"]["items"]
    expected = item["properties"]["expectedObservables"]["items"]
    assert expected["required"] == ["metricRef", "direction"]
    assert expected["additionalProperties"] is False
    assert expected["properties"]["direction"] == {
        "type": "string",
        "enum": ["Decrease", "Increase", "MonitorOnly", "NoAssumedDirection"],
    }


def test_stage_a_b_schemas_use_exact_106_value_integer_enums() -> None:
    refs = tuple(range(1, 107))
    for stage in ("A", "B"):
        schemas = []
        for tool in anthropic_stage_strict_tools(
            stage, evidence_refs=refs, warning_refs=(1,)
        ):
            properties = tool["input_schema"]["properties"]
            for section_schema in properties.values():
                item_properties = section_schema["items"]["properties"]
                schemas.extend(
                    value["items"]
                    for name, value in item_properties.items()
                    if name == "evidenceRefs" or name.endswith("EvidenceRefs")
                )
        assert len(schemas) == 4
        assert all(schema == {"type": "integer", "enum": list(refs)} for schema in schemas)
        for schema in schemas:
            validator = Draft202012Validator(schema)
            validator.validate(1)
            validator.validate(106)
            for invalid in (0, 107, "1", "EV-stage-test"):
                with pytest.raises(ValidationError):
                    validator.validate(invalid)


def test_stage_a_b_provider_visible_requests_contain_no_canonical_evidence_ids(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    package = _package_with_evidence_count(package, 106)
    aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)
    canonical = valid_response(package)
    flat = flatten_canonical_response(canonical)

    system_a, messages_a, context_a = build_anthropic_stage_prompt_parts(
        package, "A", evidence_aliases=aliases
    )
    tools_a = anthropic_stage_strict_tools(
        "A", evidence_refs=aliases.provider_refs,
        warning_refs=warning_aliases.provider_refs,
    )
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", aliases),
            "A",
            evidence_aliases=aliases,
        ).sections,
    )
    system_b, messages_b, context_b = build_anthropic_stage_prompt_parts(
        package, "B", stage_a=stage_a, evidence_aliases=aliases,
        warning_aliases=warning_aliases,
    )
    tools_b = anthropic_stage_strict_tools(
        "B", evidence_refs=aliases.provider_refs,
        warning_refs=warning_aliases.provider_refs,
    )

    for prepared in (
        {"system": system_a, "messages": messages_a, "context": context_a, "tools": tools_a},
        {"system": system_b, "messages": messages_b, "context": context_b, "tools": tools_b},
    ):
        encoded = json.dumps(prepared, ensure_ascii=False, sort_keys=True)
        assert all(value not in encoded for value in aliases.canonical_evidence_ids)


def test_compact_prompt_uses_factored_payload_not_canonical_catalog(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    package = _package_with_evidence_count(package, 106)
    system, messages, payload = build_anthropic_prompt_parts(package)
    user = messages[0]["content"]
    assert len(payload["evidence"]) == 106
    assert "# Response Contract" not in system
    assert "canonicalIdentity" not in user
    assert user.count('"sourceBundleDigest"') == 1
    # One factored quality set plus the compact brief's global quality summary.
    assert user.count('"warningCodes"') == 2
    for item in payload["evidence"]:
        assert user.count(item["evidenceId"]) == 1


def test_serialized_schema_has_seven_required_strings_and_no_optional_union(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    schema = structured_output_config(package)["format"]["schema"]
    serialized = str(schema)
    assert set(schema["properties"]) == {
        "observationsJson", "interpretationsJson", "hypothesesJson",
        "evidenceGapsJson", "changeCandidatesJson", "validationPlansJson",
        "executiveSummaryJson",
    }
    assert schema["required"] == list(schema["properties"])
    assert all(value == {"type": "string"} for value in schema["properties"].values())
    assert _optional_property_count(schema) == 0
    assert "anyOf" not in serialized
    assert "null" not in serialized


def test_seven_strict_tool_schemas_have_no_optional_union_or_nullable(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    tools = anthropic_strict_tools(package)
    assert len(tools) == 7
    assert len({tool["name"] for tool in tools}) == 7
    for tool in tools:
        schema = tool["input_schema"]
        encoded = str(schema)
        assert tool["strict"] is True
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])
        assert _optional_property_count(schema) == 0
        assert "anyOf" not in encoded
        assert "'null'" not in encoded


def _tool_blocks(canonical: dict) -> list[SimpleNamespace]:
    flat = flatten_canonical_response(canonical)
    return [
        SimpleNamespace(type="tool_use", id=f"tool-{index}", name=name, input=value)
        for index, (name, value) in enumerate(
            split_anthropic_flat_tool_inputs(flat), start=1
        )
    ]


def test_tool_collection_is_order_independent_and_ignores_text(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    blocks = list(reversed(_tool_blocks(canonical)))
    blocks.insert(2, SimpleNamespace(type="text", text="not authoritative"))
    collected = collect_anthropic_tool_response(blocks, package)
    assert collected.response == canonical
    assert collected.observed_tool_count == 7
    assert collected.ignored_text_block_count == 1


def test_tool_collection_fails_closed_for_set_and_wrapper_errors(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    blocks = _tool_blocks(canonical)
    with pytest.raises(AnthropicRequiredToolMissingError):
        collect_anthropic_tool_response(blocks[:-1], package)
    with pytest.raises(AnthropicDuplicateToolUseError):
        collect_anthropic_tool_response([*blocks, blocks[0]], package)
    unknown = deepcopy(blocks)
    unknown[0].name = "submit_unknown"
    with pytest.raises(AnthropicUnknownToolUseError):
        collect_anthropic_tool_response(unknown, package)
    invalid = deepcopy(blocks)
    invalid[0].input = {"wrong": []}
    with pytest.raises(AnthropicInvalidToolInputError):
        collect_anthropic_tool_response(invalid, package)
    wrong_container = deepcopy(blocks)
    wrong_container[0].input = {"observations": "not-an-array"}
    with pytest.raises(AnthropicInvalidToolInputError):
        collect_anthropic_tool_response(wrong_container, package)
    with pytest.raises(AnthropicMalformedResponseError):
        collect_anthropic_tool_response(
            [*blocks, SimpleNamespace(type="thinking", thinking="private")], package
        )


def test_serialized_sections_round_trip_to_exact_canonical_contract(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    envelope = serialize_canonical_response_sections(canonical)
    reconstructed = reconstruct_anthropic_serialized_response(envelope, package)
    assert reconstructed == canonical
    validate_response(package, reconstructed)


@pytest.mark.parametrize(
    ("field", "encoded", "message"),
    [
        ("observationsJson", "```json\\n[]\\n```", "strict JSON"),
        ("interpretationsJson", '[{"id":"INT-001",}]', "strict JSON"),
        ("hypothesesJson", '[{"id":"HYP-001","id":"HYP-002"}]', "strict JSON"),
        ("evidenceGapsJson", '[{"value":NaN}]', "strict JSON"),
        ("changeCandidatesJson", '[{"value":1e400}]', "strict JSON"),
        ("validationPlansJson", '{}', "JSON array"),
        ("executiveSummaryJson", '[]', "JSON object"),
    ],
)
def test_serialized_section_parser_is_strict_transport_only(
    tmp_path: Path, field: str, encoded: str, message: str,
) -> None:
    _, package = make_package(tmp_path)
    envelope = dict(serialize_canonical_response_sections(valid_response(package)))
    envelope[field] = encoded
    with pytest.raises(AnthropicMalformedResponseError, match=message) as captured:
        reconstruct_anthropic_serialized_response(envelope, package)
    assert encoded not in str(captured.value)


def test_serialized_outer_json_rejects_duplicate_and_additional_fields(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    with pytest.raises(AnthropicMalformedResponseError, match="outerEnvelope"):
        parse_anthropic_serialized_response_text(
            '{"observationsJson":"[]","observationsJson":"[]"}', package
        )

    envelope = dict(serialize_canonical_response_sections(valid_response(package)))
    envelope["unexpectedJson"] = "[]"
    with pytest.raises(AnthropicMalformedResponseError, match="top-level shape"):
        reconstruct_anthropic_serialized_response(envelope, package)

    envelope = dict(serialize_canonical_response_sections(valid_response(package)))
    envelope.pop("observationsJson")
    with pytest.raises(AnthropicMalformedResponseError, match="top-level shape"):
        reconstruct_anthropic_serialized_response(envelope, package)

    envelope = dict(serialize_canonical_response_sections(valid_response(package)))
    envelope["observationsJson"] = []
    with pytest.raises(AnthropicMalformedResponseError, match="must be a string"):
        reconstruct_anthropic_serialized_response(envelope, package)


def test_serialized_parser_defers_semantics_and_cardinality_to_validator(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    canonical["observations"] = [
        deepcopy(canonical["observations"][0]) for _ in range(11)
    ]
    for index, item in enumerate(canonical["observations"], start=1):
        item["id"] = f"OBS-{index:03d}"
    canonical["observations"][0]["findingType"] = "InventedEnum"
    reconstructed = reconstruct_anthropic_serialized_response(
        serialize_canonical_response_sections(canonical), package
    )
    assert len(reconstructed["observations"]) == 11
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, reconstructed)


def test_flat_response_round_trips_to_exact_canonical_contract(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    flat = flatten_canonical_response(canonical)
    reconstructed = reconstruct_anthropic_flat_response(flat, package)
    assert reconstructed == canonical
    validate_response(package, reconstructed)
    assert _instance_depth(flat) <= 4


def test_flat_response_parallel_and_sentinel_fail_closed(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    flat = deepcopy(flatten_canonical_response(valid_response(package)))
    flat["changeCandidates"][0]["changeAmountPercent"] = 1
    with pytest.raises(AnthropicMalformedResponseError, match="sentinel"):
        reconstruct_anthropic_flat_response(flat, package)

    flat = deepcopy(flatten_canonical_response(valid_response(package)))
    flat["changeCandidates"][0]["expectedMetricRefs"] = [
        "stageDifficulty::outcome::clearRate"
    ]
    with pytest.raises(AnthropicMalformedResponseError, match="different lengths"):
        reconstruct_anthropic_flat_response(flat, package)


def test_metric_reference_is_exact_case_sensitive_and_registry_backed() -> None:
    assert encode_metric_reference(
        "stageDifficulty", "outcome", "clearRate"
    ) == "stageDifficulty::outcome::clearRate"
    with pytest.raises(AnthropicMalformedResponseError, match="canonical registry"):
        encode_metric_reference("StageDifficulty", "outcome", "clearRate")
    with pytest.raises(AnthropicMalformedResponseError, match="canonical registry"):
        encode_metric_reference("stageDifficulty", "outcome", "inventedMetric")


def test_wire_cardinality_remains_authoritative_in_local_validator(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    canonical["observations"] = [deepcopy(canonical["observations"][0]) for _ in range(11)]
    for index, item in enumerate(canonical["observations"], start=1):
        item["id"] = f"OBS-{index:03d}"
    reconstructed = reconstruct_anthropic_flat_response(
        flatten_canonical_response(canonical), package
    )
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, reconstructed)


def _staged_tool_blocks(
    flat: dict, stage: str, aliases,
) -> list[SimpleNamespace]:
    warning_aliases = _warning_aliases_from_flat(flat)
    validation_plan_refs = (
        ValidationPlanRefTable(tuple(
            (item["id"], item["changeCandidateId"])
            for item in flat["validationPlans"]
        ))
        if stage == "C"
        else None
    )
    fields = {
        field
        for tool in anthropic_stage_strict_tools(
            stage,
            evidence_refs=aliases.provider_refs if stage in {"A", "B"} else None,
            warning_refs=(
                warning_aliases.provider_refs if stage in {"A", "B"} else None
            ),
            validation_plan_refs=(
                validation_plan_refs.provider_refs
                if validation_plan_refs is not None else None
            ),
        )
        for field in tool["input_schema"]["properties"]
    }
    canonical_fields = {
        field for _name, values in ANTHROPIC_STAGE_SPECS[stage] for field in values
    }
    staged_flat = {field: flat[field] for field in canonical_fields}
    return [
        SimpleNamespace(type="tool_use", id=f"{stage}-{index}", name=name, input=value)
        for index, (name, value) in enumerate(
            split_anthropic_stage_tool_inputs(
                staged_flat,
                stage,
                evidence_aliases=aliases if stage in {"A", "B"} else None,
                validation_plan_refs=validation_plan_refs,
            ), start=1
        )
    ]


def _tool_block(blocks: list[SimpleNamespace], name: str) -> SimpleNamespace:
    return next(block for block in blocks if block.name == name)


@pytest.mark.parametrize(
    ("counter_ids", "expected_status"),
    [
        ([], "NotIdentifiedInSuppliedBrief"),
        (["same-as-support"], "FoundInSuppliedBrief"),
    ],
)
def test_stage_b_counter_evidence_status_is_host_derived_for_both_sections(
    tmp_path: Path,
    counter_ids: list[str],
    expected_status: str,
) -> None:
    _, package = make_package(tmp_path)
    evidence_id = next(iter(package.source.evidence_by_id))
    canonical = valid_response(package)
    resolved_counter_ids = [evidence_id] if counter_ids else []
    for section in ("hypotheses", "changeCandidates"):
        canonical[section][0]["counterEvidenceIds"] = resolved_counter_ids
        canonical[section][0]["counterEvidenceSearchStatus"] = expected_status

    aliases = build_evidence_alias_table(package)
    flat = flatten_canonical_response(canonical)
    stage_a = validate_stage_a(package, {
        "observations": canonical["observations"],
        "interpretations": canonical["interpretations"],
        "evidenceGaps": canonical["evidenceGaps"],
    })
    blocks = _staged_tool_blocks(flat, "B", aliases)
    for tool_name, section in (
        ("submit_hypotheses", "hypotheses"),
        ("submit_change_candidates", "changeCandidates"),
    ):
        provider_row = _tool_block(blocks, tool_name).input[section][0]
        assert "counterEvidenceSearchStatus" not in provider_row
        assert provider_row["counterEvidenceRefs"] == (
            [aliases.provider_ref(evidence_id)] if resolved_counter_ids else []
        )

    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=aliases,
        metric_aliases=build_metric_alias_table(),
    )
    assert collected.sections == {
        "hypotheses": canonical["hypotheses"],
        "changeCandidates": canonical["changeCandidates"],
    }
    validated = validate_stage_b(package, collected.sections, stage_a)
    assert all(
        item.counter_evidence_search_status == expected_status
        for item in (*validated.hypotheses, *validated.change_candidates)
    )


def test_stage_b_schema_and_prompt_do_not_delegate_counter_status(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    aliases = build_evidence_alias_table(package)
    metric_aliases = build_metric_alias_table()
    warning_aliases = build_warning_alias_table(package)
    tools = anthropic_stage_strict_tools(
        "B",
        evidence_refs=aliases.provider_refs,
        metric_refs=metric_aliases.provider_refs,
        warning_refs=warning_aliases.provider_refs,
    )
    serialized_tools = json.dumps(tools, ensure_ascii=False, sort_keys=True)
    assert "counterEvidenceSearchStatus" not in serialized_tools

    canonical = valid_response(package)
    stage_a = validate_stage_a(package, {
        "observations": canonical["observations"],
        "interpretations": canonical["interpretations"],
        "evidenceGaps": canonical["evidenceGaps"],
    })
    system, messages, _context = build_anthropic_stage_prompt_parts(
        package,
        "B",
        stage_a=stage_a,
        evidence_aliases=aliases,
        metric_aliases=metric_aliases,
        warning_aliases=warning_aliases,
    )
    prepared_output_contract = json.dumps(
        {"system": system, "messages": messages, "tools": tools},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "counterEvidenceSearchStatus" not in prepared_output_contract
    assert "Use an empty counter-evidence list" in system
    assert "does not prove that counter evidence does not exist" in system


def test_stage_b_rejects_provider_owned_counter_status(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    aliases = build_evidence_alias_table(package)
    blocks = _staged_tool_blocks(
        flatten_canonical_response(valid_response(package)), "B", aliases
    )
    hypothesis = _tool_block(blocks, "submit_hypotheses").input["hypotheses"][0]
    hypothesis["counterEvidenceSearchStatus"] = "NotIdentifiedInSuppliedBrief"

    with pytest.raises(AnthropicFlatReconstructionError) as captured:
        collect_anthropic_stage_tool_response(
            blocks,
            "B",
            evidence_aliases=aliases,
            metric_aliases=build_metric_alias_table(),
        )
    assert captured.value.details["reconstructionInvariant"] == (
        "UNEXPECTED_RECONSTRUCTION_FIELD"
    )
    assert captured.value.details["reconstructionComponent"] == (
        "counterEvidenceSearchStatus"
    )


@pytest.mark.parametrize(
    ("mutate", "invariant", "component"),
    [
        (
            lambda item, aliases: item["expectedObservables"].append({
                "metricRef": 1,
            }),
            "MISSING_REQUIRED_RECONSTRUCTION_FIELD",
            "expectedObservableDirections",
        ),
        (
            lambda item, aliases: item.update({
                "changeAmountPercentPresent": False,
                "changeAmountPercent": 7,
            }),
            "INVALID_AMOUNT_SENTINEL_STATE",
            "changeAmountPercent",
        ),
        (
            lambda item, aliases: item.update({"targetMetricRef": [1, 2]}),
            "INVALID_TARGET_RECONSTRUCTION",
            "targetMetricRef",
        ),
        (
            lambda item, aliases: item.update({
                "targetMetricRef": len(aliases.provider_refs) + 1
            }),
            "UNKNOWN_METRIC_ALIAS",
            "targetMetricRef",
        ),
        (
            lambda item, aliases: item.update({
                "targetMetricRef": next(
                    ref for ref in aliases.provider_refs
                    if aliases.canonical_key(ref)[0] != item["targetDomain"]
                )
            }),
            "INVALID_TARGET_RECONSTRUCTION",
            "targetDomain",
        ),
        (
            lambda item, aliases: item.update({
                "expectedObservables": [{
                    "metricRef": len(aliases.provider_refs) + 1,
                    "direction": "Increase",
                }]
            }),
            "UNKNOWN_METRIC_ALIAS",
            "expectedObservableDirections",
        ),
        (
            lambda item, aliases: item.update({"changeParameter": {"secret": "x"}}),
            "INVALID_SENTINEL",
            "proposedChange",
        ),
    ],
)
def test_stage_b_reconstruction_invariants_are_safe_and_specific(
    tmp_path: Path,
    mutate,
    invariant: str,
    component: str,
) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    metric_aliases = build_metric_alias_table()
    blocks = _staged_tool_blocks(
        flatten_canonical_response(valid_response(package)),
        "B",
        evidence_aliases,
    )
    change_tool = _tool_block(blocks, "submit_change_candidates")
    item = change_tool.input["changeCandidates"][0]
    item["rationale"] = "SECRET-PAYLOAD-MARKER"
    mutate(item, metric_aliases)

    with pytest.raises(AnthropicFlatReconstructionError) as captured:
        collect_anthropic_stage_tool_response(
            blocks,
            "B",
            evidence_aliases=evidence_aliases,
            metric_aliases=metric_aliases,
        )
    error = captured.value
    assert error.code == "ANTHROPIC_FLAT_RECONSTRUCTION_FAILED"
    assert error.details["stage"] == "B"
    assert error.details["processingBoundary"] == "reconstruction"
    assert error.details["section"] == "changeCandidates"
    assert error.details["itemIndex"] == 0
    assert error.details["reconstructionComponent"] == component
    assert error.details["reconstructionInvariant"] == invariant
    serialized = json.dumps(error.details, ensure_ascii=False)
    assert "SECRET-PAYLOAD-MARKER" not in str(error)
    assert "SECRET-PAYLOAD-MARKER" not in serialized
    assert "secret" not in serialized.lower()


def test_stage_b_unknown_evidence_alias_is_structural_only(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    metric_aliases = build_metric_alias_table()
    blocks = _staged_tool_blocks(
        flatten_canonical_response(valid_response(package)),
        "B",
        evidence_aliases,
    )
    item = _tool_block(blocks, "submit_hypotheses").input["hypotheses"][0]
    item["supportingEvidenceRefs"] = [len(evidence_aliases.provider_refs) + 1]
    with pytest.raises(AnthropicFlatReconstructionError) as captured:
        collect_anthropic_stage_tool_response(
            blocks,
            "B",
            evidence_aliases=evidence_aliases,
            metric_aliases=metric_aliases,
        )
    assert captured.value.details == {
        "stage": "B",
        "processingBoundary": "reconstruction",
        "section": "hypotheses",
        "reconstructionComponent": "supportingEvidenceRefs",
        "reconstructionInvariant": "UNKNOWN_EVIDENCE_ALIAS",
        "itemIndex": 0,
        "aliasDomainSize": len(evidence_aliases.provider_refs),
    }


def test_stage_c_structured_rollback_pair_cannot_express_length_mismatch(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    flat = flatten_canonical_response(valid_response(package))
    metric_ref = "stageDifficulty::outcome::clearRate"
    flat["validationPlans"] = [{
        "id": "VAL-001",
        "changeCandidateId": "CHG-001",
        "analysesToRerun": ["stageDifficulty"],
        "metricsToWatchRefs": [metric_ref],
        "guardrailMetricRefs": [],
        "minimumEvidenceRequirements": ["MeetExistingAnalyticsThreshold"],
        "comparisonPlan": "NewContentVersionVsCurrentUsingContentVersionCompare",
        "rollbackMetricRefs": [metric_ref],
        "rollbackConditions": ["UnexpectedDirection"],
    }]
    blocks = _staged_tool_blocks(
        flat,
        "C",
        build_evidence_alias_table(package),
    )
    plan_refs = ValidationPlanRefTable((("VAL-001", "CHG-001"),))
    plan = _tool_block(blocks, "submit_validation_plans").input["validationPlans"][0]
    assert "rollbackMetricRefs" not in plan
    assert "rollbackConditions" not in plan
    assert plan["rollbackIndicators"] == [{
        "metricRef": build_metric_alias_table().provider_ref(
            ("stageDifficulty", "outcome", "clearRate")
        ),
        "condition": "UnexpectedDirection",
    }]
    del plan["rollbackIndicators"][0]["condition"]
    with pytest.raises(AnthropicFlatReconstructionError) as captured:
        collect_anthropic_stage_tool_response(
            blocks,
            "C",
            metric_aliases=build_metric_alias_table(),
            validation_plan_refs=plan_refs,
        )
    assert captured.value.details["section"] == "validationPlans"
    assert captured.value.details["reconstructionComponent"] == "rollbackIndicators"
    assert captured.value.details["reconstructionInvariant"] == (
        "MISSING_REQUIRED_RECONSTRUCTION_FIELD"
    )


@pytest.mark.parametrize("pair_count", [0, 1, 2])
def test_structured_expected_and_rollback_pairs_round_trip_exactly(
    tmp_path: Path,
    pair_count: int,
) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    metric = {
        "domain": "stageDifficulty",
        "metricFamily": "outcome",
        "metric": "clearRate",
    }
    canonical["changeCandidates"][0]["expectedObservableDirections"] = [
        {**metric, "direction": direction}
        for direction in ("Increase", "MonitorOnly")[:pair_count]
    ]
    canonical["validationPlans"] = [{
        "id": "VAL-001",
        "changeCandidateId": "CHG-001",
        "analysesToRerun": ["stageDifficulty"],
        "metricsToWatch": [],
        "guardrailMetrics": [],
        "minimumEvidenceRequirements": [],
        "comparisonPlan": "NewContentVersionVsCurrentUsingContentVersionCompare",
        "rollbackIndicators": [
            {"metric": metric, "condition": condition}
            for condition in ("UnexpectedDirection", "DesignObjectiveMiss")[:pair_count]
        ],
    }]
    canonical["changeCandidates"][0]["validationPlanId"] = "VAL-001"
    flat = flatten_canonical_response(canonical)
    aliases = build_metric_alias_table()
    for stage in ("B", "C"):
        fields = {
            field for _name, values in ANTHROPIC_STAGE_SPECS[stage]
            for field in values
        }
        stage_flat = {field: deepcopy(flat[field]) for field in fields}
        projected = project_canonical_stage_metric_aliases(
            stage_flat, stage, aliases
        )
        assert restore_provider_stage_metric_aliases(
            projected, stage, aliases
        ) == stage_flat
        rows = projected[
            "changeCandidates" if stage == "B" else "validationPlans"
        ]
        pair_field = "expectedObservables" if stage == "B" else "rollbackIndicators"
        assert len(rows[0][pair_field]) == pair_count


@pytest.mark.parametrize(
    ("stage", "pair_field", "valid_item", "invalid_item"),
    [
        (
            "B", "expectedObservables",
            {"metricRef": 1, "direction": "Increase"},
            {"metricRef": 1},
        ),
        (
            "C", "rollbackIndicators",
            {"metricRef": 1, "condition": "UnexpectedDirection"},
            {"condition": "UnexpectedDirection"},
        ),
    ],
)
def test_structured_pair_schema_requires_both_sides(
    stage: str,
    pair_field: str,
    valid_item: dict,
    invalid_item: dict,
) -> None:
    tools = anthropic_stage_strict_tools(
        stage,
        evidence_refs=(1,) if stage == "B" else None,
        warning_refs=() if stage == "B" else None,
        validation_plan_refs=(1,) if stage == "C" else None,
    )
    tool_name = (
        "submit_change_candidates" if stage == "B" else "submit_validation_plans"
    )
    section = "changeCandidates" if stage == "B" else "validationPlans"
    item_schema = next(tool for tool in tools if tool["name"] == tool_name)[
        "input_schema"
    ]["properties"][section]["items"]["properties"][pair_field]["items"]
    validator = Draft202012Validator(item_schema)
    validator.validate(valid_item)
    with pytest.raises(ValidationError):
        validator.validate(invalid_item)
    with pytest.raises(ValidationError):
        validator.validate({**valid_item, "extra": True})
    with pytest.raises(ValidationError):
        validator.validate({**valid_item, "metricRef": 0})


def test_nullable_singleton_target_metric_uses_zero_sentinel_and_validates(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    metric_aliases = build_metric_alias_table()
    flat = flatten_canonical_response(valid_response(package))
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", evidence_aliases),
            "A",
            evidence_aliases=evidence_aliases,
        ).sections,
    )
    blocks = _staged_tool_blocks(flat, "B", evidence_aliases)
    target = _tool_block(blocks, "submit_change_candidates").input[
        "changeCandidates"
    ][0]
    target.update({
        "targetType": "Conceptual",
        "targetMetricRef": 0,
        "structuredTargetRequiresGameDesignContext": False,
    })

    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=evidence_aliases,
        metric_aliases=metric_aliases,
    )
    canonical_target = collected.sections["changeCandidates"][0]["target"]
    assert canonical_target["metricFamily"] is None
    assert canonical_target["metric"] is None
    assert canonical_target["description"] is None
    assert canonical_target["requiresGameDesignContext"] is True
    validate_stage_b(package, collected.sections, stage_a)


@pytest.mark.parametrize("requires_context", [False, True])
def test_evidence_metric_uses_structured_identity_and_preserves_context_bool(
    tmp_path: Path,
    requires_context: bool,
) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", evidence_aliases),
            "A",
            evidence_aliases=evidence_aliases,
        ).sections,
    )
    blocks = _staged_tool_blocks(flat, "B", evidence_aliases)
    target = _tool_block(blocks, "submit_change_candidates").input[
        "changeCandidates"
    ][0]
    target["conceptualTargetDescription"] = "관측 비율은 20%다."
    target["structuredTargetRequiresGameDesignContext"] = requires_context

    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=evidence_aliases,
        metric_aliases=build_metric_alias_table(),
    )
    canonical_target = collected.sections["changeCandidates"][0]["target"]
    assert canonical_target["description"] is None
    assert canonical_target["requiresGameDesignContext"] is requires_context
    validate_stage_b(package, collected.sections, stage_a)


@pytest.mark.parametrize("requires_context", [False, True])
def test_evidence_entity_uses_structured_identity_and_preserves_context_bool(
    tmp_path: Path,
    requires_context: bool,
) -> None:
    _, package = make_package(tmp_path)
    package = _package_with_entity_evidence(package)
    evidence_aliases = build_evidence_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", evidence_aliases),
            "A",
            evidence_aliases=evidence_aliases,
        ).sections,
    )
    blocks = _staged_tool_blocks(flat, "B", evidence_aliases)
    target = _tool_block(blocks, "submit_change_candidates").input[
        "changeCandidates"
    ][0]
    target.update({
        "targetType": "EvidenceEntity",
        "targetEntityType": "weaponFamily",
        "targetEntityKey": "mortar2.basic",
        "targetMetricRef": 0,
        "conceptualTargetDescription": "관측 비율은 20%다.",
        "structuredTargetRequiresGameDesignContext": requires_context,
    })

    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=evidence_aliases,
        metric_aliases=build_metric_alias_table(),
    )
    canonical_target = collected.sections["changeCandidates"][0]["target"]
    assert canonical_target == {
        "targetType": "EvidenceEntity",
        "domain": "stageDifficulty",
        "entityType": "weaponFamily",
        "entityKey": "mortar2.basic",
        "metricFamily": None,
        "metric": None,
        "description": None,
        "requiresGameDesignContext": requires_context,
    }
    validate_stage_b(package, collected.sections, stage_a)


@pytest.mark.parametrize(
    ("description", "issue_code"),
    [
        ("관측 비율은 20%다.", "FREEFORM_NUMERIC_CLAIM"),
        ("This caused the observed outcome.", "UNSUPPORTED_CAUSAL_LANGUAGE"),
        ("runId value is inspected.", "FORBIDDEN_PRIVATE_TEXT"),
    ],
)
def test_conceptual_description_remains_model_owned_and_strictly_validated(
    tmp_path: Path,
    description: str,
    issue_code: str,
) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", evidence_aliases),
            "A",
            evidence_aliases=evidence_aliases,
        ).sections,
    )
    blocks = _staged_tool_blocks(flat, "B", evidence_aliases)
    target = _tool_block(blocks, "submit_change_candidates").input[
        "changeCandidates"
    ][0]
    target.update({
        "targetType": "Conceptual",
        "targetMetricRef": 0,
        "conceptualTargetDescription": description,
        # This provider-only structured flag is deliberately not Conceptual authority.
        "structuredTargetRequiresGameDesignContext": False,
    })
    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=evidence_aliases,
        metric_aliases=build_metric_alias_table(),
    )
    canonical_target = collected.sections["changeCandidates"][0]["target"]
    assert canonical_target["description"] == description
    assert canonical_target["requiresGameDesignContext"] is True
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_stage_b(package, collected.sections, stage_a)
    assert issue_code in {issue["code"] for issue in captured.value.issues}


def test_conceptual_description_valid_prose_is_preserved(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", evidence_aliases),
            "A",
            evidence_aliases=evidence_aliases,
        ).sections,
    )
    blocks = _staged_tool_blocks(flat, "B", evidence_aliases)
    target = _tool_block(blocks, "submit_change_candidates").input[
        "changeCandidates"
    ][0]
    description = "전투 흐름을 조정하는 설계 개념을 검토한다."
    target.update({
        "targetType": "Conceptual",
        "targetMetricRef": 0,
        "conceptualTargetDescription": description,
        "structuredTargetRequiresGameDesignContext": False,
    })
    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=evidence_aliases,
        metric_aliases=build_metric_alias_table(),
    )
    canonical_target = collected.sections["changeCandidates"][0]["target"]
    assert canonical_target["description"] == description
    assert canonical_target["requiresGameDesignContext"] is True
    validate_stage_b(package, collected.sections, stage_a)


def test_evidence_metric_zero_sentinel_is_rejected_by_canonical_validator(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    evidence_aliases = build_evidence_alias_table(package)
    flat = flatten_canonical_response(valid_response(package))
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", evidence_aliases),
            "A",
            evidence_aliases=evidence_aliases,
        ).sections,
    )
    blocks = _staged_tool_blocks(flat, "B", evidence_aliases)
    _tool_block(blocks, "submit_change_candidates").input[
        "changeCandidates"
    ][0]["targetMetricRef"] = 0
    collected = collect_anthropic_stage_tool_response(
        blocks,
        "B",
        evidence_aliases=evidence_aliases,
        metric_aliases=build_metric_alias_table(),
    )
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_stage_b(package, collected.sections, stage_a)
    assert any(
        issue["code"] == "UNKNOWN_METRIC" for issue in captured.value.issues
    )


def test_stage_c_prior_target_metric_uses_same_singleton_sentinel() -> None:
    aliases = build_metric_alias_table()
    prior = {
        "changeCandidates": [{
            "target": {
                "domain": "stageDifficulty",
                "metricFamily": None,
                "metric": None,
            },
            "expectedObservableDirections": [],
        }]
    }
    projected = project_canonical_prior_metric_aliases(prior, aliases)
    assert projected["changeCandidates"][0]["target"]["metricRef"] == 0
    assert "metricRefs" not in projected["changeCandidates"][0]["target"]
    assert restore_provider_prior_metric_aliases(projected, aliases) == prior


def test_stage_c_prior_context_omits_structured_description_but_preserves_context() -> None:
    aliases = build_metric_alias_table()
    prior = {
        "changeCandidates": [{
            "target": {
                "targetType": "EvidenceMetric",
                "domain": "stageDifficulty",
                "entityType": None,
                "entityKey": None,
                "metricFamily": "outcome",
                "metric": "clearRate",
                "description": None,
                "requiresGameDesignContext": True,
            },
            "expectedObservableDirections": [],
        }]
    }
    projected = project_canonical_prior_metric_aliases(prior, aliases)
    provider_target = projected["changeCandidates"][0]["target"]
    assert "description" not in provider_target
    assert provider_target["requiresGameDesignContext"] is True
    assert restore_provider_prior_metric_aliases(projected, aliases) == prior


def test_three_stage_context_keeps_evidence_authority_and_merges_exactly(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    package = _package_with_evidence_count(package, 106)
    canonical = valid_response(package)
    flat = flatten_canonical_response(canonical)
    aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)

    context_a = build_anthropic_stage_context(
        package, "A", evidence_aliases=aliases, warning_aliases=warning_aliases
    )
    assert len(context_a["evidence"]) == 106
    assert [item["evidenceRef"] for item in context_a["evidence"]] == list(range(1, 107))
    assert all("evidenceId" not in item for item in context_a["evidence"])
    assert "metricRegistry" not in context_a
    assert all(
        "warningRefs" in quality and "warningCodes" not in quality
        for quality in context_a["qualitySets"].values()
    )
    assert [item["warningRef"] for item in context_a["warningCatalog"]] == list(
        warning_aliases.provider_refs
    )
    collected_a = collect_anthropic_stage_tool_response(
        list(reversed(_staged_tool_blocks(flat, "A", aliases))),
        "A",
        evidence_aliases=aliases,
    )
    stage_a = validate_stage_a(package, collected_a.sections)

    context_b = build_anthropic_stage_context(
        package, "B", stage_a=stage_a, evidence_aliases=aliases,
        warning_aliases=warning_aliases,
    )
    assert len(context_b["evidence"]) == 106
    assert context_b["validatedPriorAnalysis"]["observations"][0]["evidenceRefs"] == [1]
    assert "evidenceIds" not in context_b["validatedPriorAnalysis"]["observations"][0]
    assert "hostAssessmentsById" in context_b["validatedPriorAnalysis"]
    assert "limitationWarningRefs" in context_b["validatedPriorAnalysis"]["interpretations"][0]
    assert "limitationWarningCodes" not in json.dumps(
        context_b["validatedPriorAnalysis"], sort_keys=True
    )
    collected_b = collect_anthropic_stage_tool_response(
        _staged_tool_blocks(flat, "B", aliases),
        "B",
        evidence_aliases=aliases,
    )
    stage_b = validate_stage_b(package, collected_b.sections, stage_a)

    context_c = build_anthropic_stage_context(
        package, "C", stage_a=stage_a, stage_b=stage_b,
        evidence_aliases=aliases, warning_aliases=warning_aliases,
    )
    assert set(context_c).isdisjoint({"evidence", "artifacts", "qualitySets"})
    assert context_c["validationPlanCatalog"] == []
    prior = context_c["validatedPriorAnalysis"]
    assert set(prior) == {
        "observations", "interpretations", "evidenceGaps", "hypotheses",
        "changeCandidates",
    }
    assert len(context_c["metricCatalog"]) == 94
    assert context_c["metricCatalog"][0]["metricRef"] == 1
    assert "::" in context_c["metricCatalog"][0]["key"]
    assert context_c["metricCatalog"] == sorted(
        context_c["metricCatalog"], key=lambda item: item["metricRef"]
    )

    assert "EV-" not in str(prior)
    assert "EvidenceIds" not in str(prior)
    assert "evidenceIds" not in str(prior)
    assert '"metricFamily"' not in json.dumps(prior, ensure_ascii=False)
    assert '"metric"' not in json.dumps(prior, ensure_ascii=False)
    assert "limitationWarningCodes" not in json.dumps(prior, sort_keys=True)
    assert "limitationWarningRefs" in json.dumps(prior, sort_keys=True)
    assert prior["observations"][0]["observationRef"] == 1
    assert prior["evidenceGaps"][0]["evidenceGapRef"] == 1
    assert prior["hypotheses"][0]["hypothesisRef"] == 1
    assert prior["changeCandidates"][0]["changeCandidateRef"] == 1
    assert "hostAssessment" in prior["hypotheses"][0]
    assert isinstance(
        prior["changeCandidates"][0]["target"]["metricRef"], int
    )
    assert "metricRefs" not in prior["changeCandidates"][0]["target"]
    assert all(
        isinstance(item["metricRef"], int)
        for item in prior["changeCandidates"][0]["expectedObservableDirections"]
    )

    plan_refs = ValidationPlanRefTable(())
    collected_c = collect_anthropic_stage_tool_response(
        _staged_tool_blocks(flat, "C", aliases),
        "C",
        validation_plan_refs=plan_refs,
    )
    stage_c = validate_stage_c(package, collected_c.sections, stage_a, stage_b)
    merged = merge_anthropic_stage_sections(
        package, stage_a, stage_b, stage_c.canonical_sections
    )
    assert merged == canonical
    validate_response(package, merged)


def test_stage_prompts_expose_canonical_limits_without_copying_full_schema(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    aliases = build_evidence_alias_table(package)
    system_a, _messages, _context = build_anthropic_stage_prompt_parts(
        package, "A", evidence_aliases=aliases
    )
    assert "at most 10 observations" in system_a
    assert "5 interpretations" in system_a
    assert "OBS-001" in system_a and "INT-001" in system_a and "GAP-001" in system_a
    assert "Do not put digits" in system_a
    assert (
        "An Evidence Gap may describe a possible causal relationship only as an unresolved"
        in system_a
    )
    assert "It must not state that relationship as an established fact" in system_a
    assert (
        "additional analysis or observation would distinguish alternative explanations"
        in system_a
    )
    assert "Keep three epistemic levels distinct" in system_a
    assert "Deaths were associated with shorter runs in this sample" in system_a
    assert "Weapon X caused more deaths" in system_a
    assert "For every Observation or Interpretation citing retentionEvidence" in system_a
    assert "Never use player-state labels" in system_a
    assert "even in a negated, qualified, or cautionary sentence" in system_a
    assert "Nobody uninstalled" in system_a
    assert "No users returned to the app" in system_a
    assert "The player never returned" in system_a
    assert "RightCensored means insufficient follow-up" in system_a
    assert "one-player historical-population limitation" in system_a
    assert "possible adverse consequences or uncertainties" not in system_a

    canonical = valid_response(package)
    flat = flatten_canonical_response(canonical)
    stage_a = validate_stage_a(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "A", aliases),
            "A",
            evidence_aliases=aliases,
        ).sections,
    )
    system_b, _messages, _context = build_anthropic_stage_prompt_parts(
        package, "B", stage_a=stage_a, evidence_aliases=aliases
    )
    assert "at most 5 hypotheses" in system_b
    assert "host assigns canonical hypothesis" in system_b
    assert "includeValidationPlan" in system_b
    assert "possible adverse consequences or uncertainties" in system_b
    assert "do not state a causal consequence as established or certain" in system_b
    assert "A causal possibility may appear only as an explicit, falsifiable hypothesis" in system_b
    assert "One hypothesis is that early difficulty contributes to abandonment" in system_b
    assert "R4 factual evidence remains unavailable as decision support" in system_b

    stage_b = validate_stage_b(
        package,
        collect_anthropic_stage_tool_response(
            _staged_tool_blocks(flat, "B", aliases),
            "B",
            evidence_aliases=aliases,
        ).sections,
        stage_a,
    )
    system_c, _messages, _context = build_anthropic_stage_prompt_parts(
        package, "C", stage_a=stage_a, stage_b=stage_b,
        evidence_aliases=aliases,
    )
    assert "at most 5 validation plans" in system_c
    assert "at most 3" in system_c
    assert "prospectively test a causal hypothesis" in system_c
    assert "does not make the existing observational evidence causal" in system_c
    assert "Do not create a retention, churn, uninstall, or permanent-loss target" in system_c
    assert "possible adverse consequences or uncertainties" not in system_c


def test_stage_collectors_are_exact_once_and_partial_validation_is_authoritative(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    canonical = valid_response(package)
    flat = flatten_canonical_response(canonical)
    aliases = build_evidence_alias_table(package)
    stage_a_blocks = _staged_tool_blocks(flat, "A", aliases)
    collected = collect_anthropic_stage_tool_response(
        [SimpleNamespace(type="text", text="ignored"), *stage_a_blocks],
        "A",
        evidence_aliases=aliases,
    )
    assert collected.observed_tool_count == len(ANTHROPIC_STAGE_SPECS["A"])
    assert collected.ignored_text_block_count == 1

    with pytest.raises(AnthropicRequiredToolMissingError):
        collect_anthropic_stage_tool_response(
            stage_a_blocks[:-1], "A", evidence_aliases=aliases
        )
    with pytest.raises(AnthropicDuplicateToolUseError):
        collect_anthropic_stage_tool_response(
            [*stage_a_blocks, stage_a_blocks[0]], "A"
            , evidence_aliases=aliases
        )

    invalid_a = deepcopy(collected.sections)
    invalid_a["observations"] = [
        deepcopy(invalid_a["observations"][0]) for _ in range(11)
    ]
    for index, item in enumerate(invalid_a["observations"], start=1):
        item["id"] = f"OBS-{index:03d}"
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_stage_a(package, invalid_a)
    assert captured.value.issues[0]["path"] == "$.observations"

    stage_a = validate_stage_a(package, collected.sections)
    stage_b_sections = collect_anthropic_stage_tool_response(
        _staged_tool_blocks(flat, "B", aliases),
        "B",
        evidence_aliases=aliases,
    ).sections
    invalid_b = deepcopy(stage_b_sections)
    invalid_b["hypotheses"][0]["evidenceGapIds"] = ["GAP-999"]
    with pytest.raises(AnalysisResponseValidationError) as captured:
        validate_stage_b(package, invalid_b, stage_a)
    assert any(
        issue["code"] == "UNKNOWN_OUTPUT_REFERENCE"
        for issue in captured.value.issues
    )
