from __future__ import annotations

import json
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
from defence_project_analytics.llm_analysis.provider_errors import (
    AnthropicConnectionError,
    AnthropicContextTooLargeError,
    AnthropicAuthenticationError,
    AnthropicMalformedResponseError,
    AnthropicPermissionError,
    AnthropicRequestValidationError,
    AnthropicResponseTruncatedError,
    AnthropicServerError,
    AnthropicStructuredOutputUnsupportedError,
    AnthropicTimeoutError,
    AnthropicTokenCountError,
)
from defence_project_analytics.llm_analysis.providers.anthropic import map_anthropic_error
from defence_project_analytics.llm_analysis.provider_schema import (
    analysis_response_json_schema,
)
from llm_analysis_fixtures import make_package, valid_response


def message_response(payload: dict, *, stop_reason: str = "end_turn") -> SimpleNamespace:
    body_keys = (
        "executiveSummary",
        "observations",
        "interpretations",
        "hypotheses",
        "evidenceGaps",
        "changeCandidates",
        "validationPlans",
    )
    body = {key: payload[key] for key in body_keys}
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(
            type="text",
            text=json.dumps(body),
        )],
        usage=SimpleNamespace(input_tokens=101, output_tokens=23),
    )


class FakeMessages:
    def __init__(self, payload: dict):
        self.payload = payload
        self.count_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=99)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return message_response(self.payload)


def fake_client(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(payload))


def test_anthropic_provider_uses_native_structured_output_and_token_preflight(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    client = fake_client(valid_response(package))
    provider = AnthropicAnalysisProvider(client=client, sleep=lambda _: None)
    response = provider.generate(package)
    assert response["analysisVersion"] == "1.0.0"
    assert provider.model_name == DEFAULT_ANTHROPIC_MODEL
    assert len(client.messages.count_calls) == 1
    assert len(client.messages.create_calls) == 1
    counted = client.messages.count_calls[0]
    created = client.messages.create_calls[0]
    assert counted["model"] == DEFAULT_ANTHROPIC_MODEL
    assert counted["output_config"] == created["output_config"]
    assert created["output_config"]["format"]["type"] == "json_schema"
    assert set(created["output_config"]["format"]["schema"]["properties"]) == {
        "executiveSummary",
        "observations",
        "interpretations",
        "hypotheses",
        "evidenceGaps",
        "changeCandidates",
        "validationPlans",
    }
    assert created["max_tokens"] == 20_000
    assert "# UNTRUSTED_ANALYTICS_DATA" not in counted["system"]
    assert counted["messages"][0]["content"].startswith("# UNTRUSTED_ANALYTICS_DATA")
    assert provider.last_run_metadata["inputTokenCount"] == 99
    assert provider.last_run_metadata["actualInputTokens"] == 101
    assert provider.last_run_metadata["actualOutputTokens"] == 23
    assert provider.last_run_metadata["providerCallCount"] == 2
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
    provider = AnthropicAnalysisProvider(
        model_name="claude-opus-5",
        client=fake_client(valid_response(package)),
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
    assert manifest["providerName"] == "anthropic"
    assert manifest["modelName"] == "claude-opus-5"
    assert manifest["structuredOutputsUsed"] is True
    assert manifest["structuredOutputSchemaMode"] == "hostIdentityStructuredBody"
    assert manifest["inputTokenCount"] == 99
    assert manifest["actualInputTokens"] == 101
    assert manifest["actualOutputTokens"] == 23
    assert manifest["providerGenerationCallCount"] == 1
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

    messages = RetryMessages(valid_response(package))
    provider = AnthropicAnalysisProvider(
        client=SimpleNamespace(messages=messages), sleep=lambda _: None
    )
    provider.generate(package)
    assert provider.last_run_metadata["providerRetryCount"] == 1
    assert provider.last_run_metadata["providerErrorCount"] == 1
    assert provider.last_run_metadata["providerCallCount"] == 3


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

    client = fake_client(valid_response(package))
    client.messages.create = lambda **_kwargs: message_response(
        valid_response(package), stop_reason="max_tokens"
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


def test_empty_or_multiple_text_blocks_are_not_repaired(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    client = fake_client(valid_response(package))
    client.messages.create = lambda **_kwargs: SimpleNamespace(
        stop_reason="end_turn",
        content=[],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    with pytest.raises(AnthropicMalformedResponseError):
        AnthropicAnalysisProvider(client=client, sleep=lambda _: None).generate(package)


def test_anthropic_schema_does_not_bypass_local_evidence_validation(
    tmp_path: Path,
) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    response["observations"][0]["evidenceIds"] = ["EV-not-supplied"]
    provider = AnthropicAnalysisProvider(
        client=fake_client(response), sleep=lambda _: None
    )
    with pytest.raises(AnalysisResponseValidationError):
        run_analysis_with_provider(
            package.request,
            provider,
            output_root=tmp_path / "generated",
            workspace_root=tmp_path,
        )
    assert not (tmp_path / "generated" / "llm-analysis").exists()
