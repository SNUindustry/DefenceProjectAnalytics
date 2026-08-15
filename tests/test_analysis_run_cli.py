from __future__ import annotations

import json
from pathlib import Path

from defence_project_analytics import cli
from defence_project_analytics.llm_analysis.provider_errors import (
    AnthropicRequestValidationError,
)
from llm_analysis_fixtures import make_package, valid_response


def test_analysis_run_cli_uses_anthropic_path_without_bigquery(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    source, package = make_package(tmp_path)

    class FakeProvider:
        provider_name = "anthropic"
        model_name = "claude-opus-5"
        last_run_metadata = {
            "providerMode": "anthropicApi",
            "providerCallCount": 2,
            "providerTokenCountCallCount": 1,
            "providerGenerationCallCount": 1,
            "providerRetryCount": 0,
            "inputTokenCount": 99,
            "actualInputTokens": 101,
            "actualOutputTokens": 23,
            "structuredOutputsUsed": True,
        }

        def __init__(self, **_kwargs):
            pass

        def generate(self, _prompt):
            return valid_response(package)

    monkeypatch.setattr(cli, "AnthropicAnalysisProvider", FakeProvider)
    monkeypatch.setattr(
        cli, "get_client", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("BigQuery"))
    )
    output = tmp_path / "out"
    code = cli.main([
        "analysis-run",
        "--source-brief", str(source),
        "--provider", "anthropic",
        "--output-root", str(output),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["providerName"] == "anthropic"
    assert payload["inputTokenCount"] == 99
    assert payload["providerCallCount"] == 2
    assert payload["validatorStatus"] == "Valid"


def test_analysis_run_cli_reports_usage_when_local_validation_rejects(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    source, package = make_package(tmp_path)
    invalid = valid_response(package)
    invalid["observations"][0]["evidenceIds"] = ["EV-not-supplied"]

    class InvalidProvider:
        provider_name = "anthropic"
        model_name = "claude-opus-5"
        last_run_metadata = {
            "providerMode": "anthropicApi",
            "providerCallCount": 2,
            "providerRetryCount": 0,
            "inputTokenCount": 99,
            "actualInputTokens": 101,
            "actualOutputTokens": 23,
        }

        def __init__(self, **_kwargs):
            pass

        def generate(self, _prompt):
            return invalid

    monkeypatch.setattr(cli, "AnthropicAnalysisProvider", InvalidProvider)
    output = tmp_path / "out"
    code = cli.main([
        "analysis-run",
        "--source-brief", str(source),
        "--provider", "anthropic",
        "--output-root", str(output),
    ])
    assert code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["errorCode"] == "ANALYSIS_RESPONSE_VALIDATION_FAILED"
    assert payload["actualInputTokens"] == 101
    assert payload["actualOutputTokens"] == 23
    assert payload["providerCallCount"] == 2
    assert not (output / "llm-analysis").exists()


def test_analysis_run_cli_reports_sanitized_provider_diagnostics(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    source, _ = make_package(tmp_path)

    class RejectedProvider:
        provider_name = "anthropic"
        model_name = "claude-opus-5"
        last_run_metadata = {
            "providerCallCount": 2,
            "providerTokenCountCallCount": 1,
            "providerGenerationCallCount": 1,
            "providerErrorCount": 1,
            "providerRetryCount": 0,
            "inputTokenCount": 123,
            "actualInputTokens": None,
            "actualOutputTokens": None,
        }

        def __init__(self, **_kwargs):
            pass

        def generate(self, _prompt):
            raise AnthropicRequestValidationError(
                "Anthropic rejected the request.",
                details={
                    "httpStatus": 400,
                    "anthropicErrorType": "invalid_request_error",
                    "errorCategory": "schemaComplexity",
                    "safeMessage": "The Structured Outputs schema is too complex for compilation.",
                },
            )

    monkeypatch.setattr(cli, "AnthropicAnalysisProvider", RejectedProvider)
    code = cli.main([
        "analysis-run",
        "--source-brief", str(source),
        "--provider", "anthropic",
        "--output-root", str(tmp_path / "out"),
    ])
    assert code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["inputTokenCount"] == 123
    assert payload["providerGenerationCallCount"] == 1
    assert payload["providerRetryCount"] == 0
    assert payload["providerDiagnostics"]["errorCategory"] == "schemaComplexity"
