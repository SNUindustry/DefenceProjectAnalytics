"""Native Anthropic Messages API transport for the strict C-2 validator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import time
from typing import Any, Callable, Mapping

from defence_project_analytics.llm_analysis.models import (
    AnalysisPromptPackage,
)
from defence_project_analytics.llm_analysis.anthropic_transport import (
    ANTHROPIC_EVIDENCE_ALIAS_VERSION,
    ANTHROPIC_METRIC_ALIAS_VERSION,
    ANTHROPIC_WARNING_ALIAS_VERSION,
    ANTHROPIC_INPUT_PROJECTION_VERSION,
    ANTHROPIC_STAGE_CONTEXT_VERSION,
    ANTHROPIC_STAGE_B_CONTEXT_VERSION,
    ANTHROPIC_STAGE_C_CONTEXT_VERSION,
    ANTHROPIC_THREE_STAGE_STRICT_TOOL_TRANSPORT_VERSION,
    ANTHROPIC_VALIDATION_PLAN_REF_VERSION,
    ANTHROPIC_OUTPUT_REF_VERSION,
    ANTHROPIC_STAGE_B_IDENTITY_VERSION,
    EvidenceAliasTable,
    MetricAliasTable,
    WarningAliasTable,
    ValidationPlanRefTable,
    StageOutputRefTables,
    assert_provider_metric_output_schema_is_aliased,
    assert_provider_request_has_no_canonical_evidence_ids,
    assert_provider_warning_output_schema_is_aliased,
    build_compact_llm_payload,
    build_evidence_alias_table,
    build_metric_alias_table,
    build_warning_alias_table,
    build_validation_plan_ref_table,
    build_stage_output_ref_tables,
    build_anthropic_stage_prompt_parts,
    collect_anthropic_stage_tool_response,
    compact_payload_digest,
    merge_anthropic_stage_sections,
)
from defence_project_analytics.llm_analysis.loader import verify_source_brief_unchanged
from defence_project_analytics.llm_analysis.provider_errors import (
    AnalysisProviderError,
    AnthropicAuthenticationError,
    AnthropicConnectionError,
    AnthropicContextTooLargeError,
    AnthropicCredentialError,
    AnthropicFlatReconstructionError,
    AnthropicMalformedResponseError,
    AnthropicPermissionError,
    AnthropicRateLimitError,
    AnthropicRefusalError,
    AnthropicRequestValidationError,
    AnthropicResponseTruncatedError,
    AnthropicServerError,
    AnthropicStructuredOutputUnsupportedError,
    AnthropicTimeoutError,
    AnthropicTokenCountError,
    AnthropicUnexpectedStopReasonError,
)
from defence_project_analytics.llm_analysis.provider_schema import (
    ANTHROPIC_STAGE_SPECS,
    ANTHROPIC_THREE_STAGE_STRICT_TOOL_VERSION,
    analysis_response_json_schema,
    anthropic_stage_strict_tools,
    anthropic_strict_tools_profile,
)
from defence_project_analytics.llm_analysis.validator import (
    ValidatedStageA,
    ValidatedStageB,
    ValidatedStageC,
    validate_stage_a,
    validate_stage_b,
    validate_stage_c,
)
from defence_project_analytics.llm_analysis.warning_authority import warning_authority_digest


DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS = 20_000
MAX_NONSTREAMING_ANTHROPIC_OUTPUT_TOKENS = 20_000
MAX_ANTHROPIC_PREFLIGHT_INPUT_TOKENS = 60_000
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 600.0
DEFAULT_ANTHROPIC_MAX_TRANSPORT_RETRIES = 1
_DATA_MARKER = "# UNTRUSTED_ANALYTICS_DATA"


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class _PreparedAnthropicStageRequest:
    stage: str
    model: str
    system: str
    messages: list[dict[str, str]]
    tools: tuple[Mapping[str, Any], ...]
    tool_choice: Mapping[str, Any]
    context_digest: str

    def identity(self) -> Mapping[str, Any]:
        return {
            "stage": self.stage,
            "model": self.model,
            "system": self.system,
            "messages": self.messages,
            "tools": self.tools,
            "toolChoice": self.tool_choice,
            "contextDigest": self.context_digest,
        }

    @property
    def digest(self) -> str:
        return _canonical_digest(self.identity())


def _safe_error_text(error: BaseException) -> str:
    """Return text only for internal classification; never expose it to callers."""

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            return nested["message"].casefold()
        if isinstance(body.get("message"), str):
            return body["message"].casefold()
    return str(error).casefold()


def _safe_error_details(error: BaseException, detail: str) -> dict[str, Any]:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    error_type = None
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("type"), str):
            error_type = nested["type"]
        elif isinstance(body.get("type"), str):
            error_type = body["type"]
    category = "providerRequestRejected"
    safe_message = "Anthropic rejected the provider request."
    if "schema is too complex" in detail or "compiled grammar is too large" in detail:
        category = "schemaComplexity"
        safe_message = "The Structured Outputs schema is too complex for compilation."
    elif "compilation timeout" in detail:
        category = "schemaCompilationTimeout"
        safe_message = "The Structured Outputs schema compilation timed out."
    elif "union" in detail and ("limit" in detail or "too many" in detail):
        category = "schemaUnionLimit"
        safe_message = "The Structured Outputs schema exceeded the union-type limit."
    elif "optional" in detail and ("limit" in detail or "too many" in detail):
        category = "schemaOptionalParameterLimit"
        safe_message = "The Structured Outputs schema exceeded the optional-parameter limit."
    elif "does not support structured" in detail or "structured outputs not supported" in detail:
        category = "modelCapability"
        safe_message = "The selected model or endpoint does not support Structured Outputs."
    elif "output_config" in detail and ("invalid" in detail or "unknown" in detail):
        category = "invalidOutputConfig"
        safe_message = "Anthropic rejected the output_config format."
    elif "tool" in detail and "schema" in detail and (
        "invalid" in detail or "compile" in detail or "complex" in detail
    ):
        category = "strictToolSchemaRejected"
        safe_message = "Anthropic rejected a strict tool input schema."
    elif ("unsupported" in detail or "not supported" in detail) and any(
        keyword.casefold() in detail
        for keyword in (
            "maxItems", "minItems", "maxLength", "minLength", "minimum",
            "maximum", "pattern", "uniqueItems", "additionalProperties",
        )
    ):
        category = "unsupportedSchemaConstraint"
        safe_message = "The Structured Outputs schema contained an unsupported constraint."
    result: dict[str, Any] = {
        "httpStatus": status if isinstance(status, int) else None,
        "anthropicErrorType": error_type,
        "errorCategory": category,
        "safeMessage": safe_message,
    }
    request_id = getattr(error, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9_-]+", request_id):
        result["requestId"] = request_id
    return result


def map_anthropic_error(error: BaseException) -> AnalysisProviderError:
    """Map SDK exceptions without copying request, credential, or raw error text."""

    name = type(error).__name__
    status = getattr(error, "status_code", None)
    detail = _safe_error_text(error)
    details = _safe_error_details(error, detail)
    if name == "AuthenticationError" or status == 401:
        return AnthropicAuthenticationError("Anthropic authentication failed.", details=details)
    if name == "PermissionDeniedError" or status == 403:
        return AnthropicPermissionError("Anthropic permission was denied.", details=details)
    if name == "RateLimitError" or status == 429:
        return AnthropicRateLimitError("Anthropic rate limit was reached.", details=details)
    if name == "APITimeoutError":
        return AnthropicTimeoutError("Anthropic request timed out.", details=details)
    if name == "APIConnectionError":
        return AnthropicConnectionError("Anthropic connection failed.", details=details)
    if status is not None and int(status) >= 500:
        return AnthropicServerError("Anthropic server returned a transient error.", details=details)
    if name in {"BadRequestError", "UnprocessableEntityError"} or status in {400, 413, 422}:
        if any(value in detail for value in (
            "context window", "context length", "prompt is too long", "too many tokens",
        )):
            return AnthropicContextTooLargeError(
                "Anthropic rejected the request because the context was too large.",
                details=details,
            )
        if details["errorCategory"] == "modelCapability":
            return AnthropicStructuredOutputUnsupportedError(
                "The selected Anthropic model or endpoint rejected Structured Outputs.",
                details=details,
            )
        return AnthropicRequestValidationError(
            "Anthropic rejected the request.", details=details
        )
    return AnalysisProviderError("Anthropic SDK transport failed.", details=details)


def _message_parts(prompt: AnalysisPromptPackage) -> tuple[str, list[dict[str, str]]]:
    before, marker, after = prompt.prompt.partition(_DATA_MARKER)
    if not marker:
        raise AnthropicRequestValidationError(
            "The provider-neutral prompt is missing the untrusted-data boundary."
        )
    system = before.rstrip()
    user = marker + after
    return system, [{"role": "user", "content": user}]


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class AnthropicAnalysisProvider:
    """One token-count preflight followed by one Structured Outputs generation."""

    provider_name = "anthropic"

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_ANTHROPIC_MODEL,
        max_output_tokens: int = DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
        max_transport_retries: int = DEFAULT_ANTHROPIC_MAX_TRANSPORT_RETRIES,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must be nonblank")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if max_output_tokens > MAX_NONSTREAMING_ANTHROPIC_OUTPUT_TOKENS:
            raise ValueError(
                "max_output_tokens exceeds the safe non-streaming C-2 limit of 20000"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_transport_retries < 0 or max_transport_retries > 2:
            raise ValueError("max_transport_retries must be between 0 and 2")
        self.model_name = model_name.strip()
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_seconds = float(timeout_seconds)
        self.max_transport_retries = int(max_transport_retries)
        self._injected_client = client
        self._sleep = sleep
        self.last_run_metadata: dict[str, Any] = {}
        self._reset_counters()

    def _reset_counters(self) -> None:
        self._provider_call_count = 0
        self._token_count_call_count = 0
        self._generation_call_count = 0
        self._error_count = 0
        self._retry_count = 0

    def _sync_counter_metadata(self) -> None:
        self.last_run_metadata.update({
            "providerCallCount": self._provider_call_count,
            "providerTokenCountCallCount": self._token_count_call_count,
            "providerGenerationCallCount": self._generation_call_count,
            "providerErrorCount": self._error_count,
            "providerRetryCount": self._retry_count,
            "configuredMaxTransportRetries": self.max_transport_retries,
        })

    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise AnthropicCredentialError(
                "ANTHROPIC_API_KEY is not visible to this process."
            )
        try:
            import anthropic

            # The official SDK reads ANTHROPIC_API_KEY itself. SDK retry is disabled
            # so the bounded retry count below remains observable and deterministic.
            return anthropic.Anthropic(
                max_retries=0,
                timeout=self.timeout_seconds,
            )
        except AnthropicCredentialError:
            raise
        except Exception as error:
            raise map_anthropic_error(error) from None

    def _invoke(self, operation: str, call: Callable[[], Any]) -> Any:
        attempts = 0
        while True:
            self._provider_call_count += 1
            if operation == "count_tokens":
                self._token_count_call_count += 1
            else:
                self._generation_call_count += 1
            self._sync_counter_metadata()
            try:
                result = call()
                self._sync_counter_metadata()
                return result
            except AnalysisProviderError:
                raise
            except Exception as error:
                mapped = map_anthropic_error(error)
                if operation == "count_tokens" and type(mapped) is AnalysisProviderError:
                    mapped = AnthropicTokenCountError(
                        "Anthropic token counting failed."
                    )
                self._error_count += 1
                self._sync_counter_metadata()
                # A generation call is never replayed: a transport failure may
                # occur after the provider accepted or billed the request.
                # Bounded retry remains available only for the read-only token
                # preflight.
                if (
                    operation == "count_tokens"
                    and mapped.retryable
                    and attempts < self.max_transport_retries
                ):
                    attempts += 1
                    self._retry_count += 1
                    self._sync_counter_metadata()
                    self._sleep(min(2.0**attempts, 2.0))
                    continue
                raise mapped from None
    def _parse_stage_response(
        self,
        response: Any,
        stage: str,
        evidence_aliases: EvidenceAliasTable,
        metric_aliases: MetricAliasTable,
        warning_aliases: WarningAliasTable,
        validation_plan_refs: ValidationPlanRefTable | None = None,
        output_refs: StageOutputRefTables | None = None,
    ) -> tuple[Mapping[str, Any], int, int, str | None]:
        stop_reason = _attr(response, "stop_reason")
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            raise AnthropicResponseTruncatedError(
                "Anthropic stopped before completing the staged structured response.",
                details={"stage": stage, "stopReason": str(stop_reason)},
            )
        if stop_reason == "refusal":
            raise AnthropicRefusalError(
                "Anthropic declined the analysis request.",
                details={"stage": stage, "stopReason": str(stop_reason)},
            )
        if stop_reason != "tool_use":
            raise AnthropicUnexpectedStopReasonError(
                "Anthropic did not finish the analysis stage with strict tool use.",
                details={"stage": stage, "stopReason": str(stop_reason)},
            )
        request_id = _attr(response, "id")
        safe_request_id = request_id if isinstance(request_id, str) else None
        content = tuple(_attr(response, "content", ()) or ())
        try:
            collected = collect_anthropic_stage_tool_response(
                content,
                stage,
                evidence_aliases=evidence_aliases if stage in {"A", "B"} else None,
                metric_aliases=metric_aliases if stage in {"B", "C"} else None,
                warning_aliases=warning_aliases if stage in {"A", "B"} else None,
                validation_plan_refs=(
                    validation_plan_refs if stage == "C" else None
                ),
                output_refs=output_refs if stage in {"B", "C"} else None,
            )
        except AnthropicFlatReconstructionError as error:
            raise AnthropicFlatReconstructionError(
                str(error),
                details={
                    **error.details,
                    "safeRequestId": safe_request_id,
                },
            ) from None
        return (
            collected.sections,
            collected.observed_tool_count,
            collected.ignored_text_block_count,
            safe_request_id,
        )

    def _refresh_aggregate_metadata(self) -> None:
        stages = self.last_run_metadata.get("stages", [])
        if not isinstance(stages, list):
            stages = []
        preflight = [
            item.get("preflightInputTokens")
            for item in stages
            if isinstance(item, Mapping)
            and isinstance(item.get("preflightInputTokens"), int)
        ]
        actual_input = [
            item.get("actualInputTokens")
            for item in stages
            if isinstance(item, Mapping)
            and isinstance(item.get("actualInputTokens"), int)
        ]
        actual_output = [
            item.get("actualOutputTokens")
            for item in stages
            if isinstance(item, Mapping)
            and isinstance(item.get("actualOutputTokens"), int)
        ]
        request_digests = [
            str(item["requestDigest"])
            for item in stages
            if isinstance(item, Mapping) and item.get("requestDigest")
        ]
        tools_digests = [
            str(item["schemaDigest"])
            for item in stages
            if isinstance(item, Mapping) and item.get("schemaDigest")
        ]
        aggregate_request_digest = (
            _canonical_digest(request_digests) if request_digests else None
        )
        aggregate_tools_digest = (
            _canonical_digest(tools_digests) if tools_digests else None
        )
        self.last_run_metadata.update({
            "providerRequestDigest": aggregate_request_digest,
            "anthropicToolsDigest": aggregate_tools_digest,
            "anthropicPreflightToolsDigest": aggregate_tools_digest,
            "anthropicGenerationToolsDigest": aggregate_tools_digest,
            "anthropicPreflightRequestDigest": aggregate_request_digest,
            "anthropicGenerationRequestDigest": aggregate_request_digest,
            "anthropicExpectedToolCount": sum(
                int(item.get("expectedToolCount", 0))
                for item in stages if isinstance(item, Mapping)
            ),
            "anthropicObservedToolCount": sum(
                int(item.get("observedToolCount", 0))
                for item in stages if isinstance(item, Mapping)
            ),
            "anthropicIgnoredTextBlockCount": sum(
                int(item.get("ignoredTextBlockCount", 0))
                for item in stages if isinstance(item, Mapping)
            ),
            "inputTokenCount": sum(preflight),
            "totalPreflightInputTokens": sum(preflight),
            "actualInputTokens": sum(actual_input) if actual_input else None,
            "actualOutputTokens": sum(actual_output) if actual_output else None,
        })
        self._sync_counter_metadata()

    def _execute_stage(
        self,
        client: Any,
        prompt: AnalysisPromptPackage,
        stage: str,
        *,
        stage_a: ValidatedStageA | None = None,
        stage_b: ValidatedStageB | None = None,
        evidence_aliases: EvidenceAliasTable,
        metric_aliases: MetricAliasTable,
        warning_aliases: WarningAliasTable,
        validation_plan_refs: ValidationPlanRefTable | None = None,
        output_refs: StageOutputRefTables | None = None,
    ) -> ValidatedStageA | ValidatedStageB | ValidatedStageC:
        system, messages, context = build_anthropic_stage_prompt_parts(
            prompt,
            stage,
            stage_a=stage_a,
            stage_b=stage_b,
            evidence_aliases=evidence_aliases,
            metric_aliases=metric_aliases,
            warning_aliases=warning_aliases,
            validation_plan_refs=validation_plan_refs,
            output_refs=output_refs,
        )
        tools = anthropic_stage_strict_tools(
            stage,
            evidence_refs=(
                evidence_aliases.provider_refs if stage in {"A", "B"} else None
            ),
            metric_refs=(
                metric_aliases.provider_refs if stage in {"B", "C"} else None
            ),
            warning_refs=(
                warning_aliases.provider_refs if stage in {"A", "B"} else None
            ),
            validation_plan_refs=(
                validation_plan_refs.provider_refs
                if stage == "C" and validation_plan_refs is not None
                else None
            ),
            evidence_gap_refs=(
                output_refs.evidence_gaps.provider_refs
                if stage in {"B", "C"} and output_refs is not None
                else None
            ),
            observation_refs=(
                output_refs.observations.provider_refs
                if stage == "C" and output_refs is not None
                else None
            ),
            hypothesis_refs=(
                output_refs.hypotheses.provider_refs
                if stage == "C"
                and output_refs is not None
                and output_refs.hypotheses is not None
                else None
            ),
            change_candidate_refs=(
                output_refs.change_candidates.provider_refs
                if stage == "C"
                and output_refs is not None
                and output_refs.change_candidates is not None
                else None
            ),
        )
        tool_choice = {"type": "any", "disable_parallel_tool_use": False}
        context_digest = compact_payload_digest(context)
        prepared = _PreparedAnthropicStageRequest(
            stage=stage,
            model=self.model_name,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            context_digest=context_digest,
        )
        tools_digest = _canonical_digest(tools)
        tool_choice_digest = _canonical_digest(tool_choice)
        request_digest = prepared.digest
        stage_metadata: dict[str, Any] = {
            "stage": stage,
            "toolNames": [str(tool["name"]) for tool in tools],
            "schemaProfile": anthropic_strict_tools_profile(tools),
            "schemaDigest": tools_digest,
            "contextDigest": context_digest,
            "requestDigest": request_digest,
            "preflightInputTokens": None,
            "actualInputTokens": None,
            "actualOutputTokens": None,
            "tokenCountCallCount": 0,
            "generationCallCount": 0,
            "retryCount": 0,
            "validationStatus": "pending",
            "safeRequestId": None,
            "stopReason": None,
            "expectedToolCount": len(tools),
            "observedToolCount": 0,
            "ignoredTextBlockCount": 0,
            "compactEvidenceCount": (
                len(context.get("evidence", []))
                if isinstance(context.get("evidence"), list)
                else 0
            ),
            "canonicalEvidenceIdLeakageCheckPassed": False,
            "metricAliasOutputSchemaCheckPassed": False,
            "warningAliasOutputSchemaCheckPassed": False,
        }
        stages = self.last_run_metadata.setdefault("stages", [])
        if not isinstance(stages, list):
            raise AnthropicRequestValidationError(
                "Anthropic stage metadata container is invalid.",
                details={"stage": stage},
            )
        stages.append(stage_metadata)
        start_token_calls = self._token_count_call_count
        start_generation_calls = self._generation_call_count
        start_retries = self._retry_count
        try:
            if stage in {"A", "B"}:
                assert_provider_request_has_no_canonical_evidence_ids(
                    prepared.identity(), evidence_aliases, stage=stage
                )
            stage_metadata["canonicalEvidenceIdLeakageCheckPassed"] = True
            assert_provider_metric_output_schema_is_aliased(
                prepared.tools, metric_aliases, stage=stage
            )
            stage_metadata["metricAliasOutputSchemaCheckPassed"] = True
            assert_provider_warning_output_schema_is_aliased(
                prepared.tools, warning_aliases, stage=stage
            )
            stage_metadata["warningAliasOutputSchemaCheckPassed"] = True
            verify_source_brief_unchanged(prompt.source)
            count = self._invoke(
                "count_tokens",
                lambda: client.messages.count_tokens(
                    model=prepared.model,
                    system=prepared.system,
                    messages=prepared.messages,
                    tools=prepared.tools,
                    tool_choice=prepared.tool_choice,
                ),
            )
            input_token_count = _attr(count, "input_tokens")
            if not isinstance(input_token_count, int) or input_token_count < 0:
                raise AnthropicTokenCountError(
                    "Anthropic token count response was invalid.",
                    details={"stage": stage},
                )
            stage_metadata["preflightInputTokens"] = input_token_count
            self._refresh_aggregate_metadata()
            if input_token_count >= MAX_ANTHROPIC_PREFLIGHT_INPUT_TOKENS:
                raise AnthropicContextTooLargeError(
                    "Anthropic stage input exceeds the C-2A 60000-token preflight gate.",
                    details={"stage": stage, "preflightInputTokens": input_token_count},
                )

            verify_source_brief_unchanged(prompt.source)
            if (
                _canonical_digest(prepared.tools) != tools_digest
                or _canonical_digest(prepared.tool_choice) != tool_choice_digest
                or prepared.digest != request_digest
                or prepared.context_digest != context_digest
            ):
                raise AnthropicRequestValidationError(
                    "Anthropic token preflight and generation requests differ.",
                    details={"stage": stage},
                )
            response = self._invoke(
                "generation",
                lambda: client.messages.create(
                    model=prepared.model,
                    max_tokens=self.max_output_tokens,
                    system=prepared.system,
                    messages=prepared.messages,
                    tools=prepared.tools,
                    tool_choice=prepared.tool_choice,
                ),
            )
            usage = _attr(response, "usage")
            actual_input = _attr(usage, "input_tokens")
            actual_output = _attr(usage, "output_tokens")
            if not isinstance(actual_input, int) or not isinstance(actual_output, int):
                raise AnthropicMalformedResponseError(
                    "Anthropic response usage metadata was invalid.",
                    details={"stage": stage},
                )
            stage_metadata["actualInputTokens"] = actual_input
            stage_metadata["actualOutputTokens"] = actual_output
            stage_metadata["stopReason"] = str(_attr(response, "stop_reason"))
            sections, observed, ignored, safe_request_id = self._parse_stage_response(
                response,
                stage,
                evidence_aliases,
                metric_aliases,
                warning_aliases,
                validation_plan_refs,
                output_refs,
            )
            stage_metadata["observedToolCount"] = observed
            stage_metadata["ignoredTextBlockCount"] = ignored
            stage_metadata["safeRequestId"] = safe_request_id
            verify_source_brief_unchanged(prompt.source)
            if stage == "A":
                validated = validate_stage_a(prompt, sections)
            elif stage == "B":
                if stage_a is None:
                    raise AnthropicRequestValidationError(
                        "Stage B is missing validated Stage A context.",
                        details={"stage": stage},
                    )
                validated = validate_stage_b(prompt, sections, stage_a)
            else:
                if stage_a is None or stage_b is None:
                    raise AnthropicRequestValidationError(
                        "Stage C is missing validated prior-stage context.",
                        details={"stage": stage},
                    )
                validated = validate_stage_c(
                    prompt,
                    sections,
                    stage_a,
                    stage_b,
                )
            stage_metadata["validationStatus"] = "passed"
            return validated
        except AnthropicFlatReconstructionError as error:
            for key in (
                "processingBoundary",
                "section",
                "itemIndex",
                "reconstructionComponent",
                "reconstructionInvariant",
                "safeRequestId",
                "leftCount",
                "rightCount",
                "metricCount",
                "directionCount",
                "aliasDomainSize",
                "expectedCount",
                "uniqueObservedCount",
                "observedCount",
                "maximumCount",
                "observedType",
                "expectedType",
                "reason",
                "state",
            ):
                if key in error.details:
                    stage_metadata[key] = error.details[key]
            stage_metadata["validationStatus"] = "failed"
            raise
        except Exception:
            stage_metadata["validationStatus"] = "failed"
            raise
        finally:
            stage_metadata["tokenCountCallCount"] = (
                self._token_count_call_count - start_token_calls
            )
            stage_metadata["generationCallCount"] = (
                self._generation_call_count - start_generation_calls
            )
            stage_metadata["retryCount"] = self._retry_count - start_retries
            self._refresh_aggregate_metadata()

    def generate(self, prompt: AnalysisPromptPackage) -> Mapping[str, object]:
        self._reset_counters()
        client = self._client()
        compact_payload = build_compact_llm_payload(prompt)
        evidence_aliases = build_evidence_alias_table(prompt)
        metric_aliases = build_metric_alias_table()
        warning_aliases = build_warning_alias_table(prompt)
        evidence_artifact = next(
            (
                artifact for artifact in prompt.source.artifacts
                if artifact.relative_path == "evidence.json"
            ),
            None,
        )
        contract_schema_digest = _canonical_digest(
            analysis_response_json_schema(prompt)
        )
        tool_choice = {"type": "any", "disable_parallel_tool_use": False}
        self.last_run_metadata = {
            "providerMode": "anthropicApi",
            "transportMode": "anthropicThreeStageStrictTools",
            "structuredOutputsUsed": True,
            "anthropicStrictToolsUsed": True,
            "structuredOutputSchemaMode": "threeStageStrictToolsV1",
            "responseContractSchemaDigest": contract_schema_digest,
            "anthropicStrictToolTransportVersion": (
                ANTHROPIC_THREE_STAGE_STRICT_TOOL_TRANSPORT_VERSION
            ),
            "anthropicThreeStageStrictToolTransportVersion": (
                ANTHROPIC_THREE_STAGE_STRICT_TOOL_TRANSPORT_VERSION
            ),
            "anthropicThreeStageStrictToolVersion": (
                ANTHROPIC_THREE_STAGE_STRICT_TOOL_VERSION
            ),
            "anthropicStageContextVersion": ANTHROPIC_STAGE_CONTEXT_VERSION,
            "anthropicStageContextVersions": {
                "A": ANTHROPIC_STAGE_CONTEXT_VERSION,
                "B": ANTHROPIC_STAGE_B_CONTEXT_VERSION,
                "C": ANTHROPIC_STAGE_C_CONTEXT_VERSION,
            },
            "anthropicToolChoiceDigest": _canonical_digest(tool_choice),
            "anthropicInputProjectionVersion": ANTHROPIC_INPUT_PROJECTION_VERSION,
            "anthropicEvidenceAliasVersion": ANTHROPIC_EVIDENCE_ALIAS_VERSION,
            "evidenceAliasCount": len(evidence_aliases.provider_refs),
            "evidenceAliasDigest": evidence_aliases.digest,
            "anthropicMetricAliasVersion": ANTHROPIC_METRIC_ALIAS_VERSION,
            "metricAliasCount": len(metric_aliases.provider_refs),
            "metricAliasDigest": metric_aliases.digest,
            "warningAuthorityDigest": warning_authority_digest(prompt),
            "anthropicWarningAliasVersion": ANTHROPIC_WARNING_ALIAS_VERSION,
            "warningAliasCount": len(warning_aliases.provider_refs),
            "warningAliasDigest": warning_aliases.digest,
            "anthropicOutputRefVersion": ANTHROPIC_OUTPUT_REF_VERSION,
            "anthropicStageBIdentityVersion": ANTHROPIC_STAGE_B_IDENTITY_VERSION,
            "compactPayloadDigest": compact_payload_digest(compact_payload),
            "canonicalEvidenceDigest": (
                evidence_artifact.sha256 if evidence_artifact is not None else None
            ),
            "compactEvidenceCount": len(compact_payload["evidence"]),
            "stageCount": len(ANTHROPIC_STAGE_SPECS),
            "stages": [],
            "inputTokenCount": 0,
            "totalPreflightInputTokens": 0,
            "actualInputTokens": None,
            "actualOutputTokens": None,
        }
        self._sync_counter_metadata()

        stage_a = self._execute_stage(
            client,
            prompt,
            "A",
            evidence_aliases=evidence_aliases,
            metric_aliases=metric_aliases,
            warning_aliases=warning_aliases,
        )
        if not isinstance(stage_a, ValidatedStageA):
            raise AnthropicRequestValidationError(
                "Anthropic Stage A returned an invalid host result.",
                details={"stage": "A"},
            )
        stage_a_output_refs = build_stage_output_ref_tables(stage_a)
        self.last_run_metadata.update({
            "evidenceGapRefCount": len(
                stage_a_output_refs.evidence_gaps.provider_refs
            ),
            "evidenceGapRefDigest": stage_a_output_refs.evidence_gaps.digest,
        })
        stage_b = self._execute_stage(
            client,
            prompt,
            "B",
            stage_a=stage_a,
            evidence_aliases=evidence_aliases,
            metric_aliases=metric_aliases,
            warning_aliases=warning_aliases,
            output_refs=stage_a_output_refs,
        )
        if not isinstance(stage_b, ValidatedStageB):
            raise AnthropicRequestValidationError(
                "Anthropic Stage B returned an invalid host result.",
                details={"stage": "B"},
            )
        validation_plan_refs = build_validation_plan_ref_table(stage_b)
        stage_output_refs = build_stage_output_ref_tables(stage_a, stage_b)
        self.last_run_metadata.update({
            "anthropicValidationPlanRefVersion": ANTHROPIC_VALIDATION_PLAN_REF_VERSION,
            "validationPlanRefCount": len(validation_plan_refs.provider_refs),
            "validationPlanRefDigest": validation_plan_refs.digest,
            "observationRefCount": len(stage_output_refs.observations.provider_refs),
            "observationRefDigest": stage_output_refs.observations.digest,
            "hypothesisRefCount": len(
                stage_output_refs.hypotheses.provider_refs
                if stage_output_refs.hypotheses is not None else ()
            ),
            "hypothesisRefDigest": (
                stage_output_refs.hypotheses.digest
                if stage_output_refs.hypotheses is not None else None
            ),
            "changeCandidateRefCount": len(
                stage_output_refs.change_candidates.provider_refs
                if stage_output_refs.change_candidates is not None else ()
            ),
            "changeCandidateRefDigest": (
                stage_output_refs.change_candidates.digest
                if stage_output_refs.change_candidates is not None else None
            ),
        })
        stage_c = self._execute_stage(
            client,
            prompt,
            "C",
            stage_a=stage_a,
            stage_b=stage_b,
            evidence_aliases=evidence_aliases,
            metric_aliases=metric_aliases,
            warning_aliases=warning_aliases,
            validation_plan_refs=validation_plan_refs,
            output_refs=stage_output_refs,
        )
        if not isinstance(stage_c, ValidatedStageC):
            raise AnthropicRequestValidationError(
                "Anthropic Stage C returned an invalid host result.",
                details={"stage": "C"},
            )
        verify_source_brief_unchanged(prompt.source)
        merged = merge_anthropic_stage_sections(
            prompt,
            stage_a,
            stage_b,
            stage_c.canonical_sections,
        )
        self._refresh_aggregate_metadata()
        return merged

__all__ = [
    "AnthropicAnalysisProvider",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS",
    "MAX_NONSTREAMING_ANTHROPIC_OUTPUT_TOKENS",
    "MAX_ANTHROPIC_PREFLIGHT_INPUT_TOKENS",
    "map_anthropic_error",
]
