from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from defence_project_analytics.llm_analysis import (
    AnthropicAnalysisProvider,
    AnthropicCredentialError,
    DEFAULT_ANTHROPIC_MODEL,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.errors import SourceBriefMutationError
from defence_project_analytics.llm_analysis.provider_errors import (
    AnthropicConnectionError,
    AnthropicContextTooLargeError,
    AnthropicAuthenticationError,
    AnthropicFlatReconstructionError,
    AnthropicEvidenceAliasError,
    AnthropicMalformedResponseError,
    AnthropicOutputRefError,
    AnthropicPermissionError,
    AnthropicRequestValidationError,
    AnthropicResponseTruncatedError,
    AnthropicServerError,
    AnthropicStructuredOutputUnsupportedError,
    AnthropicTimeoutError,
    AnthropicTokenCountError,
    AnthropicRequiredToolMissingError,
)
from defence_project_analytics.llm_analysis.providers.anthropic import map_anthropic_error
from defence_project_analytics.llm_analysis.provider_schema import (
    ANTHROPIC_STAGE_SPECS,
    analysis_response_json_schema,
    anthropic_stage_strict_tools,
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
from llm_analysis_fixtures import make_package, valid_response


def _stage_from_tools(tools: list[dict]) -> str:
    names = tuple(tool["name"] for tool in tools)
    for stage, specification in ANTHROPIC_STAGE_SPECS.items():
        expected = tuple(name for name, _fields in specification)
        if names == expected:
            return stage
    raise AssertionError(f"unexpected tool set: {names!r}")


def tool_blocks(payload: dict, stage: str, aliases, warning_aliases) -> list[SimpleNamespace]:
    flat = flatten_canonical_response(payload) if "analysisVersion" in payload else payload
    output_refs = StageOutputRefTables(
        observations=OutputRefTable(
            "observation", tuple(item["id"] for item in flat["observations"])
        ),
        evidence_gaps=OutputRefTable(
            "gap", tuple(item["id"] for item in flat["evidenceGaps"])
        ),
        hypotheses=OutputRefTable(
            "hypothesis", tuple(item["id"] for item in flat["hypotheses"])
        ),
        change_candidates=OutputRefTable(
            "change", tuple(item["id"] for item in flat["changeCandidates"])
        ),
    )
    validation_plan_refs = (
        ValidationPlanRefTable(tuple(
            (item["id"], item["changeCandidateId"])
            for item in flat["validationPlans"]
        ))
        if stage == "C" else None
    )
    fields = {
        field
        for tool in anthropic_stage_strict_tools(
            stage,
            evidence_refs=aliases.provider_refs if stage in {"A", "B"} else None,
            warning_refs=warning_aliases.provider_refs if stage in {"A", "B"} else None,
            validation_plan_refs=(
                validation_plan_refs.provider_refs if validation_plan_refs else None
            ),
            evidence_gap_refs=output_refs.evidence_gaps.provider_refs,
            observation_refs=output_refs.observations.provider_refs,
            hypothesis_refs=output_refs.hypotheses.provider_refs,
            change_candidate_refs=output_refs.change_candidates.provider_refs,
        )
        for field in tool["input_schema"]["properties"]
    }
    canonical_fields = {
        field for _name, values in ANTHROPIC_STAGE_SPECS[stage] for field in values
    }
    staged_flat = {field: flat[field] for field in canonical_fields}
    return [
        SimpleNamespace(type="tool_use", id=f"tool-{index}", name=name, input=value)
        for index, (name, value) in enumerate(
            split_anthropic_stage_tool_inputs(
                staged_flat,
                stage,
                evidence_aliases=aliases if stage in {"A", "B"} else None,
                warning_aliases=warning_aliases if stage in {"A", "B"} else None,
                validation_plan_refs=validation_plan_refs,
                output_refs=output_refs,
            ), start=1
        )
    ]


def message_response(
    payload: dict,
    stage: str,
    aliases,
    warning_aliases,
    *,
    stop_reason: str = "tool_use",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"msg-safe-{stage.lower()}",
        stop_reason=stop_reason,
        stop_details=None,
        content=tool_blocks(payload, stage, aliases, warning_aliases),
        usage=SimpleNamespace(input_tokens=101, output_tokens=23),
    )


class FakeMessages:
    def __init__(self, payload: dict, package):
        self.payload = payload
        self.aliases = build_evidence_alias_table(package)
        self.warning_aliases = build_warning_alias_table(package)
        self.count_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=99)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return message_response(
            self.payload, _stage_from_tools(kwargs["tools"]), self.aliases,
            self.warning_aliases,
        )


def fake_client(payload: dict, package) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(payload, package))


def test_anthropic_provider_uses_three_stage_strict_tools_and_token_preflight(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    client = fake_client(valid_response(package), package)
    provider = AnthropicAnalysisProvider(client=client, sleep=lambda _: None)
    response = provider.generate(package)
    assert response["analysisVersion"] == "1.0.0"
    assert provider.model_name == DEFAULT_ANTHROPIC_MODEL
    assert len(client.messages.count_calls) == 3
    assert len(client.messages.create_calls) == 3
    for counted, created, expected_count in zip(
        client.messages.count_calls,
        client.messages.create_calls,
        (3, 2, 2),
        strict=True,
    ):
        assert counted["model"] == DEFAULT_ANTHROPIC_MODEL
        assert counted["tools"] == created["tools"]
        assert counted["tool_choice"] == created["tool_choice"] == {
            "type": "any", "disable_parallel_tool_use": False,
        }
        assert "output_config" not in counted
        assert "output_config" not in created
        assert len(created["tools"]) == expected_count
        assert all(tool["strict"] is True for tool in created["tools"])
        assert created["max_tokens"] == 20_000
        assert "# UNTRUSTED_ANALYTICS_DATA" not in counted["system"]
        assert counted["messages"][0]["content"].startswith("# UNTRUSTED_ANALYTICS_DATA")
    assert provider.last_run_metadata["inputTokenCount"] == 297
    assert provider.last_run_metadata["actualInputTokens"] == 303
    assert provider.last_run_metadata["actualOutputTokens"] == 69
    assert provider.last_run_metadata["providerCallCount"] == 6
    assert response["sourceBriefIdentity"] == valid_response(package)[
        "sourceBriefIdentity"
    ]

    override = AnthropicAnalysisProvider(
        model_name="claude-opus-5-custom", client=client
    )
    assert override.model_name == "claude-opus-5-custom"


def test_provider_schema_and_local_contract_share_top_level_fields(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    schema = analysis_response_json_schema(package)
    response = valid_response(package)
    assert set(schema["properties"]) == set(response)
    assert set(schema["required"]) == set(response)
    assert schema["additionalProperties"] is False
    evidence_schema = schema["properties"]["observations"]["items"]["properties"][
        "evidenceIds"
    ]["items"]
    assert evidence_schema == {"type": "string"}


def test_anthropic_output_still_passes_existing_validator_and_records_usage(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["evidenceGaps"][0]["question"] = (
        "이 차이가 구성 차이 때문인지 확인할 추가 근거가 필요하다."
    )
    response["evidenceGaps"][0]["whyItMatters"] = (
        "어느 요인이 원인인지 현재 Evidence로 구분할 수 없다."
    )
    provider = AnthropicAnalysisProvider(
        model_name="claude-opus-5",
        client=fake_client(response, package),
        sleep=lambda _: None,
    )
    path = run_analysis_with_provider(
        package.request,
        provider,
        output_root=tmp_path / "generated",
        workspace_root=tmp_path,
    )
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["providerMode"] == "anthropicApi"
    assert manifest["cloudAccessPerformed"] is True
    assert manifest["providerName"] == "anthropic"
    assert manifest["modelName"] == "claude-opus-5"
    assert manifest["sourceBriefPortablePath"] is not None
    assert manifest["sourceBundles"]
    assert all(len(item["bundleDigest"]) == 64 for item in manifest["sourceBundles"])
    assert manifest["structuredOutputsUsed"] is True
    assert manifest["anthropicStrictToolsUsed"] is True
    assert manifest["structuredOutputSchemaMode"] == "threeStageStrictToolsV1"
    assert manifest["anthropicInputProjectionVersion"] == "1.2.0"
    assert manifest["anthropicEvidenceAliasVersion"] == "1.1.0"
    assert manifest["evidenceAliasCount"] == len(package.source.evidence_by_id)
    assert len(manifest["evidenceAliasDigest"]) == 64
    assert manifest["anthropicMetricAliasVersion"] == "1.0.0"
    assert manifest["metricAliasCount"] == 94
    assert len(manifest["metricAliasDigest"]) == 64
    assert manifest["anthropicStrictToolTransportVersion"] == "1.10.0"
    assert manifest["anthropicThreeStageStrictToolTransportVersion"] == "1.10.0"
    assert manifest["anthropicThreeStageStrictToolVersion"] == "1.10.0"
    assert manifest["anthropicStageContextVersion"] == "1.4.0"
    assert manifest["anthropicStageContextVersions"] == {
        "A": "1.4.0", "B": "1.6.0", "C": "1.7.0"
    }
    assert manifest["anthropicOutputRefVersion"] == "1.0.0"
    assert manifest["anthropicStageBIdentityVersion"] == "1.0.0"
    assert manifest["evidenceGapRefCount"] == 1
    assert len(manifest["evidenceGapRefDigest"]) == 64
    assert manifest["observationRefCount"] == 1
    assert len(manifest["observationRefDigest"]) == 64
    assert manifest["hypothesisRefCount"] == 1
    assert len(manifest["hypothesisRefDigest"]) == 64
    assert manifest["changeCandidateRefCount"] == 1
    assert len(manifest["changeCandidateRefDigest"]) == 64
    assert manifest["anthropicValidationPlanRefVersion"] == "1.0.0"
    assert manifest["validationPlanRefCount"] == 0
    assert len(manifest["validationPlanRefDigest"]) == 64
    assert manifest["anthropicWarningAliasVersion"] == "1.0.0"
    assert manifest["warningAliasCount"] == 3
    assert len(manifest["warningAliasDigest"]) == 64
    assert len(manifest["warningAuthorityDigest"]) == 64
    assert manifest["stageCount"] == 3
    assert len(manifest["stages"]) == 3
    assert [stage["stage"] for stage in manifest["stages"]] == ["A", "B", "C"]
    assert all(stage["expectedToolCount"] == stage["observedToolCount"] for stage in manifest["stages"])
    assert manifest["canonicalValidationPassed"] is True
    assert manifest["inputTokenCount"] == 297
    assert manifest["actualInputTokens"] == 303
    assert manifest["actualOutputTokens"] == 69
    assert manifest["providerTokenCountCallCount"] == 3
    assert manifest["providerGenerationCallCount"] == 3
    assert manifest["providerRetryCount"] == 0
    assert all(
        stage["canonicalEvidenceIdLeakageCheckPassed"] is True
        for stage in manifest["stages"]
    )
    assert all(
        stage["metricAliasOutputSchemaCheckPassed"] is True
        for stage in manifest["stages"]
    )
    assert all(
        stage["warningAliasOutputSchemaCheckPassed"] is True
        for stage in manifest["stages"]
    )
    analysis_json = (path / "analysis.json").read_text(encoding="utf-8")
    analysis_markdown = (path / "analysis.md").read_text(encoding="utf-8")
    manifest_text = (path / "manifest.json").read_text(encoding="utf-8")
    assert "evidenceRefs" not in analysis_json
    assert "evidenceRef" not in analysis_markdown
    assert "providerRefs" not in manifest_text
    assert "canonicalPairs" not in manifest_text
    assert "toolArguments" not in manifest_text
    assert "rawResponse" not in manifest_text
    assert next(iter(package.source.evidence_by_id)) in analysis_json
    assert not (path / "raw-response.json").exists()


def test_missing_process_credential_fails_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = make_package(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnthropicCredentialError, match="not visible"):
        AnthropicAnalysisProvider().generate(package)

    with pytest.raises(ValueError, match="non-streaming"):
        AnthropicAnalysisProvider(max_output_tokens=20_001)


def test_canonical_evidence_id_leak_blocks_preflight_and_generation(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    canonical_id = next(iter(package.source.evidence_by_id))
    package = replace(
        package,
        request=replace(package.request, analysis_objective=canonical_id),
    )
    client = fake_client(valid_response(package), package)
    provider = AnthropicAnalysisProvider(client=client, sleep=lambda _: None)

    with pytest.raises(AnthropicEvidenceAliasError):
        provider.generate(package)

    assert client.messages.count_calls == []
    assert client.messages.create_calls == []
    assert provider.last_run_metadata["providerTokenCountCallCount"] == 0
    assert provider.last_run_metadata["providerGenerationCallCount"] == 0


def test_secret_marker_is_redacted_from_provider_exception(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    marker = "sk-ant-test-secret-marker"
    AuthenticationError = type("AuthenticationError", (RuntimeError,), {})

    class FailingMessages:
        def count_tokens(self, **_kwargs):
            raise AuthenticationError(f"bad credential {marker}")

    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=FailingMessages()), sleep=lambda _: None
    )
    with pytest.raises(AnthropicAuthenticationError) as captured:
        provider.generate(package)
    assert marker not in str(captured.value)
    assert marker not in captured.value.code


def test_transient_rate_limit_has_one_bounded_retry(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    RateLimitError = type("RateLimitError", (RuntimeError,), {})

    class RetryMessages(FakeMessages):
        def count_tokens(self, **kwargs):
            self.count_calls.append(kwargs)
            if len(self.count_calls) == 1:
                raise RateLimitError("temporary")
            return SimpleNamespace(input_tokens=99)

    messages = RetryMessages(valid_response(package), package)
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages), sleep=lambda _: None
    )
    provider.generate(package)
    assert provider.last_run_metadata["providerRetryCount"] == 1
    assert provider.last_run_metadata["providerErrorCount"] == 1
    assert provider.last_run_metadata["providerCallCount"] == 7


def test_token_count_failure_is_distinct_and_never_generates(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)

    class CountFailureMessages:
        create_called = False

        def count_tokens(self, **_kwargs):
            raise RuntimeError("synthetic token count failure")

        def create(self, **_kwargs):
            self.create_called = True
            raise AssertionError("generation must not run after count failure")

    messages = CountFailureMessages()
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages),
        max_transport_retries=0,
        sleep=lambda _: None,
    )
    with pytest.raises(AnthropicTokenCountError):
        provider.generate(package)
    assert messages.create_called is False
    assert provider.last_run_metadata["providerCallCount"] == 1
    assert provider.last_run_metadata["providerTokenCountCallCount"] == 1
    assert provider.last_run_metadata["providerGenerationCallCount"] == 0
    assert provider.last_run_metadata["providerErrorCount"] == 1


def test_preflight_token_gate_blocks_generation_at_60000(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)

    class OversizedMessages:
        create_called = False

        def count_tokens(self, **_kwargs):
            return SimpleNamespace(input_tokens=60_000)

        def create(self, **_kwargs):
            self.create_called = True
            raise AssertionError("generation must not run above the C-2A gate")

    messages = OversizedMessages()
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages), sleep=lambda _: None
    )
    with pytest.raises(AnthropicContextTooLargeError, match="60000-token"):
        provider.generate(package)
    assert messages.create_called is False
    assert provider.last_run_metadata["inputTokenCount"] == 60_000
    assert provider.last_run_metadata["providerGenerationCallCount"] == 0


def test_schema_mutation_after_preflight_blocks_generation(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)

    class MutatingMessages:
        create_called = False

        def count_tokens(self, **kwargs):
            kwargs["tools"][0]["input_schema"]["properties"].pop("observations")
            return SimpleNamespace(input_tokens=99)

        def create(self, **_kwargs):
            self.create_called = True
            raise AssertionError("generation must not run with a changed schema")

    messages = MutatingMessages()
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages), sleep=lambda _: None
    )
    with pytest.raises(AnthropicRequestValidationError, match="requests differ"):
        provider.generate(package)
    assert messages.create_called is False
    assert provider.last_run_metadata["providerGenerationCallCount"] == 0


def test_generation_transport_failure_is_never_retried(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    APIConnectionError = type("APIConnectionError", (RuntimeError,), {})

    class GenerationFailureMessages:
        create_calls = 0

        def count_tokens(self, **_kwargs):
            return SimpleNamespace(input_tokens=99)

        def create(self, **_kwargs):
            self.create_calls += 1
            raise APIConnectionError("temporary")

    messages = GenerationFailureMessages()
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages), sleep=lambda _: None
    )
    with pytest.raises(AnthropicConnectionError):
        provider.generate(package)
    assert messages.create_calls == 1
    assert provider.last_run_metadata["providerGenerationCallCount"] == 1
    assert provider.last_run_metadata["providerRetryCount"] == 0


def test_flat_reconstruction_failure_preserves_usage_without_artifact(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    flat = dict(flatten_canonical_response(valid_response(package)))
    flat["changeCandidates"][0]["changeAmountPercentPresent"] = False
    flat["changeCandidates"][0]["changeAmountPercent"] = 10
    provider = AnthropicAnalysisProvider(
        client=fake_client(flat, package), sleep=lambda _: None
    )
    with pytest.raises(AnthropicFlatReconstructionError) as captured:
        run_analysis_with_provider(
            package.request,
            provider,
            output_root=tmp_path / "generated",
            workspace_root=tmp_path,
        )
    assert captured.value.details["stage"] == "B"
    assert captured.value.details["processingBoundary"] == "reconstruction"
    assert captured.value.details["section"] == "changeCandidates"
    assert captured.value.details["itemIndex"] == 0
    assert captured.value.details["reconstructionComponent"] == "changeAmountPercent"
    assert captured.value.details["reconstructionInvariant"] == "INVALID_AMOUNT_SENTINEL_STATE"
    assert captured.value.details["safeRequestId"] == "msg-safe-b"
    assert provider.last_run_metadata["actualInputTokens"] == 202
    assert provider.last_run_metadata["actualOutputTokens"] == 46
    assert provider.last_run_metadata["providerGenerationCallCount"] == 2
    assert provider.last_run_metadata["providerRetryCount"] == 0
    assert not (tmp_path / "generated" / "llm-analysis").exists()


def test_unsupported_structured_output_and_truncation_are_explicit(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    BadRequestError = type("BadRequestError", (RuntimeError,), {"status_code": 400})

    class UnsupportedMessages:
        def count_tokens(self, **_kwargs):
            raise BadRequestError("model does not support structured output json_schema")

    with pytest.raises(AnthropicStructuredOutputUnsupportedError):
        AnthropicAnalysisProvider(
            client=SimpleNamespace(messages=UnsupportedMessages()), sleep=lambda _: None
        ).generate(package)

    client = fake_client(valid_response(package), package)
    client.messages.create = lambda **kwargs: message_response(
        valid_response(package),
        _stage_from_tools(kwargs["tools"]),
        client.messages.aliases,
        client.messages.warning_aliases,
        stop_reason="max_tokens",
    )
    with pytest.raises(AnthropicResponseTruncatedError):
        AnthropicAnalysisProvider(client=client, sleep=lambda _: None).generate(package)


@pytest.mark.parametrize(
    ("name", "status", "message", "expected"),
    [
        ("PermissionDeniedError", 403, "denied secret-marker", AnthropicPermissionError),
        ("APITimeoutError", None, "timeout secret-marker", AnthropicTimeoutError),
        ("APIConnectionError", None, "connection secret-marker", AnthropicConnectionError),
        ("InternalServerError", 500, "server secret-marker", AnthropicServerError),
        ("BadRequestError", 400, "context window exceeded secret-marker", AnthropicContextTooLargeError),
    ],
)
def test_official_sdk_error_classes_map_to_stable_safe_errors(
    name: str, status: int | None, message: str, expected: type[Exception],
) -> None:
    attributes = {} if status is None else {"status_code": status}
    error_type = type(name, (RuntimeError,), attributes)
    mapped = map_anthropic_error(error_type(message))
    assert isinstance(mapped, expected)
    assert "secret-marker" not in str(mapped)


def test_schema_complexity_error_exposes_only_sanitized_diagnostics() -> None:
    BadRequestError = type("BadRequestError", (RuntimeError,), {"status_code": 400})
    error = BadRequestError("raw secret-marker")
    error.body = {
        "error": {
            "type": "invalid_request_error",
            "message": "Schema is too complex for compilation: secret-marker",
        }
    }
    error.request_id = "req_safe_test"
    mapped = map_anthropic_error(error)
    assert isinstance(mapped, AnthropicRequestValidationError)
    assert mapped.details == {
        "httpStatus": 400,
        "anthropicErrorType": "invalid_request_error",
        "errorCategory": "schemaComplexity",
        "safeMessage": "The Structured Outputs schema is too complex for compilation.",
        "requestId": "req_safe_test",
    }
    assert "secret-marker" not in json.dumps(mapped.details)


def test_unsupported_schema_constraint_error_is_safely_classified() -> None:
    BadRequestError = type("BadRequestError", (RuntimeError,), {"status_code": 400})
    error = BadRequestError("raw provider message")
    error.body = {
        "error": {
            "type": "invalid_request_error",
            "message": (
                "tools.0.custom: For 'array' type, property 'maxItems' "
                "is not supported"
            ),
        }
    }
    error.request_id = "req_safe_constraint"
    mapped = map_anthropic_error(error)
    assert isinstance(mapped, AnthropicRequestValidationError)
    assert mapped.details == {
        "httpStatus": 400,
        "anthropicErrorType": "invalid_request_error",
        "errorCategory": "unsupportedSchemaConstraint",
        "safeMessage": (
            "The Structured Outputs schema contained an unsupported constraint."
        ),
        "requestId": "req_safe_constraint",
    }
    assert "maxItems" not in json.dumps(mapped.details)


def test_text_is_ignored_but_missing_tools_are_not_repaired(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    client = fake_client(valid_response(package), package)
    client.messages.create = lambda **_kwargs: SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="text", text="ignored provider prose")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    with pytest.raises(AnthropicRequiredToolMissingError):
        AnthropicAnalysisProvider(client=client, sleep=lambda _: None).generate(package)

    client = fake_client(valid_response(package), package)
    original = client.messages.create
    client.messages.create = lambda **kwargs: SimpleNamespace(
        **{
            **vars(original(**kwargs)),
            "content": [
                SimpleNamespace(type="text", text="ignored"),
                *tool_blocks(
                    valid_response(package),
                    _stage_from_tools(kwargs["tools"]),
                    client.messages.aliases,
                    client.messages.warning_aliases,
                ),
            ],
        }
    )
    provider = AnthropicAnalysisProvider(client=client, sleep=lambda _: None)
    provider.generate(package)
    assert provider.last_run_metadata["anthropicIgnoredTextBlockCount"] == 3


def test_anthropic_alias_inverse_rejects_unknown_provider_reference(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    client = fake_client(valid_response(package), package)
    original = client.messages.create

    def invalid_alias_response(**kwargs):
        response = original(**kwargs)
        if _stage_from_tools(kwargs["tools"]) == "A":
            observation_tool = next(
                block for block in response.content
                if block.name == "submit_observations"
            )
            observation_tool.input["observations"][0]["evidenceRefs"] = [999]
        return response

    client.messages.create = invalid_alias_response
    provider = AnthropicAnalysisProvider(client=client, sleep=lambda _: None)
    with pytest.raises(AnthropicFlatReconstructionError):
        run_analysis_with_provider(
            package.request,
            provider,
            output_root=tmp_path / "generated",
            workspace_root=tmp_path,
        )
    assert not (tmp_path / "generated" / "llm-analysis").exists()
    assert provider.last_run_metadata["providerTokenCountCallCount"] == 1
    assert provider.last_run_metadata["providerGenerationCallCount"] == 1
    assert len(provider.last_run_metadata["stages"]) == 1


def test_stage_b_validation_failure_short_circuits_stage_c(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["hypotheses"][0]["evidenceGapIds"] = ["GAP-999"]
    provider = AnthropicAnalysisProvider(
        client=fake_client(response, package), sleep=lambda _: None
    )
    with pytest.raises(AnthropicOutputRefError):
        provider.generate(package)
    assert provider.last_run_metadata["providerTokenCountCallCount"] == 2
    assert provider.last_run_metadata["providerGenerationCallCount"] == 2
    assert [stage["stage"] for stage in provider.last_run_metadata["stages"]] == [
        "A", "B",
    ]
    assert provider.last_run_metadata["stages"][1]["validationStatus"] == "failed"


def test_source_mutation_after_stage_a_blocks_all_later_calls(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)

    class MutatingMessages(FakeMessages):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if len(self.create_calls) == 1:
                brief_path = package.source.path / "brief.md"
                brief_path.write_text(
                    brief_path.read_text(encoding="utf-8") + "\nmutation\n",
                    encoding="utf-8",
                )
            return response

    messages = MutatingMessages(valid_response(package), package)
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages), sleep=lambda _: None
    )
    with pytest.raises(SourceBriefMutationError):
        provider.generate(package)
    assert len(messages.count_calls) == 1
    assert len(messages.create_calls) == 1
    assert provider.last_run_metadata["providerGenerationCallCount"] == 1
    assert len(provider.last_run_metadata["stages"]) == 1
