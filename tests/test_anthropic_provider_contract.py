from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import anthropic
import httpx
from jsonschema import Draft202012Validator, ValidationError

from defence_project_analytics.llm_analysis import (
    AnthropicAnalysisProvider,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.errors import (
    AnalysisResponseValidationError,
)
from defence_project_analytics.llm_analysis.provider_schema import (
    ANTHROPIC_STAGE_SPECS,
    anthropic_flat_wire_schema,
    anthropic_reference_stage_strict_tools,
    anthropic_stage_strict_tools,
    anthropic_strict_tools_profile,
    build_anthropic_output_schema,
    canonical_anthropic_body_schema,
)
from defence_project_analytics.llm_analysis.anthropic_transport import (
    build_evidence_alias_table,
    build_warning_alias_table,
    OutputRefTable,
    StageOutputRefTables,
    ValidationPlanRefTable,
    flatten_canonical_response,
    split_anthropic_stage_tool_inputs,
)
from defence_project_analytics.llm_analysis.validator import validate_response
from llm_analysis_fixtures import make_package, valid_response


CANONICAL_BODY_FIELDS = (
    "executiveSummary",
    "observations",
    "interpretations",
    "hypotheses",
    "evidenceGaps",
    "changeCandidates",
    "validationPlans",
)


def _body(response: dict) -> dict:
    return dict(flatten_canonical_response(response))


def _canonical_body(response: dict) -> dict:
    return {field: deepcopy(response[field]) for field in CANONICAL_BODY_FIELDS}


def _output_refs(body: dict) -> StageOutputRefTables:
    return StageOutputRefTables(
        observations=OutputRefTable(
            "observation",
            tuple(
                f"OBS-{index:03d}"
                for index in range(1, len(body["observations"]) + 1)
            ),
        ),
        evidence_gaps=OutputRefTable(
            "gap", tuple(item["id"] for item in body["evidenceGaps"])
        ),
        hypotheses=OutputRefTable(
            "hypothesis", tuple(item["id"] for item in body["hypotheses"])
        ),
        change_candidates=OutputRefTable(
            "change", tuple(item["id"] for item in body["changeCandidates"])
        ),
    )


def _boundary_response(package) -> dict:
    response = valid_response(package)
    base_observation = response["observations"][0]
    response["observations"] = []
    for index in range(1, 11):
        item = deepcopy(base_observation)
        item["id"] = f"OBS-{index:03d}"
        response["observations"].append(item)

    base_gap = response["evidenceGaps"][0]
    response["evidenceGaps"] = []
    for index in range(1, 4):
        item = deepcopy(base_gap)
        item["id"] = f"GAP-{index:03d}"
        response["evidenceGaps"].append(item)

    base_hypothesis = response["hypotheses"][0]
    response["hypotheses"] = []
    for index in range(1, 4):
        item = deepcopy(base_hypothesis)
        item["id"] = f"HYP-{index:03d}"
        item["evidenceGapIds"] = [f"GAP-{index:03d}"]
        response["hypotheses"].append(item)

    base_change = response["changeCandidates"][0]
    response["changeCandidates"] = []
    for index in range(1, 4):
        item = deepcopy(base_change)
        item["id"] = f"CHG-{index:03d}"
        response["changeCandidates"].append(item)

    first_change = response["changeCandidates"][0]
    first_change["actionType"] = "Experiment"
    first_change["validationPlanId"] = "VAL-001"
    target = first_change["target"]
    metric = {
        "domain": target["domain"],
        "metricFamily": target["metricFamily"],
        "metric": target["metric"],
    }
    response["validationPlans"] = [{
        "id": "VAL-001",
        "changeCandidateId": "CHG-001",
        "analysesToRerun": [target["domain"]],
        "metricsToWatch": [metric],
        "guardrailMetrics": [],
        "minimumEvidenceRequirements": ["ClearExistingLowSampleWarning"],
        "comparisonPlan": "NewContentVersionVsCurrentUsingContentVersionCompare",
        "rollbackIndicators": [{
            "metric": metric,
            "condition": "UnexpectedDirection",
        }],
    }]
    response["executiveSummary"]["observationIds"] = [
        "OBS-001", "OBS-002", "OBS-003"
    ]
    response["executiveSummary"]["hypothesisIds"] = [
        "HYP-001", "HYP-002", "HYP-003"
    ]
    response["executiveSummary"]["evidenceGapIds"] = [
        "GAP-001", "GAP-002", "GAP-003"
    ]
    response["executiveSummary"]["changeCandidateIds"] = [
        "CHG-001", "CHG-002", "CHG-003"
    ]
    return response


def _wire_schema(package) -> dict:
    del package
    return dict(anthropic_flat_wire_schema())


def _canonical_schema(package) -> dict:
    return dict(canonical_anthropic_body_schema(package))


def _provider_for_body(body: dict, package) -> AnthropicAnalysisProvider:
    aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)
    validation_plan_refs = ValidationPlanRefTable(tuple(
        (item["id"], item["changeCandidateId"])
        for item in body["validationPlans"]
    ))
    output_refs = _output_refs(body)

    class Messages:
        def count_tokens(self, **_kwargs):
            return SimpleNamespace(input_tokens=99)

        def create(self, **kwargs):
            tool_names = tuple(tool["name"] for tool in kwargs["tools"])
            stage = next(
                stage for stage, specification in ANTHROPIC_STAGE_SPECS.items()
                if tool_names == tuple(name for name, _fields in specification)
            )
            fields = {
                field
                for tool in anthropic_stage_strict_tools(
                    stage,
                    evidence_refs=(
                        aliases.provider_refs if stage in {"A", "B"} else None
                    ),
                    warning_refs=(
                        warning_aliases.provider_refs if stage in {"A", "B"} else None
                    ),
                    validation_plan_refs=(
                        validation_plan_refs.provider_refs if stage == "C" else None
                    ),
                    evidence_gap_refs=output_refs.evidence_gaps.provider_refs,
                    observation_refs=output_refs.observations.provider_refs,
                    hypothesis_refs=output_refs.hypotheses.provider_refs,
                    change_candidate_refs=output_refs.change_candidates.provider_refs,
                )
                for field in tool["input_schema"]["properties"]
            }
            canonical_fields = {
                field for _name, values in ANTHROPIC_STAGE_SPECS[stage]
                for field in values
            }
            staged_body = {field: body[field] for field in canonical_fields}
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id=f"tool-{index}",
                        name=name,
                        input=value,
                    )
                    for index, (name, value) in enumerate(
                        split_anthropic_stage_tool_inputs(
                            staged_body,
                            stage,
                            evidence_aliases=(
                                aliases if stage in {"A", "B"} else None
                            ),
                            warning_aliases=(
                                warning_aliases if stage in {"A", "B"} else None
                            ),
                            validation_plan_refs=(
                                validation_plan_refs if stage == "C" else None
                            ),
                            output_refs=output_refs,
                        ), start=1
                    )
                ],
                usage=SimpleNamespace(input_tokens=101, output_tokens=23),
            )

    return AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=Messages()), sleep=lambda _: None
    )


def test_valid_structural_boundaries_project_and_pass_local_validator(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    response = _boundary_response(package)
    canonical = _canonical_schema(package)
    wire = _wire_schema(package)
    Draft202012Validator.check_schema(canonical)
    Draft202012Validator.check_schema(wire)
    Draft202012Validator(canonical).validate(_canonical_body(response))
    Draft202012Validator(wire).validate(_body(response))

    projected = _provider_for_body(_body(response), package).generate(package)
    validated = validate_response(package, projected)
    assert len(validated.observations) == 10
    assert len(validated.executive_summary.observation_ids) == 3
    assert len(validated.validation_plans[0].rollback_indicators) == 1
    assert projected["sourceBriefIdentity"] == response["sourceBriefIdentity"]


def test_provider_schema_rejects_eleven_observations(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = _boundary_response(package)
    response["observations"].append(deepcopy(response["observations"][0]))
    with pytest.raises(ValidationError):
        Draft202012Validator(_canonical_schema(package)).validate(
            _canonical_body(response)
        )
    body = _body(response)
    Draft202012Validator(_wire_schema(package)).validate(body)
    with pytest.raises(AnalysisResponseValidationError):
        _provider_for_body(body, package).generate(package)


def test_provider_schema_rejects_four_executive_ids(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = _boundary_response(package)
    response["executiveSummary"]["observationIds"].append("OBS-004")
    with pytest.raises(ValidationError):
        Draft202012Validator(_canonical_schema(package)).validate(
            _canonical_body(response)
        )
    body = _body(response)
    Draft202012Validator(_wire_schema(package)).validate(body)
    with pytest.raises(AnalysisResponseValidationError):
        _provider_for_body(body, package).generate(package)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("rollbackMetricRefs", [7]),
        ("rollbackConditions", [7]),
    ],
)
def test_provider_schema_rejects_invalid_rollback_shape(
    tmp_path: Path, field: str, invalid_value: object,
) -> None:
    _, package = make_package(tmp_path)
    response = _boundary_response(package)
    flat = flatten_canonical_response(response)
    flat["validationPlans"][0][field] = invalid_value
    # The same flat section schema is shared by production strict tools.
    with pytest.raises(ValidationError):
        Draft202012Validator(anthropic_flat_wire_schema()).validate(flat)


def test_source_identity_is_not_model_generated_and_projection_is_canonical(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    body = _body(valid_response(package))
    aliases = build_evidence_alias_table(package)
    schema = _wire_schema(package)
    assert "sourceBriefIdentity" not in schema["properties"]
    tampered = dict(body)
    tampered["sourceBriefIdentity"] = {"scopeHash": "tampered"}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(tampered)

    projected = _provider_for_body(body, package).generate(package)
    identity = package.source.identity
    assert projected["sourceBriefIdentity"] == {
        "semanticOutputDigest": identity.semantic_output_digest,
        "scopeHash": identity.scope_hash,
        "mode": identity.mode,
        "baselineContentVersion": identity.baseline_content_version,
        "candidateContentVersion": identity.candidate_content_version,
    }


def test_final_schema_retains_required_limits_and_rollback_shape(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    canonical = _canonical_schema(package)
    wire = _wire_schema(package)
    assert wire["additionalProperties"] is False
    assert canonical["properties"]["observations"]["maxItems"] == 10
    assert wire["properties"]["observations"]["type"] == "array"
    executive = canonical["properties"]["executiveSummary"]
    assert executive["required"] == list(executive["properties"])
    for field in (
        "observationIds", "hypothesisIds", "evidenceGapIds", "changeCandidateIds"
    ):
        assert executive["properties"][field]["maxItems"] == 3
    executive_overview = wire["properties"]["executiveQualitativeOverview"]
    assert executive_overview["type"] == "string"
    assert executive_overview["pattern"] == "^[^0-9]*$"
    assert "Numeric-free qualitative prose" in executive_overview["description"]
    rollback = canonical["properties"]["validationPlans"]["items"]["properties"][
        "rollbackIndicators"
    ]
    assert rollback["type"] == "array"
    assert rollback["maxItems"] == 10
    assert rollback["items"]["required"] == ["metric", "condition"]
    assert rollback["items"]["properties"]["metric"]["required"] == [
        "domain", "metricFamily", "metric"
    ]
    assert wire["properties"]["validationPlans"]["type"] == "array"


def test_wire_cardinality_overflow_still_fails_closed_without_artifact(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    response = _boundary_response(package)
    response["observations"].append(deepcopy(response["observations"][0]))
    body = _body(response)
    provider = _provider_for_body(body, package)
    with pytest.raises(AnalysisResponseValidationError):
        run_analysis_with_provider(
            package.request,
            provider,
            output_root=tmp_path / "out",
            workspace_root=tmp_path,
        )
    assert not (tmp_path / "out" / "llm-analysis").exists()
    assert provider.last_run_metadata["providerGenerationCallCount"] == 1


def test_official_sdk_wire_keeps_identical_count_and_generation_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = make_package(tmp_path)
    body = _body(valid_response(package))
    aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)
    validation_plan_refs = ValidationPlanRefTable(tuple(
        (item["id"], item["changeCandidateId"])
        for item in body["validationPlans"]
    ))
    output_refs = _output_refs(body)
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 99}, request=request)
        tool_names = tuple(tool["name"] for tool in payload["tools"])
        stage = next(
            stage for stage, specification in ANTHROPIC_STAGE_SPECS.items()
            if tool_names == tuple(name for name, _fields in specification)
        )
        fields = {
            field
            for tool in anthropic_stage_strict_tools(
                stage,
                evidence_refs=(
                    aliases.provider_refs if stage in {"A", "B"} else None
                ),
                warning_refs=(
                    warning_aliases.provider_refs if stage in {"A", "B"} else None
                ),
                validation_plan_refs=(
                    validation_plan_refs.provider_refs if stage == "C" else None
                ),
                evidence_gap_refs=output_refs.evidence_gaps.provider_refs,
                observation_refs=output_refs.observations.provider_refs,
                hypothesis_refs=output_refs.hypotheses.provider_refs,
                change_candidate_refs=output_refs.change_candidates.provider_refs,
            )
            for field in tool["input_schema"]["properties"]
        }
        canonical_fields = {
            field for _name, values in ANTHROPIC_STAGE_SPECS[stage]
            for field in values
        }
        staged_body = {field: body[field] for field in canonical_fields}
        tool_content = [
            {"type": "tool_use", "id": f"tool-{index}", "name": name, "input": value}
            for index, (name, value) in enumerate(
                split_anthropic_stage_tool_inputs(
                    staged_body,
                    stage,
                    evidence_aliases=aliases if stage in {"A", "B"} else None,
                    warning_aliases=warning_aliases if stage in {"A", "B"} else None,
                    validation_plan_refs=(
                        validation_plan_refs if stage == "C" else None
                    ),
                    output_refs=output_refs,
                ), start=1
            )
        ]
        return httpx.Response(200, json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": tool_content,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 101, "output_tokens": 23},
        }, request=request)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret-marker")
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk_client = anthropic.Anthropic(max_retries=0, http_client=http_client)
    provider = AnthropicAnalysisProvider(client=sdk_client, sleep=lambda _: None)
    projected = provider.generate(package)
    assert projected["sourceBriefIdentity"]["scopeHash"] == package.source.identity.scope_hash
    assert len(captured) == 6
    canonical_ids = tuple(package.source.evidence_by_id)
    for request_body in captured:
        encoded = json.dumps(request_body, sort_keys=True)
        assert all(evidence_id not in encoded for evidence_id in canonical_ids)
    for stage_index, stage in enumerate(("A", "B", "C")):
        counted = captured[stage_index * 2]
        generated = captured[stage_index * 2 + 1]
        assert counted["tools"] == generated["tools"]
        assert counted["tool_choice"] == generated["tool_choice"] == {
            "type": "any", "disable_parallel_tool_use": False,
        }
        assert "output_config" not in counted
        assert "output_config" not in generated
        assert [tool["name"] for tool in counted["tools"]] == [
            name for name, _fields in ANTHROPIC_STAGE_SPECS[stage]
        ]
        if stage in {"A", "B"}:
            assert '"evidenceRef"' in counted["messages"][0]["content"]
            assert '"evidenceId"' not in counted["messages"][0]["content"]


def test_wire_projection_matches_installed_sdk_supported_subset(
    tmp_path: Path,
) -> None:
    from anthropic.lib._parse._transform import transform_schema

    _, package = make_package(tmp_path)
    aliases = build_evidence_alias_table(package)
    warning_aliases = build_warning_alias_table(package)
    schemas = [
        tool["input_schema"]
        for stage in ("A", "B", "C")
            for tool in anthropic_stage_strict_tools(
                stage,
                evidence_refs=aliases.provider_refs if stage in {"A", "B"} else None,
                warning_refs=warning_aliases.provider_refs if stage in {"A", "B"} else None,
                validation_plan_refs=(1,) if stage == "C" else None,
        )
    ]

    def count_key(value: object, key: str) -> int:
        if isinstance(value, dict):
            return (1 if key in value else 0) + sum(
                count_key(item, key) for item in value.values()
            )
        if isinstance(value, list):
            return sum(count_key(item, key) for item in value)
        return 0

    for canonical in schemas:
        wire = build_anthropic_output_schema(canonical)
        sdk_wire = transform_schema(canonical)
        for unsupported in ("maxItems", "minLength", "maxLength"):
            assert count_key(wire, unsupported) == 0
            assert count_key(sdk_wire, unsupported) == 0
        for structural in (
            "properties", "required", "additionalProperties", "items", "anyOf", "enum"
        ):
            assert count_key(wire, structural) == count_key(sdk_wire, structural)


def test_canonical_and_wire_complexity_profile_is_stable(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)

    def profile(schema: object) -> dict[str, int]:
        result = {
            "objects": 0,
            "arrays": 0,
            "anyOf": 0,
            "nullable": 0,
            "enums": 0,
            "requiredFields": 0,
            "optionalProperties": 0,
            "maxItems": 0,
            "additionalFalse": 0,
            "refs": 0,
            "maxDepth": 0,
        }

        def walk(value: object, depth: int = 0) -> None:
            result["maxDepth"] = max(result["maxDepth"], depth)
            if isinstance(value, dict):
                result["objects"] += value.get("type") == "object"
                result["arrays"] += value.get("type") == "array"
                result["anyOf"] += "anyOf" in value
                if isinstance(value.get("anyOf"), list):
                    result["nullable"] += any(
                        isinstance(item, dict) and item.get("type") == "null"
                        for item in value["anyOf"]
                    )
                result["enums"] += "enum" in value
                required = value.get("required")
                result["requiredFields"] += len(required) if isinstance(required, list) else 0
                properties = value.get("properties")
                if isinstance(properties, dict):
                    result["optionalProperties"] += len(properties) - (
                        len(required) if isinstance(required, list) else 0
                    )
                result["maxItems"] += "maxItems" in value
                result["additionalFalse"] += value.get("additionalProperties") is False
                result["refs"] += "$ref" in value
                for item in value.values():
                    walk(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    walk(item, depth + 1)

        walk(schema)
        return result

    canonical = profile(_canonical_schema(package))
    wire = profile(_wire_schema(package))
    stage_profiles = {
            stage: anthropic_strict_tools_profile(anthropic_stage_strict_tools(
                stage,
                evidence_refs=tuple(range(1, 107)) if stage in {"A", "B"} else None,
                warning_refs=(1, 2, 3) if stage in {"A", "B"} else None,
                validation_plan_refs=(1, 2, 3, 4, 5) if stage == "C" else None,
            ))
        for stage in ("A", "B", "C")
    }
    assert canonical == {
        "objects": 15,
        "arrays": 32,
        "anyOf": 10,
        "nullable": 10,
        "enums": 13,
            "requiredFields": 88,
            "optionalProperties": 0,
        "maxItems": 32,
        "additionalFalse": 15,
        "refs": 0,
        "maxDepth": 11,
    }
    assert wire == {
        "objects": 7,
        "arrays": 34,
        "anyOf": 0,
        "nullable": 0,
        "enums": 10,
            "requiredFields": 73,
            "optionalProperties": 0,
        "maxItems": 8,
        "additionalFalse": 7,
        "refs": 0,
        "maxDepth": 8,
    }
    assert {
        stage: {
            key: profile["combined"][key]
            for key in (
                "objects",
                "arrays",
                "requiredProperties",
                "logicalDepth",
                "anyOf",
                "nullable",
                "optionalProperties",
                "evidenceRefFields",
                "evidenceRefEnumOccurrences",
                "evidenceRefEnumValues",
                "metricRefFields",
                "metricRefEnumOccurrences",
                    "metricRefEnumValues",
                    "warningRefFields",
                    "warningRefEnumOccurrences",
                    "warningRefEnumValues",
                    "schemaChars",
            )
        }
        for stage, profile in stage_profiles.items()
    } == {
        "A": {
            "objects": 6, "arrays": 8, "requiredProperties": 19, "logicalDepth": 8,
            "anyOf": 0, "nullable": 0, "optionalProperties": 0,
            "evidenceRefFields": 4, "evidenceRefEnumOccurrences": 4,
                "evidenceRefEnumValues": 424, "schemaChars": 4565,
                "metricRefFields": 0, "metricRefEnumOccurrences": 0,
                "metricRefEnumValues": 0,
                "warningRefFields": 1, "warningRefEnumOccurrences": 1,
                "warningRefEnumValues": 3,
        },
            "B": {
                "objects": 5, "arrays": 14, "requiredProperties": 35, "logicalDepth": 10,
            "anyOf": 0, "nullable": 0, "optionalProperties": 0,
            "evidenceRefFields": 4, "evidenceRefEnumOccurrences": 4,
                            "evidenceRefEnumValues": 424, "schemaChars": 7949,
                "metricRefFields": 2, "metricRefEnumOccurrences": 2,
                    "metricRefEnumValues": 177,
                "warningRefFields": 2, "warningRefEnumOccurrences": 2,
                "warningRefEnumValues": 6,
        },
        "C": {
            "objects": 4, "arrays": 10, "requiredProperties": 15, "logicalDepth": 10,
            "anyOf": 0, "nullable": 0, "optionalProperties": 0,
            "evidenceRefFields": 0, "evidenceRefEnumOccurrences": 0,
                    "evidenceRefEnumValues": 0,
                "metricRefFields": 3, "metricRefEnumOccurrences": 3,
                    "metricRefEnumValues": 264,
                "warningRefFields": 0, "warningRefEnumOccurrences": 0,
                    "warningRefEnumValues": 0,
                    "schemaChars": 3023,
        },
    }
    assert {
        key: stage_profiles["C"]["combined"][key]
        for key in (
            "validationPlanRefFields",
            "validationPlanRefEnumOccurrences",
            "validationPlanRefEnumValues",
            "minimumRequirementEnumOccurrences",
            "minimumRequirementEnumValues",
            "rollbackConditionEnumOccurrences",
            "rollbackConditionEnumValues",
        )
    } == {
        "validationPlanRefFields": 1,
        "validationPlanRefEnumOccurrences": 1,
        "validationPlanRefEnumValues": 5,
        "minimumRequirementEnumOccurrences": 1,
        "minimumRequirementEnumValues": 5,
        "rollbackConditionEnumOccurrences": 1,
        "rollbackConditionEnumValues": 3,
    }

    before = {
        stage: anthropic_strict_tools_profile(
            anthropic_reference_stage_strict_tools(stage)
        )["combined"]["schemaChars"]
        for stage in ("A", "B", "C")
    }
    assert before == {"A": 3003, "B": 5656, "C": 1720}
