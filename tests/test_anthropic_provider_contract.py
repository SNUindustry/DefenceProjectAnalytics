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
    build_anthropic_output_schema,
    canonical_anthropic_body_schema,
    structured_output_config,
)
from defence_project_analytics.llm_analysis.validator import validate_response
from llm_analysis_fixtures import make_package, valid_response


BODY_FIELDS = (
    "executiveSummary",
    "observations",
    "interpretations",
    "hypotheses",
    "evidenceGaps",
    "changeCandidates",
    "validationPlans",
)


def _body(response: dict) -> dict:
    return {field: deepcopy(response[field]) for field in BODY_FIELDS}


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
    return dict(structured_output_config(package)["format"]["schema"])


def _canonical_schema(package) -> dict:
    return dict(canonical_anthropic_body_schema(package))


def _provider_for_body(body: dict) -> AnthropicAnalysisProvider:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(body))],
        usage=SimpleNamespace(input_tokens=101, output_tokens=23),
    )

    class Messages:
        def count_tokens(self, **_kwargs):
            return SimpleNamespace(input_tokens=99)

        def create(self, **_kwargs):
            return response

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
    Draft202012Validator(canonical).validate(_body(response))
    Draft202012Validator(wire).validate(_body(response))

    projected = _provider_for_body(_body(response)).generate(package)
    validated = validate_response(package, projected)
    assert len(validated.observations) == 10
    assert len(validated.executive_summary.observation_ids) == 3
    assert len(validated.validation_plans[0].rollback_indicators) == 1
    assert projected["sourceBriefIdentity"] == response["sourceBriefIdentity"]


def test_provider_schema_rejects_eleven_observations(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    body = _body(_boundary_response(package))
    body["observations"].append(deepcopy(body["observations"][0]))
    with pytest.raises(ValidationError):
        Draft202012Validator(_canonical_schema(package)).validate(body)
    Draft202012Validator(_wire_schema(package)).validate(body)
    projected = _provider_for_body(body).generate(package)
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, projected)


def test_provider_schema_rejects_four_executive_ids(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    body = _body(_boundary_response(package))
    body["executiveSummary"]["observationIds"].append("OBS-004")
    with pytest.raises(ValidationError):
        Draft202012Validator(_canonical_schema(package)).validate(body)
    Draft202012Validator(_wire_schema(package)).validate(body)
    projected = _provider_for_body(body).generate(package)
    with pytest.raises(AnalysisResponseValidationError):
        validate_response(package, projected)


@pytest.mark.parametrize(
    "invalid_rollback",
    [
        "UnexpectedDirection",
        {"metric": {"domain": "stageDifficulty"}, "condition": "UnexpectedDirection"},
        {"metric": {"domain": "stageDifficulty", "metricFamily": "outcome", "metric": "clearRate"}},
    ],
)
def test_provider_schema_rejects_invalid_rollback_shape(
    tmp_path: Path, invalid_rollback: object,
) -> None:
    _, package = make_package(tmp_path)
    body = _body(_boundary_response(package))
    body["validationPlans"][0]["rollbackIndicators"] = [invalid_rollback]
    with pytest.raises(ValidationError):
        Draft202012Validator(_wire_schema(package)).validate(body)


def test_source_identity_is_not_model_generated_and_projection_is_canonical(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    body = _body(valid_response(package))
    schema = _wire_schema(package)
    assert "sourceBriefIdentity" not in schema["properties"]
    tampered = dict(body)
    tampered["sourceBriefIdentity"] = {"scopeHash": "tampered"}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(tampered)

    projected = _provider_for_body(body).generate(package)
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
    assert "maxItems" not in wire["properties"]["observations"]
    executive = canonical["properties"]["executiveSummary"]
    assert executive["required"] == list(executive["properties"])
    for field in (
        "observationIds", "hypothesisIds", "evidenceGapIds", "changeCandidateIds"
    ):
        assert executive["properties"][field]["maxItems"] == 3
        assert "maxItems" not in wire["properties"]["executiveSummary"][
            "properties"
        ][field]
    rollback = canonical["properties"]["validationPlans"]["items"]["properties"][
        "rollbackIndicators"
    ]
    assert rollback["type"] == "array"
    assert rollback["maxItems"] == 10
    assert rollback["items"]["required"] == ["metric", "condition"]
    assert rollback["items"]["properties"]["metric"]["required"] == [
        "domain", "metricFamily", "metric"
    ]
    wire_rollback = wire["properties"]["validationPlans"]["items"]["properties"][
        "rollbackIndicators"
    ]
    assert "maxItems" not in wire_rollback
    assert wire_rollback["items"]["required"] == ["metric", "condition"]


def test_wire_cardinality_overflow_still_fails_closed_without_artifact(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    body = _body(_boundary_response(package))
    body["observations"].append(deepcopy(body["observations"][0]))
    provider = _provider_for_body(body)
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
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 99}, request=request)
        return httpx.Response(200, json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps(body)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 101, "output_tokens": 23},
        }, request=request)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret-marker")
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    sdk_client = anthropic.Anthropic(max_retries=0, http_client=http_client)
    provider = AnthropicAnalysisProvider(client=sdk_client, sleep=lambda _: None)
    projected = provider.generate(package)
    assert projected["sourceBriefIdentity"]["scopeHash"] == package.source.identity.scope_hash
    assert len(captured) == 2
    count_schema = captured[0]["output_config"]["format"]["schema"]
    generation_schema = captured[1]["output_config"]["format"]["schema"]
    assert count_schema == generation_schema
    assert "maxItems" not in count_schema["properties"]["observations"]
    for field in (
        "observationIds", "hypothesisIds", "evidenceGapIds", "changeCandidateIds"
    ):
        assert "maxItems" not in count_schema["properties"]["executiveSummary"][
            "properties"
        ][field]
    rollback = count_schema["properties"]["validationPlans"]["items"][
        "properties"
    ]["rollbackIndicators"]
    assert rollback["items"]["additionalProperties"] is False
    assert rollback["items"]["required"] == ["metric", "condition"]


def test_wire_projection_matches_installed_sdk_supported_subset(
    tmp_path: Path,
) -> None:
    from anthropic.lib._parse._transform import transform_schema

    _, package = make_package(tmp_path)
    canonical = _canonical_schema(package)
    wire = build_anthropic_output_schema(canonical)
    sdk_wire = transform_schema(canonical)

    def count_key(value: object, key: str) -> int:
        if isinstance(value, dict):
            return (1 if key in value else 0) + sum(
                count_key(item, key) for item in value.values()
            )
        if isinstance(value, list):
            return sum(count_key(item, key) for item in value)
        return 0

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
    assert canonical == {
        "objects": 15,
        "arrays": 32,
        "anyOf": 10,
        "nullable": 10,
        "enums": 13,
        "requiredFields": 88,
        "maxItems": 32,
        "additionalFalse": 15,
        "refs": 0,
        "maxDepth": 11,
    }
    assert wire == {**canonical, "maxItems": 0}
